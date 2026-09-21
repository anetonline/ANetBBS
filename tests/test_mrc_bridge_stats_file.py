"""Regression tests for the umrc-client stats-file feature
(BridgeApp._compute_umrc_stats/_write_mrc_stats_file/
_periodic_mrc_stats_file_refresh, mrc/bridge/main.py).

Real gap this closes: umrc-client's own main-menu screen (main.c)
reads a small local file at startup to show "BBSes/Rooms/Users/
Activity" and an ONLINE/OFFLINE state derived from the file's own
mtime -- normally written by the separate umrc-bridge daemon this
bridge replaces. Since nothing wrote it, a sysop running umrc-client
against this bridge saw a correctly-working chat connection but a
permanently blank/offline-looking stats display. Confirmed live
against a real umrc-client build (2026-09-21) once mrc_stats_file_path
was wired up.

Format confirmed directly against umrc-client's own source (main.c):
one line, space-separated "bbses rooms users activity", read via
`fgets(stats, 30, file)` then split(' ') -- must include all 4 fields
(main.c indexes stat[3] unconditionally) and stay well under the
30-byte read buffer.
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import BridgeApp  # noqa: E402
from mrc.bridge.mrc_protocol import MRCProtocol  # noqa: E402


class _FakeMRCConnection:
    def __init__(self):
        self.sent = []
        self.connected = True

    async def send_packet(self, packet: str):
        self.sent.append(packet)


async def _make_bridge(tmp_path, **overrides) -> BridgeApp:
    config = {
        "mrc_host": "test.invalid",
        "mrc_port": 1,
        "bridge_bbs": "TestBBS",
        "platform_info": "TEST/1.0",
        "data_dir": str(tmp_path / "data"),
    }
    config.update(overrides)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    app = BridgeApp(str(config_path))
    app.mrc = _FakeMRCConnection()
    return app


def _userlist_packet(room: str, entries: str) -> dict:
    raw = MRCProtocol.create_packet(
        "SERVER", "", room, "CLIENT", "", room, f"USERLIST:{entries}")
    return MRCProtocol.parse_packet(raw)


class ComputeUmrcStatsTests(unittest.TestCase):
    """Unit tests against _compute_umrc_stats() directly, bypassing
    the wire-packet pipeline entirely -- just _room_userlist_cache in,
    (bbses, rooms, users, activity) out."""

    def _app(self):
        return object.__new__(BridgeApp)

    def test_empty_cache_is_all_zero(self):
        app = self._app()
        app._room_userlist_cache = {}
        self.assertEqual(app._compute_umrc_stats(), (0, 0, 0, 0))

    def test_single_room_counts_bbses_rooms_users(self):
        app = self._app()
        app._room_userlist_cache = {
            "lobby": [("alice", "BBS1"), ("bob", "BBS2"), ("carol", "BBS1")],
        }
        bbses, rooms, users, activity = app._compute_umrc_stats()
        self.assertEqual(bbses, 2)   # BBS1, BBS2
        self.assertEqual(rooms, 1)
        self.assertEqual(users, 3)
        self.assertEqual(activity, 1)  # 3 users -> LOW

    def test_same_user_in_two_rooms_counted_once(self):
        app = self._app()
        app._room_userlist_cache = {
            "lobby": [("alice", "BBS1")],
            "help":  [("alice", "BBS1"), ("bob", "BBS2")],
        }
        bbses, rooms, users, activity = app._compute_umrc_stats()
        self.assertEqual(rooms, 2)
        self.assertEqual(users, 2)   # alice counted once despite 2 rooms
        self.assertEqual(bbses, 2)

    def test_user_with_no_site_counts_as_user_not_bbs(self):
        app = self._app()
        app._room_userlist_cache = {"lobby": [("alice", "")]}
        bbses, rooms, users, activity = app._compute_umrc_stats()
        self.assertEqual(users, 1)
        self.assertEqual(bbses, 0)

    def test_activity_thresholds(self):
        app = self._app()

        def stats_for(n):
            app._room_userlist_cache = {
                "lobby": [(f"user{i}", "BBS1") for i in range(n)]
            }
            return app._compute_umrc_stats()[3]

        self.assertEqual(stats_for(0), 0)    # NUL
        self.assertEqual(stats_for(1), 1)    # LOW
        self.assertEqual(stats_for(5), 1)    # LOW (boundary)
        self.assertEqual(stats_for(6), 2)    # MED (boundary)
        self.assertEqual(stats_for(20), 2)   # MED (boundary)
        self.assertEqual(stats_for(21), 3)   # HI (boundary)


class WriteStatsFileSyncTests(unittest.TestCase):
    """The blocking writer directly -- format, atomicity."""

    def test_writes_expected_single_line_format(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "mrcstats.dat")
            BridgeApp._write_mrc_stats_file_sync(path, 2, 3, 17, 2)
            content = Path(path).read_text()
            self.assertEqual(content, "2 3 17 2\n")
            self.assertLess(len(content), 30,
                            "must stay under umrc-client's fgets(stats, 30, ...) buffer")

    def test_no_leftover_tmp_file_after_write(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "mrcstats.dat")
            BridgeApp._write_mrc_stats_file_sync(path, 1, 1, 1, 1)
            leftover = list(Path(td).glob("*.tmp"))
            self.assertEqual(leftover, [],
                            f"atomic write left a temp file behind: {leftover}")

    def test_second_write_replaces_first(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "mrcstats.dat")
            BridgeApp._write_mrc_stats_file_sync(path, 1, 1, 1, 0)
            BridgeApp._write_mrc_stats_file_sync(path, 9, 9, 99, 3)
            self.assertEqual(Path(path).read_text(), "9 9 99 3\n")


class UserlistCacheEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """The real proof: drive an actual wire USERLIST reply through
    _on_upstream_packet() (not just call _compute_umrc_stats()
    directly) and confirm the cache -- and therefore the file this
    bridge would write -- reflects it."""

    async def test_userlist_reply_populates_cache_and_stats(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            app = await _make_bridge(tmp_path)

            parsed = _userlist_packet("lobby", "alice@BBS1,bob@BBS2")
            await app._on_upstream_packet(parsed)

            self.assertEqual(
                app._room_userlist_cache.get("lobby"),
                [("alice", "BBS1"), ("bob", "BBS2")])
            bbses, rooms, users, activity = app._compute_umrc_stats()
            self.assertEqual((bbses, rooms, users), (2, 1, 2))

    async def test_write_mrc_stats_file_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            stats_path = tmp_path / "mrcstats.dat"
            app = await _make_bridge(
                tmp_path, mrc_stats_file_path=str(stats_path))

            parsed = _userlist_packet("lobby", "alice@BBS1,bob@BBS2,carol@BBS1")
            await app._on_upstream_packet(parsed)
            await app._write_mrc_stats_file()

            self.assertEqual(stats_path.read_text(), "2 1 3 1\n")

    async def test_write_mrc_stats_file_is_noop_when_unconfigured(self):
        """mrc_stats_file_path empty (the default) -- no file, no
        error -- matching mrc_tcp_enabled's own opt-in stance."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            app = await _make_bridge(tmp_path)  # no mrc_stats_file_path
            await app._write_mrc_stats_file()
            self.assertEqual(list(tmp_path.glob("*.dat")), [])


if __name__ == '__main__':
    unittest.main()
