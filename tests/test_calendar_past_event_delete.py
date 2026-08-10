"""Regression test: the calendar index page's delete button only
rendered for events in the "Upcoming" list -- the "Recent past events"
block rendered no delete/admin controls at all, so a sysop viewing the
calendar couldn't clean up old entries from that page (calendar.delete
itself already handled any event id fine; the template just never
offered the control for a past one).
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class CalendarPastEventDeleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.calendar_past_delete_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='calpastadmin', email='cpa@example.com',
                        is_admin=True, is_active=True)
            admin.set_password('adminpassword123')
            db.session.add(admin)
            db.session.commit()
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_past_event_shows_a_delete_control_for_a_sysop(self):
        from anetbbs.models import db, CalendarEvent
        with self.app.app_context():
            ev = CalendarEvent(title='Old Meetup', is_published=True,
                               starts_at=datetime.utcnow() - timedelta(days=5),
                               created_by_id=self.admin_id)
            db.session.add(ev)
            db.session.commit()
            ev_id = ev.id

        client = self._client_as(self.admin_id)
        resp = client.get('/calendar/')
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode()
        self.assertIn('Old Meetup', body)
        self.assertIn(f'/calendar/{ev_id}/delete', body,
                      'a sysop viewing a PAST event must see a delete '
                      'action, not just for upcoming events')

    def test_sysop_can_actually_delete_a_past_event(self):
        from anetbbs.models import db, CalendarEvent
        with self.app.app_context():
            ev = CalendarEvent(title='Retire Me', is_published=True,
                               starts_at=datetime.utcnow() - timedelta(days=10),
                               created_by_id=self.admin_id)
            db.session.add(ev)
            db.session.commit()
            ev_id = ev.id

        client = self._client_as(self.admin_id)
        resp = client.post(f'/calendar/{ev_id}/delete', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            self.assertIsNone(db.session.get(CalendarEvent, ev_id))


if __name__ == '__main__':
    unittest.main()
