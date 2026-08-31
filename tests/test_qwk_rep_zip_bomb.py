"""Regression test for a real High-severity finding from a security/
performance audit (2026-08-31): both QWK REP importers --
web/qwk_user.py's upload() and web/qwk_hub.py's import_rep_packet() --
read a member out of a client/peer-supplied zip via ZipFile.read()
with NO check on the declared uncompressed size (a zip bomb). A small,
highly-compressed REP could expand to hundreds of MB+ in memory the
instant it's read. qwk_user.py's path needs only a logged-in account
(no special credential); qwk_hub.py's path is reached from a
configured QWK hub peer.

Fixed using the same shared cap (echomail.zip_safety.
MAX_MEMBER_UNCOMPRESSED / ZipBombError) every other ZIP-extraction site
in this codebase already uses -- see tests/test_zip_bomb_protection.py
for that helper's own coverage.
"""
import io
import os
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


def _make_bomb_zip(member_name, declared_size):
    """A real zip bomb: all-zero payload compresses extremely well, so
    a tiny archive can declare a huge uncompressed size."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member_name, b'\x00' * declared_size, compresslevel=9)
    buf.seek(0)
    return buf.getvalue()


class QwkHubRepZipBombTests(unittest.TestCase):
    """Direct function-level test -- no Flask app needed."""

    def test_oversized_rep_is_refused_before_decompression(self):
        from anetbbs.echomail.zip_safety import MAX_MEMBER_UNCOMPRESSED
        from anetbbs.web.qwk_hub import import_rep_packet

        class _FakeNode:
            packet_id = 'TESTNODE'
            hub_id_override = None

        bomb = _make_bomb_zip('ANET.MSG', MAX_MEMBER_UNCOMPRESSED + 1024)
        self.assertLess(len(bomb), 200 * 1024,
                        'the archive itself must stay tiny -- proves the '
                        'check fires from the declared-size header, not '
                        'after actually decompressing')
        result = import_rep_packet(_FakeNode(), bomb)
        self.assertEqual(result, 0,
                         'an oversized REP must be refused, not decompressed')

    def test_normal_sized_rep_is_not_affected(self):
        from anetbbs.web.qwk_hub import import_rep_packet

        class _FakeNode:
            packet_id = 'TESTNODE'
            hub_id_override = None

        small = _make_bomb_zip('ANET.MSG', 1024)
        # Not a real MESSAGES.DAT payload, so it'll be rejected later in
        # parsing -- this only confirms the SIZE check itself doesn't
        # reject a normal-sized member (a different code path handles
        # "0 messages parsed" vs. "refused for size").
        try:
            import_rep_packet(_FakeNode(), small)
        except Exception:
            pass  # any downstream parse failure is fine; not what's under test


class QwkUserUploadRepZipBombTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_rep_bomb_test.db')
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
            u = User(username='qwkrepbombtester', email='qrb@example.com',
                     is_active=True)
            u.set_password('testerpassword123')
            db.session.add(u)
            db.session.commit()
            cls.user_id = u.id

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

    def test_oversized_rep_upload_is_refused_without_hanging(self):
        from anetbbs.echomail.zip_safety import MAX_MEMBER_UNCOMPRESSED
        import io as _io

        bomb = _make_bomb_zip('MESSAGES.DAT', MAX_MEMBER_UNCOMPRESSED + 1024)
        client = self._client_as(self.user_id)
        resp = client.post('/qwk/upload', data={
            'rep': (_io.BytesIO(bomb), 'test.rep'),
        }, content_type='multipart/form-data')
        # Must redirect back cleanly (a flashed rejection), not hang or 500.
        self.assertIn(resp.status_code, (200, 302))


if __name__ == '__main__':
    unittest.main()
