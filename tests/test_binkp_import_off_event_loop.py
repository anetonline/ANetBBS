"""Regression test for a real architectural bug found while investigating
a second peer sysop's report of multi-minute stalls on BRAND NEW BinkP
connections -- including a tiny (2236-byte) push that got no M_GOT
before the peer gave up and disconnected, logging it as failed and
re-queuing the same bundle for retry.

anetbbs/echomail/binkp_server.py runs ONE asyncio event loop
(asyncio.start_server in _serve()) shared by EVERY inbound connection.
The prior fix (see test_binkp_finish_before_import_ordering.py) moved
import to run after THIS session's own socket close, which protects
THAT session's own protocol timing -- but the import itself (DB writes,
ZIP extraction, regex parsing, potentially thousands of messages during
a large catch-up) was still running as SYNCHRONOUS code directly on the
shared event loop, monopolizing it and starving every OTHER concurrent
connection -- including one that hadn't even finished its handshake yet.

Fix: _handle_connection() now runs the whole import+poll-log step via
asyncio.to_thread(), so a slow import can't block the event loop from
servicing other sessions.

This test proves the non-blocking property directly: while one
connection's import is artificially slowed (a real blocking
time.sleep(), not a mock), a concurrently-scheduled lightweight
watchdog coroutine must still get to run promptly -- if the fix
regressed back to inline synchronous execution, the watchdog would be
frozen out until the "import" finished, and the timing assertion below
would fail.
"""
import asyncio
import struct
import sys
import time
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
    def __init__(self):
        self.sent = []
        self.closed = False

    def get_extra_info(self, key):
        return ('127.0.0.1', 12345) if key == 'peername' else None

    def write(self, data):
        self.sent.append(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class _ScriptedReader:
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


class _FakeColumn:
    def __init__(self, name):
        self.name = name

    def in_(self, values):
        return ('in', self.name, set(values))

    def isnot(self, value):
        return ('isnot_none', self.name, None)


class _FakeQuery:
    def __init__(self, rows, predicates=None):
        self._rows = list(rows)
        self._predicates = list(predicates or [])

    def filter(self, *criteria):
        return _FakeQuery(self._rows, self._predicates + list(criteria))

    def filter_by(self, **kwargs):
        extra = [('eq', k, v) for k, v in kwargs.items()]
        return _FakeQuery(self._rows, self._predicates + extra)

    def get(self, _id):
        return self._rows[0] if self._rows else None

    def _matches(self, row):
        for kind, name, value in self._predicates:
            attr = getattr(row, name, None)
            if kind == 'in' and attr not in value:
                return False
            if kind == 'isnot_none' and attr is None:
                return False
            if kind == 'eq' and attr != value:
                return False
        return True

    def first(self):
        for row in self._rows:
            if self._matches(row):
                return row
        return None

    def all(self):
        return [row for row in self._rows if self._matches(row)]


class _FakeEchomailNetwork:
    def __init__(self, id, hub_address=None, network_type='binkp',
                binkp_password=None, our_address=None,
                hub_identity_id=None, name='Net'):
        self.id = id
        self.hub_address = hub_address
        self.network_type = network_type
        self.binkp_password = binkp_password
        self.our_address = our_address
        self.hub_identity_id = hub_identity_id
        self.name = name


class _NoOpSession:
    def add(self, *a, **k):
        pass

    def commit(self, *a, **k):
        pass

    def rollback(self, *a, **k):
        pass

    def flush(self, *a, **k):
        pass


def _minimal_fts_packet():
    hdr = bytearray(58)
    struct.pack_into('<H', hdr, 18, 2)
    return bytes(hdr)


class ImportOffEventLoopTests(unittest.TestCase):
    def test_slow_import_does_not_block_a_concurrent_watchdog(self):
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.models import EchomailNetwork, EchomailMessage, db

        SLOW_IMPORT_SECONDS = 0.4

        def _slow_import_pkt_payload(pkt_bytes, network_id, filename, peer_address=None):
            # A REAL blocking sleep -- if this ever runs inline on the
            # event loop again (regression), it freezes everything
            # sharing that loop for its whole duration.
            time.sleep(SLOW_IMPORT_SECONDS)
            return 0

        network = _FakeEchomailNetwork(
            id=1, hub_address='1:200/100', network_type='binkp',
            binkp_password='secret', our_address='1:1/1')

        pkt_bytes = _minimal_fts_packet()
        frames = [
            (True,  _cmd_payload(mod.CMD_ADR, '1:200/100')),
            (True,  _cmd_payload(mod.CMD_PWD, 'secret')),
            (True,  _cmd_payload(mod.CMD_FILE, f'test.pkt {len(pkt_bytes)} 0 0')),
            (False, pkt_bytes),
            (True,  _cmd_payload(mod.CMD_EOB)),
        ]

        EchomailNetwork.query = _FakeQuery([network])
        EchomailMessage.query = _FakeQuery([])

        watchdog_ticks = []
        # Shared baseline captured BEFORE either coroutine starts, so a
        # stalled event loop shows up as a late FIRST tick -- if the
        # watchdog's own clock started only once it first got scheduled
        # (a bug an earlier version of this test had), a full-loop
        # freeze occurring before the watchdog's very first turn would
        # be invisible: the watchdog would simply start measuring its
        # own clean run AFTER the stall already happened.
        test_start = time.monotonic()

        async def _watchdog():
            # Fires every 20ms, timestamped against the shared
            # test_start baseline. If the event loop is starved by the
            # "slow" import running inline, ticks stall (including,
            # critically, the very first one) until the import
            # finishes; if the import is correctly offloaded, ticks
            # keep arriving on schedule from the start.
            while time.monotonic() - test_start < SLOW_IMPORT_SECONDS * 1.5:
                watchdog_ticks.append(time.monotonic() - test_start)
                await asyncio.sleep(0.02)

        async def _run_both():
            writer = _FakeWriter()
            reader = _ScriptedReader(frames)
            await asyncio.gather(
                mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'),
                _watchdog(),
            )

        try:
            with patch.object(EchomailNetwork, 'hub_address', _FakeColumn('hub_address')), \
                 patch.object(EchomailMessage, 'network_id', _FakeColumn('network_id')), \
                 patch.object(EchomailMessage, 'direction', _FakeColumn('direction')), \
                 patch.object(EchomailMessage, 'sent_at', _FakeColumn('sent_at')), \
                 patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', _NoOpSession()), \
                 patch.object(mod, '_import_pkt_payload', _slow_import_pkt_payload):
                asyncio.run(_run_both())
        finally:
            del EchomailNetwork.query
            del EchomailMessage.query

        self.assertTrue(watchdog_ticks, 'watchdog never got to run at all')

        # The decisive check: how late was the FIRST tick? A healthy,
        # non-blocked event loop lets the watchdog get its first turn
        # almost immediately (sub-100ms, generous margin for CI jitter).
        # If _handle_connection's import ever runs inline again instead
        # of via asyncio.to_thread(), the event loop can't schedule
        # ANYTHING else -- including the watchdog's very first tick --
        # until that ~0.4s blocking call returns, so this would jump to
        # ~SLOW_IMPORT_SECONDS.
        self.assertLess(
            watchdog_ticks[0], SLOW_IMPORT_SECONDS * 0.5,
            f'watchdog\'s first tick did not fire until '
            f'{watchdog_ticks[0]:.3f}s in -- the "slow" import is '
            f'blocking the shared event loop instead of running in a '
            f'background thread (see _handle_connection\'s '
            f'asyncio.to_thread(_import_and_log) call)')

        # Also confirm ticks kept arriving steadily throughout (not
        # just a fast start followed by a mid-run freeze).
        self.assertGreater(len(watchdog_ticks), 15)

        # No gap between consecutive ticks should be anywhere near the
        # full sleep duration -- that would mean the loop froze for the
        # import's entire span rather than just yielding briefly.
        gaps = [b - a for a, b in zip(watchdog_ticks, watchdog_ticks[1:])]
        if gaps:
            self.assertLess(
                max(gaps), SLOW_IMPORT_SECONDS * 0.5,
                'a watchdog tick gap approached the full "slow import" '
                'duration -- the event loop stalled')


if __name__ == '__main__':
    unittest.main()
