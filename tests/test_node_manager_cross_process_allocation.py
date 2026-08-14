"""Regression tests for anetbbs.games.node_manager.allocate_node()'s
cross-process awareness -- real gap found in a security/performance
audit: allocate_node() only ever checked THIS PROCESS's own in-memory
_active dict, but ANetBBS runs at least two separate OS processes that
both call it (anetbbs-web.service for browser-launched doors,
anetbbs.service for terminal-launched doors), each with its own
independent copy of _active. A door configured with max_nodes=1 could
have one active session from each process simultaneously, since
neither process's in-memory count ever saw the other's allocation.

Simulated here by creating a GameSession row directly (standing in for
what a DIFFERENT process would have committed via its own, separate
in-memory _active dict) without touching THIS process's _active at
all, then confirming allocate_node() in this process still correctly
sees the slot as taken.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


def _fresh_app(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class NodeManagerCrossProcessAllocationTests(unittest.TestCase):
    def setUp(self):
        self._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        self.addCleanup(
            lambda: setattr(cfg_mod.TestingConfig,
                            'SQLALCHEMY_DATABASE_URI', self._orig_db_uri))
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _fresh_app(str(Path(self._tmp.name) / 'node_mgr_test.db'))
        with self.app.app_context():
            from anetbbs.models import db, User, Game
            u = User(username='nodemgrtest', email='nodemgrtest@example.com',
                    password_hash='x')
            g = Game(name='Test Door', slug='node-mgr-cross-process-test',
                     game_type='door_native', max_nodes=1)
            db.session.add_all([u, g])
            db.session.commit()
            self.user_id = u.id
            self.game_id = g.id

        from anetbbs.games import node_manager
        node_manager._active.clear()

    def test_allocate_node_refuses_when_another_process_already_holds_the_only_slot(self):
        from anetbbs.games import node_manager
        from anetbbs.models import db, GameSession

        with self.app.app_context():
            # Simulates a DIFFERENT process's allocation: a real,
            # committed 'active' GameSession row, with NOTHING added to
            # THIS process's own node_manager._active dict at all.
            other_process_gs = GameSession(
                game_id=self.game_id, user_id=self.user_id,
                node_number=1, status='active')
            db.session.add(other_process_gs)
            db.session.commit()

            # This process's own in-memory dict has no idea a node is
            # taken -- before the fix, this would have wrongly
            # succeeded and returned node 1 again.
            self.assertEqual(node_manager._active, {})
            node = node_manager.allocate_node(self.game_id, max_nodes=1,
                                              session_id=-1)
            self.assertIsNone(node,
                              'must refuse -- the only slot is held by '
                              'a GameSession this process never allocated '
                              'itself, simulating a different OS process')

    def test_allocate_node_still_works_normally_with_no_cross_process_state(self):
        from anetbbs.games import node_manager
        with self.app.app_context():
            node = node_manager.allocate_node(self.game_id, max_nodes=1,
                                              session_id=-1)
        self.assertEqual(node, 1)

    def test_allocate_node_succeeds_again_once_the_other_processs_session_ends(self):
        from anetbbs.games import node_manager
        from anetbbs.models import db, GameSession

        with self.app.app_context():
            other_process_gs = GameSession(
                game_id=self.game_id, user_id=self.user_id,
                node_number=1, status='active')
            db.session.add(other_process_gs)
            db.session.commit()

            self.assertIsNone(node_manager.allocate_node(
                self.game_id, max_nodes=1, session_id=-1))

            # The other process's own cleanup path flips status away
            # from 'active' before it calls release_node() -- see
            # door_runner.py's _cleanup_session() for the real
            # precedent this mirrors.
            other_process_gs.status = 'completed'
            db.session.commit()

            node = node_manager.allocate_node(self.game_id, max_nodes=1,
                                              session_id=-1)
        self.assertEqual(node, 1)

    def test_db_check_gracefully_degrades_with_no_app_context(self):
        """allocate_node() must not raise just because no Flask app
        context happens to be active -- falls back to in-process-only
        tracking rather than crashing a resource-allocation call site
        most callers don't wrap in try/except."""
        from anetbbs.games import node_manager
        node = node_manager.allocate_node(self.game_id, max_nodes=1,
                                          session_id=-1)
        self.assertEqual(node, 1)


if __name__ == '__main__':
    unittest.main()
