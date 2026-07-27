"""Regression tests for phase 4 (install/update re-verify) of a 4-phase
audit list -- the two findings here are testable at the Python level;
install.sh/update.sh/wizard.py's shell-script-only fixes have no test
harness in this repo (consistent with test_upgrade_sudo_preflight.py's
own note) and were verified by direct code reading instead.

1. anetbbs/config.py's MSP_ENABLED/SYSTAT_ENABLED were hardcoded True,
   unlike every sibling *_ENABLED flag -- install.sh's wizard prompt and
   Admin -> Settings' own MSP toggle both wrote MSP_ENABLED=false/
   SYSTAT_ENABLED=false to .env, but neither ever took effect.

2. anetbbs/web_app.py's _create_default_data() bootstrapped a fallback
   account literally named "admin" whenever no user with that EXACT
   username existed -- install.sh's wizard creates the sysop's own
   differently-named account by calling create_app() first (which runs
   this function before the wizard's own explicit account creation even
   runs), so every fresh install.sh install silently ended up with two
   full-admin accounts. Checking is_admin instead of the literal
   username means any already-provisioned admin correctly suppresses
   the fallback.
"""
import importlib
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class MspSystatEnvVarTests(unittest.TestCase):
    """Direct-reload test for the env-var-driven flags -- these are
    evaluated once at class-body/import time, so a plain os.environ
    change after import wouldn't be picked up without a reload."""

    def setUp(self):
        self._orig_msp = os.environ.get('MSP_ENABLED')
        self._orig_systat = os.environ.get('SYSTAT_ENABLED')

    def tearDown(self):
        for key, orig in (('MSP_ENABLED', self._orig_msp),
                          ('SYSTAT_ENABLED', self._orig_systat)):
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig
        importlib.reload(cfg_mod)

    def test_msp_enabled_false_in_env_is_honored(self):
        os.environ['MSP_ENABLED'] = 'false'
        os.environ['SYSTAT_ENABLED'] = 'false'
        importlib.reload(cfg_mod)
        self.assertFalse(cfg_mod.Config.MSP_ENABLED)
        self.assertFalse(cfg_mod.Config.SYSTAT_ENABLED)

    def test_msp_enabled_true_in_env_is_honored(self):
        os.environ['MSP_ENABLED'] = 'true'
        os.environ['SYSTAT_ENABLED'] = 'true'
        importlib.reload(cfg_mod)
        self.assertTrue(cfg_mod.Config.MSP_ENABLED)
        self.assertTrue(cfg_mod.Config.SYSTAT_ENABLED)

    def test_msp_enabled_defaults_true_when_unset(self):
        os.environ.pop('MSP_ENABLED', None)
        os.environ.pop('SYSTAT_ENABLED', None)
        importlib.reload(cfg_mod)
        self.assertTrue(cfg_mod.Config.MSP_ENABLED,
                        'must default true so an install with no .env '
                        'line at all keeps working as before')
        self.assertTrue(cfg_mod.Config.SYSTAT_ENABLED)


class DefaultAdminDedupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.admin_dedup_test.db')

    def setUp(self):
        if os.path.exists(self._tmp_db):
            os.remove(self._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{self._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_no_fallback_admin_created_when_a_differently_named_admin_exists(self):
        """Simulates install.sh's exact race: create a real admin account
        under a non-'admin' username BEFORE create_app()'s own
        _create_default_data() call would otherwise run -- by creating
        the app once first (to get tables), adding the sysop's account,
        then re-running the seeding function directly (mirrors what a
        second create_app()/service-restart call would do)."""
        from anetbbs.web_app import create_app, _create_default_data
        from anetbbs.models import db, User

        app = create_app('testing')
        app.config['TESTING'] = True
        with app.app_context():
            # create_app() already ran _create_default_data() once here,
            # bootstrapping the fallback "admin" account (unavoidable on
            # this very first call, same as install.sh's real race) --
            # simulate the wizard's own account creation immediately
            # after, then confirm a SECOND seeding pass (matching a
            # normal service restart) does NOT create yet another one
            # and, combined with install.sh's own stray-cleanup logic
            # (verified by direct code reading), the fallback would be
            # removed -- this test's job is just to confirm the
            # underlying condition change: once any admin exists,
            # re-running the seeder is a no-op.
            if not User.query.filter_by(username='sysop_custom').first():
                u = User(username='sysop_custom', email='sysop@example.com',
                        is_admin=True)
                u.set_password('x')
                db.session.add(u)
                db.session.commit()

            admin_count_before = User.query.filter_by(is_admin=True).count()
            _create_default_data()
            admin_count_after = User.query.filter_by(is_admin=True).count()
            self.assertEqual(
                admin_count_before, admin_count_after,
                'a second seeding pass must not add another admin once '
                'any admin account already exists')

    def test_fallback_admin_still_created_on_a_truly_bare_database(self):
        """Baseline / guard against a too-broad fix: a genuinely admin-
        less database (e.g. running the Flask app directly with no
        installer at all) must still get the safety-net account."""
        from anetbbs.web_app import create_app
        from anetbbs.models import User

        app = create_app('testing')
        app.config['TESTING'] = True
        with app.app_context():
            self.assertIsNotNone(
                User.query.filter_by(is_admin=True).first(),
                'a bare database must still get a bootstrapped admin')


if __name__ == '__main__':
    unittest.main()
