"""Phase 5 of multi-hub-identity: BinkP listener auth + outbound
sender-stamping.

_handle_connection() (anetbbs/echomail/binkp_server.py) had ZERO real
test coverage of its auth/matching block before this file: both
existing BinkP server test files (test_binkp_server_single_adr_fix.py,
test_binkp_server_cram_md5.py) use a _FakeReader whose readexactly()
raises IncompleteReadError on the very first call, so the code never
gets past the M_NUL/M_ADR preamble -- the matching-and-auth logic at
lines ~279-360 (as of this writing) is never exercised at all. Their
_FakeQuery.filter_by(**kwargs) also silently ignores every kwarg and
returns all rows, which would mask a completely broken filter if one
were added.

This file builds a real (if minimal) BinkP session simulator: a
_ScriptedReader that feeds actual M_ADR/M_PWD/M_EOB/M_GOT frames, and a
_FakeQuery/_FakeColumn pair that genuinely evaluates .filter(col.in_(...)),
.filter(col.isnot(None)), and .filter_by(**kwargs) against an in-memory
row list -- not just "return everything". Two goals:

  1. Characterize today's real end-to-end behavior (a downstream node
     authenticates, no crash, outbound gets stamped) BEFORE any
     identity-aware changes -- the safety net for this phase.
  2. Prove the actual multi-hub-identity fix: a downstream node
     belonging to a second HubIdentity gets its outbound .pkt stamped
     with THAT identity's own AKA (via its BinkP network's own
     our_address, or a zone:net/hub_node reconstruction), not the
     single process-wide BINKP_OUR_ADDRESS default -- and that
     resolution fails OPEN (falls back to the default, never rejects
     the connection) when identity data is incomplete/missing.
"""
import asyncio
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Frame-level session simulator
# ---------------------------------------------------------------------------

def _frame(is_command, payload):
    word = (0x8000 if is_command else 0) | (len(payload) & 0x7FFF)
    return struct.pack('>H', word) + payload


def _cmd_payload(cmd, text=''):
    return bytes([cmd]) + text.encode('latin-1', errors='replace')


class _FakeWriter:
    def __init__(self):
        self.sent = []

    def get_extra_info(self, key):
        return ('127.0.0.1', 12345) if key == 'peername' else None

    def write(self, data):
        self.sent.append(data)

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


class _ScriptedReader:
    """Feeds a scripted sequence of real BinkP frames to _recv_frame(),
    then raises IncompleteReadError forever once exhausted -- mirrors a
    real peer disconnecting after its scripted lines. Deliberately has
    no .read() method, so _finish_session()'s post-EOB drain loop hits
    an immediate AttributeError (caught and swallowed by that
    function's own try/except) instead of a real multi-second timeout
    -- keeps these tests fast without needing to fake the event loop.
    """
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


def _decode_sent_commands(raw_frames):
    out = []
    for frame in raw_frames:
        word = struct.unpack('>H', frame[0:2])[0]
        is_cmd = bool(word & 0x8000)
        length = word & 0x7FFF
        payload = frame[2:2 + length]
        if is_cmd and payload:
            out.append((payload[0], payload[1:].decode('latin-1', errors='replace')))
    return out


def _standard_script(remote_addr, remote_pwd, include_got=False):
    """ADR + PWD (auth), then immediate EOB (no inbound files), then
    optionally a GOT (accepting whatever outbound .pkt the server
    offers)."""
    from anetbbs.echomail.binkp_server import CMD_ADR, CMD_PWD, CMD_EOB, CMD_GOT
    frames = [
        (True, _cmd_payload(CMD_ADR, remote_addr)),
        (True, _cmd_payload(CMD_PWD, remote_pwd)),
        (True, _cmd_payload(CMD_EOB)),
    ]
    if include_got:
        frames.append((True, _cmd_payload(CMD_GOT, f'x.pkt 0 0')))
    return frames


# ---------------------------------------------------------------------------
# Fakes that genuinely respect filter()/filter_by() -- the actual point
# of this file, per the "must not silently pass" lesson learned on the
# existing BinkP test fakes.
# ---------------------------------------------------------------------------

class _FakeColumn:
    """Stands in for a real SQLAlchemy InstrumentedAttribute for
    exactly the clause shapes _handle_connection() builds:
    `Model.col.in_(values)` and `Model.col.isnot(None)`. Evaluating a
    real SQLAlchemy BinaryExpression against a plain fake row with no
    engine/session is impractical -- swapping the class attribute
    itself for a tiny stand-in that returns an inspectable predicate is
    far more robust than trying to interpret real SQLA internals."""
    def __init__(self, name):
        self.name = name

    def in_(self, values):
        return ('in', self.name, set(values))

    def isnot(self, value):
        assert value is None, 'only isnot(None) is used by the code under test'
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
                is_active=True, binkp_zone=None, binkp_net=None,
                binkp_hub_node=1):
        self.id = id
        self.name = name
        self.is_default = is_default
        self.is_active = is_active
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


class _NoOpSession:
    """Stands in for db.session so _handle_connection's node.last_seen_at
    commit doesn't touch a real engine at all -- not even a throwaway
    sqlite file. Matches this test file's goal of staying fully
    real-DB-free, same constraint the pre-existing BinkP server tests
    documented and depended on."""
    def add(self, *a, **k):
        pass

    def commit(self, *a, **k):
        pass

    def rollback(self, *a, **k):
        pass

    def flush(self, *a, **k):
        pass


class _BinkpHandlerHarness:
    """Shared monkeypatch setup/teardown for _handle_connection() tests
    -- swaps EchomailNetwork/BinkPNode's .query and the specific column
    attributes the code under test touches, plus db.init_app/db.session,
    all via mock.patch.object context managers so everything restores
    automatically even on failure (no manual del bookkeeping needed)."""

    def _run(self, networks, nodes, remote_addr, remote_pwd,
             our_address='1:1/1', include_got=False, outbound_messages=None):
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.echomail import tosser as tosser_mod
        from anetbbs.models import EchomailNetwork, BinkPNode, EchomailMessage, HatchQueue, db

        captured = {}
        pending = outbound_messages or []

        def _sync_fake_build_ftn_packet(messages, our_addr, remote_addr_arg):
            captured['our_address'] = our_addr
            captured['remote_addr'] = remote_addr_arg
            return b'FAKEPKT'

        async def _fake_send_pkt_file(reader, writer, filename, payload,
                                      peer, state, files, transcript=None):
            captured['sent_pkt'] = True
            return include_got

        # Flask-SQLAlchemy's `.query` is a descriptor that resolves the
        # current app context on *read* -- even mock.patch.object's own
        # save-the-original-value step touches it, raising "working
        # outside of application context" before the patch is even
        # applied. Plain setattr/delattr bypasses the descriptor
        # entirely (matches the pattern already used by
        # test_binkp_server_cram_md5.py / test_binkp_server_single_adr_fix.py).
        EchomailNetwork.query = _FakeQuery(networks)
        BinkPNode.query = _FakeQuery(nodes)
        EchomailMessage.query = _FakeQuery(pending)
        # Real gap found in a full echomail-subsystem audit: _handle_
        # connection() now also queries HatchQueue for pending file-echo
        # hatch-out items on the downstream_node_id branch. This harness
        # predates that and never patched HatchQueue.query, so it fell
        # through to the real Flask-SQLAlchemy descriptor -- which,
        # combined with db.session being patched to _NoOpSession() below,
        # raised 'TypeError: _NoOpSession object is not callable' the
        # instant it was touched. Empty by default (no pending hatch
        # items) -- matches the overwhelmingly common case and every
        # existing test here, none of which care about hatching.
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
                 patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', _NoOpSession()), \
                 patch.object(mod, '_build_ftn_packet', _sync_fake_build_ftn_packet), \
                 patch.object(mod, '_send_pkt_file', _fake_send_pkt_file), \
                 patch.object(tosser_mod, 'get_pending_for_node', lambda node_id: pending), \
                 patch.object(tosser_mod, 'mark_sent_for_node', lambda node_id, ids: None):
                writer = _FakeWriter()
                reader = _ScriptedReader(
                    _standard_script(remote_addr, remote_pwd, include_got=include_got))
                asyncio.run(mod._handle_connection(
                    reader, writer, our_address, 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del BinkPNode.query
            del EchomailMessage.query
            del HatchQueue.query

        return writer, captured


class DownstreamNodeAuthCharacterizationTests(unittest.TestCase, _BinkpHandlerHarness):
    """Characterizes today's (pre- and post-fix, since the fix doesn't
    touch this part) behavior: a downstream BinkPNode authenticates
    successfully by FTN address + password, gets M_OK, no crash."""

    def test_known_node_correct_password_gets_ok(self):
        from anetbbs.echomail.binkp_server import CMD_OK, CMD_ERR
        node = _FakeBinkPNode(id=1, ftn_address='1:200/100', password='secret')
        writer, _ = self._run(networks=[], nodes=[node],
                              remote_addr='1:200/100', remote_pwd='secret')
        commands = _decode_sent_commands(writer.sent)
        self.assertIn(CMD_OK, [c for c, _ in commands])
        self.assertNotIn(CMD_ERR, [c for c, _ in commands])

    def test_unknown_address_gets_err_not_ok(self):
        from anetbbs.echomail.binkp_server import CMD_OK, CMD_ERR
        writer, _ = self._run(networks=[], nodes=[],
                              remote_addr='9:999/999', remote_pwd='whatever')
        commands = _decode_sent_commands(writer.sent)
        self.assertIn(CMD_ERR, [c for c, _ in commands])
        self.assertNotIn(CMD_OK, [c for c, _ in commands])

    def test_known_address_wrong_password_gets_err(self):
        from anetbbs.echomail.binkp_server import CMD_OK, CMD_ERR
        node = _FakeBinkPNode(id=1, ftn_address='1:200/100', password='secret')
        writer, _ = self._run(networks=[], nodes=[node],
                              remote_addr='1:200/100', remote_pwd='WRONG')
        commands = _decode_sent_commands(writer.sent)
        self.assertIn(CMD_ERR, [c for c, _ in commands])
        self.assertNotIn(CMD_OK, [c for c, _ in commands])

    def test_upstream_network_match_still_authenticates(self):
        """The EchomailNetwork (upstream-hub) match path is unrelated to
        downstream node identity resolution but shares the same query
        infrastructure -- confirm it still works with the real
        filter()-respecting fakes."""
        from anetbbs.echomail.binkp_server import CMD_OK, CMD_ERR
        net = _FakeEchomailNetwork(id=1, hub_address='2:2/2',
                                   network_type='binkp', binkp_password='hubpw')
        writer, _ = self._run(networks=[net], nodes=[],
                              remote_addr='2:2/2', remote_pwd='hubpw')
        commands = _decode_sent_commands(writer.sent)
        self.assertIn(CMD_OK, [c for c, _ in commands])
        self.assertNotIn(CMD_ERR, [c for c, _ in commands])


class OutboundIdentityStampingTests(unittest.TestCase, _BinkpHandlerHarness):
    """The actual Phase 5 fix: outbound .pkt FROM-address must reflect
    the matched downstream node's own hub identity, not always the
    single process-wide BINKP_OUR_ADDRESS."""

    def test_node_on_default_identity_uses_process_wide_address(self):
        """Characterizes the pre-existing, still-correct behavior for
        the common case (no second identity involved at all): falls
        back to the process-wide our_address exactly as before."""
        identity = _FakeHubIdentity(id=1, name='Default', is_default=True)
        node = _FakeBinkPNode(id=1, ftn_address='1:200/100', password='secret',
                              hub_identity=identity)
        from unittest.mock import MagicMock
        fake_msg = MagicMock(id=1)
        writer, captured = self._run(
            networks=[], nodes=[node], remote_addr='1:200/100',
            remote_pwd='secret', our_address='1:1/1', include_got=True,
            outbound_messages=[fake_msg])
        self.assertEqual(captured.get('our_address'), '1:1/1')

    def test_node_on_second_identity_with_own_binkp_network_uses_its_our_address(self):
        """The real fix: a second HubIdentity with its own BinkP-transport
        EchomailNetwork row (own our_address) stamps outbound mail with
        THAT address, not the process-wide default."""
        identity = _FakeHubIdentity(id=2, name='SecondNet', is_default=False)
        identity_net = _FakeEchomailNetwork(
            id=10, network_type='binkp', our_address='2:200/1',
            hub_identity_id=2)
        node = _FakeBinkPNode(id=2, ftn_address='2:200/50', password='secret2',
                              hub_identity=identity)
        from unittest.mock import MagicMock
        fake_msg = MagicMock(id=1)
        writer, captured = self._run(
            networks=[identity_net], nodes=[node], remote_addr='2:200/50',
            remote_pwd='secret2', our_address='1:1/1', include_got=True,
            outbound_messages=[fake_msg])
        self.assertEqual(captured.get('our_address'), '2:200/1')
        self.assertNotEqual(captured.get('our_address'), '1:1/1')

    def test_node_on_second_identity_without_network_falls_back_to_zone_net_hub_node(self):
        """No BinkP-transport EchomailNetwork exists for the identity --
        falls back to reconstructing zone:net/hub_node from the
        HubIdentity row itself."""
        identity = _FakeHubIdentity(id=3, name='ZoneNetOnly', is_default=False,
                                    binkp_zone=3000, binkp_net=5, binkp_hub_node=1)
        node = _FakeBinkPNode(id=3, ftn_address='3000:5/50', password='secret3',
                              hub_identity=identity)
        from unittest.mock import MagicMock
        fake_msg = MagicMock(id=1)
        writer, captured = self._run(
            networks=[], nodes=[node], remote_addr='3000:5/50',
            remote_pwd='secret3', our_address='1:1/1', include_got=True,
            outbound_messages=[fake_msg])
        self.assertEqual(captured.get('our_address'), '3000:5/1')

    def test_node_with_no_resolvable_identity_fails_open_to_default(self):
        """fail-open guarantee: a node whose hub_identity is None (a
        genuinely broken/edge-case row) must NOT be rejected or crash --
        outbound mail just falls back to the process-wide default, and
        the connection succeeds normally."""
        from anetbbs.echomail.binkp_server import CMD_OK, CMD_ERR
        node = _FakeBinkPNode(id=4, ftn_address='1:200/200', password='secret4',
                              hub_identity=None)
        from unittest.mock import MagicMock
        fake_msg = MagicMock(id=1)
        writer, captured = self._run(
            networks=[], nodes=[node], remote_addr='1:200/200',
            remote_pwd='secret4', our_address='1:1/1', include_got=True,
            outbound_messages=[fake_msg])
        commands = _decode_sent_commands(writer.sent)
        self.assertIn(CMD_OK, [c for c, _ in commands])
        self.assertNotIn(CMD_ERR, [c for c, _ in commands])
        self.assertEqual(captured.get('our_address'), '1:1/1')

    def test_two_different_identities_get_different_outbound_addresses(self):
        """End-to-end proof that two nodes on two different identities,
        authenticating in separate sessions, each get their own
        identity's address -- not each other's, not always the default."""
        identity_a = _FakeHubIdentity(id=10, name='A', is_default=False)
        net_a = _FakeEchomailNetwork(id=100, network_type='binkp',
                                     our_address='10:1/1', hub_identity_id=10)
        node_a = _FakeBinkPNode(id=10, ftn_address='10:1/50', password='pwA',
                                hub_identity=identity_a)

        identity_b = _FakeHubIdentity(id=20, name='B', is_default=False)
        net_b = _FakeEchomailNetwork(id=200, network_type='binkp',
                                     our_address='20:1/1', hub_identity_id=20)
        node_b = _FakeBinkPNode(id=20, ftn_address='20:1/50', password='pwB',
                                hub_identity=identity_b)

        from unittest.mock import MagicMock
        fake_msg = MagicMock(id=1)

        _, captured_a = self._run(
            networks=[net_a, net_b], nodes=[node_a, node_b],
            remote_addr='10:1/50', remote_pwd='pwA', our_address='1:1/1',
            include_got=True, outbound_messages=[fake_msg])
        _, captured_b = self._run(
            networks=[net_a, net_b], nodes=[node_a, node_b],
            remote_addr='20:1/50', remote_pwd='pwB', our_address='1:1/1',
            include_got=True, outbound_messages=[fake_msg])

        self.assertEqual(captured_a.get('our_address'), '10:1/1')
        self.assertEqual(captured_b.get('our_address'), '20:1/1')
        self.assertNotEqual(captured_a.get('our_address'), captured_b.get('our_address'))


if __name__ == '__main__':
    unittest.main()
