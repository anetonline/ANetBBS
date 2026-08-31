"""Regression test for a real Medium-severity finding from a security/
performance audit (2026-08-31): both anetbbs/web/web_terminal.py's
_TermSession.run() and anetbbs/web/irc_web.py's _IrcSession.run() ended
their read loop with a blind `_sessions.pop(self.sid, None)` in their
`finally` block. Two connect events racing for the same sid (a rapid
reconnect racing a slow-connecting prior session, or any other path
that lets two session objects briefly coexist for one sid) means
whichever session's read loop ends LAST evicts the dict entry
regardless of whether it's still the CURRENT session -- silently
orphaning a live, newer session from `_sessions`, so subsequent
`term_input`/`irc_send` events for that sid find nothing and the
session becomes unreachable even though its socket is still open.

Fixed by checking identity before popping: only remove the dict entry
if it still points at the session doing the cleanup.

Both classes exit their read loop immediately (straight to `finally`)
when `self.connected` is still False (the __init__ default, before
`.open()`/`.connect()` would set it True) -- so calling `.run()`
directly on a freshly-constructed, never-opened session reaches the
cleanup logic deterministically with no real network I/O needed.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TermSessionStaleCleanupTests(unittest.TestCase):
    def test_stale_session_finally_does_not_evict_a_newer_session(self):
        from anetbbs.web import web_terminal

        sid = 'shared-sid-1'
        old_sess = web_terminal._TermSession(sid, 'localhost', 2233)
        new_sess = web_terminal._TermSession(sid, 'localhost', 2233)
        web_terminal._sessions[sid] = new_sess
        self.addCleanup(web_terminal._sessions.pop, sid, None)

        with patch.object(web_terminal._TermSession, '_emit', lambda self, *a, **k: None):
            old_sess.run()  # connected=False -> exits straight to finally

        self.assertIs(
            web_terminal._sessions.get(sid), new_sess,
            "the stale session's cleanup must not evict the newer "
            'session that has since taken over the same sid')

    def test_own_session_finally_does_clean_up_normally(self):
        """Sanity check: the identity guard must not break the normal
        case where a session's own cleanup SHOULD remove its entry."""
        from anetbbs.web import web_terminal

        sid = 'shared-sid-2'
        sess = web_terminal._TermSession(sid, 'localhost', 2233)
        web_terminal._sessions[sid] = sess
        self.addCleanup(web_terminal._sessions.pop, sid, None)

        with patch.object(web_terminal._TermSession, '_emit', lambda self, *a, **k: None):
            sess.run()

        self.assertNotIn(sid, web_terminal._sessions)


class IrcSessionStaleCleanupTests(unittest.TestCase):
    def _make_session(self, sid):
        from anetbbs.web import irc_web
        return irc_web._IrcSession(sid, 'irc.example.org', 6667, False,
                                   'nick', 'user', 'Real Name')

    def test_stale_session_finally_does_not_evict_a_newer_session(self):
        from anetbbs.web import irc_web

        sid = 'shared-irc-sid-1'
        old_sess = self._make_session(sid)
        new_sess = self._make_session(sid)
        irc_web._sessions[sid] = new_sess
        self.addCleanup(irc_web._sessions.pop, sid, None)

        with patch.object(irc_web._IrcSession, '_emit', lambda self, *a, **k: None):
            old_sess.run()  # connected=False -> exits straight to finally

        self.assertIs(
            irc_web._sessions.get(sid), new_sess,
            "the stale session's cleanup must not evict the newer "
            'session that has since taken over the same sid')

    def test_own_session_finally_does_clean_up_normally(self):
        from anetbbs.web import irc_web

        sid = 'shared-irc-sid-2'
        sess = self._make_session(sid)
        irc_web._sessions[sid] = sess
        self.addCleanup(irc_web._sessions.pop, sid, None)

        with patch.object(irc_web._IrcSession, '_emit', lambda self, *a, **k: None):
            sess.run()

        self.assertNotIn(sid, irc_web._sessions)


if __name__ == '__main__':
    unittest.main()
