# anetbbs/echomail/fsxnet_sync.py
"""
fsxNet IBOL / IBLC -- real wire-format InterBBS Oneliners and Last
Callers, speaking the actual Synchronet mod protocol over fsxNet's
shared FSX_DAT echo area (zone 21). Ported from the real reference
scripts (iblc.js v0.250508 and ibol.js v0.250624, by Craig Hendricks/
codefenix, ConstructiveChaos BBS -- IBOL's original author is Andrew
Pamment/apam, Talisman BBS) per this project's standing bbs-protocol-
precedent discipline: every field order, message to/from/subject, and
the rot47 cipher below were read out of the real .js source, not
guessed. See docs/39-fsxnet-interbbs.md for full attribution and
sysop setup instructions.

Deliberately NOT part of anetbbs/echomail/interbbs_sync.py: that
module is ANetBBS's own private node-to-node relay format
(ANET_WALL/ANET_LASTCALLERS/ANET_GAMESCORES) that assumes the peer is
also ANetBBS. This module speaks a real, externally-defined protocol
that other BBS software (Synchronet, Mystic) on fsxNet already
understands, and stores results in its own FsxnetOneliner/
FsxnetLastCaller tables -- never WallPost/CallerLog.

Why there's no origin-tagged loop-prevention gate here (unlike
interbbs_sync.py's WallPost.origin_bbs check): IBOL/IBLC are not relay
protocols. A sysop posts their own content exactly once -- on explicit
user action for IBOL, automatically on login for IBLC -- directly into
the shared FSX_DAT echo. The FTN transport itself (ordinary echomail
propagation among fsxNet's member hubs, with the usual SEEN-BY
mechanism) is what fans that post out to every other participant, the
same as any other echo area. This module's inbound sync only ever
*reads* FSX_DAT to populate a local display cache -- it never writes
back anything it reads, so there is nothing to bounce. Confirmed
directly against both real scripts: neither ever re-saves a message
it read from the message base.
"""
import logging

logger = logging.getLogger(__name__)

AREA_TAG_DEFAULT = 'FSX_DAT'

IBOL_MSG_TO = 'IBBS1LINE'
IBOL_MSG_SUBJECT = 'InterBBS Oneliner'

IBLC_MSG_FROM = 'ibbslastcall'
IBLC_MSG_TO = 'All'
IBLC_MSG_SUBJECT = 'ibbslastcall-data'
IBLC_BEGIN = '>>> BEGIN'
IBLC_END = '>>> END'


def rot47(s):
    """ROT47: shift printable ASCII 33-126 by 47 (mod 94), leave
    everything else untouched. Ported verbatim from iblc.js's own
    rot47() (itself based on xqtr's Python mod for Mystic) -- this is
    the real wire encoding IBLC uses for every field in its posted
    body, not a general-purpose cipher choice of ours."""
    res = []
    for ch in s:
        j = ord(ch)
        if 33 <= j <= 126:
            res.append(chr(33 + ((j + 14) % 94)))
        else:
            res.append(ch)
    return ''.join(res)


def _configured_network(network_id):
    """Resolve the one EchomailNetwork fsxNet sync is scoped to.
    Deliberately reuses interbbs_sync.py's own helper rather than a
    local copy -- this is general network-resolution plumbing (not
    ANET_WALL/ANET_LASTCALLERS-specific), the "fully separate" scope
    decision for this feature is about not sharing WallPost/CallerLog
    data, not about never importing a shared utility function."""
    from .interbbs_sync import _configured_network as _resolve
    return _resolve(network_id)


def _configured_area(app):
    """Resolve the one EchoArea fsxNet sync reads/writes, honoring the
    sysop-editable FSXNET_AREA_TAG (default 'FSX_DAT') rather than
    assuming the tag -- mirrors iblc.ini's messageBase being user-set.
    Returns (None, None) if unconfigured.

    Self-heals via ensure_special_area() (same helper ANET_WALL/
    ANET_LASTCALLERS already use) rather than a bare lookup -- the
    admin settings route already creates this area on enable, but a
    config flag flipped any other way (a test, a .env edit, a future
    non-UI path) must not silently no-op forever just because the row
    hasn't been created yet."""
    from .interbbs_sync import ensure_special_area

    network = _configured_network(app.config.get('FSXNET_NETWORK_ID'))
    if network is None:
        return None, None
    tag = app.config.get('FSXNET_AREA_TAG') or AREA_TAG_DEFAULT
    area = ensure_special_area(network, tag)
    return network, area


def _bbs_name(app):
    return (app.config.get('FSXNET_SYSTEM_NAME') or '').strip() or app.config.get('BBS_NAME', 'ANetBBS')


def _os_name():
    import platform
    return platform.system() or 'Unknown'


def _telnet_address(app):
    import socket
    host = app.config.get('BBS_DOMAIN') or socket.getfqdn() or 'localhost'
    port = (app.config.get('FSXNET_TELNET_PORT') or '').strip()
    return f'{host}:{port}' if port else host


def post_oneliner_to_fsxnet(lines, author_alias):
    """Compose and toss a real IBOL-format message to FSX_DAT, and
    immediately materialize a local FsxnetOneliner row so the poster
    sees their own post right away -- matching the real ibol.js, which
    re-reads the message base immediately after save_msg() for the
    same "visual feedback" reason. `lines`: list of up to 10 raw text
    lines (pipe-color codes allowed, preserved verbatim -- the real
    script only converts to Ctrl-A at *display* time)."""
    from flask import current_app
    if not current_app.config.get('FSXNET_IBOL_ENABLED'):
        return
    network, area = _configured_area(current_app)
    if network is None or area is None:
        return

    from ..models import db, EchomailMessage, FsxnetOneliner
    from . import tosser

    lines = [str(line) for line in lines if str(line).strip()][:10]
    if not lines:
        return
    bbs_name = _bbs_name(current_app)
    body = 'Author: ' + author_alias + '\n' + 'Source: ' + bbs_name + '\n' + \
        '\n'.join('Oneliner: ' + line for line in lines)
    try:
        msg = EchomailMessage(
            area_id=area.id, network_id=network.id,
            from_name=author_alias[:100], from_address=(network.our_address or '')[:60],
            to_name=IBOL_MSG_TO, subject=IBOL_MSG_SUBJECT,
            body=body, direction='outbound',
        )
        db.session.add(msg)
        db.session.commit()
        tosser.toss_message(msg.id)

        db.session.add(FsxnetOneliner(
            author=author_alias[:80], source_bbs=bbs_name[:100],
            body='\n'.join(lines),
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('fsxnet_sync: failed to post oneliner')


def post_lastcall_to_fsxnet(user, service):
    """Compose and toss a real IBLC-format login broadcast to
    FSX_DAT, and immediately materialize a local FsxnetLastCaller row
    (same immediate-visibility reasoning as post_oneliner_to_fsxnet).
    `user`: the real User ORM row (needs .username/.location);
    `service` is unused by the wire format itself but kept in the
    signature to match the sibling post_lastcaller_to_interbbs() call
    shape at both call sites."""
    from flask import current_app
    from datetime import datetime as _dt
    if not current_app.config.get('FSXNET_IBLC_ENABLED'):
        return
    if current_app.config.get('FSXNET_HIDE_SYSOP') and getattr(user, 'is_admin', False):
        return
    network, area = _configured_area(current_app)
    if network is None or area is None:
        return

    from ..models import db, EchomailMessage, FsxnetLastCaller
    from . import tosser

    alias = (getattr(user, 'username', None) or '?')[:80]
    bbs_name = _bbs_name(current_app)
    now = _dt.now()
    date_str = now.strftime('%m/%d/%y')
    time_str = now.strftime('%I:%M%p').lower().lstrip('0') or now.strftime('%I:%M%p').lower()
    location = (getattr(user, 'location', None) or '')[:100]
    os_name = _os_name()
    address = _telnet_address(current_app)

    fields = [alias, bbs_name, date_str, time_str, location, os_name, address]
    body = IBLC_BEGIN + '\n' + '\n'.join(rot47(f) for f in fields) + '\n' + IBLC_END
    try:
        msg = EchomailMessage(
            area_id=area.id, network_id=network.id,
            from_name=IBLC_MSG_FROM, from_address=(network.our_address or '')[:60],
            to_name=IBLC_MSG_TO, subject=IBLC_MSG_SUBJECT,
            body=body, direction='outbound',
        )
        db.session.add(msg)
        db.session.commit()
        tosser.toss_message(msg.id)

        db.session.add(FsxnetLastCaller(
            alias=alias, bbs_name=bbs_name[:100],
            remote_date_str=date_str, remote_time_str=time_str,
            location=location, os=os_name[:40], address=address[:160],
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('fsxnet_sync: failed to post last-caller')


def _parse_oneliner(body):
    """Parse a real IBOL message body. Returns (author, source_bbs,
    lines) or None if the body doesn't actually have both an Author
    and a Source line -- same defensive requirement the real ibol.js
    enforces (0.250322: "posts no longer get shown unless both the
    Author and Source fields are populated", added specifically
    because a malformed post from another BBS could otherwise break
    the whole script)."""
    author = source_bbs = None
    lines = []
    for line in (body or '').split('\n'):
        if line.startswith('Author: '):
            author = line[len('Author: '):].strip()
        elif line.startswith('Source: '):
            source_bbs = line[len('Source: '):].strip()
        elif line.startswith('Oneliner: '):
            lines.append(line[len('Oneliner: '):].rstrip())
    if not author or not source_bbs:
        return None
    return author, source_bbs, lines


def _parse_lastcall(body):
    """Parse a real IBLC message body. Returns a dict of the 7
    rot47-decoded fields, or None if the >>> BEGIN marker (and 7
    following lines) isn't there."""
    lines = (body or '').split('\n')
    try:
        idx = lines.index(IBLC_BEGIN)
    except ValueError:
        return None
    if idx + 7 >= len(lines):
        return None
    fields = [rot47(lines[idx + 1 + i]) for i in range(7)]
    return {
        'alias': fields[0], 'bbs_name': fields[1],
        'remote_date_str': fields[2], 'remote_time_str': fields[3],
        'location': fields[4], 'os': fields[5], 'address': fields[6],
    }


def sync_fsxnet_inbound(app, params):
    """ScheduledEvent handler: materialize new inbound FSX_DAT messages
    into FsxnetOneliner/FsxnetLastCaller rows. Signature per
    events/handlers.py convention: f(app, params) -> (ok, output).

    Only `direction == 'inbound'` rows are scanned -- our own posts are
    already materialized at post-time in post_oneliner_to_fsxnet()/
    post_lastcall_to_fsxnet() above, so outbound rows are intentionally
    skipped (no double-insert, and no real echomail hub echoes a
    node's own post back to it anyway).

    FSX_DAT carries more than one message shape (per fsxNet's own
    infopack: "used by software such as InterBBS Oneliners, Last
    Caller Mods etc") -- classify each message by its to/from/subject
    before parsing, and silently skip anything that matches neither
    shape rather than guessing.
    """
    from ..models import db, EchoArea, EchomailMessage, FsxnetOneliner, FsxnetLastCaller

    imported_oneliners = 0
    imported_lastcallers = 0
    skipped_no_msgid = 0
    skipped_unrecognized = 0
    try:
        network, _area = _configured_area(app)
        if network is None:
            return True, 'no fsxNet network configured'
        tag = app.config.get('FSXNET_AREA_TAG') or AREA_TAG_DEFAULT
        area_ids = [a.id for a in EchoArea.query.filter_by(
            tag=tag, network_id=network.id).all()]
        if not area_ids:
            return True, f'no {tag} area configured'

        known_oneliner_ids = db.session.query(FsxnetOneliner.remote_msg_id).filter(
            FsxnetOneliner.remote_msg_id.isnot(None))
        known_lastcaller_ids = db.session.query(FsxnetLastCaller.remote_msg_id).filter(
            FsxnetLastCaller.remote_msg_id.isnot(None))
        rows = (EchomailMessage.query
                .filter(EchomailMessage.area_id.in_(area_ids),
                        EchomailMessage.direction == 'inbound')
                .filter(db.or_(EchomailMessage.msg_id.is_(None),
                              db.and_(~EchomailMessage.msg_id.in_(known_oneliner_ids),
                                     ~EchomailMessage.msg_id.in_(known_lastcaller_ids))))
                .all())

        ibol_enabled = app.config.get('FSXNET_IBOL_ENABLED')
        iblc_enabled = app.config.get('FSXNET_IBLC_ENABLED')
        seen_this_tick = set()
        for msg in rows:
            if not msg.msg_id:
                skipped_no_msgid += 1
                continue
            if msg.msg_id in seen_this_tick:
                continue

            # Case-insensitive to/from/subject matching. Real bug found
            # against live fsxNet traffic (2026-10-07): a sizeable chunk
            # of real inbound IBLC posts arrive as "IBBSLastCall"/
            # "IBBSLastCall-Data" rather than the lowercase
            # "ibbslastcall"/"ibbslastcall-data" the real iblc.js itself
            # posts with (and which this module's own outbound side also
            # uses) -- some other software on the network evidently
            # posts the same real wire format with different casing.
            # iblc.js's own reader (`h.subject === MSG_SUBJ`) is exactly
            # this case-sensitive, so it would silently drop the same
            # messages -- not something to faithfully replicate once a
            # real wire-format variance is confirmed live, since being
            # lenient here only recovers data, it never misclassifies
            # anything the strict match would have accepted.
            msg_to = (msg.to_name or '').strip().upper()
            msg_from = (msg.from_name or '').strip().upper()
            msg_subject = (msg.subject or '').strip().upper()

            if (ibol_enabled and msg_to == IBOL_MSG_TO.upper()
                    and msg_subject == IBOL_MSG_SUBJECT.upper()):
                parsed = _parse_oneliner(msg.body)
                if parsed is None:
                    skipped_unrecognized += 1
                    continue
                author, source_bbs, lines = parsed
                db.session.add(FsxnetOneliner(
                    author=author[:80], source_bbs=source_bbs[:100],
                    body='\n'.join(lines), remote_msg_id=msg.msg_id,
                ))
                db.session.commit()
                imported_oneliners += 1
            elif (iblc_enabled and msg_from == IBLC_MSG_FROM.upper()
                  and msg_to == IBLC_MSG_TO.upper()
                  and msg_subject == IBLC_MSG_SUBJECT.upper()):
                parsed = _parse_lastcall(msg.body)
                if parsed is None:
                    skipped_unrecognized += 1
                    continue
                db.session.add(FsxnetLastCaller(
                    alias=parsed['alias'][:80], bbs_name=parsed['bbs_name'][:100],
                    remote_date_str=parsed['remote_date_str'][:20],
                    remote_time_str=parsed['remote_time_str'][:20],
                    location=parsed['location'][:100], os=parsed['os'][:40],
                    address=parsed['address'][:160], remote_msg_id=msg.msg_id,
                ))
                db.session.commit()
                imported_lastcallers += 1
            else:
                skipped_unrecognized += 1
                continue

            if msg.msg_id:
                seen_this_tick.add(msg.msg_id)
        return True, (f'imported {imported_oneliners} oneliner(s), '
                      f'{imported_lastcallers} last-caller(s), '
                      f'skipped {skipped_no_msgid} (no msg_id), '
                      f'{skipped_unrecognized} (unrecognized)')
    except Exception as exc:
        db.session.rollback()
        logger.exception('sync_fsxnet_inbound failed')
        return False, str(exc)
