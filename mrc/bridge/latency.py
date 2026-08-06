"""Real round-trip latency measurement for the MRC upstream connection.

Deferred request from 2026-08-04 ("this can wait until the games are
finalized and live"), picked up now that they are. Ported from the
vendored reference Mystic client (mrc/mystic_client/vendor/mrc_client.py,
send_server()/deliver_mrc()) rather than invented from scratch: that
client -- run against real MRC hubs in production for years -- measures
latency by keeping a registry of {stripped outbound packet text: send
time} and, on every inbound line, checking whether it matches a pending
entry (the hub echoes CLIENT-addressed control packets back verbatim).
A match gives a real round-trip time; the whole registry is cleared on
any match, same as the reference -- this is "time since our most
recent unacknowledged send got echoed", a coarse but genuinely real
measurement, not per-packet RTT.

Shared by both connection backends (MRCConnection and
MysticMultiplexerConnection) rather than duplicated, since the
mechanism is identical regardless of transport -- only how packets get
sent/received differs between them.
"""
import time
from typing import Dict, Optional


class LatencyTracker:
    def __init__(self, max_entries: int = 200):
        self._registry: Dict[str, float] = {}
        self._max_entries = max_entries
        self.latency_ms: Optional[float] = None

    def note_sent(self, packet: str):
        stripped = (packet or '').strip()
        if not stripped:
            return
        if len(self._registry) >= self._max_entries:
            # Unbounded growth risk the reference client doesn't guard
            # against (it never evicts) -- a hub that doesn't echo back
            # regular chat traffic at all would otherwise leak one
            # entry per message sent for the life of the process. Evict
            # oldest-first, same shape as MRCConnection's own existing
            # _send_queue_max/_queue_packet eviction.
            oldest_key = next(iter(self._registry))
            self._registry.pop(oldest_key, None)
        self._registry[stripped] = time.time()

    def check_received(self, raw_line: str) -> bool:
        """Returns True (and updates self.latency_ms) if `raw_line`
        matches a pending sent packet."""
        stripped = (raw_line or '').strip()
        if not stripped or stripped not in self._registry:
            return False
        sent_at = self._registry.pop(stripped)
        self._registry.clear()
        self.latency_ms = max(0.0, (time.time() - sent_at) * 1000.0)
        return True
