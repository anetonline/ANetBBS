# anetbbs/echomail/tosser.py
"""
Echomail tosser — fan-out router for hub mode.

When ANetBBS acts as a hub, inbound messages (received via BinkP from an
upstream network, or locally composed) must be forwarded to every downstream
BinkP node that is subscribed to that echo area.

Call `toss_message(message_id)` right after a new EchomailMessage is
committed to the DB.  The tosser:
  1. Finds all BinkPNode rows subscribed to the message's area via EchoAreaNode.
  2. Skips nodes that already have a BinkPHoldQueue entry for this message
     (idempotent — safe to call more than once).
  3. Skips any node already listed in the message's SEEN-BY (FTS-0004) —
     they already have a copy, and re-sending is how mail loops start.
  4. Creates BinkPHoldQueue rows for every remaining subscriber.
  5. Returns the count of new queue entries created.

`toss_area_messages(area_id)` bulk-tosses all unqueued messages for an area —
useful after a node subscribes mid-stream and wants a catchup.
"""
import logging
import json
from datetime import datetime

logger = logging.getLogger(__name__)


def toss_message(message_id: int, exclude_peer_address: str = None) -> int:
    """Queue one echomail message for delivery to all subscribed downstream nodes.

    `exclude_peer_address`, when given, is an extra defense-in-depth
    loop-prevention check on top of SEEN-BY: the FTN address of the
    peer session this specific message was JUST received from (passed
    by the caller right after import, not stored on the message row --
    the message's own `from_address` field is the ORIGINAL AUTHOR's
    address, which is usually a completely different node than
    whichever peer relayed us this particular copy, so reusing that
    field here would misfire). If a downstream node's own tosser has a
    bug and fails to add itself to SEEN-BY before relaying a message
    that originated there, this catches it anyway. Real gap found in a
    full echomail-subsystem audit: loop-prevention relied solely on
    SEEN-BY with no independent fallback at all.

    Returns the number of new BinkPHoldQueue entries created.
    """
    from ..models import (
        db, EchomailMessage, BinkPNode, EchoAreaNode, BinkPHoldQueue,
    )

    msg = EchomailMessage.query.get(message_id)
    if msg is None:
        logger.warning('tosser: message %d not found', message_id)
        return 0

    # Collect FTN addresses already in the SEEN-BY for this message.
    # We don't re-forward to nodes that already have a copy.
    #
    # Real bug found in a full echomail-subsystem audit: this loop-
    # prevention check never actually fired. Two compounding reasons:
    #  1. Raw SEEN-BY lines as stored (binkp.py's inbound parser,
    #     `seenby.append(line[8:].strip())`) are NOT tokenized -- a
    #     single JSON list entry can be a whole "SEEN-BY: 234/567
    #     234/568 235/1"-style line with several space-separated
    #     addresses on it, per standard FTN convention. A plain `set()`
    #     over the raw JSON list treats each whole line as ONE opaque
    #     string, so nothing downstream ever matched a single address
    #     against it.
    #  2. Even ignoring (1), entries are always bare `net/node` (no
    #     zone) -- confirmed against binkp.py's own outbound packer,
    #     which writes `our_short = f'{on}/{oo}'` -- while the
    #     candidate node's own `ftn_address` is a full
    #     `zone:net/node[.point]` string. Comparing the two directly
    #     ("1:234/567" vs "234/567") can never match.
    # Net effect: a hub that imports echomail from downstream node A,
    # where A is ALSO subscribed to receive that same area back (a
    # completely normal bidirectional echo relationship), would
    # unconditionally queue the message right back to A -- a bounce/
    # duplicate-delivery bug and a contributor to mail loops. Fixed by
    # tokenizing every stored entry on whitespace and stripping the
    # zone from the comparison, matching how the addresses were
    # actually written on the wire.
    seenby_entries = set()
    if msg.seenby:
        try:
            for entry in json.loads(msg.seenby):
                seenby_entries.update((entry or '').split())
        except Exception as exc:
            logger.warning('tosser: msg %d has malformed seenby JSON, '
                           'loop-prevention check skipped: %s', message_id, exc)

    exclude_bare = ''
    if exclude_peer_address:
        exclude_bare = exclude_peer_address.split('@', 1)[0].strip()
        exclude_bare = exclude_bare.split(':', 1)[-1]

    # Find all active downstream nodes subscribed to this area.
    subs = (
        EchoAreaNode.query
        .join(BinkPNode, EchoAreaNode.node_id == BinkPNode.id)
        .filter(
            EchoAreaNode.echo_area_id == msg.area_id,
            BinkPNode.is_active == True,
        )
        .all()
    )
    if not subs:
        return 0

    # Find which nodes already have a queue entry for this message.
    existing_node_ids = {
        row.node_id for row in
        BinkPHoldQueue.query
        .filter_by(message_id=message_id)
        .with_entities(BinkPHoldQueue.node_id)
        .all()
    }

    created = 0
    for sub in subs:
        node = sub.node
        if node.id in existing_node_ids:
            continue
        # Skip if this node's address appears in SEEN-BY — they already
        # have it. seenby_entries holds bare net/node strings (no zone,
        # no @domain -- see the comment above), so the comparison value
        # must be reduced the same way: strip the zone: prefix as well
        # as any @domain suffix.
        addr_bare = (node.ftn_address or '').split('@', 1)[0].strip()
        addr_bare = addr_bare.split(':', 1)[-1]
        if addr_bare and addr_bare in seenby_entries:
            logger.debug('tosser: skipping %s (in SEEN-BY for msg %d)',
                         node.ftn_address, message_id)
            continue
        if addr_bare and exclude_bare and addr_bare == exclude_bare:
            logger.debug('tosser: skipping %s (received msg %d from this '
                         'same peer -- SEEN-BY fallback)',
                         node.ftn_address, message_id)
            continue
        entry = BinkPHoldQueue(
            node_id=node.id,
            message_id=message_id,
            status='pending',
            queued_at=datetime.utcnow(),
        )
        db.session.add(entry)
        created += 1

    if created:
        db.session.commit()
        logger.debug('tosser: queued msg %d for %d downstream node(s)', message_id, created)

    return created


def toss_area_messages(area_id: int, node_id: int = None, force: bool = False) -> int:
    """Queue all messages in an area that haven't been queued for a node yet.

    If node_id is given, only queues for that specific node (used when a node
    newly subscribes and requests a catchup).  If node_id is None, queues for
    all subscribed nodes that are missing entries.

    force: real live bug found via AreaFix's %RESCAN (which calls this with
    force=True) -- BinkPHoldQueue rows are never deleted, only flipped to
    status='sent' (see mark_sent_for_node()), and the UniqueConstraint on
    (node_id, message_id) means a message can only ever have ONE hold-queue
    row per node, ever. Without `force`, a message that already has a row
    (pending OR sent) is treated as "already queued" and skipped -- correct
    for the normal new-subscription-catchup use of this function, but it
    means %RESCAN's entire purpose (force a resend of already-delivered
    backlog) silently did nothing the second time it was called for the
    same area/node: every message's row was already 'sent' from the first
    rescan, so every subsequent one reported "0 messages" forever after.
    With force=True, an existing row is reset back to 'pending' (not
    duplicate-inserted, which would violate the unique constraint) so
    get_pending_for_node() picks it up again on the next delivery attempt.

    Returns total BinkPHoldQueue entries created or reactivated.
    """
    from ..models import (
        db, EchomailMessage, BinkPNode, EchoAreaNode, BinkPHoldQueue,
    )

    query = EchomailMessage.query.filter_by(area_id=area_id)
    messages = query.all()
    if not messages:
        return 0

    if node_id is not None:
        subs_query = EchoAreaNode.query.filter_by(
            echo_area_id=area_id, node_id=node_id)
    else:
        subs_query = (
            EchoAreaNode.query
            .join(BinkPNode, EchoAreaNode.node_id == BinkPNode.id)
            .filter(
                EchoAreaNode.echo_area_id == area_id,
                BinkPNode.is_active == True,
            )
        )
    subs = subs_query.all()
    if not subs:
        return 0

    # Fetch all existing hold entries for these messages in one query --
    # full row objects (not just id tuples) so `force` can update them
    # in place rather than only checking membership.
    msg_ids = [m.id for m in messages]
    node_ids = [s.node_id for s in subs]
    existing = {}
    for row in (
        BinkPHoldQueue.query
        .filter(
            BinkPHoldQueue.message_id.in_(msg_ids),
            BinkPHoldQueue.node_id.in_(node_ids),
        )
        .all()
    ):
        existing[(row.node_id, row.message_id)] = row

    created = 0
    for sub in subs:
        for msg in messages:
            key = (sub.node_id, msg.id)
            row = existing.get(key)
            if row is None:
                db.session.add(BinkPHoldQueue(
                    node_id=sub.node_id,
                    message_id=msg.id,
                    status='pending',
                    queued_at=datetime.utcnow(),
                ))
                created += 1
            elif force and row.status != 'pending':
                row.status = 'pending'
                row.sent_at = None
                row.queued_at = datetime.utcnow()
                created += 1

    if created:
        db.session.commit()
        logger.info('tosser: catchup queued %d entries for area %d', created, area_id)

    return created


def get_pending_for_node(node_id: int):
    """Return list of EchomailMessage objects pending for a given node id.

    Used by the BinkP listener to build the outbound .pkt when a downstream
    node connects.
    """
    from ..models import BinkPHoldQueue, EchomailMessage

    rows = (
        BinkPHoldQueue.query
        .filter_by(node_id=node_id, status='pending')
        .order_by(BinkPHoldQueue.queued_at)
        .all()
    )
    if not rows:
        return []
    msg_ids = [r.message_id for r in rows]
    msgs = EchomailMessage.query.filter(EchomailMessage.id.in_(msg_ids)).all()
    msg_map = {m.id: m for m in msgs}
    return [msg_map[r.message_id] for r in rows if r.message_id in msg_map]


def mark_sent_for_node(node_id: int, message_ids: list) -> int:
    """Mark a list of messages as sent for a node.  Returns count updated."""
    from ..models import db, BinkPHoldQueue

    now = datetime.utcnow()
    updated = (
        BinkPHoldQueue.query
        .filter(
            BinkPHoldQueue.node_id == node_id,
            BinkPHoldQueue.message_id.in_(message_ids),
            BinkPHoldQueue.status == 'pending',
        )
        .all()
    )
    for row in updated:
        row.status = 'sent'
        row.sent_at = now
    db.session.commit()
    return len(updated)


def get_pending_netmail_for_node(node):
    """Return queued outbound NetmailMessage rows addressed to a specific
    downstream BinkPNode.

    Companion to get_pending_for_node() (echomail), added for a real live
    bug: the BinkP inbound listener flushed the echomail hold queue to a
    connecting downstream node but never queried for queued outbound
    NETMAIL at all -- poller.py's own outbound-dial path (_do_poll) has
    always gathered both in the same batch, but a node that only ever
    calls IN to its hub (the normal relationship -- the hub doesn't
    usually dial a leaf node on a schedule) never triggers that path.
    Confirmed live: a sysop's own reply to a downstream node's test
    netmail, and every AreaFix bot auto-reply queued for that node, sat
    in NetmailMessage.status='queued' indefinitely -- a backlog spanning
    over a week of never-sent AreaFix replies alone.

    Matched by the node's OWN address (both the exact stored form and its
    bare, no-@domain form -- to_address on the netmail row and
    node.ftn_address aren't guaranteed to share the same one of the two
    equally-valid forms, mirroring the @domain/bare ambiguity the BinkP
    listener's own peer-address matching already has to handle), not
    just network_id -- a network can have several downstream nodes, each
    with its own hold queue, and netmail must never cross-deliver to
    whichever one happens to connect first. If the node has no
    ftn_address on file (should not normally happen), falls back to
    network_id-only so mail still gets delivered rather than silently
    stuck again -- matches this codebase's established fail-open stance
    for AKA/address resolution elsewhere in the BinkP listener.
    """
    from ..models import NetmailMessage

    if not node.network_id:
        return []
    node_addrs = []
    if node.ftn_address:
        node_addrs.append(node.ftn_address)
        bare = node.ftn_address.split('@', 1)[0]
        if bare and bare not in node_addrs:
            node_addrs.append(bare)
    query = NetmailMessage.query.filter(
        NetmailMessage.network_id == node.network_id,
        NetmailMessage.direction == 'outbound',
        NetmailMessage.status == 'queued')
    if node_addrs:
        query = query.filter(NetmailMessage.to_address.in_(node_addrs))
    return query.all()


def get_pending_netmail_for_network(network_id: int):
    """Return queued outbound NetmailMessage rows for a whole network.

    Used by the BinkP inbound listener's network-peer branch (a
    connecting peer matched against EchomailNetwork rather than a
    specific BinkPNode) -- there's no individual node address to
    disambiguate against here, so this matches poller.py's own
    network_id-only netmail gather exactly. Same underlying gap as
    get_pending_netmail_for_node() above -- see its docstring.
    """
    from ..models import NetmailMessage

    return NetmailMessage.query.filter(
        NetmailMessage.network_id == network_id,
        NetmailMessage.direction == 'outbound',
        NetmailMessage.status == 'queued').all()


def mark_netmail_sent(netmail_rows: list) -> int:
    """Mark a list of NetmailMessage rows as sent. Returns count updated.

    Companion to mark_sent_for_node() (echomail/BinkPHoldQueue) -- mirrors
    the sent_at/status/is_sent update poller.py's _do_poll() already does
    for its own outbound_nm batch after a successful send.
    """
    from ..models import db

    if not netmail_rows:
        return 0
    now = datetime.utcnow()
    for nm in netmail_rows:
        nm.sent_at = now
        nm.status = 'sent'
        nm.is_sent = True
    db.session.commit()
    return len(netmail_rows)
