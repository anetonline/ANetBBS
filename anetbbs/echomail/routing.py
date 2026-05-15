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
"""
import re
from ..models import EchomailNetwork


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
