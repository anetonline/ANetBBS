"""Regression tests for the admin-gate consolidation
(anetbbs/web/access_control.py: require_admin / require_admin_or_403).

Before this, 21 web blueprints each defined their own near-identical
admin-only gate -- split between a decorator style and a guard-function
style, with admin.py behaving differently on failure (flash + redirect)
than the other 20 (bare abort(403)), and ansi_editor.py having no named
helper at all (11 separate inline checks). All 21 files plus
ansi_editor.py's inline checks now import from one shared module,
standardized on abort(403) everywhere (including admin.py, a deliberate,
confirmed UX change -- see docs/CHANGELOG.md).

This suite spot-checks one representative route from each of the four
original styles (admin.py's flash+redirect decorator, a plain decorator
style, a guard-function style, and ansi_editor.py's inline style) plus
upgrades.py's former 401-vs-403 split, to confirm the migration didn't
silently disable gating anywhere -- not an exhaustive test of all ~30
migrated routes.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class AdminGateConsolidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.admin_gate_test.db')
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

    def _admin_client(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            admin = User.query.filter_by(username='admingatetest').first()
            if not admin:
                admin = User(username='admingatetest', is_admin=True,
                            access_level=255,
                            email='admingatetest@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def _nonadmin_client(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username='admingatetest_plain').first()
            if not u:
                u = User(username='admingatetest_plain', is_admin=False,
                         access_level=10,
                         email='admingatetest_plain@example.com')
                u.set_password('x')
                db.session.add(u)
                db.session.commit()
            uid = u.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        return client

    # -- unit-level: the shared helper itself ----------------------------

    def test_require_admin_or_403_denies_anonymous(self):
        with self.app.test_request_context('/'):
            from anetbbs.web.access_control import require_admin_or_403
            with self.assertRaises(Exception) as ctx:
                require_admin_or_403()
            self.assertEqual(getattr(ctx.exception, 'code', None), 403)

    # -- route-level: one representative site per original style --------

    def test_admin_py_route_now_aborts_403_not_flash_redirect(self):
        """admin.py used to flash + redirect to main.index for non-admins.
        Confirmed change: it now aborts 403 like every other blueprint."""
        resp = self._nonadmin_client().get('/admin/users')
        self.assertEqual(resp.status_code, 403)

    def test_admin_py_route_allows_admin(self):
        resp = self._admin_client().get('/admin/users')
        self.assertEqual(resp.status_code, 200)

    def test_decorator_style_blueprint_still_gated(self):
        """gallery_admin.py -- plain decorator style, not the admin.py exception."""
        resp = self._nonadmin_client().get('/admin/galleries/')
        self.assertEqual(resp.status_code, 403)

    def test_guard_function_style_blueprint_still_gated(self):
        """file_queue.py -- guard-function style (_require_admin() called
        as the first statement in the view body)."""
        resp = self._nonadmin_client().get('/admin/file-queue/')
        self.assertEqual(resp.status_code, 403)

    def test_wall_admin_migrated_via_bulk_script_still_gated(self):
        """wall_admin.py -- one of the 9 files migrated by the bulk
        find-and-replace pass (identical 3-line body), not hand-edited."""
        resp = self._nonadmin_client().get('/admin/wall/')
        self.assertEqual(resp.status_code, 403)

    def test_ansi_editor_inline_checks_still_gated(self):
        """ansi_editor.py had 11 separate inline admin checks and no named
        helper at all before -- confirm the consolidated version still
        gates the index route."""
        resp = self._nonadmin_client().get('/admin/ansi/')
        self.assertEqual(resp.status_code, 403)

    def test_upgrades_login_required_still_redirects_anonymous(self):
        """upgrades.py's old _require_admin() had an abort(401) branch for
        unauthenticated users, but every call site is also decorated with
        @login_required, which runs first and redirects (302) before
        _require_admin() is ever reached -- that branch was dead code, not
        a real behavior this refactor could change. Confirm the (unrelated,
        untouched) @login_required redirect still fires."""
        anon = self.app.test_client()
        resp = anon.get('/admin/upgrades/')
        self.assertEqual(resp.status_code, 302)

    def test_upgrades_still_403_for_authenticated_non_admin(self):
        """The one branch of upgrades.py's old check that WAS reachable
        (authenticated but not admin -> 403) is unchanged by the refactor."""
        resp = self._nonadmin_client().get('/admin/upgrades/')
        self.assertEqual(resp.status_code, 403)


if __name__ == '__main__':
    unittest.main()
