"""Regression test for anetbbs/core/user_manager.py's engine/session
caching -- a real bug found live while writing Phase D's SSH public-
key-auth tests, the same "frozen database URI at first import" class
already found and fixed in anetbbs/config.py's SQLALCHEMY_DATABASE_URI
(see tests/test_config_database_uri_env_freshness.py), but in this
completely separate module, which maintains its own independent
engine rather than going through anetbbs.config at all.

Symptom before the fix: user_manager.py's engine was built ONCE at
first import (`_DB_URI = _resolve_db_uri(); _engine = create_engine
(_DB_URI)`). A process that builds more than one app/database in
sequence (multiple test files sharing one pytest process is the
common real case here) silently kept every UserManager() call pointed
at whichever database was resolved FIRST -- confirmed live: running
tests/test_ssh_public_key_auth_e2e.py and
tests/test_user_manager_authenticate_by_public_key.py together in one
pytest run, both individually green, produced real cross-test-file
authentication failures once combined, purely from database mismatch.
Dormant in real production (a systemd-deployed process only ever
builds one app at startup).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class UserManagerDbUriFreshnessTests(unittest.TestCase):
    def setUp(self):
        self._orig_database_url = os.environ.get('DATABASE_URL')

    def tearDown(self):
        if self._orig_database_url is None:
            os.environ.pop('DATABASE_URL', None)
        else:
            os.environ['DATABASE_URL'] = self._orig_database_url
        # Reset the module's cache so this test's overrides don't leak
        # into whatever test runs next in the same process.
        import anetbbs.core.user_manager as um
        um._cached_engine = None
        um._cached_sessionmaker = None
        um._cached_engine_uri = None

    def test_sessionmaker_rebinds_when_database_url_changes(self):
        import anetbbs.core.user_manager as um
        um._cached_engine = None
        um._cached_sessionmaker = None
        um._cached_engine_uri = None

        os.environ['DATABASE_URL'] = 'sqlite:////tmp/um-freshness-first.db'
        sm1 = um._get_sessionmaker()
        self.assertEqual(str(sm1.kw['bind'].url), 'sqlite:////tmp/um-freshness-first.db')

        os.environ['DATABASE_URL'] = 'sqlite:////tmp/um-freshness-second.db'
        sm2 = um._get_sessionmaker()
        self.assertEqual(str(sm2.kw['bind'].url), 'sqlite:////tmp/um-freshness-second.db')
        self.assertIsNot(sm1, sm2,
                         'a changed DATABASE_URL must produce a new sessionmaker, '
                         'not silently keep serving the first one')

    def test_sessionmaker_is_reused_when_url_is_unchanged(self):
        """The cache must not rebuild on every call -- only when the
        URI actually changes, matching bbs_ui.py's _app() caching
        rationale (a fresh engine per query would be wasteful)."""
        import anetbbs.core.user_manager as um
        um._cached_engine = None
        um._cached_sessionmaker = None
        um._cached_engine_uri = None

        os.environ['DATABASE_URL'] = 'sqlite:////tmp/um-freshness-stable.db'
        sm1 = um._get_sessionmaker()
        sm2 = um._get_sessionmaker()
        self.assertIs(sm1, sm2)

    def test_Session_callable_still_works_as_a_context_manager(self):
        """Every real call site in this module uses `with _Session()
        as s:` -- confirm the new function-based _Session() still
        supports that exact calling convention."""
        import anetbbs.core.user_manager as um
        um._cached_engine = None
        um._cached_sessionmaker = None
        um._cached_engine_uri = None
        os.environ['DATABASE_URL'] = 'sqlite:////tmp/um-freshness-ctxmgr.db'

        with um._Session() as s:
            self.assertIsNotNone(s)


if __name__ == '__main__':
    unittest.main()
