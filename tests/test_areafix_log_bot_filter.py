"""Regression test: the AreaFix Log admin page silently commingled
FileFix (file-echo subscription) activity into a page titled and
described as AreaFix-only, with no visible label and no way to filter
by bot -- even though AreafixLog.bot exists specifically to distinguish
them (reused, not a separate FilefixLog table, per the model's own
docstring). Surfaced while auditing hub-management logging after a
sysop asked to "make sure we have logs for everything."
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AreafixLogBotFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.areafix_log_bot_filter_test.db')
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

    def _admin_client(self, username='areafixlog_admin'):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, is_admin=True,
                        email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    def _make_row(self, bot, from_address, request_type='subscribe'):
        from anetbbs.models import db, AreafixLog
        row = AreafixLog(from_address=from_address, request_type=request_type,
                         area_tags='TEST.AREA', response='ok', success=True,
                         bot=bot)
        db.session.add(row)
        db.session.commit()
        return row

    def test_both_bots_shown_with_labels_by_default(self):
        with self.app.app_context():
            self._make_row('areafix', '1:1/101')
            self._make_row('filefix', '1:1/102')
        client = self._admin_client('both_admin')
        r = client.get('/admin/echomail/areafix_log')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'AreaFix', r.data)
        self.assertIn(b'FileFix', r.data)
        self.assertIn(b'1:1/101', r.data)
        self.assertIn(b'1:1/102', r.data)

    def test_bot_filter_narrows_to_filefix_only(self):
        with self.app.app_context():
            self._make_row('areafix', '1:1/201')
            self._make_row('filefix', '1:1/202')
        client = self._admin_client('filter_admin')
        r = client.get('/admin/echomail/areafix_log?bot=filefix')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'1:1/202', r.data)
        self.assertNotIn(b'1:1/201', r.data)


if __name__ == '__main__':
    unittest.main()
