"""Regression test for the MRC bridge's WHOON -> USERLIST refresh fix
(2026-07-04, shipped v1.0b2.26).

Bug: tab-complete in terminal MRC couldn't find a user ("StingRay") who
was clearly visible in a /who (WHOON) roster dump. Root cause: WHOON's
reply is a plain hub-formatted cosmetic text dump with no structural
per-user markers a client can safely parse -- the client's own
nick-scraping fallback regex silently failed on every line of it (and,
separately, could produce false positives on ordinary chat text). Fixed
by removing that fallback client-side (anetbbs/features/mrc_chat.py)
and, here, having the bridge also push a real structured USERLIST
control command to the upstream hub whenever a client asks for WHOON
-- the reply comes back through the normal relay pipeline and the
client's existing (already correct) USERLIST: parser picks it up.

This test exercises _handle_server_cmd directly against a BridgeApp
instance built via object.__new__() (bypassing __init__, which needs a
real config file and opens a real upstream connection) with just the
attributes that method touches wired up.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import BridgeApp
from mrc.bridge.db import BridgeDB


def _run(coro):
    return asyncio.run(coro)


class _FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)


def _make_bridge(tmp_dir):
    app = object.__new__(BridgeApp)
    app.config = {"bridge_bbs": "TestBBS"}
    app.db = BridgeDB(tmp_dir)
    app.websockets = {}
    app.mrc = AsyncMock()
    app.join_packet_delay_ms = 0
    app.announce_join_part = False
    app.request_banners_on_join = False
    app.request_motd_on_join = False
    app.join_message_tpl = ""
    app.exit_message_tpl = ""
    return app


class BridgeWhoonUserlistTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 12345
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws
        self.app.db.save_session(str(self.ws_id), {
            "handle": "SyntaxError", "nick": "SyntaxError",
            "room": "lobby", "in_room": True,
        })

    def test_whoon_also_triggers_a_structured_userlist_refresh(self):
        _run(self.app._handle_server_cmd(self.ws_id, {"command": "WHOON"}))
        # Two packets to the upstream hub: the WHOON forward itself, and
        # the piggybacked USERLIST control command.
        self.assertEqual(self.app.mrc.send_packet.await_count, 2)
        sent_packets = [c.args[0] for c in self.app.mrc.send_packet.await_args_list]
        self.assertTrue(any('WHOON' in p for p in sent_packets))
        self.assertTrue(any('USERLIST' in p for p in sent_packets))

    def test_who_alias_also_triggers_userlist_refresh(self):
        # /who and /whoon both map to the same 'WHOON' server_cmd
        # client-side (anetbbs/features/mrc_chat.py) -- confirm case
        # normalization doesn't accidentally skip the refresh.
        _run(self.app._handle_server_cmd(self.ws_id, {"command": "whoon"}))
        sent_packets = [c.args[0] for c in self.app.mrc.send_packet.await_args_list]
        self.assertTrue(any('USERLIST' in p for p in sent_packets))

    def test_other_commands_do_not_trigger_a_userlist_refresh(self):
        _run(self.app._handle_server_cmd(self.ws_id, {"command": "MOTD"}))
        sent_packets = [c.args[0] for c in self.app.mrc.send_packet.await_args_list]
        self.assertEqual(self.app.mrc.send_packet.await_count, 1)
        self.assertFalse(any('USERLIST' in p for p in sent_packets))


if __name__ == '__main__':
    unittest.main()
