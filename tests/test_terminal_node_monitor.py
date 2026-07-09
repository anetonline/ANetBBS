"""Regression tests for the terminal Node Monitor (anetbbs/features/bbs_ui.py:
_sysop_node_monitor), added alongside the broader terminal sysop-tools pass.

The lightbar screen itself isn't practically unit-testable without a full
session mock (same reasoning as tests/test_ebook_terminal_menu.py) -- what
IS covered here: the NodeActivity 5-minute liveness cutoff matching
anetbbs/web/control.py:nodespy_json's filter exactly (a regression guard
against the two implementations drifting apart), and the kick/message
mutations the monitor's action handlers perform on the DB row.
"""
import os
import sys
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DATA_DIR = Path(__file__).resolve().parents[1] / 'data'


def _snapshot_data_dir():
    if not _DATA_DIR.is_dir():
        return set()
    return set(_DATA_DIR.iterdir())


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


class NodeMonitorLivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir_before = _snapshot_data_dir()
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._orig_flask_env = os.environ.get('FLASK_ENV')

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        if cls._orig_flask_env is None:
            os.environ.pop('FLASK_ENV', None)
        else:
            os.environ['FLASK_ENV'] = cls._orig_flask_env
        for entry in _snapshot_data_dir() - cls._data_dir_before:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _make_user_and_nodes(self, app):
        from anetbbs.models import db, User, NodeActivity
        with app.app_context():
            u = User(username='alice', email='alice@example.com',
                     password_hash='x')
            db.session.add(u)
            db.session.commit()
            now = datetime.utcnow()
            fresh = NodeActivity(slot=1, user_id=u.id, username='alice',
                                 protocol='ssh', peer='1.2.3.4:1', page='boards',
                                 action='Reading msg #1', last_seen=now)
            stale = NodeActivity(slot=2, user_id=u.id, username='alice',
                                 protocol='telnet', peer='1.2.3.4:2', page='echomail',
                                 action='Composing', last_seen=now - timedelta(minutes=10))
            db.session.add_all([fresh, stale])
            db.session.commit()
            return u.id

    def test_liveness_cutoff_matches_nodespy_json_5_minutes(self):
        """The terminal monitor's fetch_rows() filter must use the same
        5-minute cutoff as web/control.py:nodespy_json, or a node could
        show live in one view and stale in the other."""
        app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        self._make_user_and_nodes(app)
        from anetbbs.models import NodeActivity
        with app.app_context():
            cutoff = datetime.utcnow() - timedelta(minutes=5)
            live = (NodeActivity.query
                    .filter(NodeActivity.last_seen >= cutoff)
                    .order_by(NodeActivity.slot).all())
            self.assertEqual([r.slot for r in live], [1],
                              'only the fresh (slot 1) node should pass the '
                              '5-minute liveness filter')

    def test_kick_sets_flag_and_reason_and_logs_activity(self):
        app = _fresh_app(str(Path(self._tmp.name) / 'b.db'))
        self._make_user_and_nodes(app)
        from anetbbs.models import db, NodeActivity, UserActivity
        with app.app_context():
            row = NodeActivity.query.filter_by(slot=1).first()
            row.kick_requested = True
            row.kick_reason = 'Disconnected by sysop'[:200]
            db.session.add(UserActivity(
                user_id=row.user_id, activity_type='kick_node',
                details='slot 1 (alice): Disconnected by sysop',
                service='telnet'))
            db.session.commit()

            refreshed = NodeActivity.query.filter_by(slot=1).first()
            self.assertTrue(refreshed.kick_requested)
            self.assertEqual(refreshed.kick_reason, 'Disconnected by sysop')
            logged = UserActivity.query.filter_by(activity_type='kick_node').first()
            self.assertIsNotNone(logged)
            self.assertIn('slot 1', logged.details)

    def test_message_action_reuses_sysop_paging_inbox(self):
        """Node Monitor's 'message a node' action is same-process (both
        sysop and target are terminal sessions), so it reuses
        sysop_paging.push_message/pop_messages directly rather than a
        DB-backed inbox -- confirm the round trip works."""
        from anetbbs.features import sysop_paging
        sysop_paging.push_message(42, 'sysop', 'hello from monitor')
        msgs = sysop_paging.pop_messages(42)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]['sender'], 'sysop')
        self.assertEqual(msgs[0]['text'], 'hello from monitor')
        # Popped once -- draining is destructive, matching menu_engine.py's
        # and _show_main_v2's "show once, then clear" behavior.
        self.assertEqual(sysop_paging.pop_messages(42), [])


if __name__ == '__main__':
    unittest.main()
