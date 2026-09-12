"""Regression test for a real gap found in this round's security/
performance audit: anetbbs/echomail/binkp_server.py's inbound WaZOO
FREQ handling calls _freq_rate_limited() -- documented as a "per-IP"
sliding-window limiter mirroring the SYSTAT UDP responder's own
addr[0]-keyed one (msp/systat.py) -- but the call site actually passed
`remote_addr`, the BinkP session's self-CLAIMED FTN address from the
peer's own M_ADR frame, not the real socket source address.

FREQ processing is reachable from a fully anonymous, zero-password
"crashmail" session (see binkp_server.py's own module docstring and
freq.py's docstring for why unrecognized peers are accepted at all) --
nothing validates M_ADR's contents for such a peer, so an attacker can
claim a brand-new address on every single TCP connection at zero cost.
That made the limiter's "per-IP" dimension trivially bypassable,
leaving only the shared global cap (60/60s) as a backstop -- which one
such attacker, rotating claimed addresses, could exhaust alone.

Fixed by keying on the connection's real socket peer address
(writer.get_extra_info('peername')[0], already captured as `peer` in
_handle_connection) instead of `remote_addr`.

Reuses the real-frame session-simulator pattern from
test_binkp_server_crash_still_logged.py / test_binkp_eob_sent_before_receive.py.
"""
import asyncio
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _frame(is_command, payload):
    word = (0x8000 if is_command else 0) | (len(payload) & 0x7FFF)
    return struct.pack('>H', word) + payload


def _cmd_payload(cmd, text=''):
    return bytes([cmd]) + text.encode('latin-1', errors='replace')


class _FakeWriter:
    def __init__(self, peer_ip='198.51.100.42'):
        self.sent = []
        self.closed = False
        self._peer_ip = peer_ip

    def get_extra_info(self, key):
        return (self._peer_ip, 24554) if key == 'peername' else None

    def write(self, data):
        self.sent.append(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class _ScriptedReader:
    """Feeds a scripted sequence of real BinkP frames, then raises
    IncompleteReadError forever once exhausted."""
    def __init__(self, frames):
        self._buf = b''.join(_frame(is_cmd, payload) for is_cmd, payload in frames)
        self._pos = 0

    async def readexactly(self, n):
        if self._pos + n > len(self._buf):
            raise asyncio.IncompleteReadError(
                partial=self._buf[self._pos:], expected=n)
        data = self._buf[self._pos:self._pos + n]
        self._pos += n
        return data

    async def read(self, n):
        return b''


class _FakeQuery:
    """Always-empty query -- enough to drive the unrecognized-peer
    ("anonymous crashmail") branch, which is all this test needs: no
    EchomailNetwork/BinkPNode row ever matches the connecting peer."""
    def __init__(self, rows=()):
        self._rows = list(rows)

    def filter(self, *criteria):
        return self

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _NoOpSession:
    def add(self, *a, **k):
        pass

    def commit(self, *a, **k):
        pass

    def rollback(self, *a, **k):
        pass

    def flush(self, *a, **k):
        pass


class BinkpFreqRateLimitSourceIpTests(unittest.TestCase):
    def test_freq_rate_limiter_is_keyed_on_real_socket_ip_not_claimed_address(self):
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.models import EchomailNetwork, BinkPNode, db

        claimed_address = 'NOT-A-REAL-9999:9999/9999'
        real_peer_ip = '198.51.100.42'
        req_filename = '000C0002.req'
        req_payload = b'a.zip'
        mtime = 1700000000

        frames = [
            (True, _cmd_payload(mod.CMD_ADR, claimed_address)),
            (True, _cmd_payload(mod.CMD_PWD, '')),
            (True, _cmd_payload(mod.CMD_FILE,
                                f'{req_filename} {len(req_payload)} {mtime} 0')),
            (False, req_payload),
            (True, _cmd_payload(mod.CMD_EOB)),
        ]

        calls = []

        def _spy_freq_rate_limited(source_ip):
            calls.append(source_ip)
            # Return True (already rate-limited) so process_inbound_req()
            # never runs -- this test only cares what key the limiter
            # was called with, not FileArea matching.
            return True

        EchomailNetwork.query = _FakeQuery()
        BinkPNode.query = _FakeQuery()
        try:
            with patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', _NoOpSession()), \
                 patch.object(mod, '_freq_rate_limited', _spy_freq_rate_limited):
                writer = _FakeWriter(peer_ip=real_peer_ip)
                reader = _ScriptedReader(frames)
                asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del BinkPNode.query

        self.assertEqual(
            calls, [real_peer_ip],
            'the FREQ rate limiter must be keyed on the real socket source '
            "IP, not the peer's own self-claimed (unauthenticated, freely "
            'chosen) M_ADR address')
        self.assertNotIn(claimed_address, calls)


if __name__ == '__main__':
    unittest.main()
