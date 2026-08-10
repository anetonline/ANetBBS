"""Regression test for the real bug Jerry reported live: logged in via
both web and SSH simultaneously, "who's online" only showed the web
connection. Root cause: UserSession.user_id was unique=True, so a
second connection's presence write found and overwrote the first's row
instead of getting its own (anetbbs/core/presence.py::SessionPresence).
Covers the terminal side directly; test_who_online_web_multi_session.py
covers the web side (track_user_session()).

anetbbs.core.presence talks to the DB through its own module-level
SQLAlchemy engine/sessionmaker (bound once at import time via
DATABASE_URL/FLASK_ENV -- not the Flask app's engine), so tests patch
`anetbbs.core.presence._Session` to a sessionmaker bound to this test's
own app engine instead of fighting import-order/env-var timing.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class SessionPresenceMultiConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.presence_multi_conn_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            u = User(username='presencemultitest', email='pmt@example.com',
                    is_active=True)
            u.set_password('x')
            db.session.add(u)
            db.session.commit()
            cls.user_id = u.id

            from sqlalchemy.orm import sessionmaker
            cls._test_sessionmaker = sessionmaker(
                bind=db.engine, future=True, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_two_simultaneous_connections_each_get_their_own_row(self):
        from anetbbs.core.presence import SessionPresence
        from anetbbs.models import UserSession

        with patch('anetbbs.core.presence._Session', self._test_sessionmaker):
            web_like = SessionPresence(self.user_id, protocol='telnet', peer='1.2.3.4:1')
            SessionPresence(self.user_id, protocol='ssh', peer='1.2.3.4:2')

            with self.app.app_context():
                rows = UserSession.query.filter_by(user_id=self.user_id).all()
                self.assertEqual(len(rows), 2,
                                'two SessionPresence instances for the same '
                                'user_id must produce two rows, not collide '
                                'onto one')
                pages = {r.page for r in rows}
                self.assertEqual(pages, {'[telnet]', '[ssh]'},
                                'each row must show its own protocol')

            web_like.set_page('boards')
            with self.app.app_context():
                rows = UserSession.query.filter_by(user_id=self.user_id).order_by(
                    UserSession.id).all()
                self.assertEqual(rows[0].page, '[telnet] boards')
                self.assertEqual(rows[1].page, '[ssh]',
                                "updating one connection's page must not "
                                "touch the other connection's row")

    def test_disconnect_removes_only_its_own_row(self):
        from anetbbs.core.presence import SessionPresence
        from anetbbs.models import UserSession

        with patch('anetbbs.core.presence._Session', self._test_sessionmaker):
            first = SessionPresence(self.user_id, protocol='telnet', peer='')
            second = SessionPresence(self.user_id, protocol='rlogin', peer='')

            with self.app.app_context():
                self.assertEqual(
                    UserSession.query.filter_by(user_id=self.user_id).count(), 2)

            first.disconnect()

            with self.app.app_context():
                rows = UserSession.query.filter_by(user_id=self.user_id).all()
                self.assertEqual(len(rows), 1,
                                'disconnect() must delete only its own row')
                self.assertEqual(rows[0].page, '[rlogin]')

            second.disconnect()
            with self.app.app_context():
                self.assertEqual(
                    UserSession.query.filter_by(user_id=self.user_id).count(), 0)


if __name__ == '__main__':
    unittest.main()
