"""Regression tests for the umrc-client stats-file feature
(BridgeApp._parse_umrc_stats_reply/_write_mrc_stats_file/
_periodic_mrc_stats_file_refresh, mrc/bridge/main.py).

Real gap this closes: umrc-client's own main-menu screen (main.c)
reads a small local file at startup to show "BBSes/Rooms/Users/
Activity" and an ONLINE/OFFLINE state derived from the file's own
mtime -- normally written by the separate umrc-bridge daemon this
bridge replaces. Since nothing wrote it, a sysop running umrc-client
against this bridge saw a correctly-working chat connection but a
permanently blank/offline-looking stats display.

First attempt at this feature tried to compute these 4 numbers
locally from USERLIST reply data this bridge already receives --
wrong approach, disproven live: the real hub's USERLIST replies on
this network never carry the optional "@site" suffix, so a
locally-computed BBS count always read 0, and even a working per-user
tally would only ever cover users in rooms this specific BBS's own
sessions have joined -- nowhere close to network-wide. The real fix
(2026-09-21): the hub's own `STATS:` server-command reply already
carries these exact numbers, network-wide, confirmed against a real
wire capture: `SERVER~~~CLIENT~A-Net_Online_ANetBBS~~STATS:176 14 50
2 172 34.4~`. _periodic_stats_refresh() already requests this
periodically (pre-existing code, unrelated to this feature); this
feature only adds parsing/caching its reply and writing it to a file
in the format umrc-client itself reads.

Format confirmed directly against umrc-client's own source (main.c):
one line, space-separated "bbses rooms users activity", read via
`fgets(stats, 30, file)` then split(' ') -- must include all 4 fields
(main.c indexes stat[3] unconditionally) and stay well under the
30-byte read buffer.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import (  # noqa: E402
    BridgeApp, _parse_umrc_stats_reply,
)
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


def _stats_packet(body: str) -> dict:
    # Real shape captured live: from_user=SERVER, from_site/from_room
    # empty, to_user=CLIENT, msg_ext=<our own bbs name>, to_room
    # empty, message="STATS:...".
    raw = MRCProtocol.create_packet(
        "SERVER", "", "", "CLIENT", "TestBBS", "", body)
    return MRCProtocol.parse_packet(raw)


class ParseUmrcStatsReplyTests(unittest.TestCase):
    """Unit tests against the parser directly."""

    def test_parses_real_captured_reply(self):
        # Verbatim from a live wire capture, 2026-09-21.
        self.assertEqual(
            _parse_umrc_stats_reply("STATS:176 14 50 2 172 34.4"),
            (176, 14, 50, 2))

    def test_ignores_trailing_fields_beyond_the_first_4(self):
        # umrc-client's own main.c only ever reads stat[0..3] --
        # confirm extra fields don't break parsing either way.
        self.assertEqual(
            _parse_umrc_stats_reply("STATS:1 2 3 4 5 6 7 8"),
            (1, 2, 3, 4))

    def test_fewer_than_4_fields_returns_none(self):
        self.assertIsNone(_parse_umrc_stats_reply("STATS:1 2 3"))

    def test_no_colon_returns_none(self):
        self.assertIsNone(_parse_umrc_stats_reply("garbage"))

    def test_non_numeric_field_becomes_zero_matching_atoi(self):
        # umrc-client parses these with atoi(), which returns 0 for
        # anything that doesn't start with a digit -- match that
        # tolerance rather than raising.
        self.assertEqual(
            _parse_umrc_stats_reply("STATS:1 oops 3 4"),
            (1, 0, 3, 4))


class WriteStatsFileSyncTests(unittest.TestCase):
    """The blocking writer directly -- format, atomicity."""

    def test_writes_expected_single_line_format(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "mrcstats.dat")
            BridgeApp._write_mrc_stats_file_sync(path, 176, 14, 50, 2)
            content = Path(path).read_text()
            self.assertEqual(content, "176 14 50 2\n")
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


class StatsReplyEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """The real proof: drive an actual wire STATS reply through
    _on_upstream_packet() (not just call the parser directly) and
    confirm the cached value -- and therefore the file this bridge
    would write -- reflects it."""

    async def test_stats_reply_updates_last_umrc_stats(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            app = await _make_bridge(tmp_path)
            self.assertEqual(app._last_umrc_stats, (0, 0, 0, 0))

            parsed = _stats_packet("STATS:176 14 50 2 172 34.4")
            await app._on_upstream_packet(parsed)

            self.assertEqual(app._last_umrc_stats, (176, 14, 50, 2))

    async def test_write_mrc_stats_file_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            stats_path = tmp_path / "mrcstats.dat"
            app = await _make_bridge(
                tmp_path, mrc_stats_file_path=str(stats_path))

            parsed = _stats_packet("STATS:176 14 50 2 172 34.4")
            await app._on_upstream_packet(parsed)
            await app._write_mrc_stats_file()

            self.assertEqual(stats_path.read_text(), "176 14 50 2\n")

    async def test_write_mrc_stats_file_is_noop_when_unconfigured(self):
        """mrc_stats_file_path empty (the default) -- no file, no
        error -- matching mrc_tcp_enabled's own opt-in stance."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            app = await _make_bridge(tmp_path)  # no mrc_stats_file_path
            await app._write_mrc_stats_file()
            self.assertEqual(list(tmp_path.glob("*.dat")), [])

    async def test_second_stats_reply_replaces_first(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            app = await _make_bridge(tmp_path)

            await app._on_upstream_packet(_stats_packet("STATS:1 2 3 0"))
            self.assertEqual(app._last_umrc_stats, (1, 2, 3, 0))
            await app._on_upstream_packet(_stats_packet("STATS:176 14 50 2 172 34.4"))
            self.assertEqual(app._last_umrc_stats, (176, 14, 50, 2))


if __name__ == '__main__':
    unittest.main()
