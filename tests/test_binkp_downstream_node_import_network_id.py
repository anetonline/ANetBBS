"""Regression test for a real live bug: a sysop testing a SECOND
HubIdentity's echomail (multi-hub-identity feature) reported that his
message never showed up on ANetBBS, even though the BinkP transcript
showed a completely successful protocol-level transfer -- CRAM-MD5 auth
succeeded, the .pkt file was received and M_GOT-acknowledged.

Root cause, found in anetbbs/echomail/binkp_server.py's
_handle_connection(): when the connecting peer is a *downstream* node
(registered as a BinkPNode, not an upstream EchomailNetwork), the code
resolves `identity_net` (the EchomailNetwork row for that node's
HubIdentity) but used it ONLY to compute the outbound-stamping address
(`matched_our_address`) -- `identity_net.id` was never captured into
`net_id`. `net_id` stayed None for every downstream-node session,
regardless of how correctly everything else was configured, and that
None got passed straight into `_import_pkt_payload()`, which needs a
real network_id to create/attach EchoArea and EchomailMessage rows
(both NOT NULL on network_id). The resulting IntegrityError was
silently swallowed by the session's generic exception handler with no
sysop-visible trace anywhere (BadAreaLog is a poller.py-only mechanism,
never touched by this listener) -- BinkP itself looked fully successful
(M_GOT sent) while the message inside the packet was lost.

Reuses the real-frame session-simulator pattern established in
test_binkp_multi_hub_identity.py (downstream node + HubIdentity fakes)
combined with the FILE-transfer scripting pattern from
test_binkp_finish_before_import_ordering.py (a real M_FILE + DATA + EOB
frame sequence, with _import_pkt_payload patched out to capture the
network_id it was actually called with).
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
    """Like the plain no-op session the other BinkP tests use, but
    remembers every object passed to add() -- needed here to inspect
    the EchomailPollLog row the code under test constructs, since
    add()/commit() are otherwise no-ops with nothing to assert against."""
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
    """A minimal-but-structurally-valid FTS-0001 type-2 packet header
    (58 bytes) -- enough for _is_fts_packet()'s magic-byte check to
    recognize it as a real packet. _import_pkt_payload is patched out
    entirely; only the network_id it's called with matters here."""
    hdr = bytearray(58)
    struct.pack_into('<H', hdr, 18, 2)  # version = 2 (type-2 packet)
    return bytes(hdr)


class DownstreamNodeImportNetworkIdTests(unittest.TestCase):
    def _run(self, networks, nodes, remote_addr, remote_pwd):
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.echomail import tosser as tosser_mod
        from anetbbs.models import EchomailNetwork, BinkPNode, EchomailMessage, HatchQueue, FreqRequest, db

        captured = {'import_calls': []}

        def _tracking_import_pkt_payload(pkt_bytes, network_id, filename, peer_address=None, origin_ip=None):
            captured['import_calls'].append(network_id)
            return 3  # nonzero -- lets the poll-log messages_received assertion be meaningful

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
        # See test_binkp_multi_hub_identity.py's harness for why this
        # is needed -- _handle_connection() now also queries
        # HatchQueue on the downstream_node_id branch.
        HatchQueue.query = _FakeQuery([])
        FreqRequest.query = _FakeQuery([])
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
            del HatchQueue.query
            del FreqRequest.query

        captured['session_added'] = recording_session.added
        return writer, captured

    def test_downstream_node_with_resolvable_identity_net_imports_under_its_id(self):
        # The exact reported scenario: a downstream node belongs to a
        # second HubIdentity that DOES have its own BinkP EchomailNetwork
        # row -- _import_pkt_payload must be called with THAT network's
        # id, not None.
        identity = _FakeHubIdentity(id=2, name='ANotherNetwork', is_default=False)
        identity_net = _FakeEchomailNetwork(
            id=42, network_type='binkp', our_address='1200:1/1',
            hub_identity_id=2, name='ANotherNetwork')
        node = _FakeBinkPNode(id=2, ftn_address='1200:1/2', password='secret',
                              hub_identity=identity)

        writer, captured = self._run(
            networks=[identity_net], nodes=[node],
            remote_addr='1200:1/2', remote_pwd='secret')

        self.assertEqual(captured['import_calls'], [42],
                         '_import_pkt_payload must be called with the resolved '
                         "identity's EchomailNetwork id, not None -- this is "
                         'exactly why the message vanished with a fully '
                         'successful-looking BinkP transfer (M_GOT sent) but '
                         'no trace anywhere of the import itself failing.')

    def test_resolvable_identity_net_also_gets_a_poll_log_entry(self):
        # Direct, positive side effect of the same fix: net_id being
        # correctly resolved means the existing
        # `if net_id is not None:` poll-log-writing block (previously
        # only ever true for upstream-hub sessions) now also fires for
        # a downstream node on a resolvable identity -- so a sysop
        # testing a second network's echomail this way now gets a
        # visible entry under Admin -> Echomail, not just a correctly
        # imported message with no record the exchange ever happened.
        from anetbbs.models import EchomailPollLog
        identity = _FakeHubIdentity(id=2, name='ANotherNetwork', is_default=False)
        identity_net = _FakeEchomailNetwork(
            id=42, network_type='binkp', our_address='1200:1/1',
            hub_identity_id=2, name='ANotherNetwork')
        node = _FakeBinkPNode(id=2, ftn_address='1200:1/2', password='secret',
                              hub_identity=identity)

        writer, captured = self._run(
            networks=[identity_net], nodes=[node],
            remote_addr='1200:1/2', remote_pwd='secret')

        logs = [obj for obj in captured['session_added']
                if isinstance(obj, EchomailPollLog)]
        self.assertEqual(len(logs), 1,
                         'exactly one EchomailPollLog row must be created for '
                         'this downstream-node session now that net_id resolves')
        log = logs[0]
        self.assertEqual(log.network_id, 42)
        self.assertEqual(log.status, 'success')
        self.assertEqual(log.messages_received, 3)
        self.assertEqual(log.node_id, 2,
                         'EchomailPollLog.node_id must be populated for a '
                         'downstream-node session, so the admin poll-log page '
                         'can filter by node, not just by network')

    def test_downstream_node_on_default_identity_still_gets_none_handled_gracefully(self):
        # A node whose hub_identity is None (or has no matching
        # EchomailNetwork row at all) must not crash -- the connection
        # still completes, the packet import is skipped with a loud log
        # instead of an uncaught IntegrityError.
        node = _FakeBinkPNode(id=1, ftn_address='1:200/100', password='secret',
                              hub_identity=None)
        from anetbbs.echomail.binkp_server import CMD_OK, CMD_ERR

        writer, captured = self._run(
            networks=[], nodes=[node],
            remote_addr='1:200/100', remote_pwd='secret')

        self.assertEqual(captured['import_calls'], [],
                         '_import_pkt_payload must not be called at all when no '
                         'network_id can be resolved -- skip loudly, not crash silently')

        def _decode(raw_frames):
            out = []
            for f in raw_frames:
                word = struct.unpack('>H', f[0:2])[0]
                length = word & 0x7FFF
                payload = f[2:2 + length]
                if word & 0x8000 and payload:
                    out.append(payload[0])
            return out
        commands = _decode(writer.sent)
        self.assertIn(CMD_OK, commands)
        self.assertNotIn(CMD_ERR, commands)


if __name__ == '__main__':
    unittest.main()
