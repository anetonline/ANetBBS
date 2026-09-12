"""Regression test for a real N+1 query pattern in the SYSTAT/ActiveUser
UDP responder found in a security/performance audit.

anetbbs/msp/systat.py's _build_response() renders the "who's online"
listing this unauthenticated UDP responder sends to ANY peer that
queries it (see the rate-limit comments in that module -- this is a
deliberately reachable, lightly-protected socket). For every active
web `UserSession` row it accessed `s.user.username` -- `UserSession.user`
is a lazy=True relationship (models.py), so each access fired its own
extra SELECT against `users` instead of being folded into the original
query via a JOIN. Cost scaled with the number of concurrently-online
web users on every single SYSTAT query answered, not with a constant
number of queries.

Fixed by adding `.options(db.joinedload(UserSession.user))` to the
UserSession query in _build_response().

This test seeds several distinct users each with an active UserSession,
calls _build_response() directly, and captures the real SQL sent to the
DB via SQLAlchemy's before_cursor_execute hook (same technique as
test_tosser_bounded_catchup_query.py) to confirm the number of `users`
queries stays constant regardless of how many UserSession rows exist.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class SystatBuildResponseNPlusOneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.systat_nplus1_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def tearDown(self):
        # Tests share one class-level DB (see setUpClass) -- clean up
        # this test's own rows so a later test's "who's online" count
        # isn't inflated by a previous test's still-fresh sessions.
        from anetbbs.models import db, User, UserSession
        with self.app.app_context():
            ids = [u.id for u in User.query.filter(
                User.username.like('systat_np1_%')).all()]
            if ids:
                UserSession.query.filter(UserSession.user_id.in_(ids)).delete(
                    synchronize_session=False)
                User.query.filter(User.id.in_(ids)).delete(
                    synchronize_session=False)
                db.session.commit()

    def _seed_users_with_sessions(self, n, tag):
        from datetime import datetime
        from anetbbs.models import db, User, UserSession
        for i in range(n):
            uname = f'systat_np1_{tag}_{i}'
            u = User(username=uname, email=f'{uname}@example.com')
            u.set_password('x')
            db.session.add(u)
            db.session.flush()
            db.session.add(UserSession(
                user_id=u.id, session_key=f'sk_{tag}_{i}',
                last_seen=datetime.utcnow(), page='/boards/'))
        db.session.commit()

    def test_users_query_count_does_not_scale_with_session_count(self):
        """The actual regression guard: rendering the SYSTAT listing for
        many concurrently-online web users must not issue one `users`
        SELECT per session row."""
        from sqlalchemy import event
        from anetbbs.models import db
        from anetbbs.msp.systat import _build_response

        with self.app.app_context():
            self._seed_users_with_sessions(12, tag='count')

            captured = []

            def _capture(conn, cursor, statement, parameters, context, executemany):
                captured.append(statement)

            event.listen(db.engine, 'before_cursor_execute', _capture)
            try:
                text = _build_response(self.app)
            finally:
                event.remove(db.engine, 'before_cursor_execute', _capture)

            # Sanity: the listing actually rendered all 12 users.
            for i in range(12):
                self.assertIn(f'systat_np1_count_{i}', text)

            users_selects = [
                s for s in captured
                if 'SELECT' in s.upper() and 'FROM users' in s]
            self.assertLessEqual(
                len(users_selects), 2,
                'expected _build_response() to fetch usernames for all '
                'active UserSession rows via a JOIN (joinedload), not one '
                'extra SELECT against users PER row -- got '
                f'{len(users_selects)} separate `users` queries for 12 '
                'sessions:\n' + '\n'.join(users_selects))

    def test_all_online_users_still_rendered_correctly(self):
        """Functional correctness: joinedload must not change what's
        actually rendered, just how many queries it costs."""
        from anetbbs.msp.systat import _build_response

        with self.app.app_context():
            self._seed_users_with_sessions(3, tag='render')
            text = _build_response(self.app)

        self.assertIn('Total active: 3', text)
        for i in range(3):
            self.assertIn(f'systat_np1_render_{i}', text)


if __name__ == '__main__':
    unittest.main()
