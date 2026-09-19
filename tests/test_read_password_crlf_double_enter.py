"""Regression test: BBSSession.read_password() (anetbbs/core/session.py)
used to treat EITHER \\r or \\n as "Enter pressed" -- but never actually
consumed the paired byte a client sends alongside it, despite a comment
claiming it did. Most real telnet/SSH clients (PuTTY, plain `telnet`)
send \\r\\n for Enter; SyncTERM apparently sends a bare \\r, which is why
this only showed up for some clients.

Real live report: new-user registration's password confirmation step
("Confirm password:") always failed with "Passwords don't match" over
telnet/PuTTY, even when the exact same password was typed both times.
Root cause: the leftover \\n from the FIRST read_password() call's
"password\\r\\n" was still sitting unread when the SECOND read_password()
call ("Confirm password:") started -- that lone \\n byte immediately
matched the old `ch in (b'\\r', b'\\n')` terminator check, ending the
second read instantly with an empty string before the user had typed
anything, guaranteeing a mismatch against whatever they typed next
(which itself became garbage typed into the following prompt).

Fixed by matching read_line()'s own, already-correct convention: only
\\r ends input. A stray \\n then falls through to read_password()'s
existing `ch < b' '` control-byte filter and is silently ignored,
exactly how read_line() already tolerates the same leftover byte
between prompts.

Reuses the _FakeWriter/_QueueReader/_make_session fixture pattern from
test_read_password_telnet_iac_handling.py.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.session import BBSSession


class _FakeWriter:
    def __init__(self, peer=('1.2.3.4', 1234)):
        self._peer = peer
        self.written = bytearray()
        self._closing = False

    def get_extra_info(self, key):
        return self._peer if key == 'peername' else None

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    async def wait_closed(self):
        pass


class _QueueReader:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n=1):
        if self._chunks:
            return self._chunks.pop(0)
        return b''

    async def readexactly(self, n):
        if self._chunks:
            chunk = self._chunks.pop(0)
            if len(chunk) < n:
                raise asyncio.IncompleteReadError(chunk, n)
            return chunk
        raise asyncio.IncompleteReadError(b'', n)


def _make_session(reader_chunks, **kwargs):
    reader = _QueueReader(reader_chunks)
    writer = _FakeWriter()
    session = BBSSession(reader, writer, config={}, **kwargs)
    return session, writer


class ReadPasswordCrlfDoubleEnterTests(unittest.TestCase):

    def test_crlf_terminated_password_then_confirm_both_read_correctly(self):
        # Exactly the real new-user registration flow: two back-to-back
        # read_password() calls sharing ONE continuous byte stream,
        # each entry terminated with \r\n (PuTTY/plain telnet's real
        # Enter sequence) -- the actual bug was a leftover byte from
        # the first call's \r\n surviving to corrupt the second call.
        # \r and \n queued as separate single-byte chunks -- matching
        # real asyncio.StreamReader.read(1) semantics, which never
        # returns more than the requested byte count even when more is
        # already buffered (unlike this fake reader's "whole chunk at
        # once" shortcut, which is only realistic when each queued
        # chunk is itself a single byte).
        chunks = ([bytes([c]) for c in b'hunter2'] + [b'\r', b'\n']
                 + [bytes([c]) for c in b'hunter2'] + [b'\r', b'\n'])
        session, _writer = _make_session(chunks)
        first = asyncio.run(session.read_password("Choose password: "))
        second = asyncio.run(session.read_password("Confirm password: "))
        self.assertEqual(first, 'hunter2')
        self.assertEqual(second, 'hunter2')
        self.assertEqual(first, second)

    def test_leftover_lf_before_a_second_read_password_call_is_ignored(self):
        # Directly reproduces the failure mode: a lone \n (as if it
        # survived from a previous \r\n Enter) sitting at the front of
        # the stream when a NEW read_password() call starts. Before the
        # fix, this single byte alone would end the read instantly with
        # an empty string.
        chunks = [b'\n'] + [bytes([c]) for c in b'hunter2'] + [b'\r']
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_password())
        self.assertEqual(result, 'hunter2')

    def test_bare_cr_only_client_is_unaffected(self):
        # SyncTERM-style: bare \r, no paired \n at all -- must keep
        # working exactly as before.
        chunks = [bytes([c]) for c in b'hunter2'] + [b'\r']
        session, _writer = _make_session(chunks)
        result = asyncio.run(session.read_password())
        self.assertEqual(result, 'hunter2')


if __name__ == '__main__':
    unittest.main()
