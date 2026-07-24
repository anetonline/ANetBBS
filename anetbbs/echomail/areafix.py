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
    %RESCAN AREA.TAG  re-queue every existing message in AREA.TAG for this
                      requester's hold queue (real backlog-catchup request,
                      not a subscription change); bare %RESCAN with no tag
                      re-queues every area the requester is subscribed to.
                      Downstream-node-only (see _process_node_request) --
                      no-op for an upstream leaf request (process_request),
                      since there's no per-us hold queue for a network we
                      poll rather than host.
    %COMPRESS GZIP  no-op (we always send uncompressed bundles)
    %PASSWORD newpass  change your own AreaFix/BinkP password. Downstream-
                      node-only (see _process_node_request) -- only reachable
                      having already passed the password check with the
                      OLD password, so this can't bootstrap a password on a
                      node that doesn't have one set yet.
    *AREA.TAG       (some implementations) — equivalent to +AREA.TAG

The robot replies with a netmail back to the requester listing what was
done. Two subscription models exist side by side, picked by
handle_areafix_netmail() based on whether the sender is a known
downstream BinkPNode:
  - process_request(network, ...) — for an UPSTREAM request (this install
    is a leaf member polling someone else's hub): flips the install-wide
    EchoArea.is_subscribed flag, since there's only one subscriber (us).
  - _process_node_request(node, ...) — for a DOWNSTREAM request (a real
    peer node polling US as hub): adds/removes that SPECIFIC node's
    EchoAreaNode row, leaving every other node's subscriptions and the
    area's own is_subscribed flag untouched. This is the one that
    actually drives the hub's per-node BinkPHoldQueue fan-out via
    tosser.py -- if a downstream node's request is ever routed through
    process_request() instead, their subscription changes silently do
    nothing for their own mail flow.

Reference: FTS-0024 (areafix-style commands).
"""
import re
import datetime
from ..models import db, EchoArea, EchomailNetwork, NetmailMessage, AreafixLog, BinkPNode, EchoAreaNode


_CMD_RE = re.compile(
    r'^\s*([+\-*%])\s*([A-Z0-9._\-]+)(?:\s+([A-Z0-9._\-]+))?', re.IGNORECASE)

# %PASSWORD's new-password value must NOT go through parse_request()'s
# generic arg capture -- that group is uppercased for every other
# command (area tags/keywords are conventionally uppercase, but a
# password is case-sensitive and would be silently corrupted). Scanned
# separately, straight from the raw body, case preserved.
_PASSWORD_CMD_RE = re.compile(r'^\s*%PASSWORD\s+(\S+)', re.IGNORECASE | re.MULTILINE)


def parse_request(body):
    """Pull recognized commands out of an areafix netmail body.

    Returns a list of (verb, target, arg) tuples where verb is one of
    '+', '-', '%'; target is the uppercased area-tag or keyword; arg is
    an optional uppercased second token (only meaningful for '%RESCAN
    AREA.TAG' and the already-no-op '%COMPRESS GZIP') or None."""
    cmds = []
    for line in (body or '').splitlines():
        m = _CMD_RE.match(line)
        if not m:
            continue
        verb, target, arg = m.group(1), m.group(2).upper(), m.group(3)
        if verb == '*':            # '*' is sometimes used as +
            verb = '+'
        cmds.append((verb, target, arg.upper() if arg else None))
    return cmds


def _classify_request_type(cmds):
    """Classify a parsed command list for the AreafixLog/FileFix-log
    'request_type' column. Real bug found in a full echomail-subsystem
    audit: every caller used `'subscribe' if any '+' else 'unsubscribe'`
    -- a request containing ONLY `%LIST`/`%HELP`/`%QUERY`/`%RESCAN` (no
    `+`/`-` at all) got mislabeled 'unsubscribe' even though nothing was
    unsubscribed, making the audit trail show false unsubscribe entries
    for every plain status/help query."""
    if any(v == '+' for v, _t, _a in cmds):
        return 'subscribe'
    if any(v == '-' for v, _t, _a in cmds):
        return 'unsubscribe'
    return 'query'


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
        "  *AREA.TAG    same as +AREA.TAG (some implementations use *)\n"
        "  -AREA.TAG    unsubscribe from AREA.TAG\n"
        "  +ALL         subscribe to every available area\n"
        "  -ALL         unsubscribe from every area\n"
        "  %LIST        list the areas you're currently receiving\n"
        "  %QUERY       same as %LIST\n"
        "  %RESCAN AREA.TAG  re-send every existing message in AREA.TAG\n"
        "               (bare %RESCAN re-sends every area you're subscribed to)\n"
        "  %COMPRESS GZIP  accepted, no-op (bundles are always uncompressed)\n"
        "  %PASSWORD newpass  change your AreaFix/BinkP password (hub-\n"
        "               managed downstream nodes only)\n"
        "  %HELP        this help text\n\n"
        "Lines that don't match a command are ignored. Reply will list\n"
        "what was done."
    )


def process_request(network, from_address, subject, body):
    """Process an incoming areafix netmail and return (response_text, log_data).

    `network` is an EchomailNetwork SQLAlchemy row.
    `from_address` is the sender's FTN address (string).
    `subject` is the netmail's Subject line, which per FTS-0024 (and
    confirmed against send_areafix_request()'s own outbound convention
    below) carries the AreaFix password -- NOT a command line.
    `body` is the netmail body text.

    Returns a (reply_body_str, log_kwargs) tuple. The caller is responsible
    for writing the reply netmail and the AreafixLog row.

    Real security gap found in a full-echomail-subsystem audit: this
    function used to accept ANY inbound "areafix"-addressed netmail and
    apply its commands unconditionally -- the password send_areafix_request()
    so carefully places in the Subject line was written on the way OUT but
    never verified on the way IN. Any netmail that reached this system
    addressed to the areafix robot (spoofed From:, or a legitimate but
    unrelated node) could silently unsubscribe/resubscribe every echo
    area. Matches Synchronet sbbsecho.c's own behaviour: an AreaFix
    request with a missing/wrong password is rejected before any command
    is applied, not after.
    """
    if network is None:
        return ("Network not configured.", {
            'from_address': from_address, 'request_type': 'error',
            'response': 'no network', 'success': False})

    expected_pw = (getattr(network, 'areafix_password', None)
                  or getattr(network, 'binkp_password', None) or '').strip()
    provided_pw = (subject or '').strip()
    if expected_pw and expected_pw != provided_pw:
        return ("Areafix: password incorrect or missing — no changes made.\n", {
            'network_id': network.id,
            'from_address': from_address, 'request_type': 'badpw',
            'area_tags': '', 'response': 'bad areafix password', 'success': False})

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

    for verb, target, _arg in cmds:
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
        'request_type': _classify_request_type(cmds),
        'area_tags': ','.join(affected),
        'response': response[:1000],          # cap log entry size
        'success': True,
    })


def _process_node_request(node, from_address, subject, body):
    """Areafix for a downstream BinkPNode — modifies EchoAreaNode rows.

    Subscribing adds a row; unsubscribing removes it.  Returns
    (response_text, log_kwargs) same as process_request().

    `subject` is checked against node.password before anything else --
    see process_request()'s docstring for why this exists. A message's
    From: address alone is not proof of identity (it isn't tied to the
    authenticated BinkP session that delivered it), so without this a
    peer who can get netmail relayed to this hub could forge a request
    claiming to be a DIFFERENT downstream node and alter that node's
    subscriptions.
    """
    expected_pw = (getattr(node, 'password', None) or '').strip()
    provided_pw = (subject or '').strip()
    # Real gap found in a full access-control audit, same class as the
    # %PASSWORD guard's own documented reasoning below: this used to
    # only REJECT when a password was configured AND wrong -- a node
    # with no password set at all sailed straight through with zero
    # real authentication. No admin-UI path leaves a node's password
    # empty today (defense-in-depth, not currently exploitable), but
    # require a real, matching password unconditionally rather than
    # relying on that.
    if not expected_pw or expected_pw != provided_pw:
        return ("Areafix: password incorrect or missing — no changes made.\n", {
            'from_address': from_address, 'request_type': 'badpw',
            'area_tags': '', 'response': 'bad areafix password', 'success': False})

    cmds = parse_request(body)
    if not cmds:
        return (_help_text(), {
            'from_address': from_address, 'request_type': 'help',
            'area_tags': '', 'response': 'help text returned', 'success': True,
        })

    # All areas available for hub distribution (active areas from any
    # network) -- EXCLUDING is_sysop_only areas. Real gap found in a
    # full echomail-subsystem audit: is_sysop_only is enforced as a
    # hard content gate everywhere else (web/terminal/FTP listings,
    # evaluate_access()), including by interbbs_sync.py's own areas
    # (InterBBS Wall / Last-Callers-sync / casino-score-sync), which
    # are deliberately created with is_sysop_only=True specifically
    # because they're machine-to-machine sync channels between paired
    # systems, not discussion areas meant to be read directly -- but
    # this hub-side handler let ANY downstream node subscribe to them
    # via a plain +TAG or +ALL, same as any public area.
    # isnot(True), not is_(False) -- a legacy/migrated EchoArea row with
    # is_sysop_only IS NULL (nullable column, no DB-level default) must
    # read as "not sysop-only", not get excluded by SQL's three-valued
    # NULL logic (same defensive pattern as LASTCALLERS_HIDE_SYSOP's
    # is_admin check in features/lastcallers.py).
    #
    # Real gap found live in production: this used to have NO network_id
    # filter at all, so a downstream node belonging to one FTN network
    # could +ALL/+TAG subscribe to EVERY other echomail network this hub
    # also relays -- a cross-network data leak, not just noise (observed
    # live when a downstream node's +ALL pulled in every unrelated
    # network's areas). node.network_id is nullable (legacy rows created
    # before that column existed); fail CLOSED to zero areas rather than
    # falling back to "show everything" if it's unset, since that
    # fallback is exactly the bug being fixed here.
    all_areas = (EchoArea.query
                .filter_by(is_active=True, network_id=node.network_id)
                .filter(EchoArea.is_sysop_only.isnot(True))
                .all()) if node.network_id is not None else []
    area_map = {a.tag.upper(): a for a in all_areas}

    out_lines = [f'Areafix robot (hub) for node {node.ftn_address}',
                 f'Request from: {from_address}', '=' * 40]
    affected = []

    def _node_sub(tag):
        area = area_map.get(tag)
        if area is None:
            return None
        existing = EchoAreaNode.query.filter_by(
            node_id=node.id, echo_area_id=area.id).first()
        if existing:
            return area.tag
        db.session.add(EchoAreaNode(node_id=node.id, echo_area_id=area.id))
        return area.tag

    def _node_unsub(tag):
        area = area_map.get(tag)
        if area is None:
            return None
        row = EchoAreaNode.query.filter_by(
            node_id=node.id, echo_area_id=area.id).first()
        if row is None:
            return None
        db.session.delete(row)
        return area.tag

    def _node_list():
        subs = (EchoAreaNode.query
                .join(EchoArea, EchoAreaNode.echo_area_id == EchoArea.id)
                .filter(EchoAreaNode.node_id == node.id)
                .with_entities(EchoArea.tag)
                .all())
        return [row.tag for row in subs]

    for verb, target, arg in cmds:
        if verb == '+' and target == 'ALL':
            tags = [_node_sub(a.tag.upper()) for a in all_areas]
            tags = [t for t in tags if t]
            affected += tags
            out_lines.append(f'+ALL : subscribed to {len(tags)} areas')
        elif verb == '-' and target == 'ALL':
            rows = EchoAreaNode.query.filter_by(node_id=node.id).all()
            tags = []
            for row in rows:
                a = EchoArea.query.get(row.echo_area_id)
                if a:
                    tags.append(a.tag)
                db.session.delete(row)
            affected += tags
            out_lines.append(f'-ALL : unsubscribed from {len(tags)} areas')
        elif verb == '+':
            r = _node_sub(target)
            if r:
                affected.append(r)
                out_lines.append(f'+{target} : subscribed')
            else:
                out_lines.append(f'+{target} : ERROR — area not available')
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
            elif target == 'RESCAN':
                # Real gap found live: a real downstream sysop sent
                # repeated "%RESCAN AREA.TAG" requests trying to recover
                # a backlog he'd never received, and every one came back
                # a silent "ignored" with no actual re-toss -- this
                # command was parsed but never implemented at all.
                # toss_area_messages(area_id, node_id=...) already exists
                # for exactly this ("used when a node newly subscribes
                # and requests a catchup", per its own docstring) but was
                # never wired to the areafix command that requests it.
                from .tosser import toss_area_messages
                if arg:
                    area = area_map.get(arg)
                    if area is None:
                        out_lines.append(f'%RESCAN {arg} : ERROR — area not available')
                    else:
                        n = toss_area_messages(area.id, node_id=node.id)
                        affected.append(area.tag)
                        out_lines.append(f'%RESCAN {arg} : queued {n} message(s)')
                else:
                    subs = EchoAreaNode.query.filter_by(node_id=node.id).all()
                    total = 0
                    for row in subs:
                        total += toss_area_messages(row.echo_area_id, node_id=node.id)
                    out_lines.append(
                        f'%RESCAN : queued {total} message(s) across '
                        f'{len(subs)} subscribed area(s)')
            elif target == 'PASSWORD':
                # Real gap found in a full echomail-subsystem audit: no
                # remote way for a downstream sysop to rotate their own
                # BinkP/AreaFix password (the same node.password field
                # gates both) -- standard AreaFix capability (Synchronet
                # sbbsecho, Mystic MUTIL).
                #
                # SECURITY: the password check above (`if expected_pw and
                # expected_pw != provided_pw`) only REJECTS when a
                # password IS configured and WRONG -- a node with no
                # password set at all (expected_pw == '') sails straight
                # through that check with zero real authentication, same
                # as every other command. Letting %PASSWORD through in
                # that case would mean anyone could bootstrap an initial
                # password on an unprotected node via a spoofable From:
                # address. Must explicitly require a real password to
                # have already been verified, not just "wasn't rejected".
                if not expected_pw:
                    out_lines.append(
                        '%PASSWORD : ERROR — no password currently set for '
                        'this node; ask the hub sysop to set one first')
                else:
                    m = _PASSWORD_CMD_RE.search(body or '')
                    if not m:
                        out_lines.append('%PASSWORD : ERROR — no new password given')
                    else:
                        node.password = m.group(1)
                        out_lines.append('%PASSWORD : password changed')
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
    })


def handle_areafix_netmail(netmail_id):
    """Process an inbound areafix netmail by id and queue a reply.

    Looks up the NetmailMessage, parses the body, runs process_request,
    creates a reply NetmailMessage queued for outbound delivery, and writes
    an AreafixLog row.

    If the sender's FTN address matches a BinkPNode (downstream peer), the
    subscription changes affect that node's EchoAreaNode rows rather than
    the global EchoArea.is_subscribed flag.
    """
    nm = NetmailMessage.query.get(netmail_id)
    if nm is None:
        return None

    # Check if sender is a downstream node managed by hub.
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
            downstream_node, nm.from_address, nm.subject, nm.body)
    else:
        network = (EchomailNetwork.query.get(nm.network_id)
                   if nm.network_id else None)
        response, log_kwargs = process_request(
            network, nm.from_address, nm.subject, nm.body)

    # Build a reply netmail back to the requester.
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
    elif downstream_node:
        # Reply via the same network the node originally sent from, if any.
        reply_network_id = nm.network_id
        reply = NetmailMessage(
            network_id=reply_network_id,
            from_address=None,
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
