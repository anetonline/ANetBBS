"""Regression test for the Inter-BBS IM inbox unread-row fix.

Before this fix, unread rows used Bootstrap's raw `table-warning`
class, re-skinned in base.html with a HARDCODED amber-on-brown color
pair (#332b00 / #ffe066) regardless of which of the site's themes is
active. Real report from Jerry (screenshot against the neon-magenta
"hackers" theme): the hardcoded brown patch clashed with the theme and
was hard to read. Fixed by switching to a theme-variable-driven
`.imsg-unread` class (var(--theme-primary)) plus an explicit unread
dot, so contrast holds under any theme instead of one hardcoded pair.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ImsgInboxUnreadTheming(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.imsg_inbox_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import User, InstantMessage, db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            user = User(username='imsguser', email='imsguser@example.com',
                       is_active=True)
            user.set_password('password12345')
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id
            db.session.add(InstantMessage(
                recipient_id=user.id, sender_label='StingRay',
                sender_host='192.168.1.152', body='unread test',
                is_read=False))
            db.session.add(InstantMessage(
                recipient_id=user.id, sender_label='OldFriend',
                sender_host='10.0.0.5', body='read test',
                is_read=True))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        return client

    def test_no_raw_bootstrap_table_warning_class(self):
        """The old hardcoded-brown class must not come back on the row
        itself. (base.html's shared stylesheet still DEFINES
        .table-warning for other pages that legitimately use it --
        this only checks the imsg row markup doesn't apply it.)"""
        resp = self._client().get('/imsg/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('class="table-warning"', resp.data.decode())

    def test_unread_row_gets_theme_driven_class_and_dot(self):
        body = self._client().get('/imsg/').data.decode()
        self.assertIn('imsg-unread', body)
        self.assertIn('imsg-unread-dot', body)

    def test_css_uses_theme_variable_not_hardcoded_color(self):
        """The unread styling must derive from --theme-primary so it
        adapts to whichever theme is active, not a fixed hex pair."""
        body = self._client().get('/imsg/').data.decode()
        self.assertIn('var(--theme-primary', body)


if __name__ == '__main__':
    unittest.main()
