"""Regression test: anetbbs/core/rlogin_server.py's _read_rlogin_header()
looped `raw += chunk` indefinitely waiting for 3 NUL bytes, with no cap
on total accumulated size or iteration count -- a client that never
sends a NUL byte could force unbounded buffer growth (a per-connection
memory DoS) before IncompleteReadError/EOF ever fired.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.rlogin_server import _read_rlogin_header


class _EndlessNoNulReader:
    """Sends the initial NUL, then an endless stream of non-NUL bytes --
    never completes the required 3-NUL-terminated-string handshake."""
    def __init__(self):
        self._first = True

    async def readexactly(self, n):
        if self._first:
            self._first = False
            return b'\x00'
        raise AssertionError('readexactly should not be called again')

    async def read(self, n):
        return b'A' * n


class _WellFormedReader:
    def __init__(self):
        self._first = True
        self._payload = b'alice\x00bob\x00vt100/9600\x00'

    async def readexactly(self, n):
        if self._first:
            self._first = False
            return b'\x00'
        raise asyncio.IncompleteReadError(b'', n)

    async def read(self, n):
        if self._payload:
            chunk, self._payload = self._payload, b''
            return chunk
        return b''


class RloginHeaderSizeCapTests(unittest.TestCase):
    def test_endless_non_terminated_input_raises_instead_of_growing_forever(self):
        with self.assertRaises(ValueError) as cm:
            asyncio.run(_read_rlogin_header(_EndlessNoNulReader()))
        self.assertIn('exceeded', str(cm.exception))

    def test_well_formed_header_still_parses_correctly(self):
        header = asyncio.run(_read_rlogin_header(_WellFormedReader()))
        self.assertEqual(header['client_user'], 'alice')
        self.assertEqual(header['server_user'], 'bob')
        self.assertEqual(header['terminal_speed'], 'vt100/9600')


if __name__ == '__main__':
    unittest.main()
