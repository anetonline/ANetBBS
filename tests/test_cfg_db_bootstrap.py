"""Tests for anetbbs/cfg/db_bootstrap.py -- the lightweight app-context
factory anetbbs-cfg uses instead of anetbbs.web_app.create_app(). See
db_bootstrap.py's docstring: the whole point is to avoid importing
anetbbs.web_app at all (eventlet, flask-socketio, flask-migrate, ~76
blueprint registrations), which profiling showed as the majority of a
~10s Pi3 startup for what's just a config screen that needs db access.

The "doesn't import web_app" check has to run in a subprocess -- within
the same pytest process, other test files have almost certainly already
imported anetbbs.web_app, which would make an in-process sys.modules
check meaningless (it'd "pass" even if db_bootstrap.py itself started
importing web_app, since some earlier test already put it there).
"""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent


class DbBootstrapDoesNotImportWebAppTests(unittest.TestCase):
    def test_importing_db_bootstrap_never_pulls_in_web_app_or_its_heavy_deps(self):
        script = (
            "import sys; "
            "from anetbbs.cfg.db_bootstrap import create_minimal_app; "
            "heavy = ('anetbbs.web_app', 'eventlet', 'flask_socketio', 'flask_migrate'); "
            "loaded = [m for m in heavy if m in sys.modules]; "
            "print('LOADED:' + ','.join(loaded))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LOADED:", result.stdout)
        loaded = result.stdout.strip().split("LOADED:")[-1]
        self.assertEqual(loaded, "", f"db_bootstrap import pulled in: {loaded}")


class DbBootstrapFunctionalTests(unittest.TestCase):
    def setUp(self):
        self.tmp_db = Path(__file__).resolve().parent / ".cfg_db_bootstrap_test.db"
        if self.tmp_db.exists():
            self.tmp_db.unlink()
        import os
        self._old_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = f"sqlite:///{self.tmp_db}"
        # DevelopmentConfig.SQLALCHEMY_DATABASE_URI (anetbbs/config.py) is
        # a class attribute resolved from DATABASE_URL once, at whenever
        # anetbbs.config is FIRST imported anywhere in the process -- not
        # re-read per test. In a full pytest run, dozens of earlier test
        # files import it before the os.environ mutation above ever gets
        # a chance to matter, so create_minimal_app("development") would
        # silently bind to a stale/shared database and see leftover Board
        # rows from other tests ("UNIQUE constraint failed: boards.name").
        # Passed every time in isolation only because nothing else had
        # imported anetbbs.config first. Patching the class attribute
        # directly (same idiom test_chat_mrc_toggle.py already uses for
        # MRC_BRIDGE_ENABLED) sidesteps the import-order dependency.
        import anetbbs.config as cfg_mod
        self._old_dev_db_uri = cfg_mod.DevelopmentConfig.SQLALCHEMY_DATABASE_URI
        cfg_mod.DevelopmentConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{self.tmp_db}"

    def tearDown(self):
        import os
        if self._old_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._old_db_url
        import anetbbs.config as cfg_mod
        cfg_mod.DevelopmentConfig.SQLALCHEMY_DATABASE_URI = self._old_dev_db_uri
        if self.tmp_db.exists():
            self.tmp_db.unlink()

    def test_create_all_runs_on_a_brand_new_database(self):
        from anetbbs.cfg.db_bootstrap import create_minimal_app
        from anetbbs.models import Board

        app = create_minimal_app("development")
        with app.app_context():
            self.assertEqual(Board.query.all(), [])

    def test_queries_and_writes_work_through_the_bound_db(self):
        from anetbbs.cfg.db_bootstrap import create_minimal_app
        from anetbbs.models import db, Board

        app = create_minimal_app("development")
        with app.app_context():
            b = Board(name="Bootstrap Test Board", order=1)
            db.session.add(b)
            db.session.commit()

        app2 = create_minimal_app("development")
        with app2.app_context():
            self.assertEqual([x.name for x in Board.query.all()], ["Bootstrap Test Board"])


if __name__ == "__main__":
    unittest.main()
