"""Regression tests for anetbbs/monitor/app.py, the anetbbs-monitor CLI
(live node monitor, the shell-launched sibling of web/control.py's
NodeSpy panel and features/bbs_ui.py's in-BBS Node Monitor).

Same reasoning as tests/test_terminal_node_monitor.py's own docstring:
the curses screen itself isn't practically unit-testable without a
real terminal, so what IS covered here is the data layer -- the
NodeActivity 5-minute liveness cutoff matching web/control.py's
nodespy_json exactly (a regression guard against a THIRD
implementation drifting from the other two), and the kick mutation
anetbbs.monitor.app.kick_node() performs on the DB.
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


class NodeMonitorCliTests(unittest.TestCase):
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
                                 action='Reading msg #1',
                                 started_at=now - timedelta(minutes=2),
                                 last_seen=now)
            stale = NodeActivity(slot=2, user_id=u.id, username='alice',
                                 protocol='telnet', peer='1.2.3.4:2', page='echomail',
                                 action='Composing',
                                 started_at=now - timedelta(minutes=15),
                                 last_seen=now - timedelta(minutes=10))
            db.session.add_all([fresh, stale])
            db.session.commit()
            return u.id

    def test_liveness_cutoff_matches_nodespy_json_5_minutes(self):
        """anetbbs.monitor.app.fetch_live_nodes() must use the same
        5-minute cutoff as web/control.py:nodespy_json and
        features/bbs_ui.py's _sysop_node_monitor, or a node could show
        live in one view and stale in another -- this is now the third
        implementation of that same query."""
        from anetbbs.monitor.app import fetch_live_nodes, ONLINE_CUTOFF_MINUTES
        self.assertEqual(ONLINE_CUTOFF_MINUTES, 5)
        app = _fresh_app(str(Path(self._tmp.name) / 'a.db'))
        self._make_user_and_nodes(app)
        with app.app_context():
            nodes = fetch_live_nodes()
            self.assertEqual(list(nodes.keys()), [1],
                              'only the fresh (slot 1) node should pass the '
                              '5-minute liveness filter')
            self.assertEqual(nodes[1].username, 'alice')

    def test_kick_node_sets_flag_and_reason_and_logs_activity(self):
        from anetbbs.monitor.app import kick_node
        app = _fresh_app(str(Path(self._tmp.name) / 'b.db'))
        self._make_user_and_nodes(app)
        from anetbbs.models import NodeActivity, UserActivity
        with app.app_context():
            ok, msg = kick_node(1, 'be right back')
            self.assertTrue(ok)
            self.assertIn('slot 1', msg)

            refreshed = NodeActivity.query.filter_by(slot=1).first()
            self.assertTrue(refreshed.kick_requested)
            self.assertEqual(refreshed.kick_reason, 'be right back')

            logged = UserActivity.query.filter_by(activity_type='kick_node').first()
            self.assertIsNotNone(logged)
            self.assertIn('slot 1', logged.details)
            self.assertIn('alice', logged.details)
            self.assertIsNone(logged.user_id)
            self.assertEqual(logged.service, 'cli')

    def test_kick_node_on_empty_slot_fails_without_touching_db(self):
        from anetbbs.monitor.app import kick_node
        app = _fresh_app(str(Path(self._tmp.name) / 'c.db'))
        self._make_user_and_nodes(app)
        from anetbbs.models import UserActivity
        with app.app_context():
            ok, msg = kick_node(7, 'nobody home')
            self.assertFalse(ok)
            self.assertIn('7', msg)
            self.assertIsNone(
                UserActivity.query.filter_by(activity_type='kick_node').first())

    def test_kick_reason_defaults_and_is_trimmed_to_200_bytes(self):
        from anetbbs.monitor.app import kick_node
        app = _fresh_app(str(Path(self._tmp.name) / 'd.db'))
        self._make_user_and_nodes(app)
        from anetbbs.models import NodeActivity
        with app.app_context():
            kick_node(1, '   ')
            self.assertEqual(NodeActivity.query.filter_by(slot=1).first().kick_reason,
                              'Disconnected by sysop')

            kick_node(1, 'x' * 500)
            self.assertEqual(
                len(NodeActivity.query.filter_by(slot=1).first().kick_reason), 200)

    def test_bbs_nodes_reads_env_and_clamps(self):
        from anetbbs.monitor.app import _bbs_nodes
        orig = os.environ.get('BBS_NODES')
        try:
            os.environ['BBS_NODES'] = '12'
            self.assertEqual(_bbs_nodes(), 12)
            os.environ['BBS_NODES'] = '0'
            self.assertEqual(_bbs_nodes(), 1)
            os.environ['BBS_NODES'] = '9999'
            self.assertEqual(_bbs_nodes(), 100)
        finally:
            if orig is None:
                os.environ.pop('BBS_NODES', None)
            else:
                os.environ['BBS_NODES'] = orig


if __name__ == '__main__':
    unittest.main()
