"""L-18: the test configuration must never point runtime paths at the
source tree.

Before the fix, TestingConfig inherited Config.DATA_DIR (<repo>/data) and
Config.LOG_FILE (<repo>/bbs.log). Any create_app('testing') against a
database with no admin ran the first-admin bootstrap, which overwrote the
developer's real <repo>/data/admin_password.txt; app logging appended to
the developer's real bbs.log.
"""
import os
import tempfile
import unittest
from pathlib import Path

import anetbbs.config as cfg_mod

REPO = Path(__file__).resolve().parent.parent


def _inside_repo(path):
    p = Path(path).resolve()
    return p == REPO or REPO in p.parents


class TestingConfigRuntimePaths(unittest.TestCase):
    PATH_KEYS = ('DATA_DIR', 'LOG_FILE', 'DOWNLOADS_DIR', 'FILE_BULLETINS_DIR',
                 'TDF_FONTS_DIR', 'UPLOADS_DIR', 'AVATARS_DIR',
                 'NETWORK_JOIN_DIR', 'GAMES_DATA_DIR', 'ECHOMAIL_DATA_DIR')

    def test_runtime_paths_are_outside_the_source_tree(self):
        for key in self.PATH_KEYS:
            with self.subTest(key=key):
                value = getattr(cfg_mod.TestingConfig, key)
                self.assertFalse(_inside_repo(value),
                                 f'TestingConfig.{key}={value} is inside the repo')

    def test_ansi_editor_browse_dirs_are_outside_the_source_tree(self):
        for entry in cfg_mod.TestingConfig.ANSI_EDITOR_BROWSE_DIRS.split(';'):
            _label, _, path = entry.partition(':')
            self.assertFalse(_inside_repo(path), entry)


class FirstAdminBootstrapStaysOutOfRepo(unittest.TestCase):
    """End-to-end: an app on an empty database bootstraps an admin and
    writes the password file -- into the test runtime dir, not the repo."""

    def test_bootstrap_password_file_lands_in_test_data_dir(self):
        repo_pw = REPO / 'data' / 'admin_password.txt'
        before = repo_pw.stat().st_mtime_ns if repo_pw.exists() else None
        tmpdb = tempfile.mkdtemp(prefix='l18-db-')
        orig_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmpdb}/t.db'
        try:
            from anetbbs.web_app import create_app
            app = create_app('testing')
            data_dir = app.config['DATA_DIR']
            self.assertFalse(_inside_repo(data_dir), data_dir)
            self.assertFalse(_inside_repo(app.config['LOG_FILE']))
            with app.app_context():
                from anetbbs.models import User
                if User.query.filter_by(username='admin', is_admin=True).first():
                    self.assertTrue(
                        os.path.exists(os.path.join(data_dir, 'admin_password.txt')))
        finally:
            cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = orig_uri
        after = repo_pw.stat().st_mtime_ns if repo_pw.exists() else None
        self.assertEqual(before, after, 'repo data/admin_password.txt was touched')
        self.assertFalse(repo_pw.exists() and before is None,
                         'repo data/admin_password.txt was created')


class BootstrapRefusesSourceTreeWhenTesting(unittest.TestCase):
    """Second layer: even if a test points DATA_DIR into the source tree,
    a testing app refuses to write the password file there. The "source
    tree" is patched to a temp dir so this test itself never writes into
    the real checkout."""

    def test_source_tree_data_dir_is_refused_for_test_apps(self):
        from unittest import mock
        import anetbbs.web_app as web_app
        fake_src = os.path.realpath(tempfile.mkdtemp(prefix='l18-src-'))
        fake_data = os.path.join(fake_src, 'data')
        tmpdb = tempfile.mkdtemp(prefix='l18b-db-')
        orig_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        orig_data = cfg_mod.TestingConfig.DATA_DIR
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmpdb}/t.db'
        cfg_mod.TestingConfig.DATA_DIR = fake_data
        try:
            with mock.patch.object(web_app, '_source_tree_root', return_value=fake_src):
                app = web_app.create_app('testing')
            with app.app_context():
                from anetbbs.models import User
                self.assertIsNone(
                    User.query.filter_by(username='admin').first(),
                    'no fallback admin may be created when the file is refused')
        finally:
            cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = orig_uri
            cfg_mod.TestingConfig.DATA_DIR = orig_data
        self.assertFalse(os.path.exists(os.path.join(fake_data, 'admin_password.txt')))


if __name__ == '__main__':
    unittest.main()
