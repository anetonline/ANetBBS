"""Regression tests for three real bugs found in a deep review of
anetbbs/features/anetirc2.py, prompted by a live bug report about the
key parser (see test_anetirc_key_parser.py for that fix).

1. CTCP handling (_IRC._handle, PRIVMSG branch): only CTCP ACTION was
   ever recognized. Any OTHER CTCP request (VERSION, PING, ...) fell
   into the plain-chat display branch, where _strip() -- the mIRC-color
   stripper -- ALSO matches the generic "\\x01...\\x01" CTCP framing as
   one of its patterns and deletes it entirely, so the user saw a blank
   ghost line from that nick and the requester got no reply at all.
   Real IRC etiquette expects a VERSION/PING reply; many bots/clients
   flag a nick that never answers. Fixed: VERSION and PING now get a
   real CTCP NOTICE reply and are not displayed in chat; any other CTCP
   verb is silently ignored (not displayed either) instead of leaking a
   blank line.

2. Tab-complete cycling (ANetIRC._tab_complete): re-derived the partial
   from the buffer on every call, so a second consecutive Tab press
   (buffer now holding the previous completion's output) found no
   matches and did nothing -- repeated Tab could never actually cycle
   to a different candidate.

3. Read-loop robustness (_IRC.read_loop): a single malformed server
   line could raise inside _handle() with no guard at all. Since
   read_loop runs as a detached background task, this silently killed
   it with zero user-visible error -- chat would just stop receiving
   anything forever, indistinguishable from a hung connection.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeWriter:
    def __init__(self):
        self.sent = []
        self._closing = False

    def write(self, data):
        self.sent.append(data.decode('utf-8', errors='replace'))

    async def drain(self):
        pass

    def is_closing(self):
        return self._closing


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


class CtcpHandlingTests(unittest.IsolatedAsyncioTestCase):
    def _irc(self):
        from anetbbs.features.anetirc2 import _IRC
        client = _FakeClient()
        irc = _IRC(client)
        irc.nick = 'ourself'
        irc.channel = '#test'
        irc.writer = _FakeWriter()
        return irc, client

    async def test_ctcp_action_still_displays_as_before(self):
        irc, client = self._irc()
        await irc._handle(':other!u@h PRIVMSG #test :\x01ACTION waves\x01')
        self.assertEqual(len(client.lines), 1)
        self.assertIn('waves', client.lines[0].text)
        self.assertEqual(irc.writer.sent, [], 'ACTION must not trigger a reply')

    async def test_ctcp_version_replies_and_does_not_appear_in_chat(self):
        irc, client = self._irc()
        await irc._handle(':other!u@h PRIVMSG #test :\x01VERSION\x01')
        self.assertEqual(client.lines, [],
                         'a CTCP VERSION request must not show as a chat line')
        self.assertEqual(len(irc.writer.sent), 1)
        sent = irc.writer.sent[0]
        self.assertIn('NOTICE other', sent)
        self.assertIn('\x01VERSION', sent)

    async def test_ctcp_ping_echoes_argument_back(self):
        irc, client = self._irc()
        await irc._handle(':other!u@h PRIVMSG #test :\x01PING 1234567890\x01')
        self.assertEqual(client.lines, [])
        self.assertEqual(len(irc.writer.sent), 1)
        sent = irc.writer.sent[0]
        self.assertIn('NOTICE other', sent)
        self.assertIn('\x01PING 1234567890\x01', sent)

    async def test_unknown_ctcp_verb_silently_ignored_not_leaked_as_blank_line(self):
        irc, client = self._irc()
        await irc._handle(':other!u@h PRIVMSG #test :\x01CLIENTINFO\x01')
        self.assertEqual(client.lines, [],
                         'an unhandled CTCP verb must not leak a blank chat line')
        self.assertEqual(irc.writer.sent, [],
                         'an unhandled CTCP verb must not get a made-up reply')

    async def test_normal_privmsg_unaffected(self):
        irc, client = self._irc()
        await irc._handle(':other!u@h PRIVMSG #test :hello there')
        self.assertEqual(len(client.lines), 1)
        self.assertEqual(client.lines[0].text, 'hello there')
        self.assertEqual(client.lines[0].nick, 'other')


class _FakeReader:
    """Feeds a scripted sequence of chunks, then blocks forever (matching
    a real idle socket) so read_loop's own timeout/cancellation governs
    when the test ends, rather than the reader silently returning EOF
    and letting the loop exit "successfully" without proving anything."""
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n):
        if self._chunks:
            return self._chunks.pop(0)
        await asyncio.Event().wait()  # never resolves


class ReadLoopRobustnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_line_does_not_kill_the_read_loop(self):
        """Regression test for a real robustness gap found in a deep
        review: _handle() can raise (e.g. a ':'-prefixed line with no
        following space raises ValueError from raw.index(' ')) and the
        per-line call in read_loop() was completely unguarded. Since
        read_loop runs as a detached background task, an uncaught
        exception here used to silently kill it with zero user-visible
        error -- chat would just stop receiving anything forever,
        indistinguishable from a hung connection. Now it must log a
        visible error and keep processing subsequent lines."""
        from anetbbs.features.anetirc2 import _IRC
        client = _FakeClient()
        irc = _IRC(client)
        irc.nick = 'ourself'
        irc.connected = True
        # A malformed line (':' with no following space -- raises
        # ValueError in the old, unguarded code) immediately followed
        # by a well-formed one in the SAME read_loop run.
        irc.reader = _FakeReader([
            b':nospacehere\r\n',
            b':other!u@h PRIVMSG #test :still works\r\n',
        ])

        task = asyncio.ensure_future(irc.read_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        self.assertTrue(irc.connected,
                        'a malformed line must not tear down the connection')
        self.assertTrue(
            any('error' in s.lower() for s in client.sys_lines),
            f'expected a visible error notice, got: {client.sys_lines!r}')
        self.assertEqual(len(client.lines), 1,
                         'the well-formed line after the bad one must still '
                         'be processed -- the loop must not have died')
        self.assertEqual(client.lines[0].text, 'still works')


class TabCompleteCyclingTests(unittest.TestCase):
    def _client(self, users):
        from anetbbs.features.anetirc2 import ANetIRC
        import tempfile, os
        tmpdir = tempfile.mkdtemp()
        client = ANetIRC(session=None, cfg_path=os.path.join(tmpdir, 'x.cfg'))
        client.irc.users = list(users)
        return client

    def test_first_tab_after_typing_lands_on_first_match(self):
        client = self._client(['Alice', 'Alan', 'Bob'])
        client._inp = 'al'
        client._cur = 2
        client._tab_complete()
        self.assertTrue(client._inp.lower().startswith('alice'),
                        f'first tab-complete should hit the first match, got {client._inp!r}')

    def test_repeated_tab_cycles_through_matches(self):
        """The actual regression: the OLD implementation re-derived the
        partial from the buffer on every call, so a second consecutive
        Tab press (buffer now holding the first completion's output,
        e.g. "Alice: ") found no matches and silently did nothing --
        repeated Tab could never reach a different candidate no matter
        how many users shared that prefix."""
        client = self._client(['Alice', 'Alan'])
        client._inp = 'al'; client._cur = 2
        client._tab_complete()
        first = client._inp
        # Simulate pressing Tab again immediately (no other key in
        # between, cursor right after the just-inserted text) -- this
        # is what a real second consecutive Tab press looks like.
        client._cur = len(client._inp)
        client._tab_complete()
        second = client._inp
        self.assertNotEqual(first, second,
                            'a second consecutive Tab press must advance to the next match')
        # A third press should cycle back to the first match again
        # (only 2 candidates).
        client._cur = len(client._inp)
        client._tab_complete()
        self.assertEqual(client._inp, first,
                         'cycling through all matches should wrap back to the first')

    def test_new_partial_after_a_previous_tab_session_starts_fresh(self):
        """Tab-complete 'al' to its 2nd match, then start typing a
        completely different partial -- the very next Tab press must
        land on ITS first match (Bob, not Bill -- user-list order),
        not continue cycling from the old session's index."""
        client = self._client(['Alice', 'Alan', 'Bob', 'Bill'])
        client._inp = 'al'; client._cur = 2
        client._tab_complete()
        client._cur = len(client._inp)
        client._tab_complete()  # now on the 2nd 'al*' match

        # Simulate the real key-handling path: any non-TAB keypress
        # ends the tab-complete cycle (the actual fix), exercised via
        # the real _chat_key dispatcher rather than poking state
        # directly. Clear the input first so this behaves like the
        # user erasing the completed name and starting a fresh word.
        import asyncio
        client._inp = ''; client._cur = 0
        asyncio.run(client._chat_key('b'))
        self.assertEqual(client._inp, 'b')
        self.assertIsNone(client._tab_state,
                          'typing a new partial must end the previous tab-complete cycle')

        client._cur = len(client._inp)
        client._tab_complete()
        self.assertTrue(client._inp.lower().startswith('bob'),
                        f'expected the first b* match (Bob), got {client._inp!r}')


if __name__ == '__main__':
    unittest.main()
