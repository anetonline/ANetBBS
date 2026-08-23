"""Regression test: _handle_connection()'s inbound handshake loop
(anetbbs/echomail/binkp_server.py) only ever bounded the gap BETWEEN
frames (a 30s asyncio.wait_for timeout), never the TOTAL time spent
waiting for the peer to complete M_ADR/M_PWD. A peer -- malicious or
just slow-loris style -- that sends one harmless M_NUL frame every
~29 seconds forever keeps resetting that per-frame timer indefinitely,
holding the connection open without ever authenticating. Found in a
security/performance audit.

Fixed with an overall wall-clock deadline across the whole loop
(120s), matching the same deadline-based pattern already used
elsewhere in this file (_send_pkt_file's own M_GOT wait).

The fake reader below feeds an unlimited stream of M_NUL frames (never
M_ADR/M_PWD) and asyncio.get_event_loop().time() is patched to jump
forward each call, so the test proves the loop actually bails once the
overall deadline is exceeded -- without a real 120-second wait.
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


class _EndlessNulReader:
    """Feeds an unbounded stream of M_NUL frames, one per readexactly()
    pair (header then body) -- never M_ADR/M_PWD -- simulating a peer
    that keeps the handshake alive indefinitely with harmless traffic."""
    def __init__(self, cmd_nul):
        self._pending = b''
        self._cmd_nul = cmd_nul

    def _refill(self):
        self._pending += _frame(True, _cmd_payload(self._cmd_nul, 'keepalive'))

    async def readexactly(self, n):
        while len(self._pending) < n:
            self._refill()
        data = self._pending[:n]
        self._pending = self._pending[n:]
        return data

    async def read(self, n):
        return b''


class _RealAdrPwdReader:
    """A well-behaved peer: sends M_ADR + M_PWD immediately."""
    def __init__(self, cmd_adr, cmd_pwd):
        self._buf = (_frame(True, _cmd_payload(cmd_adr, '1:200/100')) +
                    _frame(True, _cmd_payload(cmd_pwd, 'secret')))
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


class BinkpHandshakeOverallDeadlineTests(unittest.TestCase):
    def test_endless_nul_frames_are_cut_off_by_the_overall_deadline(self):
        from anetbbs.echomail import binkp_server as mod

        # Simulated clock: starts at 1000 (deadline = 1120), then jumps
        # forward 50s on every subsequent .time() call -- past the 120s
        # overall budget within 3 iterations, without any real waiting
        # (the fake reader always returns immediately, so
        # asyncio.wait_for's timeout value never actually matters here).
        times = iter([1000.0, 1050.0, 1100.0, 1150.0, 1200.0, 1250.0])

        def _fake_time():
            try:
                return next(times)
            except StopIteration:
                return 999999.0

        fake_loop = type('FakeLoop', (), {'time': staticmethod(_fake_time)})()

        writer = _FakeWriter()
        reader = _EndlessNulReader(mod.CMD_NUL)
        with patch.object(mod.asyncio, 'get_event_loop', return_value=fake_loop):
            asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))

        # No exception -- the handler must return cleanly once the
        # overall deadline is exceeded, exactly like the existing
        # per-frame timeout path already does.

    def test_a_prompt_real_handshake_is_unaffected(self):
        """Baseline / guard against a too-aggressive fix: a peer that
        sends M_ADR + M_PWD right away must not be cut off just because
        the overall-deadline bookkeeping now runs on every iteration."""
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.models import EchomailNetwork, EchomailMessage, EchomailPollLog, HatchQueue, db

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

            def order_by(self, *args, **kwargs):
                return self

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

        class _RecordingSession:
            def __init__(self):
                self.added = []
                self._next_id = 1

            def add(self, obj, *a, **k):
                if getattr(obj, 'id', None) is None:
                    obj.id = self._next_id
                    self._next_id += 1
                self.added.append(obj)

            def commit(self, *a, **k):
                pass

            def rollback(self, *a, **k):
                pass

            def flush(self, *a, **k):
                pass

        class _LiveRowQuery:
            def __init__(self, rows_ref, model_cls):
                self._rows_ref = rows_ref
                self._model_cls = model_cls

            def get(self, _id):
                for row in self._rows_ref:
                    if isinstance(row, self._model_cls) and getattr(row, 'id', None) == _id:
                        return row
                return None

        network = _FakeEchomailNetwork(
            id=1, hub_address='1:200/100', network_type='binkp',
            binkp_password='secret', our_address='1:1/1')

        session = _RecordingSession()
        EchomailNetwork.query = _FakeQuery([network])
        EchomailMessage.query = _FakeQuery([])
        EchomailPollLog.query = _LiveRowQuery(session.added, EchomailPollLog)
        HatchQueue.query = _FakeQuery([])
        try:
            with patch.object(EchomailNetwork, 'hub_address', _FakeColumn('hub_address')), \
                 patch.object(EchomailMessage, 'network_id', _FakeColumn('network_id')), \
                 patch.object(EchomailMessage, 'direction', _FakeColumn('direction')), \
                 patch.object(EchomailMessage, 'sent_at', _FakeColumn('sent_at')), \
                 patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', session), \
                 patch('anetbbs.echomail.tosser.get_pending_netmail_for_network', lambda network_id, include_hold=False: []):
                writer = _FakeWriter()
                reader = _RealAdrPwdReader(mod.CMD_ADR, mod.CMD_PWD)
                asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del EchomailMessage.query
            del EchomailPollLog.query
            del HatchQueue.query

        poll_logs = [obj for obj in session.added if isinstance(obj, EchomailPollLog)]
        self.assertEqual(len(poll_logs), 1)
        self.assertEqual(poll_logs[0].status, 'success')


if __name__ == '__main__':
    unittest.main()
