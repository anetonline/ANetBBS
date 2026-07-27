"""Regression test for a TIC/file-echo hatch-out gap in the THIRD BinkP
delivery direction (the first two -- hub dials out to a node, and a node
dials in to the hub -- were already fixed and covered by
test_hatch_delivery_to_downstream_nodes.py).

This one is: our UPSTREAM HUB dials IN to us (a real two-way BinkP
pattern -- either side may initiate a session). binkp_server.py's
_handle_connection() already had a `net_id is not None` branch for this
exact case (matched via EchomailNetwork.hub_address), and it already
flushed queued outbound EchomailMessage/NetmailMessage rows there -- but
never queried HatchQueue at all, so a file echo we're subscribed to send
upstream via FileFix just sat in status='pending' forever unless we
happened to poll OUT to the hub first ourselves. Mirrors the exact fix
already shipped for the downstream_node_id branch (see
binkp_server.py's _send_hatch_items() docstring), just keyed on
network.hub_address instead of node.ftn_address.

Reuses the real-frame session-simulator harness from
test_binkp_downstream_node_import_network_id.py.
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


class _FakeHatchQueue:
    def __init__(self, id, peer_address, status='pending'):
        self.id = id
        self.peer_address = peer_address
        self.status = status
        self.sent_at = None


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


class UpstreamHubInboundHatchOutTests(unittest.TestCase):
    def _run(self, network, hatch_rows, remote_addr, remote_pwd,
             send_hatch_result=None):
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.echomail import tosser as tosser_mod
        from anetbbs.models import (EchomailNetwork, BinkPNode, EchomailMessage,
                                    HatchQueue, db)

        captured = {'hatch_calls': []}

        async def _fake_send_hatch_items(reader, writer, hatch_items,
                                         our_address, peer, state, files,
                                         transcript=None):
            captured['hatch_calls'].append(list(hatch_items))
            if send_hatch_result is not None:
                return send_hatch_result, []
            return [item.id for item in hatch_items], []

        frames = [
            (True, _cmd_payload(mod.CMD_ADR, remote_addr)),
            (True, _cmd_payload(mod.CMD_PWD, remote_pwd)),
            (True, _cmd_payload(mod.CMD_EOB)),
        ]

        recording_session = _RecordingSession()
        EchomailNetwork.query = _FakeQuery([network])
        BinkPNode.query = _FakeQuery([])
        EchomailMessage.query = _FakeQuery([])
        HatchQueue.query = _FakeQuery(hatch_rows)
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
                 patch.object(tosser_mod, 'get_pending_netmail_for_network', lambda net_id: []), \
                 patch.object(mod, '_send_hatch_items', _fake_send_hatch_items):
                writer = _FakeWriter()
                reader = _ScriptedReader(frames)
                asyncio.run(mod._handle_connection(reader, writer, '1200:2/2', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del BinkPNode.query
            del EchomailMessage.query
            del HatchQueue.query

        return writer, captured

    def test_pending_hatch_item_for_hub_is_shipped_when_hub_dials_in(self):
        network = _FakeEchomailNetwork(
            id=7, hub_address='1200:2/1@testnet', network_type='binkp',
            binkp_password='secret', our_address='1200:2/2')
        item = _FakeHatchQueue(id=101, peer_address='1200:2/1@testnet')

        writer, captured = self._run(
            network=network, hatch_rows=[item],
            remote_addr='1200:2/1@testnet', remote_pwd='secret')

        self.assertEqual(len(captured['hatch_calls']), 1,
                         '_send_hatch_items must be called exactly once for '
                         "the upstream hub's pending file-echo items")
        self.assertEqual([i.id for i in captured['hatch_calls'][0]], [101])

    def test_hatch_item_marked_sent_after_successful_ship(self):
        network = _FakeEchomailNetwork(
            id=8, hub_address='1200:3/1@testnet', network_type='binkp',
            binkp_password='secret', our_address='1200:3/2')
        item = _FakeHatchQueue(id=202, peer_address='1200:3/1@testnet')

        self._run(network=network, hatch_rows=[item],
                  remote_addr='1200:3/1@testnet', remote_pwd='secret')

        self.assertEqual(item.status, 'sent')
        self.assertIsNotNone(item.sent_at)

    def test_hatch_item_for_a_different_peer_is_not_included(self):
        network = _FakeEchomailNetwork(
            id=9, hub_address='1200:4/1@testnet', network_type='binkp',
            binkp_password='secret', our_address='1200:4/2')
        item = _FakeHatchQueue(id=303, peer_address='1200:4/99@othernet')

        writer, captured = self._run(
            network=network, hatch_rows=[item],
            remote_addr='1200:4/1@testnet', remote_pwd='secret')

        self.assertEqual(captured['hatch_calls'], [],
                         '_send_hatch_items must not be called when nothing '
                         'matches this peer')
        self.assertEqual(item.status, 'pending')

    def test_not_acked_item_is_not_marked_sent(self):
        network = _FakeEchomailNetwork(
            id=10, hub_address='1200:5/1@testnet', network_type='binkp',
            binkp_password='secret', our_address='1200:5/2')
        item = _FakeHatchQueue(id=404, peer_address='1200:5/1@testnet')

        self._run(network=network, hatch_rows=[item],
                  remote_addr='1200:5/1@testnet', remote_pwd='secret',
                  send_hatch_result=[])

        self.assertEqual(item.status, 'pending')
        self.assertIsNone(item.sent_at)


if __name__ == '__main__':
    unittest.main()
