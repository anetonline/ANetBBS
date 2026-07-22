# anetbbs/echomail/routing.py
"""
Multi-zone routing helpers for FTN netmail and echomail.

A FidoNet system can be a member of multiple networks (e.g. fidonet zone 1,
fsxnet zone 21, retronet zone 80). Each network is its own EchomailNetwork
row with its own `our_address` (zone:net/node) — and outbound mail must be
sent through the network whose zone matches the destination.

This module exposes:
  - parse_address(s)       -> (zone, net, node, point) or None
  - find_network_for_address(addr)  -> EchomailNetwork that handles `addr`'s zone
  - find_aka_for_network(user, network)  -> UserAka the user should send from
  - format_address(zone, net, node, point=0)  -> canonical 'z:n/no[.p]' string
  - resolve_netmail_recipient(to_name, to_address, network) -> local User or None
  - self_hub_binkp_network(hub_identity_id)  -> the one binkp EchomailNetwork
    under that identity where we ARE the hub, or None if zero/ambiguous
"""
import re
from sqlalchemy import func
from ..models import EchomailNetwork, User, UserAka


_ADDR_RE = re.compile(r'^(\d+):(\d+)/(\d+)(?:\.(\d+))?$')


def parse_address(s):
    """Parse an FTN address string. Returns (zone, net, node, point) or None.

    Accepts the standard `zone:net/node[.point]` form. Whitespace is stripped.
    """
    if not s:
        return None
    m = _ADDR_RE.match(s.strip())
    if not m:
        return None
    z, n, no, p = m.groups()
    return (int(z), int(n), int(no), int(p) if p else 0)


def format_address(zone, net, node, point=0):
    """Render a canonical FTN address."""
    if point:
        return f'{zone}:{net}/{node}.{point}'
    return f'{zone}:{net}/{node}'


def find_network_for_address(address):
    """Pick the EchomailNetwork whose `our_address` zone matches `address`.

    If the destination is `2:5020/1042`, we want the network whose
    `our_address` is also in zone 2 (e.g. `2:230/1`). Falls back to the
    only-active-network if exactly one matches; otherwise None.

    Returns an EchomailNetwork row or None.
    """
    parsed = parse_address(address)
    if parsed is None:
        return None
    target_zone = parsed[0]

    candidates = (EchomailNetwork.query
                  .filter_by(is_active=True)
                  .filter(EchomailNetwork.our_address.isnot(None))
                  .all())
    matches = []
    for net in candidates:
        ours = parse_address(net.our_address)
        if ours and ours[0] == target_zone:
            matches.append(net)

    if not matches:
        # Last resort — if only one network is active, use it. The poller will
        # log a warning and the upstream may bounce, but at least we make a
        # best-effort attempt instead of dropping the message silently.
        return candidates[0] if len(candidates) == 1 else None

    # Prefer the network whose `our_address` matches the destination's net too
    target_net = parsed[1]
    for net in matches:
        ours = parse_address(net.our_address)
        if ours and ours[1] == target_net:
            return net
    return matches[0]


def self_hub_binkp_network(hub_identity_id):
    """The binkp EchomailNetwork under `hub_identity_id` that a downstream
    BinkPNode polling in actually belongs to.

    A single HubIdentity can own several binkp EchomailNetwork rows: one
    where we're the hub (downstream nodes poll us) and any number where
    we're a leaf member of someone else's network (we poll them). When
    there's exactly one binkp network under the identity, there's no
    ambiguity -- return it (matches every single-network install, where
    hub_address is often left unset since it's irrelevant to a leaf-only
    setup). When there's more than one, narrow to the one where we ARE
    the hub (our_address == hub_address) -- the only case a downstream
    node polling in makes sense for; filtering on hub_identity_id alone
    is ambiguous once both kinds of network exist under the same identity
    (see BinkPNode.network_id).

    Returns the matching EchomailNetwork, or None if it can't be resolved
    unambiguously (caller should fall back to manual sysop resolution,
    not guess).
    """
    if hub_identity_id is None:
        return None
    candidates = (EchomailNetwork.query
                  .filter_by(hub_identity_id=hub_identity_id, network_type='binkp')
                  .filter(EchomailNetwork.our_address.isnot(None))
                  .all())
    if len(candidates) == 1:
        return candidates[0]
    matches = [n for n in candidates
              if (n.our_address or '').strip()
              and (n.our_address or '').strip() == (n.hub_address or '').strip()]
    return matches[0] if len(matches) == 1 else None


def find_aka_for_network(user, network):
    """Return the UserAka this user should use to send via `network`.

    Picks the AKA whose zone matches the network's `our_address`. Falls back
    to the user's primary AKA if no zone match. Returns None if user has no
    AKAs configured at all (caller should fall back to network.our_address).
    """
    if user is None or network is None:
        return None
    akas = list(user.akas) if hasattr(user, 'akas') else []
    if not akas:
        return None

    net_parsed = parse_address(network.our_address)
    if net_parsed:
        target_zone = net_parsed[0]
        for a in akas:
            p = parse_address(a.address)
            if p and p[0] == target_zone:
                return a

    # Fall back to primary
    for a in akas:
        if a.is_primary:
            return a
    return akas[0]


def resolve_user_by_name_or_address(to_name, to_address):
    """Match a to_name/to_address pair to a local User account by
    UserAka.address exact match, then username, then display_name
    (case-insensitive). Returns None if nothing matches. No network-level
    default-recipient fallback here -- that's netmail-specific (a public
    echomail/QWK message with an unmatched TO name just isn't for anyone
    local; there's no "catch-all sysop" convention for those the way
    there is for netmail).

    Shared by resolve_netmail_recipient (below, which adds the netmail-
    only default-recipient fallback) and
    anetbbs.echomail.notify_reply.maybe_notify_recipient (echomail/QWK
    reply-to-a-real-user notifications).
    """
    to_name = (to_name or '').strip()
    to_address = (to_address or '').strip()

    user = None
    if to_address:
        aka = UserAka.query.filter_by(address=to_address).first()
        if aka:
            user = User.query.get(aka.user_id)
    if user is None and to_name:
        user = (User.query
                .filter(func.lower(User.username) == to_name.lower())
                .first())
        if user is None:
            user = (User.query
                    .filter(func.lower(User.display_name) == to_name.lower())
                    .first())
    return user


def resolve_netmail_recipient(to_name, to_address, network):
    """Match an inbound netmail's recipient to a local User account.

    Match order: resolve_user_by_name_or_address(), then the network's
    configured DefaultRecipient as a last-resort catch-all. Returns None
    if nothing matches -- the netmail still gets stored, just unlinked to
    any user (and so never gets a Notification).

    Shared by both inbound netmail import paths (anetbbs/echomail/poller.py's
    _import_netmail, used by the QWK/poll-response path, and
    anetbbs/echomail/binkp_server.py's _import_pkt_payload, used by the
    real-time BinkP listener) -- they used to duplicate this logic, and the
    BinkP listener path didn't do it at all (netmail received over a live
    BinkP session was never linked to a User, so its recipient never got
    notified of new mail).
    """
    user = resolve_user_by_name_or_address(to_name, to_address)
    if user is None:
        default_name = (getattr(network, 'default_recipient', '') or '').strip()
        if default_name:
            user = (User.query
                    .filter(func.lower(User.username) == default_name.lower())
                    .first())
    return user
