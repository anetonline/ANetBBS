"""Tests for anetbbs/features/notify.py's live-toast push guard.

Real production incident on bbs.a-net.fyi: notify() used to do a bare
`from ..web_app import socketio` inside a try/except to push a live
in-browser toast. That module calls eventlet.monkey_patch() unconditionally
at import time, and monkey_patch() doesn't raise on failure -- it just logs
warnings and leaves already-created threading primitives half-patched. A
process that was never meant to run under eventlet (anetbbs-binkp.service,
which runs plain asyncio + std-threading) "succeeded" at that import the
first time an inbound BinkP session delivered netmail to a real local user,
permanently corrupting its own SQLAlchemy connection pool ("cannot notify
on un-acquired lock" on every commit after) -- and the try/except never saw
anything to catch, since the corruption was a side effect of a successful
import, not a raised exception.

The fix checks current_app.extensions.get('socketio') instead (same
pattern msp/server.py already used safely) so web_app.py is never
imported from a process that didn't already load it.

The "doesn't import web_app" check has to run in a subprocess -- same
reasoning as test_cfg_db_bootstrap.py's own version of this check: within
the same pytest process, other test files have almost certainly already
imported anetbbs.web_app, which would make an in-process sys.modules check
meaningless.
"""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent


class NotifyDoesNotImportWebAppTests(unittest.TestCase):
    def test_notify_from_a_fresh_minimal_app_never_pulls_in_web_app_or_eventlet(self):
        tmp_db = Path(__file__).resolve().parent / ".notify_no_web_app_test.db"
        if tmp_db.exists():
            tmp_db.unlink()
        script = (
            "import os, sys; "
            f"os.environ['DATABASE_URL'] = 'sqlite:///{tmp_db}'; "
            "from anetbbs.cfg.db_bootstrap import create_minimal_app; "
            "from anetbbs.models import db, User; "
            "from anetbbs.features.notify import notify; "
            "app = create_minimal_app('development'); "
            "ctx = app.app_context(); ctx.push(); "
            "u = User(username='notifytest', email='n@example.com', "
            "         password_hash='x'); "
            "db.session.add(u); db.session.commit(); "
            "notify(u.id, 'test', title='T', body='B'); "
            "heavy = ('anetbbs.web_app', 'eventlet', 'flask_socketio'); "
            "loaded = [m for m in heavy if m in sys.modules]; "
            "n = db.session.query(__import__('anetbbs.models', fromlist=['Notification']).Notification).count(); "
            "print('LOADED:' + ','.join(loaded)); "
            "print('NOTIFICATIONS:' + str(n))"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("LOADED:", result.stdout)
            loaded = result.stdout.split("LOADED:")[-1].split("\n")[0]
            self.assertEqual(loaded, "", f"notify() pulled in: {loaded}")
            self.assertIn("NOTIFICATIONS:1", result.stdout,
                          "notify() should still write the Notification row "
                          "even when there is no live socketio to push to")
        finally:
            if tmp_db.exists():
                tmp_db.unlink()


if __name__ == "__main__":
    unittest.main()
