# anetbbs/echomail/poller.py
"""
Background polling scheduler for echomail networks.

Runs poll jobs on configured intervals for each active network.
Updates EchomailPollLog records with results.
Thread-safe — uses Flask app context for all DB operations.
"""
import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_poller_thread = None
_stop_event = threading.Event()

# Caps for EchomailPollLog.transcript -- BinkP session transcripts are
# normally a few dozen lines even for a real file transfer, but there's
# no retention/cleanup for EchomailPollLog rows at all, so bound each
# one rather than let a single large transfer make a row unbounded.
_TRANSCRIPT_MAX_LINES = 500
_TRANSCRIPT_MAX_CHARS = 100_000

# Window for the content-based netmail dedup fallback in _import_netmail()
# below -- covers a peer resending the same netmail with a freshly
# regenerated MSGID every poll (observed live at a ~10 minute cadence),
# without treating two genuinely distinct messages with unlucky identical
# subject/body sent days apart as duplicates.
_CONTENT_DEDUP_WINDOW_HOURS = 48


def _format_transcript(lines):
    """Join transcript lines into one string, truncated to a bounded
    size (keeps the LAST lines, since a failure's most useful context
    is usually right before the session ends)."""
    if len(lines) > _TRANSCRIPT_MAX_LINES:
        omitted = len(lines) - _TRANSCRIPT_MAX_LINES
        lines = [f'...transcript truncated, {omitted} earlier lines omitted...'] + \
                lines[-_TRANSCRIPT_MAX_LINES:]
    text = '\n'.join(lines)
    if len(text) > _TRANSCRIPT_MAX_CHARS:
        text = f'...transcript truncated to last {_TRANSCRIPT_MAX_CHARS} chars...\n' + \
               text[-_TRANSCRIPT_MAX_CHARS:]
    return text


class _NetmailAdapter:
    """Adapt a NetmailMessage row to the attribute set the FTS-0001 packet
    builder (anetbbs.echomail.binkp._build_ftn_packet) expects to see on
    an EchomailMessage. Netmail differs from echomail in three ways:
      - no AREA: line (handled by area=None)
      - no SEEN-BY/PATH (also signaled by area=None)
      - the destination header points at a single node, so we expose
        from_address/to_address for the builder to write @INTL when the
        zones differ.
    Field name shims: NetmailMessage uses msgid/reply_msgid; the builder
    expects msg_id/reply_id.
    """
    __slots__ = ('_nm', 'area', 'body', 'from_name', 'to_name', 'subject',
                 'tear_line', 'origin_line', 'kludges', 'seenby', 'path',
                 'chrs', 'msg_id', 'reply_id', 'from_address', 'to_address',
                 'direction')

    def __init__(self, nm):
        self._nm = nm
        self.area = None                       # netmail: no area
        self.body = nm.body or ''
        self.from_name = nm.from_name
        self.to_name = nm.to_name
        self.subject = nm.subject or ''
        self.tear_line = None                  # build_message will default
        self.origin_line = None
        self.kludges = nm.kludges
        self.seenby = None
        self.path = None
        self.chrs = nm.chrs or 'CP437 2'
        self.msg_id = nm.msgid
        self.reply_id = nm.reply_msgid
        self.from_address = nm.from_address or ''
        self.to_address = nm.to_address or ''
        self.direction = 'netmail'             # signals private to packet builder

    def __setattr__(self, key, value):
        super().__setattr__(key, value)
        # Auto-generated msgid in the builder should propagate back to the row
        if key == 'msg_id' and getattr(self, '_nm', None) is not None:
            self._nm.msgid = value


def start_poller(app):
    """
    Start the background poller thread.
    Should be called once after the Flask app is fully initialized.
    """
    global _poller_thread

    if not app.config.get('ECHOMAIL_POLL_ENABLED', True):
        logger.info("Echomail poller disabled by configuration")
        return

    if _poller_thread and _poller_thread.is_alive():
        logger.warning("Echomail poller already running")
        return

    _stop_event.clear()
    _poller_thread = threading.Thread(
        target=_poller_loop,
        args=(app,),
        daemon=True,
        name='echomail-poller'
    )
    _poller_thread.start()
    logger.info("Echomail poller thread started")


def stop_poller():
    """Signal the poller thread to stop."""
    _stop_event.set()


def poll_network_now(app, network_id: int):
    """
    Trigger an immediate poll for a specific network.
    Runs synchronously in the calling thread (use from a background thread or
    in a request context where latency is acceptable).
    """
    with app.app_context():
        from ..models import EchomailNetwork
        network = EchomailNetwork.query.get(network_id)
        if network:
            _do_poll(app, network)


def poll_all_now(app):
    """Trigger an immediate poll for all active networks."""
    with app.app_context():
        from ..models import EchomailNetwork
        networks = EchomailNetwork.query.filter_by(is_active=True).all()
        for network in networks:
            try:
                _do_poll(app, network)
            except Exception as exc:
                logger.error("Poller: error polling %s: %s", network.name, exc)


def poll_node_now(app, node_id: int):
    """Trigger an immediate hub-initiated poll of a downstream BinkP node
    (dial OUT and push its hold queue), mirroring poll_network_now() for
    upstream networks. Runs synchronously in the calling thread."""
    with app.app_context():
        from ..models import BinkPNode
        node = BinkPNode.query.get(node_id)
        if node:
            _do_poll_node(app, node)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _poller_loop(app):
    """Main loop — checks every 60 seconds whether any network needs polling."""
    logger.info("Echomail poller loop started")
    while not _stop_event.wait(timeout=60):
        try:
            with app.app_context():
                from ..models import EchomailNetwork
                networks = EchomailNetwork.query.filter_by(is_active=True).all()
                now = datetime.utcnow()
                for network in networks:
                    if _is_poll_due(network, now):
                        try:
                            _do_poll(app, network)
                        except Exception as exc:
                            logger.error("Poller: error polling %s: %s", network.name, exc)
        except Exception as exc:
            logger.error("Poller loop error: %s", exc)

    logger.info("Echomail poller loop stopped")


_MIN_POLL_INTERVAL_MIN = 5  # floor — protect remote hubs from sub-5min polling


def _is_poll_due(network, now: datetime) -> bool:
    """Return True if the network is due for a poll."""
    if not network.last_poll_at:
        return True
    minutes = max(network.poll_interval_minutes or 60, _MIN_POLL_INTERVAL_MIN)
    return now >= network.last_poll_at + timedelta(minutes=minutes)


def _self_referential_reason(network, app):
    """Return a reason string if *network* looks like it's configured to
    poll our own BBS instead of a genuine remote peer, else None.

    This happens when a sysop is the hub for a network (so the seeded
    "point at the hub" row also exists on the hub's own install) and
    activates/configures that same row on their own BBS -- there's
    nothing wrong with the row itself (other sysops need it exactly as
    seeded to reach the hub), it's just never valid for the hub's own
    install to dial itself. Areas stay visible either way (that's keyed
    off EchomailNetwork.is_active, which this deliberately doesn't
    touch) -- this only stops the pointless/failing dial-out attempt.
    """
    if network.network_type == 'binkp':
        our = (network.our_address or '').strip().lower()
        hub = (network.hub_address or '').strip().lower()
        if our and hub and our == hub:
            return (f"our_address ({network.our_address}) matches "
                    f"hub_address ({network.hub_address})")
    elif network.network_type == 'qwk':
        our_host = ((app.config.get('BBS_PUBLIC_HOST')
                     or app.config.get('BBS_DOMAIN') or '')
                    .strip().lower())
        qwk_host = (network.qwk_host or '').strip().lower()
        if our_host and qwk_host and our_host == qwk_host:
            return (f"qwk_host ({network.qwk_host}) matches this BBS's "
                    f"own public host")
    return None


def _do_poll(app, network):
    """
    Perform a poll for *network*.  Creates a PollLog entry, runs the
    appropriate client, and imports inbound messages.
    """
    from ..models import db, EchomailMessage, EchomailPollLog

    # Check self-referential BEFORE creating any log row at all -- this
    # is expected, static, unchanging configuration state (a hub's own
    # network row pointing at itself), not a real poll attempt, and the
    # poller loop re-checks every network once a minute. Logging a full
    # PollLog row for it every single minute drowned the real activity
    # out of the admin UI's poll log -- live-caught after a hub had
    # been running for under 20 minutes and the log was already
    # dozens of rows deep. A single debug-level log line (invisible at
    # the default INFO level) is enough for anyone actually debugging
    # this specific thing; the web UI shouldn't see it at all.
    self_ref = _self_referential_reason(network, app)
    if self_ref:
        logger.debug("Poller: skipping %s -- %s. This is expected if "
                     "this install is the hub for this network; peer "
                     "sysops should point their own install at the hub, "
                     "not the hub at itself.", network.name, self_ref)
        return

    # Real gap found in a full echomail-subsystem audit: nothing guarded
    # against two overlapping poll attempts for the SAME network -- a
    # sysop double-clicking "Poll Now" (echomail_admin.py's poll_now(),
    # which spawns a bare daemon thread with no dedup check at all), or
    # a manual poll landing while the scheduled _poller_loop's own tick
    # for this network is already mid-flight. Two concurrent BinkP
    # sessions to the same peer risk duplicate sends and interleaved
    # ack-gated-stamping/hold-queue writes. Reuses the existing
    # status='running' row this function already writes (added for poll-
    # in-progress visibility) as the dedup signal, rather than a new
    # locking primitive -- a stale 'running' row genuinely blocking
    # future polls forever isn't a new risk this introduces: every exit
    # path below (including the except/finally) already unconditionally
    # flips status away from 'running' before returning.
    already_running = EchomailPollLog.query.filter_by(
        network_id=network.id, status='running').first()
    if already_running:
        logger.info(
            "Poller: skipping %s -- a poll is already in progress "
            "(EchomailPollLog #%d, started %s)",
            network.name, already_running.id, already_running.started_at)
        return

    log = EchomailPollLog(
        network_id=network.id,
        poll_type='both',
        started_at=datetime.utcnow(),
        # 'running', not 'error' -- this row is committed BEFORE the poll
        # has even attempted anything. The old placeholder value meant a
        # sysop who happened to view the poll log while a poll was still
        # in flight saw it reported as already failed. Real report (FR):
        # "no place to see if a poll is going on... you can't tell if one
        # is ongoing currently or not" -- this was one reason why: the
        # one signal that WOULD have existed (a running row) looked
        # exactly like a failure. Every exit path below still correctly
        # overwrites this to 'success'/'error' before the function returns.
        status='running',
    )
    db.session.add(log)
    db.session.commit()

    # Caller-owned list -- BinkPClient appends frame-by-frame lines to
    # this same list object, so it's still readable here even if
    # _run_client() raises partway through a session (the client
    # instance itself goes out of scope with the exception, but this
    # list doesn't, since _do_poll() -- not the client -- owns it).
    transcript_lines = []

    try:
        from ..models import NetmailMessage

        # Gather queued outbound messages.
        # - EchomailMessage covers public echomail (direction='outbound') and
        #   private QWK netmail (direction='netmail') — used by both BinkP
        #   echomail packs and QWK REP packets.
        # - NetmailMessage is the FidoNet netmail inbox/sent table — applies
        #   to BinkP networks only. Wrapped in _NetmailAdapter so the FTS-0001
        #   packet builder can iterate over a uniform list.
        outbound_echo = EchomailMessage.query.filter(
            EchomailMessage.network_id == network.id,
            EchomailMessage.direction.in_(('outbound', 'netmail')),
            EchomailMessage.sent_at.is_(None),
        ).all()

        outbound_nm = []
        if network.network_type == 'binkp':
            outbound_nm = NetmailMessage.query.filter(
                NetmailMessage.network_id == network.id,
                NetmailMessage.direction == 'outbound',
                NetmailMessage.status == 'queued',
            ).all()

        outbound = list(outbound_echo) + [_NetmailAdapter(nm) for nm in outbound_nm]

        result = _run_client(network, outbound, app, transcript=transcript_lines)

        # Snapshot the current max message id so the tosser can find new ones.
        from ..models import EchomailMessage as _EM_PRE
        pre_import_max = (db.session.query(db.func.max(_EM_PRE.id))
                          .filter_by(network_id=network.id, direction='inbound')
                          .scalar() or 0)

        # Import inbound messages. _import_message returns 1=imported,
        # 0=duplicate, -1=dropped (loop/unknown-area/unsubscribed).
        imported = 0
        duplicates = 0
        dropped = 0
        for msg_data in result.get('received', []):
            rc = _import_message(network, msg_data)
            if rc > 0:
                imported += 1
            elif rc == 0:
                duplicates += 1
            else:
                dropped += 1

        log.status = 'success'
        log.messages_sent = result.get('sent', 0)
        log.messages_received = imported
        if duplicates or dropped:
            err_bits = []
            if duplicates:
                err_bits.append(f'{duplicates} duplicate')
            if dropped:
                err_bits.append(f'{dropped} dropped (loop/unknown/unsub)')
            log.error_message = ', '.join(err_bits)

        # Stamp outbound messages as sent -- ONLY if the hub actually
        # acknowledged the packet (result['sent'] nonzero). _send_messages()
        # bundles the whole outbound_echo+outbound_nm batch into one .pkt
        # and sends it as a single BinkP file transfer, accepted or
        # rejected as a unit (no partial ack) -- so "sent" here is really
        # "all-or-nothing for this batch". Confirmed real bug found in a
        # full-subsystem audit: this loop used to run unconditionally,
        # so a busy/unstable hub replying M_SKIP or M_ERR (both normal,
        # spec-legal responses) still got every queued message marked
        # sent/delivered here, even though _send_messages had already
        # correctly reported 0 sent -- messages were silently lost with
        # no retry, and the poll log still read "success".
        if result.get('sent', 0):
            now = datetime.utcnow()
            for msg in outbound_echo:
                msg.sent_at = now
            for nm in outbound_nm:
                nm.sent_at = now
                nm.status = 'sent'
                nm.is_sent = True
        elif outbound:
            logger.warning(
                "Poller: %s — hub did not acknowledge outbound batch, "
                "%d message(s) left queued for retry next poll",
                network.name, len(outbound))

        logger.info("Poller: %s — sent=%d received=%d",
                    network.name, log.messages_sent, log.messages_received)

        # Hub tosser — fan out newly imported messages to downstream nodes.
        if imported:
            try:
                from .tosser import toss_message
                from ..models import EchomailMessage as _EM
                new_msgs = (_EM.query
                            .filter(_EM.network_id == network.id,
                                    _EM.direction == 'inbound',
                                    _EM.id > pre_import_max)
                            .all())
                for em in new_msgs:
                    toss_message(em.id, exclude_peer_address=network.hub_address)
            except Exception:
                logger.exception('Hub tosser failed after poll of %s', network.name)

    except Exception as exc:
        log.status = 'error'
        # Always include the exception type -- str(exc) alone can be
        # empty or unhelpful for some failure types (bare
        # socket.timeout, some ConnectionErrors), which left sysops
        # with close to nothing to diagnose a failed poll from.
        detail = str(exc)
        log.error_message = f'{type(exc).__name__}: {detail}' if detail else type(exc).__name__
        db.session.commit()
        logger.error("Poller: poll failed for %s: %s", network.name, exc)
        raise
    finally:
        # Stamp last_poll_at unconditionally (success or failure) --
        # previously only the success path set this, so a failed poll
        # left it stale and _is_poll_due() saw the network as still
        # "due" on the very next scheduler tick (60s later), retrying
        # in a tight loop regardless of the configured poll interval
        # instead of backing off. Reported live by a real sysop
        # (external GitHub issue #8): some upstream hubs started
        # blocking the repeated attempts, which only made the original
        # failure worse.
        network.last_poll_at = datetime.utcnow()
        log.completed_at = datetime.utcnow()
        if transcript_lines:
            log.transcript = _format_transcript(transcript_lines)
        db.session.commit()


def _do_poll_node(app, node):
    """Hub-initiated outbound poll of a downstream BinkP node: dial OUT
    to node.binkp_host, push its BinkPHoldQueue, and import anything it
    sends back. Mirrors _do_poll()'s structure but for a BinkPNode instead
    of an upstream EchomailNetwork -- the roles are reversed (we're the
    caller, they're the answering hub for this one session), but the
    packet transport, message import, and poll-log bookkeeping are the
    same BinkP mechanics either way.
    """
    from ..models import db, EchomailPollLog
    from .routing import self_hub_binkp_network

    if not (node.binkp_host or '').strip():
        logger.warning(
            "Poller: cannot poll node %s (%s) -- no BinkP host configured "
            "(this node is poll-in only)", node.name, node.ftn_address)
        return

    # Same resolution BinkPNode.network_id exists to make unambiguous (see
    # its own model comment) -- reused here rather than re-derived, since
    # a node with no resolvable network has nowhere valid to stamp
    # outbound mail's AKA or attach an EchomailPollLog row (NOT NULL).
    network = node.network or self_hub_binkp_network(node.hub_identity_id)
    if network is None:
        logger.warning(
            "Poller: cannot poll node %s (%s) -- no resolvable BinkP "
            "network to stamp outbound mail/log against; set the node's "
            "Network field explicitly", node.name, node.ftn_address)
        return

    log = EchomailPollLog(
        network_id=network.id,
        poll_type='both',
        started_at=datetime.utcnow(),
        status='running',
    )
    db.session.add(log)
    db.session.commit()

    transcript_lines = []
    try:
        from .tosser import (get_pending_for_node, mark_sent_for_node,
                             get_pending_netmail_for_node, mark_netmail_sent)
        from ..models import EchomailMessage as _EM_PRE

        outbound_echo = get_pending_for_node(node.id)
        # Real gap found in a full echomail-subsystem audit: this only
        # ever gathered the echomail hold queue -- binkp_server.py's
        # inbound-listener downstream_node_id branch already gathers
        # BOTH echomail and netmail for this exact node (see
        # get_pending_netmail_for_node()'s own docstring for the
        # original bug this closed on that path), but the hub-initiated
        # "Poll Node" dial-OUT direction never got the same fix -- a
        # sysop's reply or AreaFix auto-reply queued for a node stayed
        # stuck in NetmailMessage.status='queued' unless that node
        # happened to dial IN first, even after explicitly clicking
        # "Poll Node".
        outbound_nm = get_pending_netmail_for_node(node)
        outbound = list(outbound_echo) + [_NetmailAdapter(nm) for nm in outbound_nm]

        # Snapshot BEFORE import, same as _do_poll(), so the tosser
        # fan-out below can find exactly the rows this poll just added.
        pre_import_max = (db.session.query(db.func.max(_EM_PRE.id))
                          .filter_by(network_id=network.id, direction='inbound')
                          .scalar() or 0)

        result = _run_node_client(node, network, outbound, app,
                                  transcript=transcript_lines)

        imported = duplicates = dropped = 0
        for msg_data in result.get('received', []):
            rc = _import_message(network, msg_data)
            if rc > 0:
                imported += 1
            elif rc == 0:
                duplicates += 1
            else:
                dropped += 1

        log.status = 'success'
        log.messages_sent = result.get('sent', 0)
        log.messages_received = imported
        if duplicates or dropped:
            err_bits = []
            if duplicates:
                err_bits.append(f'{duplicates} duplicate')
            if dropped:
                err_bits.append(f'{dropped} dropped (loop/unknown/unsub)')
            log.error_message = ', '.join(err_bits)

        # Same ack-gating as _do_poll() -- only mark the hold queue (and
        # queued netmail) sent if the node actually acknowledged the
        # batch (see test_poller_ack_gated_stamping.py for why this
        # matters). Marked separately, not via a shared `outbound` id
        # list, since mark_sent_for_node() expects BinkPHoldQueue ids
        # specifically -- outbound also contains _NetmailAdapter-wrapped
        # NetmailMessage rows, which mark_netmail_sent() handles instead.
        if result.get('sent', 0) and outbound:
            if outbound_echo:
                mark_sent_for_node(node.id, [m.id for m in outbound_echo])
            if outbound_nm:
                mark_netmail_sent(outbound_nm)
        elif outbound:
            logger.warning(
                "Poller: node %s (%s) -- did not acknowledge outbound "
                "batch, %d message(s) left queued for retry",
                node.name, node.ftn_address, len(outbound))

        node.last_seen_at = datetime.utcnow()

        logger.info("Poller: node %s (%s) -- sent=%d received=%d",
                    node.name, node.ftn_address, log.messages_sent,
                    log.messages_received)

        if imported:
            try:
                from .tosser import toss_message
                from ..models import EchomailMessage as _EM
                new_msgs = (_EM.query
                           .filter(_EM.network_id == network.id,
                                   _EM.direction == 'inbound',
                                   _EM.id > pre_import_max)
                           .all())
                for em in new_msgs:
                    toss_message(em.id, exclude_peer_address=node.ftn_address)
            except Exception:
                logger.exception('Hub tosser failed after polling node %s',
                                 node.name)

    except Exception as exc:
        log.status = 'error'
        detail = str(exc)
        log.error_message = f'{type(exc).__name__}: {detail}' if detail else type(exc).__name__
        db.session.commit()
        logger.error("Poller: poll failed for node %s (%s): %s",
                     node.name, node.ftn_address, exc)
        raise
    finally:
        log.completed_at = datetime.utcnow()
        if transcript_lines:
            log.transcript = _format_transcript(transcript_lines)
        db.session.commit()


def _run_node_client(node, network, outbound_messages, app, transcript=None):
    """Instantiate a BinkPClient and dial OUT to a downstream node --
    the node-poll counterpart of _run_client(), which dials upstream
    EchomailNetwork rows instead. Separated out (rather than inlined in
    _do_poll_node) so tests can patch this one seam exactly like existing
    poller tests patch _run_client (see test_poller_ack_gated_stamping.py)."""
    from .binkp import BinkPClient
    from ..models import db, HatchQueue
    client = BinkPClient(
        host=node.binkp_host or '',
        port=node.binkp_port or 24554,
        our_address=network.our_address or '1:1/1',
        hub_address=node.ftn_address,
        password=node.password or '',
        use_tls=bool(getattr(node, 'binkp_tls', False)),
        domain=(getattr(network, 'ftn_domain', None)
                or network.name or '').strip().lower() or None,
        transcript=transcript,
        packet_password=getattr(network, 'packet_password', None) or '',
        default_crash=bool(getattr(network, 'default_crash', False)),
        default_hold=bool(getattr(network, 'default_hold', False)),
        default_direct=bool(getattr(network, 'default_direct', False)),
    )
    # Fallback changed from '/tmp' to 'data' (bandit B108 sweep, pre-
    # release audit): config.py's ECHOMAIL_DATA_DIR is always set in
    # practice, so this default is defensive-only, but inbound echomail
    # is real user data -- the same "don't default real data into /tmp"
    # lesson already learned once for backups (see backups_admin.py).
    data_dir = app.config.get('ECHOMAIL_DATA_DIR', 'data')

    # Real gap found in a full echomail-subsystem audit: this always
    # passed hatch_items=[], so a downstream node's FileFix subscription
    # (tic.py's process_tic()/hatch_local_file() queue HatchQueue rows
    # keyed by the node's own ftn_address as peer_address) never
    # actually got delivered on this path -- the hub's own "Pending"
    # counter went up and the file just sat there forever with no error
    # anywhere. Mirrors _run_client()'s already-correct hatch-out for
    # the leaf-dialing-upstream-hub direction, just keyed on the node's
    # ftn_address instead of network.hub_address.
    hatch_items = (HatchQueue.query
                   .filter(HatchQueue.peer_address == (node.ftn_address or ''),
                           HatchQueue.status == 'pending')
                   .order_by(HatchQueue.queued_at)
                   .all())
    out = client.poll(outbound_messages=outbound_messages, data_dir=data_dir,
                      hatch_items=hatch_items)
    for hid in out.get('hatched_ids', []):
        row = HatchQueue.query.get(hid)
        if row is not None:
            row.status = 'sent'
            row.sent_at = datetime.utcnow()
    for failed_item, message in out.get('hatch_failures', []):
        from .tic import record_hatch_attempt_failure
        record_hatch_attempt_failure(failed_item, message)
    if out.get('hatched_ids') or out.get('hatch_failures'):
        db.session.commit()
    return out


def _run_client(network, outbound_messages, app, transcript=None):
    """Instantiate and run the appropriate protocol client.

    `transcript`, if given, is a caller-owned list that BinkPClient
    appends frame-by-frame session lines to -- QWK ignores it, this
    concept is BinkP-specific."""
    if network.network_type == 'binkp':
        from .binkp import BinkPClient
        from ..models import db, HatchQueue
        client = BinkPClient(
            host=network.binkp_host or '',
            port=network.binkp_port or 24554,
            our_address=network.our_address or '1:1/1',
            hub_address=network.hub_address or '1:1/0',
            password=network.binkp_password or '',
            use_tls=bool(getattr(network, 'binkp_tls', False)),
            domain=(getattr(network, 'ftn_domain', None)
                    or network.name or '').strip().lower() or None,
            transcript=transcript,
            packet_password=getattr(network, 'packet_password', None) or '',
            default_crash=bool(getattr(network, 'default_crash', False)),
            default_hold=bool(getattr(network, 'default_hold', False)),
            default_direct=bool(getattr(network, 'default_direct', False)),
        )
        data_dir = app.config.get('ECHOMAIL_DATA_DIR', 'data')
        # Pick up any pending hatch-out files for this peer (the network's
        # hub_address is the destination we're polling). Older subscriptions
        # may target the hub directly; newer ones may target other downstream
        # peers we route through this hub. We send everything bound for the
        # hub address on this connection.
        hatch_items = (HatchQueue.query
                       .filter(HatchQueue.peer_address == (network.hub_address or ''),
                               HatchQueue.status == 'pending')
                       .order_by(HatchQueue.queued_at)
                       .all())
        out = client.poll(outbound_messages=outbound_messages,
                          data_dir=data_dir,
                          hatch_items=hatch_items)
        # Mark shipped items as sent.
        for hid in out.get('hatched_ids', []):
            row = HatchQueue.query.get(hid)
            if row is not None:
                row.status = 'sent'
                row.sent_at = datetime.utcnow()
        for failed_item, message in out.get('hatch_failures', []):
            from .tic import record_hatch_attempt_failure
            record_hatch_attempt_failure(failed_item, message)
        if out.get('hatched_ids') or out.get('hatch_failures'):
            db.session.commit()
        return out

    elif network.network_type == 'qwk':
        from .qwk import QWKClient
        from ..models import EchoArea
        # Stamp the hub conference number on each outbound message.
        # QWK area tags are plain numeric strings (e.g. '2010') matching the
        # hub's conference number. Legacy installs may have 'QWK_N' tags —
        # both formats are supported. _build_rep_packet reads _qwk_conf_num.
        _area_cache = {}
        for _msg in outbound_messages:
            _aid = getattr(_msg, 'area_id', None)
            if not _aid:
                continue
            if _aid not in _area_cache:
                _area_cache[_aid] = EchoArea.query.get(_aid)
            _area = _area_cache.get(_aid)
            _tag = (_area.tag or '').strip() if _area else ''
            _conf = 0
            if _tag.isdigit():
                # Current format: plain numeric tag (e.g. '2010')
                _conf = int(_tag)
            elif _tag.upper().startswith('QWK_') and _tag[4:].isdigit():
                # Legacy format: 'QWK_2010' — still extract the number
                _conf = int(_tag[4:])
            _msg._qwk_conf_num = _conf
            if _conf == 0 and getattr(_msg, 'direction', '') != 'netmail':
                logger.warning(
                    "QWK outbound: area tag %r on msg %r (area_id=%s) could "
                    "not be parsed as a conference number — will send to conf 0 "
                    "(personal mail, NOT broadcast). Area tag must be a plain "
                    "number e.g. '2010'.",
                    _tag or 'N/A', getattr(_msg, 'subject', '?'), _aid)
        client = QWKClient(
            host=network.qwk_host or '',
            port=network.qwk_port or 80,
            # QNET-FTP-style hubs (e.g. ANotherNetwork) authenticate the
            # FTP login using the Packet ID as the username -- fall back
            # to it if qwk_username is blank, since a blank username
            # always fails login anyway (nothing lost by trying).
            username=network.qwk_username or network.qwk_packet_id or '',
            password=network.qwk_password or '',
            packet_id=network.qwk_packet_id or 'ANET',
            download_url=getattr(network, 'qwk_download_url', '') or '',
            upload_url=getattr(network, 'qwk_upload_url', '') or '',
            hub_id=getattr(network, 'qwk_hub_id', '') or '',
        )
        data_dir = app.config.get('ECHOMAIL_DATA_DIR', 'data')
        return client.poll(outbound_messages=outbound_messages, data_dir=data_dir)

    else:
        raise ValueError(f"Unknown network type: {network.network_type}")


def _record_bad_area(network, area_tag, msg_data, reason, title, body):
    """Upsert a BadAreaLog row for a dropped message (see reason values in
    the model's docstring). One row per (network, tag) -- `reason` is
    overwritten with the latest classification so a tag that was, say,
    'unsubscribed' and got fully removed later would read 'unknown' on
    its next sighting instead of showing a stale reason.
    """
    from ..models import db, BadAreaLog
    try:
        existing_bad = BadAreaLog.query.filter_by(
            network_id=network.id, tag=area_tag).first()
        if existing_bad:
            existing_bad.reason = reason
            existing_bad.count = (existing_bad.count or 0) + 1
            existing_bad.last_seen_at = datetime.utcnow()
        else:
            db.session.add(BadAreaLog(
                network_id=network.id, tag=area_tag, reason=reason,
                sample_from=msg_data.get('from_name', '')[:100],
                sample_subject=msg_data.get('subject', '')[:200],
                count=1,
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
            ))
            # Only on first sighting of this (network, tag) pair --
            # existing_bad above absorbs every later message for the
            # same bad area, so this can't turn into a per-message
            # notification flood.
            from ..features.notify import notify_admins
            notify_admins('bad_area', title=title, body=body,
                          target_url='/admin/echomail/bad_areas')
    except Exception as exc:
        logger.debug("BadAreaLog write failed (table may be absent): %s", exc)


def _import_message(network, msg_data: dict) -> int:
    """
    Import a single parsed message dict into the database.
    Returns 1 if imported, 0 if duplicate, -1 if dropped (loop / unsubscribed).
    """
    from ..models import db, EchoArea, EchomailMessage, maybe_tag_ansi_subject
    import json as _json

    area_tag = msg_data.get('area_tag')

    # ----------------- Netmail (no AREA: kludge) -----------------
    if not area_tag:
        return _import_netmail(network, msg_data)

    # ----------------- Echomail -----------------
    # NO PATH-based loop detection on import. The old check rejected any
    # message whose PATH contained our address — but Mystic (and every
    # FTS-0004-compliant tosser) **correctly** appends the destination
    # address to PATH right before sending. So every single message
    # legitimately destined for us has our address in PATH, and the
    # earlier check loop-dropped the entire feed.
    #
    # True echomail loop detection happens via:
    #   - msg_id deduplication below (per-area uniqueness)
    #   - SEEN-BY checks during FORWARDING decisions, not on import
    #
    # The TQWnet rescan of ~25k messages from `1337:3/100` was getting
    # entirely loop-dropped here because Mystic put `3/231` (us) in
    # PATH before shipping — exactly as the spec says it must.

    # Find the echo area. Behavior differs by network type:
    # - QWK: hub publishes its conference list in CONTROL.DAT, so it's safe
    #        to auto-create areas the parser already validated against it.
    # - BinkP/FTN: tags are arbitrary, so unknown ones go to BadAreaLog
    #        for sysop review (SBBSecho's BadAreaFile semantics).
    area = EchoArea.query.filter_by(network_id=network.id, tag=area_tag).first()
    if not area and network.network_type == 'qwk' and area_tag.isdigit():
        # Migration: older ANetBBS installs tagged QWK areas as 'QWK_N'.
        # If an area with the old format exists, rename it to the plain
        # numeric tag ('2010') so outbound conf_num extraction works correctly.
        old_tag = f'QWK_{area_tag}'
        area = EchoArea.query.filter_by(network_id=network.id, tag=old_tag).first()
        if area:
            logger.info("QWK: migrating area tag %r → %r", old_tag, area_tag)
            area.tag = area_tag
            db.session.flush()
    if not area:
        if network.network_type == 'qwk':
            area = EchoArea(
                network_id=network.id,
                tag=area_tag,
                name=msg_data.get('area_name') or area_tag,
                is_subscribed=True,
                is_active=True,
            )
            db.session.add(area)
            db.session.flush()
        else:
            _record_bad_area(
                network, area_tag, msg_data, reason='unknown',
                title=f'Unknown echomail area: {area_tag} on {network.name}',
                body=f'Inbound mail arrived tagged for {area_tag!r}, '
                     f'which this BBS does not carry. Review under '
                     f'Admin -> Echomail -> Bad Areas.')
            logger.info("Echomail: dropping msg for unknown area %s on %s",
                        area_tag, network.name)
            return -1

    if not area.is_subscribed or not area.is_active:
        # Area exists locally -- sysop just isn't taking it right now.
        # Previously silently dropped (logger.debug only, no persisted
        # record), so there was no way to see which areas were quietly
        # losing mail without grepping the log. Applies to every network
        # type (unlike the 'unknown' branch above, which is BinkP/FTN-
        # only -- QWK auto-creates unknown areas so can't hit this path
        # via an unrecognized tag, only via a *known* area the sysop
        # unsubscribed/deactivated after it was created).
        _record_bad_area(
            network, area_tag, msg_data, reason='unsubscribed',
            title=f'Mail dropped for unsubscribed area: {area_tag} on {network.name}',
            body=f'Inbound mail arrived for {area_tag!r}, which this BBS '
                 f'carries but is not subscribed to (or has deactivated). '
                 f'Review under Admin -> Echomail -> Bad Areas.')
        logger.debug("Echomail: skipping msg for unsubscribed area %s", area_tag)
        return -1

    # Deduplicate by msg_id if present
    msg_id = msg_data.get('msg_id')
    if msg_id:
        existing = EchomailMessage.query.filter_by(msg_id=msg_id, area_id=area.id).first()
        if existing:
            return 0

    # CRITICAL: wrap the insert in a nested transaction (SAVEPOINT) so
    # one bad message in a 50k-batch rescan doesn't poison the whole
    # session. Without this, a single row that violates a constraint
    # (length overflow, FK miss, unique-clash race) propagates an
    # IntegrityError on the next flush — leaving the SQLAlchemy session
    # in 'invalid' state. Subsequent _import_message calls then fail
    # with "Can't reconnect until invalid transaction is rolled back",
    # the outer commit collapses, and the ENTIRE batch is lost (which
    # is exactly the symptom of the 50k-message TQWnet rescan that
    # disappeared into the void on first attempt).
    try:
        with db.session.begin_nested():
            msg = EchomailMessage(
                area_id=area.id,
                network_id=network.id,
                msg_id=msg_id,
                reply_id=msg_data.get('reply_id'),
                from_name=(msg_data.get('from_name') or '')[:120],
                from_address=(msg_data.get('from_address') or '')[:60],
                to_name=(msg_data.get('to_name') or 'All')[:120],
                to_address=(msg_data.get('to_address') or '')[:60],
                subject=maybe_tag_ansi_subject(
                    msg_data.get('subject') or '(no subject)',
                    msg_data.get('body', ''))[:200],
                body=msg_data.get('body', ''),
                tear_line=(msg_data.get('tear_line') or '')[:200] or None,
                origin_line=(msg_data.get('origin_line') or '')[:200] or None,
                chrs=(msg_data.get('chrs') or 'CP437 2')[:40],
                kludges=_json.dumps(msg_data.get('kludges') or []),
                seenby=_json.dumps(msg_data.get('seenby') or []),
                path=_json.dumps(msg_data.get('path') or []),
                direction='inbound',
            )
            db.session.add(msg)
            # Force flush inside the savepoint so any IntegrityError
            # surfaces NOW and gets contained by the savepoint, not
            # leaked to the outer transaction at final-commit time.
            db.session.flush()
            # Update area stats — also inside the savepoint so failure
            # rolls these back too (no orphaned counter increment).
            area.total_messages = (area.total_messages or 0) + 1
            area.last_message_at = datetime.utcnow()
    except Exception as exc:
        # Savepoint already rolled back; outer session is still valid.
        logger.warning(
            "Echomail: skipping malformed msg in %s (msgid=%r from=%r): %s",
            area_tag, msg_id, msg_data.get('from_name', ''), exc)
        return -1

    try:
        from ..features.webhooks import fire
        fire('echomail', {'from_name': msg.from_name, 'subject': msg.subject,
                          'area_tag': area_tag, 'content': msg.body})
    except Exception:
        # Webhooks are best-effort by design (a broken/unreachable
        # webhook target must never block real mail import), but a
        # completely silent swallow left no trace at all when one
        # failed -- at least log it at debug level.
        logger.debug("Echomail: webhook fire failed for msg in %s", area_tag,
                    exc_info=True)

    from .notify_reply import maybe_notify_recipient
    maybe_notify_recipient(msg, area, network)

    return 1


def _import_netmail(network, msg_data: dict) -> int:
    """Import an inbound netmail message and link it to a local user
    when the recipient name or to_address matches one of their AKAs.
    Falls back to the configured DefaultRecipient if no match.
    """
    from ..models import db, NetmailMessage
    from .routing import resolve_netmail_recipient
    import json as _json

    msg_id = msg_data.get('msg_id') or ''
    if msg_id:
        # NetmailMessage's actual column is `msgid` (no underscore) --
        # filter_by(msg_id=...) raised AttributeError unconditionally
        # whenever msg_id was truthy (i.e. on essentially every real
        # inbound netmail carrying a MSGID kludge), crashing this
        # function before it ever got to import the message. Pre-
        # existing bug, caught by CI (not local testing -- no Flask in
        # this sandbox) when adding netmail notification support.
        existing = NetmailMessage.query.filter_by(msgid=msg_id).first()
        if existing:
            return 0

    # `or ''`/`or '(no subject)'`, not a plain .get() default, so an
    # explicit None in msg_data (not just a missing key) still coerces to
    # a concrete string -- matches the to_name/to_address null-safety
    # pattern already used below, and keeps the dedup equality filter
    # below from silently failing to match against a NULL column.
    from_name = msg_data.get('from_name') or ''
    from_address = msg_data.get('from_address') or ''
    subject = msg_data.get('subject') or '(no subject)'
    body = msg_data.get('body') or ''

    # Content-based dedup fallback. Some peer mailers regenerate MSGID
    # on every resend of the same netmail (observed live: a peer
    # re-flooding an "Area Management Request"/"List of Available Areas"
    # notice from SBBSecho every ~10 minutes, matching its poll cadence,
    # defeating the exact-MSGID check above). The original version of
    # this fallback also required an exact `body` match -- confirmed
    # live NOT sufficient: real resends kept creating new rows anyway,
    # meaning the body isn't byte-identical across regenerations (likely
    # a timestamp or similar generated into the body text itself, not
    # just the MSGID kludge). Dropped the body comparison entirely --
    # sender+subject+network within the dedup window is already a very
    # strong signal for automated administrative netmail like this (a
    # sysop is never going to send/receive two genuinely distinct
    # "Area Management Request" messages from the same address within
    # the window), and it's also cheaper: no TEXT-column comparison
    # needed, which matters under eventlet -- this app's web process
    # monkey-patches threading but NOT sqlite3, so a slow query here
    # blocks every user on every page, and an ever-growing table of
    # never-deduped rows (from the body check never matching) made that
    # query slower every single cycle. See also the received_at index
    # added in v1.0b2.145 -- this filter was previously unindexed too.
    # EXCEPT for netmail addressed to a robot (AreaFix/FileFix and
    # their aliases) -- see binkp_server.py's matching fix for the full
    # story: each one is a distinct command, not a duplicate broadcast,
    # and a sysop resending an AreaFix request often reuses the same
    # subject. Applying this dedup to robot netmail silently drops
    # real commands with zero error. The exact-MSGID check above still
    # catches a literal retransmit of the same packet.
    to_name = (msg_data.get('to_name') or '').strip()
    _is_robot_netmail = to_name.lower() in (
        'areafix', 'area fix', 'areamgr',
        'filefix', 'file fix', 'filemgr')

    recent_cutoff = datetime.utcnow() - timedelta(hours=_CONTENT_DEDUP_WINDOW_HOURS)
    content_dup = None if _is_robot_netmail else NetmailMessage.query.filter(
        NetmailMessage.network_id == network.id,
        NetmailMessage.direction == 'inbound',
        NetmailMessage.from_name == from_name,
        NetmailMessage.from_address == from_address,
        NetmailMessage.subject == subject,
        NetmailMessage.received_at >= recent_cutoff,
    ).first()
    if content_dup:
        return 0

    to_address = (msg_data.get('to_address') or '').strip()
    to_user = resolve_netmail_recipient(to_name, to_address, network)

    # NetmailMessage has no tear_line / origin_line columns (those are
    # echomail-specific — see EchomailMessage). For netmail, the tear and
    # origin lines stay in the body verbatim. Use only the model's actual
    # column set to avoid `'X' is an invalid keyword argument` failures.
    nm = NetmailMessage(
        network_id=network.id,
        msgid=msg_id or None,
        reply_msgid=msg_data.get('reply_id') or None,
        from_name=from_name,
        from_address=from_address,
        to_name=to_name,
        to_address=to_address,
        to_user_id=to_user.id if to_user else None,
        subject=subject,
        body=body,
        chrs=msg_data.get('chrs') or 'CP437 2',
        kludges=_json.dumps(msg_data.get('kludges') or []),
        direction='inbound',
        status='received',
        created_at=datetime.utcnow(),
        received_at=datetime.utcnow(),
    )
    db.session.add(nm)
    db.session.flush()  # assigns nm.id, needed below regardless of to_user

    if to_user is not None:
        try:
            from ..features.notify import notify
            notify(to_user.id, 'netmail',
                  title=f'Netmail from {nm.from_name}',
                  body=nm.subject or '',
                  target_url=f'/netmail/{nm.id}')
        except Exception as exc:
            logger.debug("Netmail notify() failed for user %s: %s",
                        to_user.id, exc)

    # Real gap found in a full echomail-subsystem audit: AreaFix/FileFix
    # only ever got dispatched from binkp_server.py's INBOUND-listener
    # session (a peer dialing INTO us) -- this outbound-poll receive
    # path (a hub dialing OUT to an upstream network, or the newer
    # hub-initiated "Poll Node" dialing OUT to a downstream node) never
    # checked the recipient name at all, so a netmail addressed to
    # AreaFix/FileFix sent to us in response to OUR OWN poll (a very
    # real scenario once hub-initiated polling exists) just sat there
    # as a plain unread netmail, never processed. Same recognized-name
    # set as binkp_server.py's dispatch.
    to_lower = to_name.lower()
    if to_lower in ('areafix', 'area fix', 'areamgr'):
        try:
            from .areafix import handle_areafix_netmail
            handle_areafix_netmail(nm.id)
        except Exception:
            logger.exception('Areafix bot failed for netmail %d (outbound-poll path)', nm.id)
    elif to_lower in ('filefix', 'file fix', 'filemgr'):
        try:
            from .filefix import handle_filefix_netmail
            handle_filefix_netmail(nm.id)
        except Exception:
            logger.exception('FileFix bot failed for netmail %d (outbound-poll path)', nm.id)

    return 1
