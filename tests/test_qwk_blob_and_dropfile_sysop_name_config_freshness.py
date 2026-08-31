"""Regression test for a real Low-severity finding from a security/
performance audit (2026-08-31): several BBS_NAME/SYSOP_NAME reads used
`os.environ.get(...)` only, while the setup wizard
(web/admin.py's _setup_wizard_impl()) pushes a changed name straight
into `current_app.config` for immediate effect and only rewrites the
.env file on disk (which needs a process restart to change
os.environ). An env-only read would keep embedding the STALE name
until the next restart, while every other web/*.py call site
(registry.py, imsg.py, hub_admin.py, qwk_hub.py) already correctly
prefers the live config.

Fixed in two places:
  - anetbbs/web/qwk_user.py's _build_qwk_blob() (QWK packet header)
  - anetbbs/games/door_runner.py's launch_door_game() (the `sysop`
    value that feeds both %-token substitution and every dropfile
    format's sysop-name field)

door_runner.py's version wraps the current_app access in try/except
(it can genuinely run outside any Flask context in some launch paths),
falling back to the environment exactly as before when no app context
is active -- this test only covers the case where a context IS active,
since that's the only case the fix changes behavior for.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class QwkBlobSysopNameFreshnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.qwk_blob_sysop_test.db')
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

    def test_qwk_blob_uses_live_config_over_stale_environ(self):
        from anetbbs.models import db, User
        from anetbbs.web.qwk_user import _build_qwk_blob

        with self.app.app_context():
            old_environ_val = os.environ.get('SYSOP_NAME')
            old_environ_bbs = os.environ.get('BBS_NAME')
            try:
                os.environ['SYSOP_NAME'] = 'StaleEnvSysop'
                os.environ['BBS_NAME'] = 'StaleEnvBBS'
                self.app.config['SYSOP_NAME'] = 'FreshConfigSysop'
                self.app.config['BBS_NAME'] = 'FreshConfigBBS'

                u = User(username='qwkblobtester', email='qbt@example.com',
                        password_hash='x', access_level=10)
                db.session.add(u)
                db.session.commit()

                blob = _build_qwk_blob(u)
                # CONTROL.DAT is stored uncompressed inside the returned
                # zip blob; the sysop/BBS name appear as plain text lines.
                # _build_qwk_blob() returns an already-seekable BytesIO
                # buffer (fed straight into flask.send_file elsewhere),
                # not raw bytes.
                import zipfile
                blob.seek(0)
                zf = zipfile.ZipFile(blob)
                control = zf.read('CONTROL.DAT').decode('cp437', errors='replace')
                self.assertIn('FreshConfigBBS', control,
                             'QWK CONTROL.DAT must reflect the live config '
                             "BBS name, not a stale os.environ value")
                self.assertNotIn('StaleEnvBBS', control)
                self.assertIn('FreshConfigSysop', control,
                             'QWK CONTROL.DAT must reflect the live config '
                             "sysop name, not a stale os.environ value")
                self.assertNotIn('StaleEnvSysop', control)
            finally:
                if old_environ_val is None:
                    os.environ.pop('SYSOP_NAME', None)
                else:
                    os.environ['SYSOP_NAME'] = old_environ_val
                if old_environ_bbs is None:
                    os.environ.pop('BBS_NAME', None)
                else:
                    os.environ['BBS_NAME'] = old_environ_bbs
                self.app.config.pop('SYSOP_NAME', None)
                self.app.config.pop('BBS_NAME', None)


class DoorRunnerSysopNamePrefersConfigTests(unittest.TestCase):
    """door_runner.py's sysop-name resolution is inline inside
    launch_door_game(), not its own testable function -- verified via
    source inspection (the established pattern in this codebase for
    logic embedded in large, hard-to-isolate methods; see
    test_time_budget_enforcement.py)."""

    def test_launch_door_game_prefers_live_config_over_environ(self):
        import inspect
        from anetbbs.games import door_runner

        src = inspect.getsource(door_runner.launch_door_game)
        # Must reference current_app.config for SYSOP_NAME before
        # falling back to os.environ, matching the pattern already
        # used a bit further down in the same file for
        # BBS_SYSOP_NAME/Synchronet @SYSOP@ expansion.
        idx_config = src.find("_ca.config.get('SYSOP_NAME'")
        idx_sysop_assign = src.find("sysop = os.environ.get('SYSOP_NAME'")
        self.assertNotEqual(
            idx_config, -1,
            'launch_door_game() must resolve SYSOP_NAME from the live '
            'Flask app config (with an environ fallback), not read '
            'os.environ unconditionally')
        # The bare `sysop = os.environ.get(...)` assignment (no config
        # lookup at all) must no longer be the ONLY way `sysop` gets
        # set -- it's fine as a fallback inside the except/or branch,
        # but a config-aware resolution must come first in the source.
        self.assertLess(
            idx_config, idx_sysop_assign if idx_sysop_assign != -1 else float('inf'),
            'the config-aware SYSOP_NAME lookup must appear before any '
            'unconditional os.environ-only fallback assignment')


if __name__ == '__main__':
    unittest.main()
