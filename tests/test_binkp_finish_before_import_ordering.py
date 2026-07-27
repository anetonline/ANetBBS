"""Regression test for the BinkP inbound-listener reordering: the
session must complete its M_EOB handshake and close the socket BEFORE
importing/parsing whatever files were received, not after.

Real production bug report: a peer hub with ~100 downstream links
traced a persistent mail-loop (the same bundles re-sent every
connection, compounding with every new bundle created in between)
straight back to the OLD ordering in
anetbbs/echomail/binkp_server.py's _handle_connection() -- a large
historical catch-up took long enough to parse+import+toss that the
peer's own connection gave up waiting mid-import and the session ended
without ever completing the M_EOB handshake, even though every file
had already been individually M_GOT-acknowledged. Because the session
itself never closed cleanly, the peer's own bookkeeping never marked
those bundles delivered.

Fix: finish the handshake (_finish_session, which sends M_EOB and
closes the socket) immediately after receiving+GOT-acknowledging every
file and sending our own outbound mail, THEN import what we received.
This test proves that ordering holds and stays proven if anyone
reorders it back by accident.

Reuses the same real-frame session-simulator pattern established in
test_binkp_multi_hub_identity.py (a _ScriptedReader/_FakeWriter pair
feeding actual BinkP frames through _recv_frame(), not a mocked
transport) -- see that file's own docstring for why a real frame-level
simulator matters here (the two older BinkP server test files never
get past the M_NUL/M_ADR preamble at all).
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


class _ScriptedReader:
    """Feeds a scripted sequence of real BinkP frames, then raises
    IncompleteReadError forever once exhausted. Also answers a bare
    .read() (used by _finish_session's post-EOB drain loop) with empty
    bytes once the script is exhausted, so that loop exits promptly
    instead of hitting the AttributeError-swallow path the simpler
    harness in test_binkp_multi_hub_identity.py relies on -- this test
    needs _finish_session to run to completion for real, not bail out
    early, since the whole point is proving IT runs before import."""
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
    """A minimal-but-structurally-valid FTS-0001 type-2 packet header
    (58 bytes) -- enough for _is_fts_packet()'s magic-byte check
    (bytes 18-19 == 02 00) to recognize it, so the file lands in
    _extract_packets()'s "real packet" branch rather than the
    non-packet/TIC-scan fallback path. Message parsing beyond the
    header isn't exercised here -- _import_pkt_payload is patched out
    entirely for this test, only ITS CALL ORDER relative to
    _finish_session matters."""
    import struct as _s
    hdr = bytearray(58)
    _s.pack_into('<H', hdr, 18, 2)   # version = 2 (type-2 packet)
    return bytes(hdr)


class FinishBeforeImportOrderingTests(unittest.TestCase):
    def test_finish_session_runs_before_import_pkt_payload(self):
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.models import EchomailNetwork, EchomailMessage, HatchQueue, db

        order = []

        real_finish_session = mod._finish_session

        async def _tracking_finish_session(*args, **kwargs):
            order.append('finish_session')
            return await real_finish_session(*args, **kwargs)

        def _tracking_import_pkt_payload(pkt_bytes, network_id, filename, peer_address=None):
            order.append('import_pkt_payload')
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
        HatchQueue.query = _FakeQuery([])
        try:
            with patch.object(EchomailNetwork, 'hub_address', _FakeColumn('hub_address')), \
                 patch.object(EchomailMessage, 'network_id', _FakeColumn('network_id')), \
                 patch.object(EchomailMessage, 'direction', _FakeColumn('direction')), \
                 patch.object(EchomailMessage, 'sent_at', _FakeColumn('sent_at')), \
                 patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', _NoOpSession()), \
                 patch('anetbbs.echomail.tosser.get_pending_netmail_for_network', lambda network_id: []), \
                 patch.object(mod, '_finish_session', _tracking_finish_session), \
                 patch.object(mod, '_import_pkt_payload', _tracking_import_pkt_payload):
                writer = _FakeWriter()
                reader = _ScriptedReader(frames)
                asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del EchomailMessage.query
            del HatchQueue.query

        self.assertEqual(order, ['finish_session', 'import_pkt_payload'],
                         'BinkP session must finish/close before importing '
                         'received files -- see this file\'s module docstring.')
        self.assertTrue(writer.closed, 'session should have closed the socket')


if __name__ == '__main__':
    unittest.main()
