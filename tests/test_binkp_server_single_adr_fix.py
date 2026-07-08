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
"""
import asyncio
import os
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
        import tempfile
        import anetbbs.config as cfg_mod
        tmp_db = tempfile.mktemp(suffix='.db')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, EchomailNetwork
        app = create_app('testing')
        with app.app_context():
            db.create_all()
            for name, addr in network_rows:
                db.session.add(EchomailNetwork(
                    name=name, network_type='binkp', our_address=addr,
                    is_active=True))
            db.session.commit()

        from anetbbs.echomail.binkp_server import _handle_connection, CMD_ADR
        reader = _FakeReader()
        writer = _FakeWriter()
        asyncio.run(_handle_connection(reader, writer, '1:114/30', 'ANetBBS'))

        os.remove(tmp_db)
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
