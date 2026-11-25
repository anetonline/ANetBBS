"""H-3: a user upload must never replace or delete someone else's file.

Before the fix, both web upload routes saved straight to
<storage_path>/<name> (overwriting an existing file), and a virus/archive
rejection then os.remove()d that path -- deleting the original. Over FTP,
non-admin sessions could STOR over, DELE or RNFR any area file.
"""
import io
import os
import tempfile
import unittest
from unittest import mock

import anetbbs.config as cfg_mod


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp = tempfile.mkdtemp(prefix='h3-')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp}/t.db'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, FileArea
        cls.app = create_app('testing')
        cls.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False,
                              FILE_MOD_QUEUE_ENABLED=False)
        cls.store = os.path.join(cls._tmp, 'area')
        os.makedirs(cls.store)
        with cls.app.app_context():
            db.create_all()
            u = User(username='h3user', email='h3@example.test', is_verified=True,
                     is_active=True, access_level=10)
            u.set_password('x-Passw0rd-x')
            a = User(username='h3sysop', email='h3s@example.test', is_verified=True,
                     is_active=True, is_admin=True, access_level=255)
            a.set_password('x-Passw0rd-x')
            db.session.add_all([u, a])
            area = FileArea(tag='H3GEN', name='General', storage_path=cls.store,
                            upload_permission='users', is_active=True)
            db.session.add(area)
            db.session.commit()
            cls.uid, cls.aid_user, cls.area_id = u.id, a.id, area.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_uri

    def setUp(self):
        for n in os.listdir(self.store):
            os.remove(os.path.join(self.store, n))
        with open(os.path.join(self.store, 'README.TXT'), 'wb') as f:
            f.write(b'ORIGINAL')

    def _client(self, uid):
        c = self.app.test_client()
        with self.app.app_context():
            from anetbbs.models import User
            ident = User.query.get(uid).get_id()
        with c.session_transaction() as s:
            s['_user_id'] = ident
            s['_fresh'] = True
        return c

    def _original(self):
        with open(os.path.join(self.store, 'README.TXT'), 'rb') as f:
            return f.read()

    def _leftovers(self):
        return [n for n in os.listdir(self.store) if n.startswith('.upload-')]


class WebUploadNoClobber(_Base):
    def _upload(self, uid, route, name='README.TXT', data=b'TROJAN'):
        payload = {'file': (io.BytesIO(data), name)}
        if route == 'smart':
            payload['area_id'] = str(self.area_id)
            return self._client(uid).post('/file-areas/smart-upload', data=payload,
                                          content_type='multipart/form-data')
        return self._client(uid).post(f'/file-areas/{self.area_id}/upload', data=payload,
                                      content_type='multipart/form-data')

    def test_same_name_upload_does_not_replace(self):
        for route in ('area', 'smart'):
            with self.subTest(route=route):
                self._upload(self.uid, route)
                self.assertEqual(self._original(), b'ORIGINAL')
                self.assertEqual(self._leftovers(), [])

    def test_rejected_upload_does_not_delete_original(self):
        infected = mock.Mock(infected=True, signature='EICAR')
        for route in ('area', 'smart'):
            with self.subTest(route=route), \
                    mock.patch('anetbbs.features.virus_scan.scan_path', return_value=infected):
                self._upload(self.uid, route)
                self.assertEqual(self._original(), b'ORIGINAL')
                self.assertEqual(self._leftovers(), [])

    def test_rejected_archive_does_not_delete_original(self):
        bad = mock.Mock(ok=False, message='corrupt')
        with mock.patch('anetbbs.features.archive_meta.test_archive_integrity',
                        return_value=bad) as m:
            self._upload(self.uid, 'area')
        self.assertEqual(self._original(), b'ORIGINAL')
        # The check ran against a staged file that keeps the real name as
        # its suffix (format detection is extension-based).
        self.assertTrue(m.call_args[0][0].endswith('-README.TXT'), m.call_args)

    def test_new_name_upload_still_works(self):
        self._upload(self.uid, 'area', name='NEW.TXT', data=b'hello')
        with open(os.path.join(self.store, 'NEW.TXT'), 'rb') as f:
            self.assertEqual(f.read(), b'hello')
        self.assertEqual(self._leftovers(), [])

    def test_admin_may_replace(self):
        self._upload(self.aid_user, 'area', data=b'SYSOP-NEW')
        self.assertEqual(self._original(), b'SYSOP-NEW')


class FtpNoClobber(_Base):
    def _handler(self, user_id):
        from anetbbs.ftp.server import AnetbbsFTPHandler
        h = AnetbbsFTPHandler.__new__(AnetbbsFTPHandler)
        h.app = self.app
        h.username = 'whatever'
        h._qwk_node_id = None
        h._bbs_user_id = user_id
        h.responses = []
        h.respond = h.responses.append
        h._area_write_allowed = lambda path: True
        return h

    def test_non_admin_cannot_overwrite_delete_or_rename(self):
        h = self._handler(self.uid)
        target = os.path.join(self.store, 'README.TXT')
        with mock.patch('pyftpdlib.handlers.FTPHandler.ftp_STOR') as stor, \
                mock.patch('pyftpdlib.handlers.FTPHandler.ftp_DELE') as dele, \
                mock.patch('pyftpdlib.handlers.FTPHandler.ftp_RNFR') as rnfr, \
                mock.patch('pyftpdlib.handlers.FTPHandler.ftp_RMD') as rmd:
            h.ftp_STOR(target)
            h.ftp_STOR(target, mode='a')
            h.ftp_DELE(target)
            h.ftp_RNFR(target)
            h.ftp_RMD(self.store)
        for m in (stor, dele, rnfr, rmd):
            self.assertFalse(m.called)
        self.assertEqual(len(h.responses), 5)
        self.assertTrue(all(r.startswith('550') for r in h.responses), h.responses)

    def test_non_admin_can_store_new_file(self):
        h = self._handler(self.uid)
        with mock.patch('pyftpdlib.handlers.FTPHandler.ftp_STOR') as stor:
            h.ftp_STOR(os.path.join(self.store, 'NEW.ZIP'))
        self.assertTrue(stor.called)

    def test_admin_keeps_full_control(self):
        h = self._handler(self.aid_user)
        target = os.path.join(self.store, 'README.TXT')
        with mock.patch('pyftpdlib.handlers.FTPHandler.ftp_STOR') as stor, \
                mock.patch('pyftpdlib.handlers.FTPHandler.ftp_DELE') as dele:
            h.ftp_STOR(target)
            h.ftp_DELE(target)
        self.assertTrue(stor.called and dele.called)

    def test_demoted_admin_loses_control_mid_session(self):
        from anetbbs.models import db, User
        h = self._handler(self.aid_user)
        with self.app.app_context():
            u = db.session.get(User, self.aid_user)
            u.is_admin = False
            db.session.commit()
        try:
            with mock.patch('pyftpdlib.handlers.FTPHandler.ftp_DELE') as dele:
                h.ftp_DELE(os.path.join(self.store, 'README.TXT'))
            self.assertFalse(dele.called)
        finally:
            with self.app.app_context():
                u = db.session.get(User, self.aid_user)
                u.is_admin = True
                db.session.commit()


if __name__ == '__main__':
    unittest.main()
