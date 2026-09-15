"""Regression test for a real gap found in a security/performance audit:
Flask's own default (no SEND_FILE_MAX_AGE_DEFAULT set) sends NO
Cache-Control header at all on /static/* responses. The documented
"production" topology fronts the app with nginx, which already caches
/static/ for 1 day via deploy/anetbbs-nginx.conf.template -- this
config setting is the fallback for a sysop running the Flask app's own
static serving directly (dev, or a non-nginx-fronted "behind" install).

Fixed by setting Config.SEND_FILE_MAX_AGE_DEFAULT = 86400 (1 day), the
same lifetime and reasoning as the nginx template's own /static/ block
(intentionally not long-lived/immutable, since asset filenames carry no
version hash).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class StaticAssetCacheControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.static_cache_control_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_config_sets_a_one_day_default(self):
        self.assertEqual(cfg_mod.Config.SEND_FILE_MAX_AGE_DEFAULT, 86400)

    def test_a_real_static_asset_response_carries_cache_control(self):
        client = self.app.test_client()
        resp = client.get('/static/css/darkforces.css')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Cache-Control', resp.headers)
        self.assertIn('max-age=86400', resp.headers['Cache-Control'])


if __name__ == '__main__':
    unittest.main()
