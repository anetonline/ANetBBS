"""Regression tests for the per-security-level daily file download quota
(FR: Firehawke, 2026-07-24) -- anetbbs/features/file_quota.py.

Covers: tier resolution (highest qualifying access level wins, not an
exact match), unlimited when no tier applies, admin bypass, the
check/consume split, the Eastern-day lazy-reset bucket, and the web
download route's actual enforcement (file_areas.py's download()).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class FileQuotaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_quota_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_user(self, username, access_level, is_admin=False):
        from anetbbs.models import db, User
        u = User(username=username, email=f'{username}@example.com',
                password_hash='x', is_admin=is_admin, access_level=access_level)
        db.session.add(u)
        db.session.commit()
        return u

    def test_highest_qualifying_tier_wins_not_exact_match(self):
        """Tiers at 50 and 100; a level-75 user must get the level-50
        tier (the highest one they qualify for), not the level-100 one
        and not 'no tier' just because there's no exact-75 row."""
        from anetbbs.models import db, FileQuotaTier
        from anetbbs.features.file_quota import resolve_quota_bytes

        with self.app.app_context():
            db.session.add_all([
                FileQuotaTier(min_access_level=50, daily_quota_bytes=100 * 1024 * 1024),
                FileQuotaTier(min_access_level=100, daily_quota_bytes=1000 * 1024 * 1024),
            ])
            db.session.commit()
            user = self._make_user('quotauser75', access_level=75)

            self.assertEqual(resolve_quota_bytes(user), 100 * 1024 * 1024)

    def test_no_qualifying_tier_is_unlimited(self):
        from anetbbs.models import db, FileQuotaTier
        from anetbbs.features.file_quota import resolve_quota_bytes

        with self.app.app_context():
            db.session.add(FileQuotaTier(min_access_level=61,
                                         daily_quota_bytes=100 * 1024 * 1024))
            db.session.commit()
            user = self._make_user('quotauser10', access_level=10)

            self.assertIsNone(resolve_quota_bytes(user))

    def test_admin_always_unlimited_regardless_of_tiers(self):
        from anetbbs.models import db, FileQuotaTier
        from anetbbs.features.file_quota import resolve_quota_bytes

        with self.app.app_context():
            db.session.add(FileQuotaTier(min_access_level=999,
                                         daily_quota_bytes=1))
            db.session.commit()
            admin = self._make_user('quotaadmin', access_level=1000, is_admin=True)

            self.assertIsNone(resolve_quota_bytes(admin))

    def test_check_quota_rejects_over_limit_and_reports_remaining(self):
        from anetbbs.models import db, FileQuotaTier
        from anetbbs.features.file_quota import check_quota, consume_quota

        with self.app.app_context():
            db.session.add(FileQuotaTier(min_access_level=12,
                                         daily_quota_bytes=1000))
            db.session.commit()
            user = self._make_user('quotacheck1', access_level=12)

            ok, msg = check_quota(user, 400)
            self.assertTrue(ok)
            consume_quota(user, 400)

            ok, msg = check_quota(user, 700)
            self.assertFalse(ok)
            self.assertIn('600', msg)   # 1000 - 400 = 600 remaining

    def test_check_quota_allows_when_unlimited(self):
        from anetbbs.features.file_quota import check_quota

        with self.app.app_context():
            user = self._make_user('quotaunlimited1', access_level=10)
            ok, msg = check_quota(user, 999_999_999_999)
            self.assertTrue(ok)
            self.assertEqual(msg, '')

    def test_consume_quota_accumulates_across_calls(self):
        from anetbbs.models import db, FileQuotaTier, FileQuotaUsage
        from anetbbs.features.file_quota import consume_quota

        with self.app.app_context():
            db.session.add(FileQuotaTier(min_access_level=13,
                                         daily_quota_bytes=10_000))
            db.session.commit()
            user = self._make_user('quotaaccum1', access_level=13)

            consume_quota(user, 100)
            consume_quota(user, 250)

            row = FileQuotaUsage.query.filter_by(user_id=user.id).first()
            self.assertEqual(row.bytes_used_today, 350)

    def test_admin_consume_quota_is_a_no_op(self):
        from anetbbs.models import db, FileQuotaUsage
        from anetbbs.features.file_quota import consume_quota

        with self.app.app_context():
            admin = self._make_user('quotaadminnoop', access_level=100, is_admin=True)
            consume_quota(admin, 500)
            self.assertIsNone(FileQuotaUsage.query.filter_by(user_id=admin.id).first())

    def test_day_rollover_resets_usage(self):
        """Real bug shape this guards against: without a lazy reset, a
        user's usage from a PREVIOUS day would silently carry over and
        permanently exhaust today's quota."""
        from anetbbs.models import db, FileQuotaTier, FileQuotaUsage
        from anetbbs.features.file_quota import check_quota, consume_quota

        with self.app.app_context():
            db.session.add(FileQuotaTier(min_access_level=14,
                                         daily_quota_bytes=1000))
            db.session.commit()
            user = self._make_user('quotarollover1', access_level=14)

            consume_quota(user, 900)
            row = FileQuotaUsage.query.filter_by(user_id=user.id).first()
            row.day = '2000-01-01'   # force a stale bucket
            db.session.commit()

            ok, msg = check_quota(user, 900)
            self.assertTrue(ok, 'a stale day bucket must reset to 0, not '
                              'carry over usage from a previous day')

    def test_web_download_blocked_over_quota_and_allowed_under(self):
        """End-to-end: web/file_areas.py's download() route actually
        enforces the quota, not just the helper module in isolation."""
        import tempfile
        from anetbbs.models import db, FileArea, FileQuotaTier

        with self.app.app_context():
            db.session.add(FileQuotaTier(min_access_level=15,
                                         daily_quota_bytes=50))
            user = self._make_user('quotaweb1', access_level=15)
            db.session.commit()

            storage_dir = tempfile.mkdtemp()
            with open(os.path.join(storage_dir, 'big.bin'), 'wb') as f:
                f.write(b'x' * 100)   # 100 bytes > 50-byte quota

            area = FileArea(tag='QUOTA.WEB', name='Quota Web Test',
                            is_active=True, storage_path=storage_dir)
            db.session.add(area)
            db.session.commit()
            area_id = area.id
            user_id = user.id

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True

        resp = client.get(f'/file-areas/{area_id}/big.bin', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'quota', resp.data.lower())

        with self.app.app_context():
            from anetbbs.models import FileQuotaUsage
            row = FileQuotaUsage.query.filter_by(user_id=user_id).first()
            used = row.bytes_used_today if row else 0
            self.assertEqual(used, 0,
                             'a rejected download must not be counted '
                             'against the quota (check_quota() may lazily '
                             'create the usage row, but must not consume it)')

    def test_web_download_allowed_when_under_quota(self):
        import tempfile
        from anetbbs.models import db, FileArea, FileQuotaTier

        with self.app.app_context():
            db.session.add(FileQuotaTier(min_access_level=16,
                                         daily_quota_bytes=1_000_000))
            user = self._make_user('quotaweb2', access_level=16)
            db.session.commit()

            storage_dir = tempfile.mkdtemp()
            with open(os.path.join(storage_dir, 'small.bin'), 'wb') as f:
                f.write(b'x' * 100)

            area = FileArea(tag='QUOTA.WEB2', name='Quota Web Test 2',
                            is_active=True, storage_path=storage_dir)
            db.session.add(area)
            db.session.commit()
            area_id = area.id
            user_id = user.id

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True

        resp = client.get(f'/file-areas/{area_id}/small.bin')
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            from anetbbs.models import FileQuotaUsage
            row = FileQuotaUsage.query.filter_by(user_id=user_id).first()
            self.assertIsNotNone(row)
            self.assertEqual(row.bytes_used_today, 100)


if __name__ == '__main__':
    unittest.main()
