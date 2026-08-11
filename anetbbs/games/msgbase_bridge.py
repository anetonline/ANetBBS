#!/usr/bin/env python3
"""Synchronous CLI bridge letting a Synchronet-JS door's real MsgBase
calls (see synchronet_compat.py's MsgBase class) reach ANetBBS's actual
echomail data (EchoArea/EchomailMessage in anetbbs/models.py).

There's no existing IPC path from the Node.js door subprocess back into
the Flask/SQLAlchemy process -- this is that path, matching the same
one-shot-synchronous-subprocess pattern already used elsewhere in the
compat shim (console.exec's own child_process.spawnSync). One operation
per invocation; a throwaway Flask+DB app context is pushed for each call
(same shape as door_runner.py's play_door_game_telnet bootstrap) since
this runs as a brand new process every time, not a long-lived service.

Usage:
    msgbase_bridge.py open <area_tag>
    msgbase_bridge.py get_index <area_tag> <after_id>
    msgbase_bridge.py get_header <area_tag> <msg_id>
    msgbase_bridge.py get_body <area_tag> <msg_id>
    msgbase_bridge.py save_msg <area_tag> <json: {"to":..,"from":..,"subject":..,"body":..}>

Always prints exactly one JSON object to stdout and exits 0, even on a
handled error (the caller checks the "ok" field) -- exit code is only
nonzero for a genuine crash (bad args, unhandled exception), matching
how the JS-side wrapper distinguishes "the door asked for something
invalid" (ok:false, keep going) from "the bridge itself is broken"
(nonzero exit, surface loudly).
"""
import json
import os
import sys

# Spawned as a plain subprocess (via Node's child_process.spawnSync from
# the compat shim), so there's no guarantee `anetbbs` is already
# importable the way it is for code run through the normal app entry
# points -- make sure the repo root (two levels up from this file) is on
# sys.path regardless of the caller's own cwd/PYTHONPATH.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _app():
    """Push a throwaway Flask+DB app context, same shape as
    door_runner.py's play_door_game_telnet bootstrap -- this process is
    spawned fresh per call, so there's no existing context to reuse."""
    from flask import Flask
    from anetbbs.config import get_config
    from anetbbs.models import db

    app = Flask(__name__)
    app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
    db.init_app(app)
    return app


def _find_area(tag):
    from anetbbs.models import EchoArea
    return EchoArea.query.filter(EchoArea.tag.ilike(tag)).first()


def op_open(area_tag, _args):
    area = _find_area(area_tag)
    if not area:
        return {'ok': False, 'error': 'area not found: ' + area_tag}
    from anetbbs.models import EchomailMessage, db
    last_msg = db.session.query(
        db.func.max(EchomailMessage.id)).filter(
        EchomailMessage.area_id == area.id).scalar() or 0
    return {'ok': True, 'area_id': area.id, 'network_id': area.network_id,
            'last_msg': last_msg}


def op_get_index(area_tag, args):
    area = _find_area(area_tag)
    if not area:
        return {'ok': False, 'error': 'area not found: ' + area_tag}
    if not args:
        return {'ok': False, 'error': 'get_index requires <after_id>'}
    after_id = int(args[0])
    from anetbbs.models import EchomailMessage
    rows = (EchomailMessage.query
            .filter(EchomailMessage.area_id == area.id,
                    EchomailMessage.id > after_id)
            .order_by(EchomailMessage.id)
            .all())
    # Header + body fields embedded inline (one query already has them
    # loaded) so the JS-side MsgBase shim can cache a whole area's worth
    # of messages from THIS one subprocess call, instead of spawning a
    # fresh Python process + Flask app per message via separate
    # get_header/get_body calls. Real report: DOVE-Net score-sharing in
    # Minesweeper's own get_winners() calls get_msg_header()+get_msg_body()
    # once per matching index entry in a tight loop -- with a synced
    # area holding a normal amount of InterBBS history that was hundreds
    # of extra subprocess spawns (each with its own Flask+SQLAlchemy
    # startup cost), easily minutes of wall time with no progress
    # indicator -- indistinguishable from a hang. See MsgBase.get_index's
    # own comment in synchronet_compat.py for the caching side of this
    # fix. Same field shapes as op_get_header/op_get_body's own responses
    # (from_net_type mirrors real Synchronet semantics: only a message
    # that genuinely arrived via the network counts as a real InterBBS
    # win).
    entries = [{'number': r.id, 'to': r.to_name or '', 'subject': r.subject or '',
                'from': r.from_name or '', 'body': r.body or '',
                'from_net_type': (r.direction == 'inbound'),
                'from_net_addr': r.from_address or ''}
               for r in rows]
    return {'ok': True, 'entries': entries}


def op_get_header(area_tag, args):
    area = _find_area(area_tag)
    if not area:
        return {'ok': False, 'error': 'area not found: ' + area_tag}
    if not args:
        return {'ok': False, 'error': 'get_header requires <msg_id>'}
    msg_id = int(args[0])
    from anetbbs.models import EchomailMessage
    row = EchomailMessage.query.filter_by(id=msg_id, area_id=area.id).first()
    if not row:
        return {'ok': False, 'error': 'message not found: ' + str(msg_id)}
    # from_net_type mirrors real Synchronet semantics: only a message that
    # genuinely arrived via the network counts as a real InterBBS post --
    # a door's own outbound post, re-read from the same area before it's
    # ever round-tripped through the network, must NOT look like a
    # network win to itself. direction='inbound' is exactly that signal.
    return {'ok': True, 'header': {
        'from': row.from_name or '', 'to': row.to_name or '',
        'subject': row.subject or '', 'number': row.id,
        'from_net_type': (row.direction == 'inbound'),
        'from_net_addr': row.from_address or '',
    }}


def op_get_body(area_tag, args):
    area = _find_area(area_tag)
    if not area:
        return {'ok': False, 'error': 'area not found: ' + area_tag}
    if not args:
        return {'ok': False, 'error': 'get_body requires <msg_id>'}
    msg_id = int(args[0])
    from anetbbs.models import EchomailMessage
    row = EchomailMessage.query.filter_by(id=msg_id, area_id=area.id).first()
    if not row:
        return {'ok': False, 'error': 'message not found: ' + str(msg_id)}
    return {'ok': True, 'body': row.body or ''}


def op_save_msg(area_tag, args):
    area = _find_area(area_tag)
    if not area:
        return {'ok': False, 'error': 'area not found: ' + area_tag}
    if not args:
        return {'ok': False, 'error': 'save_msg requires a JSON payload'}
    try:
        payload = json.loads(args[0])
    except ValueError as exc:
        return {'ok': False, 'error': 'bad JSON payload: ' + str(exc)}

    from anetbbs.models import db, EchomailMessage
    from anetbbs.echomail.tosser import toss_message

    # Minimal field set the existing outbound pipeline (leaf poll AND hub
    # fan-out) already picks up with no extra wiring -- exactly mirrors
    # the two existing local-compose call sites (bbs_ui.py's
    # _compose_echomail, web/echomail.py's compose()). msg_id is
    # deliberately left unset -- make_msgid() generates it lazily at
    # pack time, same as every other outbound message.
    msg = EchomailMessage(
        area_id=area.id,
        network_id=area.network_id,
        from_name=str(payload.get('from') or '')[:100],
        to_name=str(payload.get('to') or '')[:100],
        subject=str(payload.get('subject') or '')[:200],
        body=str(payload.get('body') or ''),
        direction='outbound',
    )
    db.session.add(msg)
    db.session.commit()
    toss_message(msg.id)
    return {'ok': True, 'id': msg.id}


_OPS = {
    'open': op_open,
    'get_index': op_get_index,
    'get_header': op_get_header,
    'get_body': op_get_body,
    'save_msg': op_save_msg,
}


def main(argv):
    if len(argv) < 3:
        print(json.dumps({'ok': False, 'error': 'usage: msgbase_bridge.py <op> <area_tag> [args...]'}))
        return
    op_name, area_tag = argv[1], argv[2]
    fn = _OPS.get(op_name)
    if fn is None:
        print(json.dumps({'ok': False, 'error': 'unknown op: ' + op_name}))
        return
    app = _app()
    with app.app_context():
        result = fn(area_tag, argv[3:])
    print(json.dumps(result))


if __name__ == '__main__':
    main(sys.argv)
