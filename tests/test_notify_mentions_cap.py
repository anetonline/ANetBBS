"""Regression test for a real finding from a security/performance audit
(2026-09-10): anetbbs/features/notify.py's notify_mentions() had no cap
on how many DISTINCT @names it would process from a single call. A
single post/PM/shout body containing many distinct @username tokens
fanned out into one Notification INSERT + commit + live socketio push
PER name, all synchronously in the same posting turn -- no rate limit
anywhere else in the notify() call chain would have caught this.

Fixed by capping the number of distinct mentions notify_mentions()
processes per call (_MAX_MENTIONS_PER_CALL).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fresh_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class NotifyMentionsCapTests(unittest.TestCase):
    def setUp(self):
        import anetbbs.config as cfg_mod
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'mentions_cap.db'))
        with self.app.app_context():
            from anetbbs.models import db, User
            author = User(username='author', email='author@example.com',
                         password_hash='x')
            db.session.add(author)
            db.session.commit()
            for i in range(80):
                db.session.add(User(username=f'mentee{i:03d}',
                                    email=f'mentee{i:03d}@example.com',
                                    password_hash='x'))
            db.session.commit()

    def test_notification_fanout_is_capped(self):
        from anetbbs.features.notify import notify_mentions
        from anetbbs.features.notify import _MAX_MENTIONS_PER_CALL
        from anetbbs.models import Notification

        text = ' '.join(f'@mentee{i:03d}' for i in range(80))
        with self.app.app_context():
            n = notify_mentions(text, 'author', target_url='/boards/1/1')
            self.assertLessEqual(n, _MAX_MENTIONS_PER_CALL)
            self.assertEqual(
                Notification.query.count(), n,
                'every counted mention must correspond to exactly one '
                'persisted Notification row')
            self.assertLess(
                n, 80,
                'a body with 80 distinct @mentions must not fan out into '
                '80 notifications')

    def test_small_mention_count_is_unaffected(self):
        from anetbbs.features.notify import notify_mentions
        from anetbbs.models import Notification

        with self.app.app_context():
            n = notify_mentions('hey @mentee000 and @mentee001, check this',
                                'author', target_url='/boards/1/1')
            self.assertEqual(n, 2)
            self.assertEqual(Notification.query.count(), 2)


if __name__ == '__main__':
    unittest.main()
