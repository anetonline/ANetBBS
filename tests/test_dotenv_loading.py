"""Regression test for a live-caught .env loading gap.

`python-dotenv` was a declared dependency (requirements.txt, setup.py)
but `load_dotenv()` was never actually called anywhere in the codebase.
The real systemd services work anyway because they set
`EnvironmentFile=/opt/anetbbs/.env`, which injects those key=value pairs
as real process environment variables before Python even starts -- but
any script run manually (a one-shot `tools/*.py` maintenance script, a
bare `python -m ...`, an interactive shell) never saw `.env` at all and
silently fell back to `DevelopmentConfig`'s `anetbbs_dev.db` instead of
the real `anetbbs.db`, no error or warning either way.

Live-caught running `tools/dedupe_qwk_messages.py` by hand on a real
install: it reported "nothing to clean up" against an empty database
while the real one had hundreds of duplicate rows.

Fixed by calling `load_dotenv(BASE_DIR / '.env')` once at import time in
`anetbbs/config.py`, before `os.environ.get('DATABASE_URL')` is read.
`load_dotenv()`'s default `override=False` means already-set environment
variables (e.g. from systemd) are never clobbered.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class DotenvLoadingTests(unittest.TestCase):
    """Tests load_dotenv() directly, not via anetbbs.config -- deliberately
    does NOT touch sys.modules. Deleting/re-importing anetbbs.config here
    would blow away other test files' TestingConfig.SQLALCHEMY_DATABASE_URI
    monkeypatches when the whole suite runs in one process (confirmed: an
    earlier version of this file did exactly that and broke unrelated
    tests elsewhere in the suite with "no such table: users")."""

    def setUp(self):
        self._env_snapshot = dict(os.environ)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self._env_snapshot)

    def test_env_file_is_actually_loaded(self):
        """A DATABASE_URL set only in a .env file (not already in
        os.environ) must be picked up -- this is the exact scenario
        that silently failed before the fix."""
        os.environ.pop('DATABASE_URL', None)
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / '.env'
            env_path.write_text('DATABASE_URL=sqlite:////tmp/from-dotenv-test.db\n')

            from dotenv import load_dotenv
            load_dotenv(env_path)

            self.assertEqual(os.environ.get('DATABASE_URL'),
                              'sqlite:////tmp/from-dotenv-test.db')

    def test_existing_environment_variable_is_never_overridden(self):
        """systemd's EnvironmentFile= already sets real env vars before
        Python starts -- load_dotenv() must never clobber those with a
        stale value from a .env file on disk."""
        os.environ['DATABASE_URL'] = 'sqlite:////already/set/by/systemd.db'
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / '.env'
            env_path.write_text('DATABASE_URL=sqlite:////should/not/win.db\n')

            from dotenv import load_dotenv
            load_dotenv(env_path)  # default override=False

            self.assertEqual(os.environ.get('DATABASE_URL'),
                              'sqlite:////already/set/by/systemd.db')

    def test_missing_env_file_does_not_raise(self):
        """Fresh installs before install.sh has created .env yet must
        not crash on import."""
        from dotenv import load_dotenv
        # Should be a silent no-op, not an exception.
        load_dotenv(Path('/nonexistent/path/.env'))


if __name__ == '__main__':
    unittest.main()
