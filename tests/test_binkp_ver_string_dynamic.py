"""Regression test for the BinkP VER handshake line being hardcoded to
'ANetBBS/1.0a' regardless of the actual running version.

Live-caught: reviewing a real interop failure with a peer's binkd hub,
their logs showed our VER line as 'ANetBBS/1.0a binkp/1.1' even though
the reporting sysop's install was confirmed up to date on the latest
beta release (v1.0b2.58 at the time). The string was a literal,
never-updated constant in both binkp.py (client handshake) and
binkp_server.py (server handshake) -- it would have said "1.0a" on
every release since this code was first written, making it useless
for diagnosing which version a peer is actually running from their
own connection logs. Fixed by pulling from anetbbs.__version__.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class BinkPVerStringDynamicTests(unittest.TestCase):
    def test_client_handshake_ver_line_uses_real_version(self):
        from anetbbs.echomail.binkp import BinkPClient, CMD_OK
        from anetbbs import __version__ as real_version

        client = BinkPClient(host='x', port=1, our_address='1:114/30',
                             hub_address='1:114/0', password='secret')

        sent = []

        def _fake_send_cmd(cmd, text=''):
            sent.append((cmd, text))

        recv_calls = {'n': 0}

        def _fake_recv(*a, **kw):
            recv_calls['n'] += 1
            if recv_calls['n'] > 6:
                return (True, bytes([CMD_OK]) + b'')
            return (True, bytes([CMD_OK]) + b'stub')

        client._send_cmd = _fake_send_cmd
        client._recv_frame_logged = _fake_recv

        try:
            client._handshake()
        except Exception:
            pass  # only the outbound VER line matters for this test

        ver_lines = [text for cmd, text in sent if text.startswith('VER ')]
        self.assertEqual(len(ver_lines), 1)
        self.assertEqual(ver_lines[0], f'VER ANetBBS/{real_version} binkp/1.1')
        self.assertNotIn('1.0a', ver_lines[0])

    def test_server_handshake_ver_line_uses_real_version(self):
        import asyncio
        from anetbbs import __version__ as real_version
        from anetbbs.echomail import binkp_server

        sent = []

        async def _fake_send_cmd(writer, cmd, text='', transcript=None):
            sent.append(text)

        class _FakeWriter:
            def get_extra_info(self, key):
                return ('1.2.3.4', 12345)
            def write(self, data):
                pass
            async def drain(self):
                pass
            def close(self):
                pass
            async def wait_closed(self):
                pass

        class _FakeReader:
            async def read(self, n):
                return b''

        orig_send_cmd = binkp_server._send_cmd
        binkp_server._send_cmd = _fake_send_cmd
        try:
            asyncio.run(binkp_server._handle_connection(
                _FakeReader(), _FakeWriter(), '1:1/1', 'Test BBS'))
        except Exception:
            pass  # only the outbound VER line matters for this test
        finally:
            binkp_server._send_cmd = orig_send_cmd

        ver_lines = [t for t in sent if t.startswith('VER ')]
        self.assertEqual(len(ver_lines), 1)
        self.assertEqual(ver_lines[0], f'VER ANetBBS/{real_version} binkp/1.1')
        self.assertNotIn('1.0a', ver_lines[0])


if __name__ == '__main__':
    unittest.main()
