# anetbbs/echomail/filefix.py
"""
FileFix robot — file-echo counterpart to AreaFix (anetbbs/echomail/areafix.py)
for netmail addressed to "filefix" (or our configured robot name).

Same command grammar as AreaFix (reused directly, not reimplemented — see
areafix.parse_request, which is already generic, not echo-specific):

    +AREA.TAG       subscribe to file area AREA.TAG
    -AREA.TAG       unsubscribe from AREA.TAG
    +ALL            subscribe to all available file areas
    -ALL            unsubscribe from everything
    %LIST           reply with the file areas the requester is subscribed to
    %HELP           reply with a help text

Before this module existed, FileFix was only half-built: the leaf-side
outbound half already worked (anetbbs/web/admin.py's file_areas_subscribe_all
/unsubscribe_all call areafix.send_areafix_request(..., robot_name='FileFix')),
and the underlying subscription data model already existed
(FileArea.is_subscribed for the leaf side, FileEchoSubscription for the hub
side, both already consumed by anetbbs/echomail/tic.py's hatch-out fan-out)
-- but there was no netmail-robot front-end at all: binkp_server.py never
dispatched inbound "filefix" netmail anywhere, so it fell through unhandled.

One real structural difference from AreaFix, not a copy-paste: AreaFix's
hub side (EchoAreaNode) is keyed by a real node_id FK to BinkPNode.
FileEchoSubscription is keyed by a raw peer_address string instead, so
_process_node_request() here takes a peer_address, not a BinkPNode row --
no BinkPNode lookup is needed for the subscription row itself, only for
the leaf-vs-hub routing decision in handle_filefix_netmail() (same as
AreaFix).

Reference: FTS-0024 (areafix-style commands), same convention FileFix
implementations elsewhere in the wild use for file echoes.
"""
import datetime
from ..models import (db, FileArea, EchomailNetwork, NetmailMessage,
                       AreafixLog, BinkPNode, FileEchoSubscription)
from .areafix import parse_request, _classify_request_type


def _sub_all(network):
    network.file_areas.update({FileArea.is_subscribed: True})
    return [a.tag for a in network.file_areas.filter_by(is_subscribed=True).all()]


def _unsub_all(network):
    tags = [a.tag for a in network.file_areas.filter_by(is_subscribed=True).all()]
    network.file_areas.update({FileArea.is_subscribed: False})
    return tags


def _sub(network, tag):
    a = network.file_areas.filter_by(tag=tag).first()
    if a is None:
        return None
    a.is_subscribed = True
    return a.tag


def _unsub(network, tag):
    a = network.file_areas.filter_by(tag=tag).first()
    if a is None:
        return None
    a.is_subscribed = False
    return a.tag


def _list(network):
    return [a.tag for a in network.file_areas.filter_by(is_subscribed=True)
                                              .order_by(FileArea.tag).all()]


def _help_text():
    return (
        "FileFix robot — subscribe/unsubscribe to file echoes.\n\n"
        "Commands (one per line):\n"
        "  +AREA.TAG    subscribe to AREA.TAG\n"
        "  *AREA.TAG    same as +AREA.TAG (some implementations use *)\n"
        "  -AREA.TAG    unsubscribe from AREA.TAG\n"
        "  +ALL         subscribe to every available file area\n"
        "  -ALL         unsubscribe from every file area\n"
        "  %LIST        list the file areas you're currently receiving\n"
        "  %QUERY       same as %LIST\n"
        "  %HELP        this help text\n\n"
        "Lines that don't match a command are ignored. Reply will list\n"
        "what was done."
    )


def process_request(network, from_address, subject, body):
    """Leaf side: mirrors areafix.process_request, on FileArea.is_subscribed
    (via network.file_areas) instead of EchoArea.is_subscribed.

    `subject` carries the AreaFix/FileFix password per FTS-0024 (FileFix
    reuses the same network-level password fields as AreaFix -- see
    areafix.process_request's docstring for the security gap this check
    closes; the same gap existed here, unfixed independently, since
    handle_filefix_netmail() never passed the subject through either).

    Returns (reply_body_str, log_kwargs) -- caller writes the reply
    netmail and the AreafixLog row (bot='filefix').
    """
    if network is None:
        return ("Network not configured.", {
            'from_address': from_address, 'request_type': 'error',
            'response': 'no network', 'success': False, 'bot': 'filefix'})

    expected_pw = (getattr(network, 'areafix_password', None)
                  or getattr(network, 'binkp_password', None) or '').strip()
    provided_pw = (subject or '').strip()
    if expected_pw and expected_pw != provided_pw:
        return ("FileFix: password incorrect or missing — no changes made.\n", {
            'network_id': network.id,
            'from_address': from_address, 'request_type': 'badpw',
            'area_tags': '', 'response': 'bad filefix password',
            'success': False, 'bot': 'filefix'})

    cmds = parse_request(body)
    if not cmds:
        return (_help_text(), {
            'network_id': network.id,
            'from_address': from_address, 'request_type': 'help',
            'area_tags': '', 'response': 'help text returned', 'success': True,
            'bot': 'filefix',
        })

    out_lines = [f"FileFix robot @ {network.our_address or 'unknown'}",
                 f"For: {from_address}", "=" * 40]
    affected = []

    for verb, target, _arg in cmds:
        if verb == '+' and target == 'ALL':
            tags = _sub_all(network)
            affected += tags
            out_lines.append(f"+ALL : subscribed to {len(tags)} file areas")
        elif verb == '-' and target == 'ALL':
            tags = _unsub_all(network)
            affected += tags
            out_lines.append(f"-ALL : unsubscribed from {len(tags)} file areas")
        elif verb == '+':
            r = _sub(network, target)
            if r:
                affected.append(r)
                out_lines.append(f"+{target} : subscribed")
            else:
                out_lines.append(f"+{target} : ERROR — file area not available")
        elif verb == '-':
            r = _unsub(network, target)
            if r:
                affected.append(r)
                out_lines.append(f"-{target} : unsubscribed")
            else:
                out_lines.append(f"-{target} : ERROR — not subscribed")
        elif verb == '%':
            if target in ('LIST', 'QUERY'):
                tags = _list(network)
                out_lines.append(f"%LIST : {len(tags)} subscriptions")
                for t in tags:
                    out_lines.append(f"  {t}")
            elif target == 'HELP':
                out_lines.append("%HELP :")
                out_lines.append(_help_text())
            else:
                out_lines.append(f"%{target} : ignored")

    db.session.commit()
    response = '\n'.join(out_lines) + '\n'
    return (response, {
        'network_id': network.id,
        'from_address': from_address,
        'request_type': _classify_request_type(cmds),
        'area_tags': ','.join(affected),
        'response': response[:1000],
        'success': True,
        'bot': 'filefix',
    })


def _process_node_request(peer_address, from_address, subject, body,
                          node_password):
    """Hub side. Unlike areafix's EchoAreaNode (FK-keyed to BinkPNode.id),
    FileEchoSubscription is keyed by a raw peer_address string -- no
    BinkPNode row lookup needed for the subscription row itself, only for
    the leaf-vs-hub routing decision (done by the caller, which is also
    where node_password comes from -- see areafix._process_node_request's
    docstring for why a From: address alone isn't proof of identity).

    Returns (reply_body_str, log_kwargs), same shape as process_request().
    """
    expected_pw = (node_password or '').strip()
    provided_pw = (subject or '').strip()
    if expected_pw and expected_pw != provided_pw:
        return ("FileFix: password incorrect or missing — no changes made.\n", {
            'from_address': from_address, 'request_type': 'badpw',
            'area_tags': '', 'response': 'bad filefix password',
            'success': False, 'bot': 'filefix'})

    cmds = parse_request(body)
    if not cmds:
        return (_help_text(), {
            'from_address': from_address, 'request_type': 'help',
            'area_tags': '', 'response': 'help text returned', 'success': True,
            'bot': 'filefix',
        })

    # Real gap found in a full echomail-subsystem audit (identical fix
    # applied to areafix.py's hub side): is_sysop_only areas must not
    # be subscribable via a plain +TAG/+ALL from any downstream node --
    # see areafix.py's _process_node_request for the full rationale.
    # isnot(True), not is_(False), for the same NULL-safety reason as
    # LASTCALLERS_HIDE_SYSOP's is_admin check.
    all_areas = (FileArea.query
                .filter_by(is_active=True)
                .filter(FileArea.is_sysop_only.isnot(True))
                .all())
    area_map = {a.tag.upper(): a for a in all_areas}

    out_lines = [f'FileFix robot (hub) for {peer_address}',
                 f'Request from: {from_address}', '=' * 40]
    affected = []

    def _node_sub(tag):
        area = area_map.get(tag)
        if area is None:
            return None
        existing = FileEchoSubscription.query.filter_by(
            file_area_id=area.id, peer_address=peer_address).first()
        if existing:
            existing.is_active = True
            return area.tag
        db.session.add(FileEchoSubscription(
            file_area_id=area.id, peer_address=peer_address))
        return area.tag

    def _node_unsub(tag):
        area = area_map.get(tag)
        if area is None:
            return None
        row = FileEchoSubscription.query.filter_by(
            file_area_id=area.id, peer_address=peer_address).first()
        if row is None:
            return None
        db.session.delete(row)
        return area.tag

    def _node_list():
        subs = (FileEchoSubscription.query
                .join(FileArea, FileEchoSubscription.file_area_id == FileArea.id)
                .filter(FileEchoSubscription.peer_address == peer_address,
                        FileEchoSubscription.is_active.is_(True))
                .with_entities(FileArea.tag)
                .all())
        return [row.tag for row in subs]

    for verb, target, _arg in cmds:
        if verb == '+' and target == 'ALL':
            tags = [_node_sub(a.tag.upper()) for a in all_areas]
            tags = [t for t in tags if t]
            affected += tags
            out_lines.append(f'+ALL : subscribed to {len(tags)} file areas')
        elif verb == '-' and target == 'ALL':
            rows = FileEchoSubscription.query.filter_by(
                peer_address=peer_address).all()
            tags = []
            for row in rows:
                a = FileArea.query.get(row.file_area_id)
                if a:
                    tags.append(a.tag)
                db.session.delete(row)
            affected += tags
            out_lines.append(f'-ALL : unsubscribed from {len(tags)} file areas')
        elif verb == '+':
            r = _node_sub(target)
            if r:
                affected.append(r)
                out_lines.append(f'+{target} : subscribed')
            else:
                out_lines.append(f'+{target} : ERROR — file area not available')
        elif verb == '-':
            r = _node_unsub(target)
            if r:
                affected.append(r)
                out_lines.append(f'-{target} : unsubscribed')
            else:
                out_lines.append(f'-{target} : not subscribed or not found')
        elif verb == '%':
            if target in ('LIST', 'QUERY'):
                tags = _node_list()
                out_lines.append(f'%LIST : {len(tags)} subscriptions')
                for t in tags:
                    out_lines.append(f'  {t}')
            elif target == 'HELP':
                out_lines.append(_help_text())
            else:
                out_lines.append(f'%{target} : ignored')

    db.session.commit()
    response = '\n'.join(out_lines) + '\n'
    return (response, {
        'from_address': from_address,
        'request_type': _classify_request_type(cmds),
        'area_tags': ','.join(affected),
        'response': response[:1000],
        'success': True,
        'bot': 'filefix',
    })


def handle_filefix_netmail(netmail_id):
    """Process an inbound filefix netmail by id and queue a reply.

    Same shape as areafix.handle_areafix_netmail: resolve the sender's
    FTN address against BinkPNode to pick leaf vs. hub path, call the
    matching process_request()/_process_node_request(), queue a reply
    NetmailMessage, write an AreafixLog row (bot='filefix').
    """
    nm = NetmailMessage.query.get(netmail_id)
    if nm is None:
        return None

    downstream_node = None
    if nm.from_address:
        bare = nm.from_address.split('@', 1)[0].strip()
        candidates = [nm.from_address, bare] if bare != nm.from_address else [nm.from_address]
        downstream_node = (BinkPNode.query
                           .filter(BinkPNode.ftn_address.in_(candidates))
                           .filter_by(is_active=True)
                           .first())

    network = None
    if downstream_node:
        response, log_kwargs = _process_node_request(
            downstream_node.ftn_address, nm.from_address, nm.subject,
            nm.body, downstream_node.password)
    else:
        network = (EchomailNetwork.query.get(nm.network_id)
                   if nm.network_id else None)
        response, log_kwargs = process_request(
            network, nm.from_address, nm.subject, nm.body)

    if network is not None:
        reply = NetmailMessage(
            network_id=network.id,
            from_address=network.our_address,
            to_address=nm.from_address,
            from_name='FileFix',
            to_name=nm.from_name,
            subject=f'Re: {nm.subject or "filefix"}',
            body=response,
            direction='outbound',
            status='queued',
            chrs='UTF-8 4',
            is_local=True,
            created_at=datetime.datetime.utcnow(),
        )
        db.session.add(reply)
    elif downstream_node:
        reply = NetmailMessage(
            network_id=nm.network_id,
            from_address=None,
            to_address=nm.from_address,
            from_name='FileFix',
            to_name=nm.from_name,
            subject=f'Re: {nm.subject or "filefix"}',
            body=response,
            direction='outbound',
            status='queued',
            chrs='UTF-8 4',
            is_local=True,
            created_at=datetime.datetime.utcnow(),
        )
        db.session.add(reply)

    db.session.add(AreafixLog(**log_kwargs))
    db.session.commit()
    return response
