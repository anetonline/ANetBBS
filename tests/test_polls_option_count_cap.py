"""Regression test for a real Low-severity finding from a security/
performance audit (2026-08-31): polls.py's new_poll() route had no cap
on how many options a poll could have -- creation is any-logged-in-
user, not admin-gated, so an unbounded options textarea let any user
create a poll with thousands of PollOption rows, bloating the DB and
making view_poll() render a proportionally huge page every time anyone
visits it.

Fixed with _MAX_POLL_OPTIONS (20), far more than any real poll would
ever need.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PollsOptionCountCapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.polls_option_cap_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_user(self, username):
        from anetbbs.models import db, User
        u = User(username=username, email=f'{username}@example.com',
                is_active=True)
        u.set_password('polloptioncaptestpass123')
        db.session.add(u)
        db.session.commit()
        return u.id

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_excessive_options_are_rejected_not_all_inserted(self):
        from anetbbs.models import Poll
        from anetbbs.web.polls import _MAX_POLL_OPTIONS
        with self.app.app_context():
            user_id = self._make_user('polloptioncapper')

        client = self._client_as(user_id)
        question = 'Too many choices in this specific poll?'
        options_text = '\n'.join(f'Option {i}' for i in range(_MAX_POLL_OPTIONS + 50))
        resp = client.post('/polls/new', data={
            'question': question,
            'description': '',
            'options': options_text,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            self.assertIsNone(
                Poll.query.filter_by(question=question).first(),
                'a poll exceeding the option cap must not be created at all')

    def test_normal_option_count_still_works(self):
        with self.app.app_context():
            user_id = self._make_user('pollnormalcreator')

        client = self._client_as(user_id)
        resp = client.post('/polls/new', data={
            'question': 'Best door game?',
            'description': '',
            'options': 'LORD\nTradeWars 2002\nUsurper',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            from anetbbs.models import Poll, PollOption
            poll = Poll.query.filter_by(question='Best door game?').first()
            self.assertIsNotNone(poll)
            self.assertEqual(
                PollOption.query.filter_by(poll_id=poll.id).count(), 3)

    def test_exactly_at_the_cap_still_works(self):
        from anetbbs.web.polls import _MAX_POLL_OPTIONS
        with self.app.app_context():
            user_id = self._make_user('pollcapexact')

        client = self._client_as(user_id)
        options_text = '\n'.join(f'Choice {i}' for i in range(_MAX_POLL_OPTIONS))
        resp = client.post('/polls/new', data={
            'question': 'Exactly at the cap',
            'description': '',
            'options': options_text,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            from anetbbs.models import Poll, PollOption
            poll = Poll.query.filter_by(question='Exactly at the cap').first()
            self.assertIsNotNone(poll)
            self.assertEqual(
                PollOption.query.filter_by(poll_id=poll.id).count(),
                _MAX_POLL_OPTIONS)


if __name__ == '__main__':
    unittest.main()
