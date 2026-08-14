"""Regression test: _IRC.read_loop() (anetbbs/features/anetirc2.py)
accumulated incoming socket bytes into self._rbuf while waiting for a
'\\n' line terminator, with no cap on how large it could grow. Users
point this client at whatever IRC server they choose, so a malicious
or just broken server (or a MITM on the connection) sending an endless
stream of bytes with no line terminator at all is genuinely
attacker-reachable, and would make this buffer unboundedly. Found in a
security/performance audit.

Fixed with an explicit cap (8192 bytes, generous headroom over the
512-byte line limit RFC 2812/1459 actually specifies) -- once _rbuf
grows past that with no terminator in sight, read_loop disconnects
instead of continuing to grow, mirroring the same fix applied to
anetbbs/web/irc_web.py's own read loop.

Reuses the _FakeClient/_FakeReader fixture pattern from
test_anetirc_ctcp_and_tabcomplete.py.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeClient:
    def __init__(self):
        self.lines = []
        self.sys_lines = []
        self.dirty_status = False
        self.dirty_users = False

    def _add(self, line):
        self.lines.append(line)

    def _sys(self, text):
        self.sys_lines.append(text)


class _EndlessNoNewlineReader:
    """Returns 4096 bytes of junk (no '\\n') on every read() call,
    forever -- simulating a peer that never sends a line terminator."""
    def __init__(self):
        self.read_calls = 0

    async def read(self, n):
        self.read_calls += 1
        return b'X' * n


class ReadBufferCapTests(unittest.IsolatedAsyncioTestCase):
    async def test_endless_data_with_no_terminator_disconnects_instead_of_growing_forever(self):
        from anetbbs.features.anetirc2 import _IRC
        client = _FakeClient()
        irc = _IRC(client)
        irc.nick = 'ourself'
        irc.connected = True
        reader = _EndlessNoNewlineReader()
        irc.reader = reader

        await asyncio.wait_for(irc.read_loop(), timeout=5)

        self.assertFalse(irc.connected,
                         'read_loop must disconnect once the line-length '
                         'cap is exceeded, not keep buffering forever')
        # 8192 / 4096 == 2, so only a small handful of read() calls
        # should have happened before the cap kicked in.
        self.assertLessEqual(reader.read_calls, 4)
        self.assertTrue(
            any('exceeded max length' in s for s in client.sys_lines),
            f'expected a visible disconnect notice, got: {client.sys_lines!r}')

    async def test_normal_lines_are_unaffected(self):
        from anetbbs.features.anetirc2 import _IRC
        client = _FakeClient()
        irc = _IRC(client)
        irc.nick = 'ourself'
        irc.connected = True

        class _NormalReader:
            def __init__(self):
                self._chunks = [
                    b':other!u@h PRIVMSG #test :hello\r\n',
                    b'',
                ]

            async def read(self, n):
                if self._chunks:
                    return self._chunks.pop(0)
                return b''

        irc.reader = _NormalReader()
        await asyncio.wait_for(irc.read_loop(), timeout=5)

        self.assertEqual(len(client.lines), 1)
        self.assertEqual(client.lines[0].text, 'hello')


if __name__ == '__main__':
    unittest.main()
