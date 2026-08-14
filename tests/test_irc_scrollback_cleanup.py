"""Regression test for a real gap found in a security/performance
audit: anetbbs/web/irc_web.py's per-user `_scrollback` dict was never
cleaned up when the underlying IRC connection ended -- only an
explicit /clearscroll from the user ever removed an entry, so every
distinct user who ever connected got a permanent entry for the life
of the process (module-level dict, same leak shape as this project's
own v1.0.21 production incident).

`_scrollback` is deliberately keyed per-user (not per-sid) so it
survives a browser reconnect while the underlying IRC connection is
still alive (see the dict's own comment) -- the fix hooks into
_IrcSession.run()'s own finally block, the one place that already
knows the underlying connection has ended for good.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.web.irc_web import _IrcSession, _push_scroll, _scrollback, _sessions


class _FakeEOFSocket:
    """First recv() returns b'' (remote closed), matching a real
    IRC-server-side disconnect -- exercises run()'s "remote closed
    (EOF)" exit path and its finally block."""

    def recv(self, n):
        return b''

    def close(self):
        pass


class IrcScrollbackCleanupTests(unittest.TestCase):
    def setUp(self):
        _scrollback.clear()
        _sessions.clear()
        self.addCleanup(_scrollback.clear)
        self.addCleanup(_sessions.clear)

    def _make_session(self, sid, user_id):
        sess = _IrcSession(sid=sid, server='irc.example.com', port=6667,
                           use_ssl=False, nick='tester', username='tester',
                           realname='Tester', user_id=user_id)
        sess.sock = _FakeEOFSocket()
        sess.connected = True
        return sess

    def test_scrollback_entry_removed_when_irc_connection_ends(self):
        _push_scroll(42, 'irc_message', {'text': 'hello'})
        self.assertIn(42, _scrollback, 'sanity: entry should exist before the session ends')

        sess = self._make_session(sid='sid-a', user_id=42)
        with patch.object(_IrcSession, '_emit'):
            sess.run()

        self.assertNotIn(42, _scrollback,
                         'scrollback entry must be removed once the underlying '
                         'IRC connection ends, not require an explicit /clearscroll')

    def test_other_users_scrollback_is_not_disturbed(self):
        _push_scroll(42, 'irc_message', {'text': 'from 42'})
        _push_scroll(99, 'irc_message', {'text': 'from 99'})

        sess = self._make_session(sid='sid-a', user_id=42)
        with patch.object(_IrcSession, '_emit'):
            sess.run()

        self.assertNotIn(42, _scrollback)
        self.assertIn(99, _scrollback, 'an unrelated user\'s scrollback must survive')

    def test_session_removed_from_sessions_dict_too(self):
        sess = self._make_session(sid='sid-b', user_id=7)
        _sessions['sid-b'] = sess
        with patch.object(_IrcSession, '_emit'):
            sess.run()
        self.assertNotIn('sid-b', _sessions)


if __name__ == '__main__':
    unittest.main()
