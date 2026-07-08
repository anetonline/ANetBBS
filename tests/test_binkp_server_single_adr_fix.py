"""Regression test for the inbound (server) half of the dual-address
BinkP bug -- see tests/test_binkp_dual_adr_fix.py for the outbound
(client) half and the full root-cause writeup.

Jerry confirmed this is bidirectional: it also happens when a real
binkd FidoNet hub calls IN to an ANetBBS install. binkp_server.py
(anetbbs/echomail/binkp_server.py, the inbound listener) had the exact
same "advertise both qualified and bare forms" pattern per configured
EchomailNetwork row when announcing our own AKAs to whoever connects
in -- same self-collision risk on the calling peer's busy-lock logic.
Fixed by sending exactly one form per configured address.

Deliberately does NOT create a real Flask app / call create_app() or
db.init_app() anywhere in this file. _handle_connection() (the function
under test) already creates its own internal Flask app + db.init_app()
to look up EchomailNetwork rows -- confirmed via bisection that a
SECOND, test-created app/engine coexisting with that one in the same
process (even torn down carefully before/after) leaves scoped-session
state that corrupts unrelated test files' own create_app() calls
elsewhere in the same pytest run (surfaced as a spurious "UNIQUE
constraint failed: users.username" in test_mrc_integration.py). Mocking
the EchomailNetwork.query lookup directly sidesteps needing any real
Flask/SQLAlchemy app in this file at all, and is a tighter unit test
for what's actually being verified here (address dedup/formatting
logic, not real DB round-tripping).
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeWriter:
    def __init__(self):
        self.sent = []  # list of raw bytes written

    def get_extra_info(self, key):
        return ('127.0.0.1', 12345) if key == 'peername' else None

    def write(self, data):
        self.sent.append(data)

    async def drain(self):
        pass


class _FakeReader:
    """readexactly() always raises IncompleteReadError -- binkp_server.py
    catches this specific exception and returns cleanly right after the
    handshake's first read attempt, which is exactly what we want: let
    the AKA-sending code run, then stop before needing to simulate a
    full session."""
    async def readexactly(self, n):
        raise asyncio.IncompleteReadError(partial=b'', expected=n)


class _FakeNetworkRow:
    def __init__(self, name, our_address):
        self.name = name
        self.our_address = our_address


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter_by(self, **kwargs):
        return self

    def all(self):
        return self._rows


def _decode_sent_commands(raw_frames):
    """Reconstruct (cmd, text) tuples from the raw BinkP frame bytes
    _send_cmd() wrote, using the same frame format binkp.py builds."""
    import struct
    out = []
    for frame in raw_frames:
        word = struct.unpack('>H', frame[0:2])[0]
        length = word & 0x7FFF
        payload = frame[2:2 + length]
        cmd = payload[0]
        text = payload[1:].decode('latin-1', errors='replace')
        out.append((cmd, text))
    return out


class BinkPServerSingleAdrFormTests(unittest.TestCase):
    def _run(self, network_rows):
        from anetbbs.echomail.binkp_server import _handle_connection, CMD_ADR
        from anetbbs.models import EchomailNetwork

        fake_rows = [_FakeNetworkRow(name, addr) for name, addr in network_rows]
        reader = _FakeReader()
        writer = _FakeWriter()

        # EchomailNetwork.query is a Flask-SQLAlchemy descriptor that
        # requires an active app context just to be READ -- unittest.mock
        # .patch.object() tries to read the "original" value up front to
        # save it for restoration, which raises with no app context
        # active. Plain class-attribute assignment shadows the inherited
        # descriptor without reading it first; del restores the
        # inherited descriptor behavior afterward.
        EchomailNetwork.query = _FakeQuery(fake_rows)
        try:
            asyncio.run(_handle_connection(reader, writer, '1:114/30', 'ANetBBS'))
        finally:
            del EchomailNetwork.query

        commands = _decode_sent_commands(writer.sent)
        return [text for cmd, text in commands if cmd == CMD_ADR]

    def test_single_network_sends_one_address_token(self):
        adr_sends = self._run([('fidonet', '1:114/30')])
        self.assertEqual(len(adr_sends), 1)
        tokens = adr_sends[0].split()
        self.assertEqual(len(tokens), 1,
                         f'expected one token, got: {adr_sends[0]!r}')
        self.assertEqual(tokens[0], '1:114/30@fidonet')

    def test_multiple_networks_send_one_token_each_not_duplicated(self):
        """A real hub legitimately has multiple DIFFERENT AKAs across
        networks -- that's fine and expected. What must NOT happen is
        each one appearing twice (qualified + bare)."""
        adr_sends = self._run([
            ('fidonet', '1:114/30'),
            ('fsxnet', '21:1/100'),
        ])
        self.assertEqual(len(adr_sends), 1)
        tokens = adr_sends[0].split()
        self.assertEqual(len(tokens), 2,
                         f'expected exactly one token per network, got: {adr_sends[0]!r}')
        self.assertEqual(set(tokens), {'1:114/30@fidonet', '21:1/100@fsxnet'})


if __name__ == '__main__':
    unittest.main()
