"""Regression test for the per-connection SQLAlchemy-engine leak in
binkp_server.py's inbound listener.

_handle_connection() creates its own throwaway Flask app + calls
db.init_app() on it (twice per connection: once for the early AKA-
announcement lookup, again for the main network/node match + import).
Each db.init_app() call binds a brand-new SQLAlchemy engine (its own
SQLite connection pool) to that app -- nothing ever disposed it before
this fix. Confirmed live: with 5 active BinkP networks polling in every
~10 minutes, this leaked a steady stream of open sqlite3 connections
between service restarts; under WAL mode those hold back checkpointing
and add just enough intermittent lock contention that a peer's own
session timeout could fire before we finished responding -- even though
our side completed and logged the poll as a success (a real Fidonet
hub and a second sysop, firehawke, both reported the same packets being
resent dozens of times despite ANetBBS receiving and GOT-acknowledging
them every time).

This reuses test_binkp_multi_hub_identity.py's harness (a real, if
minimal, BinkP session simulator) and just asserts _dispose_app_engine()
actually gets invoked -- on the success path AND on every early-return
rejection path (bad password, unknown address), since those leaked too.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_binkp_multi_hub_identity import (
    _BinkpHandlerHarness, _FakeBinkPNode, _FakeEchomailNetwork,
)


class EngineDisposedTests(unittest.TestCase, _BinkpHandlerHarness):

    def test_successful_session_disposes_engine(self):
        from anetbbs.echomail import binkp_server as mod
        node = _FakeBinkPNode(id=1, ftn_address='1:200/100', password='secret')
        with patch.object(mod, '_dispose_app_engine') as disposed:
            self._run(networks=[], nodes=[node],
                     remote_addr='1:200/100', remote_pwd='secret')
        # Exactly 2: one for the early AKA-announcement app, one for the
        # main network/node-match app (via the end-of-function finally).
        # An exact count -- not just ">= 1" -- matters here: the AKA-lookup
        # dispose alone would satisfy a ">=1" check even if the OTHER
        # call site (the one this fix actually added) were missing.
        self.assertEqual(disposed.call_count, 2,
            'a successful session must dispose both apps it created')

    def test_bad_password_disposes_engine(self):
        from anetbbs.echomail import binkp_server as mod
        network = _FakeEchomailNetwork(id=1, hub_address='1:123/3003',
                                       binkp_password='correct')
        with patch.object(mod, '_dispose_app_engine') as disposed:
            self._run(networks=[network], nodes=[],
                     remote_addr='1:123/3003', remote_pwd='wrong')
        self.assertEqual(disposed.call_count, 2,
            'a rejected (bad password) session must still dispose both apps')

    def test_unknown_address_disposes_engine(self):
        from anetbbs.echomail import binkp_server as mod
        with patch.object(mod, '_dispose_app_engine') as disposed:
            self._run(networks=[], nodes=[],
                     remote_addr='9:999/999', remote_pwd='whatever')
        self.assertEqual(disposed.call_count, 2,
            'a rejected (unknown address) session must still dispose both apps')


if __name__ == '__main__':
    unittest.main()
