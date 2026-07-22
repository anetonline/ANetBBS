"""Regression test for a real live bug: a sysop (hub of ANotherNetwork,
zone 1200) is ALSO a leaf member of four other real-world binkp networks
(tqwnet, sp00knet, IREX, Fidonet) -- all five EchomailNetwork rows share
the install's one default HubIdentity, since multi-hub-identity is only
needed when a sysop runs more than one distinct hub. A real downstream
node (GateKeeper, 1200:1/2) polled in successfully -- CRAM-MD5 auth OK,
.pkt received, M_GOT acknowledged, and the exchange even showed up via
the unrelated InterBBS Last Callers feature -- but nothing appeared
under ANotherNetwork in Admin -> Echomail poll log.

Root cause, found in anetbbs/echomail/binkp_server.py's
_handle_connection(): resolving `identity_net` for a downstream node
used to filter EchomailNetwork purely by `hub_identity_id=identity.id,
network_type='binkp'` and take `.first()` -- with 5 matching rows under
one identity, `.first()` returned whichever sorted first by id (tqwnet),
completely unrelated to which network the connecting node actually
belongs to. GateKeeper's mail got silently imported/logged under tqwnet
instead of ANotherNetwork.

Fixed two ways: (1) BinkPNode gained an explicit `network_id` FK --
the only truly unambiguous source of truth, set at node-creation time
by approve_join_request()/the admin form; (2) for legacy rows without
it, routing.self_hub_binkp_network() narrows the ambiguous
hub_identity_id candidates down to the one network under that identity
where we're actually the hub (our_address == hub_address) -- the only
case a downstream node polling IN makes sense for.

Reuses the real-frame session-simulator pattern from
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


class _FakeQuery:
    def __init__(self, rows, predicates=None):
        self._rows = list(rows)
        self._predicates = list(predicates or [])

    def filter(self, *criteria):
        return _FakeQuery(self._rows, self._predicates + list(criteria))

    def filter_by(self, **kwargs):
        extra = [('eq', k, v) for k, v in kwargs.items()]
        return _FakeQuery(self._rows, self._predicates + extra)

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

    def get(self, pk):
        for row in self._rows:
            if row.id == pk:
                return row
        return None


class _FakeHubIdentity:
    def __init__(self, id, name='Identity', is_default=False,
                binkp_zone=None, binkp_net=None, binkp_hub_node=1):
        self.id = id
        self.name = name
        self.is_default = is_default
        self.binkp_zone = binkp_zone
        self.binkp_net = binkp_net
        self.binkp_hub_node = binkp_hub_node


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

    def add(self, obj, *a, **k):
        self.added.append(obj)

    def commit(self, *a, **k):
        pass

    def rollback(self, *a, **k):
        pass

    def flush(self, *a, **k):
        pass


def _minimal_fts_packet():
    hdr = bytearray(58)
    struct.pack_into('<H', hdr, 18, 2)  # version = 2 (type-2 packet)
    return bytes(hdr)


def _real_world_networks(identity_id=1):
    """The exact shape of Jerry's real live data: 5 binkp networks under
    one HubIdentity, only ANotherNetwork (id=5) is a self-hub network
    (our_address == hub_address); the rest are leaf memberships. tqwnet
    (id=3) sorts first by id -- the old buggy `.first()` picked it."""
    return [
        _FakeEchomailNetwork(id=3, name='tqwnet', our_address='1337:3/231',
                             hub_address='1337:3/100', hub_identity_id=identity_id),
        _FakeEchomailNetwork(id=4, name='sp00knet', our_address='700:100/111',
                             hub_address='700:100/0', hub_identity_id=identity_id),
        _FakeEchomailNetwork(id=5, name='ANotherNetwork', our_address='1200:1/1',
                             hub_address='1200:1/1', hub_identity_id=identity_id),
        _FakeEchomailNetwork(id=7, name='IREX', our_address='111:1111/3',
                             hub_address='111:111/2', hub_identity_id=identity_id),
        _FakeEchomailNetwork(id=8, name='Fidonet', our_address='1:123/3003',
                             hub_address='1:3634/12', hub_identity_id=identity_id),
    ]


class NetworkDisambiguationTests(unittest.TestCase):
    def _run(self, networks, nodes, remote_addr, remote_pwd):
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.echomail import tosser as tosser_mod
        from anetbbs.models import EchomailNetwork, BinkPNode, EchomailMessage, db

        captured = {'import_calls': []}

        def _tracking_import_pkt_payload(pkt_bytes, network_id, filename):
            captured['import_calls'].append(network_id)
            return 3

        pkt_bytes = _minimal_fts_packet()
        frames = [
            (True, _cmd_payload(mod.CMD_ADR, remote_addr)),
            (True, _cmd_payload(mod.CMD_PWD, remote_pwd)),
            (True, _cmd_payload(mod.CMD_FILE, f'test.pkt {len(pkt_bytes)} 0 0')),
            (False, pkt_bytes),
            (True, _cmd_payload(mod.CMD_EOB)),
        ]

        recording_session = _RecordingSession()
        EchomailNetwork.query = _FakeQuery(networks)
        BinkPNode.query = _FakeQuery(nodes)
        EchomailMessage.query = _FakeQuery([])
        try:
            with patch.object(EchomailNetwork, 'hub_address', _FakeColumn('hub_address')), \
                 patch.object(EchomailNetwork, 'our_address', _FakeColumn('our_address')), \
                 patch.object(EchomailNetwork, 'hub_identity_id', _FakeColumn('hub_identity_id')), \
                 patch.object(EchomailNetwork, 'network_type', _FakeColumn('network_type')), \
                 patch.object(BinkPNode, 'ftn_address', _FakeColumn('ftn_address')), \
                 patch.object(EchomailMessage, 'network_id', _FakeColumn('network_id')), \
                 patch.object(EchomailMessage, 'direction', _FakeColumn('direction')), \
                 patch.object(EchomailMessage, 'sent_at', _FakeColumn('sent_at')), \
                 patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', recording_session), \
                 patch.object(tosser_mod, 'get_pending_for_node', lambda node_id: []), \
                 patch.object(tosser_mod, 'mark_sent_for_node', lambda node_id, ids: None), \
                 patch.object(mod, '_import_pkt_payload', _tracking_import_pkt_payload):
                writer = _FakeWriter()
                reader = _ScriptedReader(frames)
                asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del BinkPNode.query
            del EchomailMessage.query

        captured['session_added'] = recording_session.added
        return writer, captured

    def test_legacy_node_without_network_id_resolves_via_self_hub_not_first_by_id(self):
        # The exact reported scenario: node.network_id unset (legacy row),
        # 5 binkp networks share one hub_identity_id, tqwnet (id=3) sorts
        # first. Must resolve to ANotherNetwork (id=5) -- the network
        # where we're actually the hub -- not tqwnet.
        identity = _FakeHubIdentity(id=1, name='ANotherNetwork')
        networks = _real_world_networks(identity_id=1)
        node = _FakeBinkPNode(id=9, ftn_address='1200:1/2', password='secret',
                              name='GateKeeper', hub_identity=identity,
                              network_id=None)

        writer, captured = self._run(
            networks=networks, nodes=[node],
            remote_addr='1200:1/2', remote_pwd='secret')

        self.assertEqual(captured['import_calls'], [5],
                         'must resolve to ANotherNetwork (id=5, our_address=='
                         'hub_address), not tqwnet (id=3) which only sorted '
                         'first by id -- this is exactly why real ANotherNetwork '
                         'mail was landing under the wrong network')

    def test_legacy_node_poll_log_lands_under_anothernetwork_not_tqwnet(self):
        # Directly encodes the reported symptom: Admin -> Echomail poll
        # log showed nothing for ANotherNetwork despite a fully successful
        # BinkP session -- because the entry was silently landing under
        # tqwnet's network_id instead.
        from anetbbs.models import EchomailPollLog
        identity = _FakeHubIdentity(id=1, name='ANotherNetwork')
        networks = _real_world_networks(identity_id=1)
        node = _FakeBinkPNode(id=9, ftn_address='1200:1/2', password='secret',
                              name='GateKeeper', hub_identity=identity,
                              network_id=None)

        writer, captured = self._run(
            networks=networks, nodes=[node],
            remote_addr='1200:1/2', remote_pwd='secret')

        logs = [obj for obj in captured['session_added']
               if isinstance(obj, EchomailPollLog)]
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].network_id, 5,
                         'poll log must be attributed to ANotherNetwork (5), '
                         'not tqwnet (3)')

    def test_explicit_network_id_overrides_ambiguous_heuristic(self):
        # A node with network_id set explicitly must use it directly, even
        # in a case the self-hub heuristic alone could NOT resolve (two
        # networks under the identity both happen to be self-hub rows --
        # contrived, but proves the explicit FK is authoritative and
        # doesn't depend on the heuristic succeeding).
        identity = _FakeHubIdentity(id=1, name='ANotherNetwork')
        networks = [
            _FakeEchomailNetwork(id=3, name='tqwnet', our_address='1337:3/231',
                                 hub_address='1337:3/231',  # also "self-hub" -- ambiguous
                                 hub_identity_id=1),
            _FakeEchomailNetwork(id=5, name='ANotherNetwork', our_address='1200:1/1',
                                 hub_address='1200:1/1', hub_identity_id=1),
        ]
        node = _FakeBinkPNode(id=9, ftn_address='1200:1/2', password='secret',
                              name='GateKeeper', hub_identity=identity,
                              network_id=5)

        writer, captured = self._run(
            networks=networks, nodes=[node],
            remote_addr='1200:1/2', remote_pwd='secret')

        self.assertEqual(captured['import_calls'], [5],
                         'explicit node.network_id must be used directly, not '
                         're-derived from the (here-ambiguous) hub_identity_id heuristic')


if __name__ == '__main__':
    unittest.main()
