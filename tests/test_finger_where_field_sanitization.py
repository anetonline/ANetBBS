"""Regression test for anetbbs.core.finger_server._online_users()'s
'where' field -- real gap found in a security/performance audit,
follow-up to an earlier fix in the same file (_handle()'s per-user
profile fields: display_name/location/tagline/bio) that already
applies strip_untrusted_escapes() before displaying attacker-settable
text to whoever fingers a user over TCP/79 (no local account needed by
the viewer). 'where' (UserSession.page, shown in the "currently
online" listing) was missed -- while mostly built from sysop-
configured menu labels, at least one path (mrc_chat.py's
"chat:mrc #<room>" presence) can carry a room name a user typed
themselves, so it's sanitized the same way now for consistency.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class FingerWhereFieldSanitizationTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'finger_where.db'))

    def test_ansi_escape_in_page_field_is_stripped(self):
        from anetbbs.core.finger_server import _online_users
        from anetbbs.models import db, User, UserSession
        with self.app.app_context():
            u = User(username='fingertest', email='fingertest@example.com',
                    password_hash='x')
            db.session.add(u)
            db.session.commit()
            db.session.add(UserSession(
                user_id=u.id, session_key='sess1',
                page='chat:mrc #\x1b[2Jevilroom',
                last_seen=datetime.utcnow()))
            db.session.commit()

            users = _online_users()
            self.assertEqual(len(users), 1)
            self.assertNotIn('\x1b', users[0]['where'])
            self.assertIn('evilroom', users[0]['where'])
            self.assertIn('chat:mrc', users[0]['where'])

    def test_ordinary_page_value_is_unaffected(self):
        from anetbbs.core.finger_server import _online_users
        from anetbbs.models import db, User, UserSession
        with self.app.app_context():
            u = User(username='fingertest2', email='fingertest2@example.com',
                    password_hash='x')
            db.session.add(u)
            db.session.commit()
            db.session.add(UserSession(
                user_id=u.id, session_key='sess2', page='main',
                last_seen=datetime.utcnow()))
            db.session.commit()

            users = _online_users()
            self.assertEqual(users[0]['where'], 'main')

    def test_offline_users_are_excluded(self):
        from anetbbs.core.finger_server import _online_users
        from anetbbs.models import db, User, UserSession
        with self.app.app_context():
            u = User(username='fingertest3', email='fingertest3@example.com',
                    password_hash='x')
            db.session.add(u)
            db.session.commit()
            db.session.add(UserSession(
                user_id=u.id, session_key='sess3', page='games',
                last_seen=datetime.utcnow() - timedelta(minutes=30)))
            db.session.commit()

            self.assertEqual(_online_users(), [])


if __name__ == '__main__':
    unittest.main()
