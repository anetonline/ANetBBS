"""Regression test for a real Medium finding from a follow-up security
audit: anetbbs/echomail/binkp_server.py's inbound WaZOO FREQ handling
used to call freq.process_inbound_req(payload, remote_addr) for ANY
accepted session, including a fully anonymous "crashmail" one whose
remote_addr is a self-claimed, never-validated M_ADR.

The existing "Low" rating on FREQ reachability (see this module's own
_FREQ_PER_IP_LIMIT comment) reasoned only that an anonymous requester
can never have the matched files delivered back TO ITSELF -- it missed
that an attacker can instead claim a REAL third-party peer's public FTN
address, causing the poller/binkp client (which match
HatchQueue.peer_address against a real configured peer by exact string)
to deliver files to that uninvolved peer on its next legitimate
session.

Fixed by only calling process_inbound_req() when the session actually
authenticated as a known hub (net_id) or downstream node
(downstream_node_id) -- an anonymous/unrecognized peer's FREQ is now
ignored outright.

Reuses the real-frame session-simulator pattern from
test_binkp_freq_rate_limit_source_ip.py / test_binkp_server_crash_still_logged.py.
"""
import asyncio
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _frame(is_command, payload):
    word = (0x8000 if is_command else 0) | (len(payload) & 0x7FFF)
    return struct.pack('>H', word) + payload


def _cmd_payload(cmd, text=''):
    return bytes([cmd]) + text.encode('latin-1', errors='replace')


class _FakeWriter:
    def __init__(self, peer_ip='198.51.100.7'):
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
    """Returns `rows` regardless of filter criteria -- enough to drive
    either the unrecognized-peer branch (rows=()) or a matched-network
    branch (rows=(one fake network,))."""
    def __init__(self, rows=()):
        self._rows = list(rows)

    def filter(self, *criteria):
        return self

    def filter_by(self, **kwargs):
        return self

    def order_by(self, *criteria):
        return self

    def limit(self, n):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeNetworkRow:
    def __init__(self, id_, name, hub_address):
        self.id = id_
        self.name = name
        self.network_type = 'binkp'
        self.hub_address = hub_address
        self.our_address = ''
        self.binkp_password = 'hubsecret'  # passwordless links are non-secure (audit M-4)
        self.packet_password = ''
        self.default_crash = False
        self.default_hold = False
        self.default_direct = False
        self.compress_outbound = False


class _NoOpSession:
    def add(self, *a, **k):
        pass

    def commit(self, *a, **k):
        pass

    def rollback(self, *a, **k):
        pass

    def flush(self, *a, **k):
        pass


def _freq_frames(claimed_address, req_filename='000C0003.req', req_payload=b'a.zip'):
    from anetbbs.echomail import binkp_server as mod
    return [
        (True, _cmd_payload(mod.CMD_ADR, claimed_address)),
        (True, _cmd_payload(mod.CMD_PWD, 'hubsecret')),
        (True, _cmd_payload(mod.CMD_FILE,
                            f'{req_filename} {len(req_payload)} 1700000000 0')),
        (False, req_payload),
        (True, _cmd_payload(mod.CMD_EOB)),
    ]


class FreqRequiresAuthenticatedPeerTests(unittest.TestCase):

    def test_anonymous_crashmail_freq_is_never_queued(self):
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.models import EchomailNetwork, BinkPNode, db

        claimed_address = 'NOT-A-REAL-9999:9999/9999'
        EchomailNetwork.query = _FakeQuery(rows=())
        BinkPNode.query = _FakeQuery(rows=())
        try:
            with patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', _NoOpSession()), \
                 patch.object(mod, '_freq_rate_limited', lambda ip: False), \
                 patch('anetbbs.echomail.freq.process_inbound_req') as spy:
                writer = _FakeWriter()
                reader = _ScriptedReader(_freq_frames(claimed_address))
                asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del BinkPNode.query

        spy.assert_not_called()

    def test_authenticated_hub_freq_is_still_queued(self):
        from anetbbs.echomail import binkp_server as mod
        from anetbbs.models import (
            EchomailNetwork, BinkPNode, EchomailMessage, HatchQueue,
            FreqRequest, db)

        real_hub_address = '1:234/567'
        network = _FakeNetworkRow(id_=1, name='RealHub', hub_address=real_hub_address)
        EchomailNetwork.query = _FakeQuery(rows=(network,))
        BinkPNode.query = _FakeQuery(rows=())
        EchomailMessage.query = _FakeQuery(rows=())
        HatchQueue.query = _FakeQuery(rows=())
        FreqRequest.query = _FakeQuery(rows=())
        try:
            with patch.object(db, 'init_app', lambda app: None), \
                 patch.object(db, 'session', _NoOpSession()), \
                 patch.object(mod, '_freq_rate_limited', lambda ip: False), \
                 patch('anetbbs.echomail.tosser.get_pending_netmail_for_network',
                       return_value=[]), \
                 patch('anetbbs.echomail.freq.process_inbound_req',
                       MagicMock(return_value=[1])) as spy:
                writer = _FakeWriter()
                reader = _ScriptedReader(_freq_frames(real_hub_address))
                asyncio.run(mod._handle_connection(reader, writer, '1:1/1', 'ANetBBS'))
        finally:
            del EchomailNetwork.query
            del BinkPNode.query
            del EchomailMessage.query
            del HatchQueue.query
            del FreqRequest.query

        spy.assert_called_once()
        self.assertEqual(spy.call_args[0][1], real_hub_address)


if __name__ == '__main__':
    unittest.main()
