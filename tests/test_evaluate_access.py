"""Regression tests for anetbbs/features/access_control.py:evaluate_access()
and its confirmed migration sites (echomail.py's _check_area_access,
file_areas.py's _visible_to).

Before this, every feature re-implemented this same numeric-level +
is_sysop_only comparison inline, each with its own default-fallback and
its own (inconsistent) decision about whether an admin bypasses the level
check. This suite covers the pure function directly (no Flask needed for
most cases) plus the two confirmed migration/bug-fix sites.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _FakeUser:
    def __init__(self, access_level=10, is_admin=False):
        self.access_level = access_level
        self.is_admin = is_admin


class EvaluateAccessPureTests(unittest.TestCase):
    """No Flask/DB needed -- evaluate_access() has no such dependency."""

    def test_none_user_is_anonymous_level_0_not_10(self):
        """SECURITY: this function's own docstring has always promised
        None == anonymous == access_level 0 -- the implementation
        previously fell through to a level-10 ("registered") default
        instead, silently granting every anonymous visitor "registered
        users only" access on any board/area/feed using the standard
        default min_access_level=10. Only min_level=0 (public) may pass
        for an anonymous caller."""
        from anetbbs.features.access_control import evaluate_access
        self.assertTrue(evaluate_access(None, min_level=0))
        self.assertFalse(evaluate_access(None, min_level=1))
        self.assertFalse(evaluate_access(None, min_level=10))
        self.assertFalse(evaluate_access(None, min_level=20))
        self.assertFalse(evaluate_access(None, is_sysop_only=True))

    def test_flask_login_anonymous_user_mixin_is_treated_as_level_0(self):
        """The REAL anonymous object every web caller actually passes is
        Flask-Login's AnonymousUserMixin -- not None. It has
        is_authenticated=False and no access_level attribute at all, so
        this must be caught the same way None is, not fall through to
        the "assume registered" default meant for genuinely-
        authenticated callers missing the attribute for some other
        reason."""
        from anetbbs.features.access_control import evaluate_access

        class _FakeAnonymousUserMixin:
            is_authenticated = False
            is_admin = False

        anon = _FakeAnonymousUserMixin()
        self.assertTrue(evaluate_access(anon, min_level=0))
        self.assertFalse(evaluate_access(anon, min_level=10))

    def test_authenticated_user_missing_access_level_still_defaults_to_10(self):
        """A genuinely-authenticated caller (is_authenticated=True, or no
        such attribute at all -- e.g. a plain terminal dict) that's
        missing access_level for some other reason must still get the
        old "assume registered" default, not be wrongly treated as
        anonymous."""
        from anetbbs.features.access_control import evaluate_access

        class _FakeAuthedNoLevel:
            is_authenticated = True
            is_admin = False

        self.assertTrue(evaluate_access(_FakeAuthedNoLevel(), min_level=10))
        self.assertFalse(evaluate_access(_FakeAuthedNoLevel(), min_level=20))
        # Plain dict with no is_authenticated key at all -- the terminal/
        # session.user shape this module is designed to also support.
        self.assertTrue(evaluate_access({'is_admin': False}, min_level=10))

    def test_level_gate(self):
        from anetbbs.features.access_control import evaluate_access
        low = _FakeUser(access_level=5)
        high = _FakeUser(access_level=50)
        self.assertFalse(evaluate_access(low, min_level=10))
        self.assertTrue(evaluate_access(high, min_level=10))

    def test_min_level_none_treated_as_zero(self):
        from anetbbs.features.access_control import evaluate_access
        user = _FakeUser(access_level=0)
        self.assertTrue(evaluate_access(user, min_level=None))

    def test_sysop_only_blocks_non_admin_regardless_of_level(self):
        from anetbbs.features.access_control import evaluate_access
        high_level_non_admin = _FakeUser(access_level=200, is_admin=False)
        self.assertFalse(evaluate_access(high_level_non_admin, min_level=0,
                                         is_sysop_only=True))

    def test_sysop_only_allows_admin(self):
        from anetbbs.features.access_control import evaluate_access
        admin = _FakeUser(access_level=10, is_admin=True)
        self.assertTrue(evaluate_access(admin, min_level=0, is_sysop_only=True))

    def test_bypass_admin_true_ignores_level(self):
        from anetbbs.features.access_control import evaluate_access
        admin = _FakeUser(access_level=0, is_admin=True)
        self.assertTrue(evaluate_access(admin, min_level=255, bypass_admin=True))

    def test_bypass_admin_false_does_not_ignore_level(self):
        """The RSS/games call sites preserve today's behavior: an admin
        under the level threshold is still denied when bypass_admin=False."""
        from anetbbs.features.access_control import evaluate_access
        admin = _FakeUser(access_level=0, is_admin=True)
        self.assertFalse(evaluate_access(admin, min_level=255, bypass_admin=False))

    def test_accepts_plain_dict_user(self):
        """Shaped to work against session.user dicts too, for a later
        terminal-side migration -- not wired up yet, but the contract
        should already hold."""
        from anetbbs.features.access_control import evaluate_access
        d = {'access_level': 50, 'is_admin': False}
        self.assertTrue(evaluate_access(d, min_level=10))
        self.assertFalse(evaluate_access(d, min_level=100))


class MigratedSiteTests(unittest.TestCase):
    """DB-level tests for the two confirmed migration/bug-fix sites."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.evaluate_access_test.db')
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

    def test_file_areas_visible_to_now_checks_min_access_level(self):
        """The scoped bug-fix: _visible_to() used to ignore
        min_access_level entirely."""
        from anetbbs.web.file_areas import _visible_to
        from anetbbs.models import FileArea

        with self.app.app_context():
            gated = FileArea(tag='GATEDTEST', name='Gated', storage_path='/tmp/x',
                             is_active=True, min_access_level=50)
            open_area = FileArea(tag='OPENTEST', name='Open', storage_path='/tmp/x',
                                 is_active=True, min_access_level=0)

            low_user = _FakeUser(access_level=10, is_admin=False)
            high_user = _FakeUser(access_level=100, is_admin=False)
            admin_user = _FakeUser(access_level=10, is_admin=True)

            self.assertFalse(_visible_to(low_user, gated),
                             'low-level user should no longer see a gated area')
            self.assertTrue(_visible_to(high_user, gated))
            self.assertTrue(_visible_to(admin_user, gated),
                            'admin bypasses the level gate')
            self.assertTrue(_visible_to(low_user, open_area))

    def test_echomail_check_area_access_still_gates_sysop_only(self):
        from anetbbs.web.echomail import _check_area_access
        from anetbbs.models import EchomailNetwork, EchoArea, db
        from flask import Flask
        from flask_login import LoginManager

        with self.app.app_context():
            net = EchomailNetwork(name='EATest', network_type='binkp')
            db.session.add(net)
            db.session.flush()
            sysop_area = EchoArea(network_id=net.id, tag='SYSOPONLY',
                                  name='Sysop Only', is_active=True,
                                  is_sysop_only=True, min_access_level=0)
            db.session.add(sysop_area)
            db.session.commit()

            # Drive it through a real request context so current_user /
            # abort() behave normally.
            with self.app.test_request_context('/'):
                from flask_login import current_user as _cu
                # Anonymous request context -- current_user is AnonymousUserMixin.
                with self.assertRaises(Exception) as ctx:
                    _check_area_access(sysop_area)
                self.assertEqual(getattr(ctx.exception, 'code', None), 403)


if __name__ == '__main__':
    unittest.main()
