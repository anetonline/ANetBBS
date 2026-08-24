"""Regression tests for two real gaps found in a docs-freshness audit
and a follow-up code check:

1. FILE_MOD_QUEUE_ENABLED was documented (docs/07-file-areas.md,
   docs/11-spam-control.md) as a .env setting a sysop could turn on to
   require approval of uploads, but anetbbs/config.py's Config class
   never defined it at all -- file_areas.py only ever read it via
   current_app.config.get('FILE_MOD_QUEUE_ENABLED', False), which is
   always False unless something else sets the key directly (as the
   existing moderation-queue tests do, bypassing the real Config-load
   path entirely). Setting it in .env and restarting did nothing.

2. Once wired into Config as a real boolean, a second gotcha applies:
   FILE_MOD_QUEUE_ENABLED has restart_flag=False in admin.py's
   EDITABLE_SETTINGS, meaning the admin Settings page live-updates
   current_app.config[key] with the raw submitted STRING ('true' or
   'false') rather than requiring a restart -- and bool('false') is
   True in Python. file_areas.py's two read sites now parse via
   str(...).lower() == 'true' instead of bool(...) to handle both that
   string form and the real bool Config sets at boot.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)


class FileModQueueConfigDefaultTests(unittest.TestCase):
    def test_default_config_defines_the_key_and_it_is_false(self):
        import anetbbs.config as cfg_mod
        self.assertTrue(hasattr(cfg_mod.Config, 'FILE_MOD_QUEUE_ENABLED'))
        self.assertFalse(cfg_mod.Config.FILE_MOD_QUEUE_ENABLED)

    def test_env_var_true_is_actually_picked_up(self):
        # A fresh subprocess, not importlib.reload() in-process -- this
        # project has already hit real cross-test isolation breakage
        # from reloading a module mid-suite (a reload doesn't just
        # affect this test; other already-imported references to the
        # old module/class objects go stale for the rest of the run).
        env = dict(os.environ, FILE_MOD_QUEUE_ENABLED='true')
        out = subprocess.run(
            [sys.executable, '-c',
             'import anetbbs.config as c; print(c.Config.FILE_MOD_QUEUE_ENABLED)'],
            cwd=_REPO_ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.strip(), 'True', out.stderr)


class FileModQueueStringBoolParsingTests(unittest.TestCase):
    """file_areas.py's queue_on parsing must treat the string 'false'
    (what the no-restart-required admin Settings save path writes into
    current_app.config) as OFF, not On -- bool('false') would get this
    backwards."""

    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.file_mod_queue_str_test.db')
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
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_string_false_from_admin_settings_save_reads_as_off(self):
        with self.app.app_context():
            self.app.config['FILE_MOD_QUEUE_ENABLED'] = 'false'
            from flask import current_app
            queue_on = str(current_app.config.get(
                'FILE_MOD_QUEUE_ENABLED', False)).lower() == 'true'
            self.assertFalse(queue_on)

    def test_string_true_from_admin_settings_save_reads_as_on(self):
        with self.app.app_context():
            self.app.config['FILE_MOD_QUEUE_ENABLED'] = 'true'
            from flask import current_app
            queue_on = str(current_app.config.get(
                'FILE_MOD_QUEUE_ENABLED', False)).lower() == 'true'
            self.assertTrue(queue_on)

    def test_real_bool_still_works(self):
        with self.app.app_context():
            self.app.config['FILE_MOD_QUEUE_ENABLED'] = False
            from flask import current_app
            queue_on = str(current_app.config.get(
                'FILE_MOD_QUEUE_ENABLED', False)).lower() == 'true'
            self.assertFalse(queue_on)


if __name__ == '__main__':
    unittest.main()
