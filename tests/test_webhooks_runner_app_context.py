"""Regression test for a real Medium finding from a security/
performance audit (2026-09-02): anetbbs/features/webhooks.py's
fire()'s background delivery thread (_runner(), started via a bare
threading.Thread) called Webhook.query.get()/db.session.commit() with
NO Flask app context of its own -- unlike sysop_paging.py's own
webhook-firing call site, which explicitly wraps in `with
_app().app_context()` for exactly this reason. A new thread has no
inherited app context, so this raised "RuntimeError: Working outside
of application context", silently swallowed by the bare
`except Exception: pass`. Effect: last_called_at/last_status/
last_error were NEVER persisted for any webhook delivery, even a
successful one -- the admin webhook UI would show "never called"
forever.

Fixed by wrapping _runner()'s body in `with _app().app_context():`,
the same lightweight transient Flask+SQLAlchemy context used elsewhere
in this codebase for cross-context background work.
"""
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

# Captured BEFORE any patching so _JoinableThread's own use of it below
# doesn't recurse into itself once the test patches
# webhooks_mod.threading.Thread (a module-level attribute -- patching
# it affects every user of `threading.Thread`, including this file's
# own import of the real threading module).
_REAL_THREAD_CLS = threading.Thread


class _JoinableThread:
    """Stand-in for threading.Thread that behaves like the real one
    (runs target() on an ACTUAL separate thread, so there's no
    inherited Flask app context -- reproducing the real bug requires
    a genuine thread boundary, not just an inline call) but is
    captured so the test can .join() it deterministically before
    asserting, instead of racing a fire-and-forget daemon thread."""
    def __init__(self, target=None, daemon=None):
        self._real = _REAL_THREAD_CLS(target=target, daemon=daemon)

    def start(self):
        self._real.start()
        self._real.join(timeout=5)


class WebhookRunnerAppContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.webhook_runner_ctx_test.db')
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

    def setUp(self):
        from anetbbs.models import db, Webhook
        with self.app.app_context():
            Webhook.query.delete()
            db.session.commit()

    def _make_webhook(self, event='login'):
        from anetbbs.models import db, Webhook
        with self.app.app_context():
            w = Webhook(name='CtxTestHook', url='http://example.test/hook',
                       event=event, is_active=True)
            db.session.add(w)
            db.session.commit()
            return w.id

    def test_delivery_status_is_persisted_from_the_background_thread(self):
        import anetbbs.features.webhooks as webhooks_mod
        wid = self._make_webhook(event='login')

        with self.app.app_context(), \
             patch.object(webhooks_mod.threading, 'Thread', _JoinableThread), \
             patch.object(webhooks_mod, '_do_post', return_value=(200, None)), \
             patch('anetbbs.features.bbs_ui._app', lambda: self.app):
            webhooks_mod.fire('login', {'user': 'x', 'service': 'web'})

        from anetbbs.models import Webhook
        with self.app.app_context():
            w = Webhook.query.get(wid)
            self.assertEqual(w.last_status, 200,
                             'delivery status must be persisted, not silently '
                             'dropped by a missing app context')
            self.assertIsNotNone(w.last_called_at)
            self.assertIsNone(w.last_error)

    def test_delivery_error_is_also_persisted(self):
        import anetbbs.features.webhooks as webhooks_mod
        wid = self._make_webhook(event='login')

        with self.app.app_context(), \
             patch.object(webhooks_mod.threading, 'Thread', _JoinableThread), \
             patch.object(webhooks_mod, '_do_post',
                          return_value=(0, 'Connection refused')), \
             patch('anetbbs.features.bbs_ui._app', lambda: self.app):
            webhooks_mod.fire('login', {'user': 'x', 'service': 'web'})

        from anetbbs.models import Webhook
        with self.app.app_context():
            w = Webhook.query.get(wid)
            self.assertEqual(w.last_status, 0)
            self.assertEqual(w.last_error, 'Connection refused')
            self.assertIsNotNone(w.last_called_at)


if __name__ == '__main__':
    unittest.main()
