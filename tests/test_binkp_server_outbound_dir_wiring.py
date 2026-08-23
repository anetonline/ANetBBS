"""Wiring tests: binkp_server.py's inbound-listener session
(_handle_connection) must flush the new per-peer outbound spool
directory (see anetbbs/echomail/binkp.py's resolve_outbound_dir
docstring) on BOTH branches -- a downstream node dialing IN to us, and
our own upstream hub dialing IN to us -- exactly like it already does
for HatchQueue (_send_hatch_items). Without this, a file dropped for a
peer that only ever calls IN (never polled OUT to) would sit in the
spool forever with no delivery path at all.

Reuses the real-frame session-simulator harness established in
test_binkp_upstream_hatch_out_inbound.py, with resolve_outbound_dir
and _send_outbound_dir_items patched out so the test doesn't depend on
the real Flask app's DATA_DIR/filesystem state.
"""
import asyncio
import os
import struct
import sys
import tempfile
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

    def __eq__(self, value):
        return ('eq', self.name, value)


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

    def get(self, id_):
        for row in self._rows:
            if getattr(row, 'id', None) == id_:
                return row
        return None


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


class _FakeBinkPNode:
    def __init__(self, id, ftn_address, password, name='Node',
                is_active=True, hub_identity=None, network_id=None):
        self.id = id
        self.ftn_address = ftn_address
        self.password = password
        self.name = name
        self.is_active = is_active
        self.hub_identity = hub_identity
        self.network_id = network_id
        self.last_seen_at = None


class _RecordingSession:
    def __init__(self):
        self.added = []

    def add(self, obj, *a, **k):
        self.added.append(obj)

    def commit(self, *a, **k):
        pass

    def rollback(self, *a, **k):
        pass

    def flush(self, *a, **k):
        pass


class OutboundDirWiringTests(unittest.TestCase):
    def _run(self, networks, nodes, remote_addr, remote_pwd, outbound_dir):
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.echomail import tosser as tosser_mod
        from anetbbs.models import (EchomailNetwork, BinkPNode, EchomailMessage,
                                    HatchQueue, db)

        captured = {'dir_calls': []}

        async def _fake_send_outbound_dir_items(reader, writer, outbound_dir_arg,
                                                 peer, state, files, transcript=None):
            captured['dir_calls'].append(outbound_dir_arg)
            return 0, []

        frames = [
            (True, _cmd_payload(mod.CMD_ADR, remote_addr)),
            (True, _cmd_payload(mod.CMD_PWD, remote_pwd)),
            (True, _cmd_payload(mod.CMD_EOB)),
        ]

        recording_session = _RecordingSession()
        EchomailNetwork.query = _FakeQuery(networks)
        BinkPNode.query = _FakeQuery(nodes)
        EchomailMessage.query = _FakeQuery([])
        HatchQueue.query = _FakeQuery([])
        try:
            with patch.object(EchomailNetwork, 'hub_address', _FakeColumn('hub_address')), \
                 patch.object(EchomailNetwork, 'our_address', _FakeColumn('our_address')), \
                 patch.object(EchomailNetwork, 'hub_identity_id', _FakeColumn('hub_identity_id')), \
                 patch.object(EchomailNetwork, 'network_type', _FakeColumn('network_type')), \
                 patch.object(BinkPNode, 'ftn_address', _FakeColumn('ftn_address')), \
                 patch.object(EchomailMessage, 'network_id', _FakeColumn('network_id')), \
                 patch.object(EchomailMessage, 'direction', _FakeColumn('direction')), \
                 patch.object(EchomailMessage, 'sent_at', _FakeColumn('sent_at')), \
                 patch.object(HatchQueue, 'peer_address', _FakeColumn('peer_address')), \
                 patch.object(HatchQueue, 'status', _FakeColumn('status')), \
                 patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', recording_session), \
                 patch.object(tosser_mod, 'get_pending_for_node', lambda node_id: []), \
                 patch.object(tosser_mod, 'get_pending_netmail_for_node',
                             lambda node, include_hold=False: []), \
                 patch.object(tosser_mod, 'get_pending_netmail_for_network',
                             lambda net_id, include_hold=False: []), \
                 patch.object(mod, 'resolve_outbound_dir', lambda data_dir, addr: outbound_dir), \
                 patch.object(mod, '_send_outbound_dir_items', _fake_send_outbound_dir_items):
                writer = _FakeWriter()
                reader = _ScriptedReader(frames)
                asyncio.run(mod._handle_connection(reader, writer, '1200:2/2', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del BinkPNode.query
            del EchomailMessage.query
            del HatchQueue.query

        return captured

    def test_downstream_node_dialing_in_gets_its_spool_flushed(self):
        node = _FakeBinkPNode(id=1, ftn_address='1200:2/1@testnet',
                              password='secret')
        with tempfile.TemporaryDirectory() as tmpdir:
            outbound_dir = os.path.join(tmpdir, 'spool')
            os.makedirs(outbound_dir)
            captured = self._run(
                networks=[], nodes=[node],
                remote_addr='1200:2/1@testnet', remote_pwd='secret',
                outbound_dir=outbound_dir)

        self.assertEqual(captured['dir_calls'], [outbound_dir],
                         '_send_outbound_dir_items must be called exactly '
                         'once, with the resolved per-node spool directory, '
                         'when a downstream node dials in and the directory exists')

    def test_upstream_hub_dialing_in_gets_its_spool_flushed(self):
        network = _FakeEchomailNetwork(
            id=7, hub_address='1200:2/1@testnet', network_type='binkp',
            binkp_password='secret', our_address='1200:2/2')
        with tempfile.TemporaryDirectory() as tmpdir:
            outbound_dir = os.path.join(tmpdir, 'spool')
            os.makedirs(outbound_dir)
            captured = self._run(
                networks=[network], nodes=[],
                remote_addr='1200:2/1@testnet', remote_pwd='secret',
                outbound_dir=outbound_dir)

        self.assertEqual(captured['dir_calls'], [outbound_dir],
                         '_send_outbound_dir_items must be called exactly '
                         'once, with the resolved per-network spool '
                         'directory, when our upstream hub dials in and the '
                         'directory exists')

    def test_nonexistent_spool_directory_is_not_flushed(self):
        node = _FakeBinkPNode(id=2, ftn_address='1200:3/1@testnet',
                              password='secret')
        captured = self._run(
            networks=[], nodes=[node],
            remote_addr='1200:3/1@testnet', remote_pwd='secret',
            outbound_dir='/nonexistent/path/for/this/test')

        self.assertEqual(captured['dir_calls'], [],
                         'a peer with no spool directory yet must not '
                         'trigger a send attempt at all')


if __name__ == '__main__':
    unittest.main()
