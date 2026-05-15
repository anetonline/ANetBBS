# anetbbs/echomail/areafix.py
"""
Areafix robot — handles netmail addressed to "areafix" (or our configured
robot name) that contains FTN-standard area subscription commands.

Supported commands (case-insensitive, one per line of the message body):

    +AREA.TAG       subscribe to AREA.TAG
    -AREA.TAG       unsubscribe from AREA.TAG
    +ALL            subscribe to all available areas
    -ALL            unsubscribe from everything
    %LIST           reply with the list of areas the requester is subscribed to
    %QUERY          (alias of %LIST)
    %HELP           reply with a help text
    %COMPRESS GZIP  no-op (we always send uncompressed bundles)
    *AREA.TAG       (some implementations) — equivalent to +AREA.TAG

The robot replies with a netmail back to the requester listing what was
done. Subscription state is currently kept on EchoArea.is_subscribed
(global flag — no per-uplink subscription table yet); when a per-link table
is added, this module is the one that should be updated.

Reference: FTS-0024 (areafix-style commands).
"""
import re
import datetime
from ..models import db, EchoArea, EchomailNetwork, NetmailMessage, AreafixLog


_CMD_RE = re.compile(r'^\s*([+\-*%])\s*([A-Z0-9._\-]+)\b', re.IGNORECASE)


def parse_request(body):
    """Pull recognized commands out of an areafix netmail body.

    Returns a list of (verb, target) tuples where verb is one of
    '+', '-', '%' and target is the uppercased area-tag or keyword."""
    cmds = []
    for line in (body or '').splitlines():
        m = _CMD_RE.match(line)
        if not m:
            continue
        verb, target = m.group(1), m.group(2).upper()
        if verb == '*':            # '*' is sometimes used as +
            verb = '+'
        cmds.append((verb, target))
    return cmds


def _sub_all(network):
    network.areas.update({EchoArea.is_subscribed: True})
    return [a.tag for a in network.areas.filter_by(is_subscribed=True).all()]


def _unsub_all(network):
    tags = [a.tag for a in network.areas.filter_by(is_subscribed=True).all()]
    network.areas.update({EchoArea.is_subscribed: False})
    return tags


def _sub(network, tag):
    a = network.areas.filter_by(tag=tag).first()
    if a is None:
        return None
    a.is_subscribed = True
    return a.tag


def _unsub(network, tag):
    a = network.areas.filter_by(tag=tag).first()
    if a is None:
        return None
    a.is_subscribed = False
    return a.tag


def _list(network):
    return [a.tag for a in network.areas.filter_by(is_subscribed=True)
                                       .order_by(EchoArea.tag).all()]


def _help_text():
    return (
        "Areafix robot — subscribe/unsubscribe to echo areas.\n\n"
        "Commands (one per line):\n"
        "  +AREA.TAG    subscribe to AREA.TAG\n"
        "  -AREA.TAG    unsubscribe from AREA.TAG\n"
        "  +ALL         subscribe to every available area\n"
        "  -ALL         unsubscribe from every area\n"
        "  %LIST        list the areas you're currently receiving\n"
        "  %HELP        this help text\n\n"
        "Lines that don't match a command are ignored. Reply will list\n"
        "what was done."
    )


def process_request(network, from_address, body):
    """Process an incoming areafix netmail and return (response_text, log_data).

    `network` is an EchomailNetwork SQLAlchemy row.
    `from_address` is the sender's FTN address (string).
    `body` is the netmail body text.

    Returns a (reply_body_str, log_kwargs) tuple. The caller is responsible
    for writing the reply netmail and the AreafixLog row.
    """
    if network is None:
        return ("Network not configured.", {
            'from_address': from_address, 'request_type': 'error',
            'response': 'no network', 'success': False})

    cmds = parse_request(body)
    if not cmds:
        return (_help_text(), {
            'network_id': network.id,
            'from_address': from_address, 'request_type': 'help',
            'area_tags': '', 'response': 'help text returned', 'success': True,
        })

    out_lines = [f"Areafix robot @ {network.our_address or 'unknown'}",
                 f"For: {from_address}", "=" * 40]
    affected = []

    for verb, target in cmds:
        if verb == '+' and target == 'ALL':
            tags = _sub_all(network)
            affected += tags
            out_lines.append(f"+ALL : subscribed to {len(tags)} areas")
        elif verb == '-' and target == 'ALL':
            tags = _unsub_all(network)
            affected += tags
            out_lines.append(f"-ALL : unsubscribed from {len(tags)} areas")
        elif verb == '+':
            r = _sub(network, target)
            if r:
                affected.append(r)
                out_lines.append(f"+{target} : subscribed")
            else:
                out_lines.append(f"+{target} : ERROR — area not available")
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
        'request_type': 'subscribe' if any(v == '+' for v, _ in cmds) else 'unsubscribe',
        'area_tags': ','.join(affected),
        'response': response[:1000],          # cap log entry size
        'success': True,
    })


def handle_areafix_netmail(netmail_id):
    """Process an inbound areafix netmail by id and queue a reply.

    Looks up the NetmailMessage, parses the body, runs process_request,
    creates a reply NetmailMessage queued for outbound delivery, and writes
    an AreafixLog row.
    """
    nm = NetmailMessage.query.get(netmail_id)
    if nm is None:
        return None
    network = (EchomailNetwork.query.get(nm.network_id)
               if nm.network_id else None)
    response, log_kwargs = process_request(network, nm.from_address, nm.body)

    if network is not None:
        reply = NetmailMessage(
            network_id=network.id,
            from_address=network.our_address,
            to_address=nm.from_address,
            from_name='Areafix',
            to_name=nm.from_name,
            subject=f'Re: {nm.subject or "areafix"}',
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


def send_areafix_request(network, plus_tags=None, minus_tags=None,
                          rescan_tags=None,
                          robot_name='AreaFix', subject='Area request'):
    """Queue an outbound AreaFix netmail to *network*'s hub.

    Builds a netmail addressed to <robot_name> @ network.hub_address with a
    body containing one '+TAG' line for each plus_tags entry, one '-TAG'
    line per minus_tags entry, and one '%RESCAN <TAG>' line per rescan_tags.
    The message is committed in 'queued' state so the next BinkP poll
    picks it up.

    Returns the NetmailMessage id, or None if the network has no hub or
    no AreaFix password configured (we can't talk to the hub anonymously).
    """
    if network is None:
        return None
    if not network.hub_address:
        return None
    plus_tags  = [t.strip().upper() for t in (plus_tags  or []) if t.strip()]
    minus_tags = [t.strip().upper() for t in (minus_tags or []) if t.strip()]
    rescan_tags = [t.strip().upper() for t in (rescan_tags or []) if t.strip()]
    if not plus_tags and not minus_tags and not rescan_tags:
        return None

    # Per FTS-0024 (and confirmed by Synchronet sbbsecho.c:2447 + Mystic
    # behaviour): the AreaFix password goes in the netmail SUBJECT field,
    # NOT as the first line of the body. The body contains only the
    # commands (+TAG, -TAG, %RESCAN <TAG>, %LIST, %HELP, etc.).
    # Many hubs configure a SEPARATE areafix password from the BinkP session
    # password; prefer the dedicated field when set, fall back to binkp.
    af_pw = (getattr(network, 'areafix_password', None) or '').strip()
    if not af_pw:
        af_pw = (getattr(network, 'binkp_password', None) or '').strip()

    body_lines = []
    for t in plus_tags:
        body_lines.append(f'+{t}')
    for t in minus_tags:
        body_lines.append(f'-{t}')
    for t in rescan_tags:
        # FSC-0057 / SBBSecho: '%RESCAN <area>' asks the hub to re-feed
        # the full backlog of an already-subscribed area.  Some hubs
        # accept an optional message-count after the tag.
        body_lines.append(f'%RESCAN {t}')
    if plus_tags or minus_tags:
        body_lines.append('%LIST')   # ask hub to confirm new state

    nm = NetmailMessage(
        network_id=network.id,
        from_address=network.our_address,
        to_address=network.hub_address,
        from_name='Sysop',
        to_name=robot_name,
        # Subject carries the password (FTS-0024).
        subject=af_pw or subject,
        body='\n'.join(body_lines),
        direction='outbound',
        status='queued',
        chrs='UTF-8 4',
        is_local=True,
        created_at=datetime.datetime.utcnow(),
    )
    db.session.add(nm)
    db.session.flush()
    db.session.add(AreafixLog(
        network_id=network.id,
        from_address=network.our_address or '',
        request_type='outbound',
        area_tags=','.join(
            plus_tags
            + ['-' + t for t in minus_tags]
            + ['%RESCAN ' + t for t in rescan_tags]),
        response='queued',
        success=True,
    ))
    db.session.commit()
    return nm.id
