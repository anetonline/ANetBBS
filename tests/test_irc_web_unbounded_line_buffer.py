"""Regression test: _IrcSession.run() (anetbbs/web/irc_web.py) accumulated
incoming socket bytes into `buf` while waiting for a '\\r\\n' line
terminator, with no cap on how large `buf` could grow. A malicious or
just broken IRC server (or a MITM on the connection) that sends an
endless stream of bytes with no line terminator at all would make this
process buffer literally everything in RAM forever, per active web-IRC
session. Found in a security/performance audit.

Fixed with an explicit cap (8192 bytes, generous headroom over the
512-byte line limit RFC 2812/1459 actually specifies) -- once buf grows
past that with no terminator in sight, the session disconnects instead
of continuing to grow.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.web import irc_web


class _EndlessNoNewlineSocket:
    """Returns 4096 bytes of junk (no \\r\\n) on every recv() call,
    forever -- simulating a peer that never sends a line terminator."""
    def __init__(self):
        self.recv_calls = 0

    def recv(self, n):
        self.recv_calls += 1
        return b'X' * n

    def close(self):
        pass


class _NormalLinesSocket:
    """Baseline: sends two well-formed lines, then EOF."""
    def __init__(self):
        self._chunks = [b'PING :abc\r\n', b'PING :def\r\n', b'']
        self.recv_calls = 0

    def recv(self, n):
        self.recv_calls += 1
        if self._chunks:
            return self._chunks.pop(0)
        return b''

    def close(self):
        pass


def _make_session():
    return irc_web._IrcSession(
        sid='sid1', server='irc.example.com', port=6667, use_ssl=False,
        nick='tester', username='tester', realname='Tester')


class IrcWebUnboundedLineBufferTests(unittest.TestCase):
    def test_endless_data_with_no_terminator_disconnects_instead_of_growing_forever(self):
        session = _make_session()
        session.connected = True
        sock = _EndlessNoNewlineSocket()
        session.sock = sock

        emitted = []
        with patch.object(irc_web._IrcSession, '_emit',
                          lambda self, event, payload: emitted.append((event, payload))):
            session.run()

        self.assertFalse(session.connected)
        # The cap must kick in well before the socket is drained
        # "forever" -- 8192 / 4096 == 2, so at most a small handful of
        # recv() calls should have happened, not an unbounded number.
        self.assertLessEqual(sock.recv_calls, 4,
                             'run() must stop reading once the line-length '
                             'cap is exceeded, not keep buffering forever')
        disconnect_events = [p for e, p in emitted if e == 'irc_disconnected']
        self.assertEqual(len(disconnect_events), 1)
        self.assertIn('exceeded max length', disconnect_events[0]['reason'])

    def test_normal_lines_are_unaffected(self):
        session = _make_session()
        session.connected = True
        session.sock = _NormalLinesSocket()

        emitted = []
        with patch.object(irc_web._IrcSession, '_emit',
                          lambda self, event, payload: emitted.append((event, payload))), \
             patch.object(irc_web._IrcSession, '_handle_line',
                          lambda self, line: emitted.append(('line', line))):
            session.run()

        self.assertFalse(session.connected)
        lines = [p for e, p in emitted if e == 'line']
        self.assertEqual(lines, ['PING :abc', 'PING :def'])
        disconnect_events = [p for e, p in emitted if e == 'irc_disconnected']
        self.assertEqual(len(disconnect_events), 1)
        self.assertEqual(disconnect_events[0]['reason'], 'remote closed (EOF)')


if __name__ == '__main__':
    unittest.main()
