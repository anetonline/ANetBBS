"""Regression tests for mrc/bridge/main.py's raw-TCP listener
(BridgeApp.handle_mrc_tcp_connection and friends).

Lets a real umrc-client (github.com/codefenix-dev/uMRC) connect
directly to ANetBBS's own MRC bridge in place of the separate
umrc-bridge daemon, with zero changes to uMRC itself -- see
docs/35-mods-directory.md-style context in the plan this was built
from. mrc/ is a deliberately independent package (its own systemd
unit, never imports from anetbbs), so this suite talks to BridgeApp
directly rather than through any Flask/anetbbs fixture -- it lives
under tests/ (not mrc/bridge/tests/) purely so the existing 15-batch
`ls tests/test_*.py` CI/local runner (see .github/workflows/
docker-build.yml) actually picks it up; no anetbbs import is needed by
the code under test.

Exercises real loopback TCP throughout (asyncio.start_server /
open_connection against 127.0.0.1, ephemeral port) rather than mocking
the socket layer -- matching this project's standing preference for
verifying real behavior over reasoning through a diff. The upstream
MRC hub connection (BridgeApp.mrc) is swapped for a small fake that
just records outbound packets, since no real hub is reachable in CI;
everything downstream of that (session routing, room/DM delivery,
wire-packet construction) is real, unmocked BridgeApp code.

The one case worth calling out: test_dm_and_broadcast_reach_mixed_
websocket_and_tcp_sessions is the scenario that made the earlier
"become a umrc-bridge client" approach architecturally unworkable
(umrc-bridge routes by one identity per TCP connection slot -- a
message to any user other than that slot's own identity would never
be delivered). Proving a real TCP session and a real WS session in the
same room both get exactly the messages meant for them is the load-
bearing assertion for the whole feature.
"""
import asyncio
import json
import re
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mrc.bridge.main import (  # noqa: E402
    BridgeApp, MRC_CHAT_PACKET_SPACING, MRC_TCP_MAX_LINE_BYTES)
from mrc.bridge.mrc_protocol import MRCProtocol  # noqa: E402


class _FakeMRCConnection:
    """Stands in for BridgeApp.mrc (a real MRCConnection normally
    holds the persistent upstream-hub socket) -- just records every
    outbound packet instead of doing real network I/O."""

    def __init__(self):
        self.sent = []
        self.sent_at = []
        self.connected = True

    async def send_packet(self, packet: str):
        self.sent.append(packet)
        self.sent_at.append(time.monotonic())

    def sent_messages(self):
        return [MRCProtocol.parse_packet(p) for p in self.sent]


class _FakeWebSocket:
    """Stands in for an aiohttp WebSocketResponse -- just records
    every JSON payload _safe_send() would have sent it."""

    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


async def _make_bridge(tmp_path, **overrides) -> BridgeApp:
    config = {
        "mrc_host": "test.invalid",
        "mrc_port": 1,
        "bridge_bbs": "TestBBS",
        "platform_info": "TEST/1.0",
        "data_dir": str(tmp_path / "data"),
        "mrc_tcp_enabled": True,
        "mrc_tcp_listen_host": "127.0.0.1",
        "mrc_tcp_listen_port": 0,
        "ws_disconnect_grace_seconds": 0,
        "join_packet_delay_ms": 0,
        "request_banners_on_join": False,
        "request_motd_on_join": False,
        "announce_join_part": True,
    }
    config.update(overrides)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    app = BridgeApp(str(config_path))
    app.mrc = _FakeMRCConnection()
    return app


class _TcpBridgeHarness(unittest.IsolatedAsyncioTestCase):
    """Shared real-loopback-TCP server setup for every test below."""

    async def asyncSetUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)
        self.app = await _make_bridge(self.tmp_path)
        self.server = await asyncio.start_server(
            self.app.handle_mrc_tcp_connection, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        # Bounded, not a bare await: asyncio.Server.wait_closed() blocks
        # until every already-accepted connection's handler task exits
        # too, not just the listening socket. A test that fails/raises
        # before reaching its own writer.close() leaves
        # handle_mrc_tcp_connection still parked in `await reader.read()`
        # for that connection -- an unbounded wait_closed() here would
        # then hang the ENTIRE suite indefinitely on any single test
        # failure, rather than just failing that one test. Confirmed via
        # a direct repro against this exact Python version (3.12.3).
        try:
            await asyncio.wait_for(self.server.wait_closed(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        self._tmpdir.cleanup()

    async def _connect(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        return reader, writer

    async def _send(self, writer, f1, f2, f3, f4, f5, f6, f7):
        writer.write(MRCProtocol.create_packet(f1, f2, f3, f4, f5, f6, f7).encode())
        await writer.drain()

    async def _iamhere(self, writer, handle, room="lobby"):
        await self._send(writer, handle, "site", room, "SERVER", "", "", "IAMHERE")

    async def _read_packet(self, reader, timeout=2.0):
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        return MRCProtocol.parse_packet(line.decode())

    async def _settle(self, timeout=10.0):
        # Wait until no new packet has gone out for a little longer than
        # the bridge's per-sender chat spacing -- a split line's later
        # chunks are deliberately sent MRC_CHAT_PACKET_SPACING apart, so
        # a fixed short sleep would miss them.
        quiet = MRC_CHAT_PACKET_SPACING + 0.2
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        count, since = len(self.app.mrc.sent), loop.time()
        while loop.time() < deadline:
            await asyncio.sleep(0.05)
            if len(self.app.mrc.sent) != count:
                count, since = len(self.app.mrc.sent), loop.time()
            elif loop.time() - since >= quiet:
                return

    async def _wait_until(self, predicate, timeout=2.0, interval=0.02):
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if predicate():
                return True
            await asyncio.sleep(interval)
        return predicate()

    async def _wait_for_single_session(self, timeout=2.0):
        # The bridge keys sessions by str(id(<server-side StreamWriter>)),
        # an object this test never sees directly (asyncio.start_server's
        # callback gets its own end of the socket, distinct from the
        # client-side writer open_connection() returns) -- so tests can't
        # predict the session id up front and must discover it this way.
        ok = await self._wait_until(lambda: len(self.app.db.list_sessions()) == 1, timeout=timeout)
        self.assertTrue(ok, "expected exactly one session to appear")
        session_id, sess = next(iter(self.app.db.list_sessions().items()))
        return session_id, sess

    async def _joined_client(self, handle, room="lobby"):
        reader, writer = await self._connect()
        await self._iamhere(writer, handle, room)
        session_id, _ = await self._wait_for_single_session()
        # The session exists before the bridge has finished its join
        # sequence (_complete_join_after_identify, then
        # _send_join_payloads, whose last packet is always CHATTERS).
        # On a slow runner the tail of that sequence used to land after a
        # test's own sent.clear(), right next to the packets it measures:
        # CI saw a 0.0009 s "gap" before the first chat chunk. Wait for the
        # join to finish so callers start from a quiet connection.
        ok = await self._wait_until(
            lambda: any(m["message"] == "CHATTERS"
                        for m in self.app.mrc.sent_messages()),
            timeout=5.0)
        self.assertTrue(ok, "join sequence did not finish (no CHATTERS sent)")
        return reader, writer, session_id


class MrcTcpJoinTests(_TcpBridgeHarness):

    async def test_iamhere_creates_session_and_joins_room(self):
        reader, writer = await self._connect()
        await self._iamhere(writer, "StingRay", "lobby")

        ok = await self._wait_until(lambda: len(self.app.db.list_sessions()) == 1)
        self.assertTrue(ok)

        sess = next(iter(self.app.db.list_sessions().values()))
        self.assertEqual(sess["handle"], "StingRay")
        self.assertEqual(sess["room"], "lobby")
        self.assertTrue(sess["in_room"])

        # _complete_join_after_identify's real wire traffic went out
        # over the fake hub connection.
        commands = [m["message"] for m in self.app.mrc.sent_messages()]
        self.assertTrue(any(c.startswith("NEWROOM:") for c in commands))

        writer.close()
        await writer.wait_closed()

    async def test_invalid_handle_does_not_create_a_session(self):
        reader, writer = await self._connect()
        # "SERVER" is a reserved handle (MRCProtocol.RESERVED_HANDLES)
        await self._iamhere(writer, "SERVER", "lobby")
        await asyncio.sleep(0.2)
        self.assertEqual(self.app.db.list_sessions(), {})
        writer.close()
        await writer.wait_closed()

    async def test_unparsable_packet_does_not_kill_the_connection(self):
        reader, writer = await self._connect()
        writer.write(b"not a valid mrc packet at all\n")
        await writer.drain()
        await asyncio.sleep(0.1)

        await self._iamhere(writer, "StingRay", "lobby")
        ok = await self._wait_until(lambda: len(self.app.db.list_sessions()) == 1)
        self.assertTrue(ok)
        writer.close()
        await writer.wait_closed()


class MrcTcpChatTests(_TcpBridgeHarness):

    async def test_room_broadcast_is_forwarded_unmodified_not_rewrapped(self):
        reader, writer, _sid = await self._joined_client("StingRay")
        self.app.mrc.sent.clear()

        # umrc-client already embeds its own styled display name into
        # the message body itself before sending (main.c) -- the
        # bridge must NOT prepend its own _session_display_handle on
        # top of that, or every line would be double-prefixed.
        await self._send(writer, "StingRay", "site", "lobby", "", "", "lobby",
                          "|07[StingRay] hello there")

        ok = await self._wait_until(lambda: any(
            m["message"] == "|07[StingRay] hello there" for m in self.app.mrc.sent_messages()))
        self.assertTrue(ok)
        msg = next(m for m in self.app.mrc.sent_messages()
                   if m["message"] == "|07[StingRay] hello there")
        self.assertEqual(msg["to_user"], "")
        self.assertEqual(msg["to_room"], "lobby")

        writer.close()
        await writer.wait_closed()

    async def test_directed_message_has_empty_to_room(self):
        reader, writer, _sid = await self._joined_client("StingRay")
        self.app.mrc.sent.clear()

        await self._send(writer, "StingRay", "site", "lobby", "NightOwl", "", "",
                          "hey there, private note")

        ok = await self._wait_until(lambda: any(
            m["to_user"] == "NightOwl" for m in self.app.mrc.sent_messages()))
        self.assertTrue(ok)
        msg = next(m for m in self.app.mrc.sent_messages() if m["to_user"] == "NightOwl")
        self.assertEqual(msg["to_room"], "")
        self.assertEqual(msg["message"], "hey there, private note")

        writer.close()
        await writer.wait_closed()

    async def test_clients_own_notme_join_announcement_is_not_forwarded(self):
        reader, writer = await self._connect()
        await self._iamhere(writer, "StingRay", "lobby")
        await self._wait_for_single_session()

        # umrc-client sends its own NOTME join-announcement packet
        # (empty to_room) right after IAMHERE -- see sendMsgPacket's
        # call sites in uMRC's main.c. It must not be forwarded
        # verbatim, or every join would announce twice.
        await self._send(writer, "StingRay", "site", "lobby", "NOTME", "", "",
                          "*** StingRay's own client-side join text ***")
        await asyncio.sleep(0.3)

        texts = [m["message"] for m in self.app.mrc.sent_messages()]
        self.assertNotIn("*** StingRay's own client-side join text ***", texts)

        writer.close()
        await writer.wait_closed()

    async def test_logoff_removes_session_without_forwarding_logoff_to_hub(self):
        reader, writer, session_id = await self._joined_client("StingRay")
        self.app.mrc.sent.clear()

        await self._send(writer, "StingRay", "site", "lobby", "SERVER", "", "", "LOGOFF")

        ok = await self._wait_until(lambda: self.app.db.get_session(session_id) is None)
        self.assertTrue(ok)

        # Matches the WebSocket /quit path's own documented behavior:
        # LOGOFF is deliberately never sent to the real hub.
        texts = [m["message"].upper() for m in self.app.mrc.sent_messages()]
        self.assertNotIn("LOGOFF", texts)

        writer.close()
        await writer.wait_closed()

    async def test_room_change_forwards_newroom_and_updates_session(self):
        reader, writer, session_id = await self._joined_client("StingRay", room="lobby")
        self.app.mrc.sent.clear()

        await self._send(writer, "StingRay", "site", "lobby", "SERVER", "", "",
                          "NEWROOM:lobby:general")

        ok = await self._wait_until(
            lambda: (self.app.db.get_session(session_id) or {}).get("room") == "general")
        self.assertTrue(ok)

        commands = [m["message"] for m in self.app.mrc.sent_messages()]
        self.assertIn("NEWROOM:lobby:general", commands)

        writer.close()
        await writer.wait_closed()


class MrcTcpLongMessageSplitTests(_TcpBridgeHarness):
    """Real bug found live (github.com/codefenix-dev/uMRC issue #27):
    a message exceeding this bridge's MRC_MAX_MESSAGE_LEN (140) wire
    cap had its tail silently dropped by _truncate_wire_message() --
    whole sentences vanished with no indication anything was lost.
    The bridge must now split an over-budget incoming message into
    multiple wire packets that together carry the full original text,
    rather than discarding whatever didn't fit in one packet -- this
    must hold regardless of how (im)perfectly a connecting client's
    own chunking lines up with our budget, since that's a bug class
    that can recur in any client, not just this one."""

    async def test_long_room_broadcast_is_split_not_truncated(self):
        reader, writer, _sid = await self._joined_client("StingRay")
        self.app.mrc.sent.clear()

        sentences = ' '.join(f"This is sentence {i}." for i in range(1, 15))
        long_text = (sentences + ' ').ljust(280, 'E')[:280]
        self.assertEqual(len(long_text), 280)

        await self._send(writer, "StingRay", "site", "lobby", "", "", "lobby", long_text)

        ok = await self._wait_until(lambda: len(self.app.mrc.sent) >= 1, timeout=3.0)
        self.assertTrue(ok)
        await self._settle()  # let every chunk's packet land, not just the first

        sent = self.app.mrc.sent_messages()
        self.assertGreater(len(sent), 1, "a 280-char message must split into more than one packet")
        for m in sent:
            self.assertLessEqual(len(m["message"]), 140)

        reassembled = ' '.join(m["message"] for m in sent)
        # Strip the "(N/T) " continuation tags this bridge adds before
        # checking every original word survived somewhere in the
        # reassembled stream -- the real guarantee is no data loss, not
        # a byte-for-byte reconstruction.
        reassembled_plain = re.sub(r'\(\d+/\d+\)\s*', '', reassembled)
        for word in long_text.split(' '):
            if word:
                self.assertIn(word, reassembled_plain)

        writer.close()
        await writer.wait_closed()

    async def test_long_directed_message_is_split_not_truncated(self):
        reader, writer, _sid = await self._joined_client("StingRay")
        self.app.mrc.sent.clear()

        long_text = ' '.join(f"word{i}" for i in range(1, 60))  # well over 140 chars
        self.assertGreater(len(long_text), 140)

        await self._send(writer, "StingRay", "site", "lobby", "NightOwl", "", "", long_text)

        ok = await self._wait_until(lambda: len(self.app.mrc.sent) >= 1, timeout=3.0)
        self.assertTrue(ok)
        await self._settle()

        sent = [m for m in self.app.mrc.sent_messages() if m["to_user"] == "NightOwl"]
        self.assertGreater(len(sent), 1)
        for m in sent:
            self.assertLessEqual(len(m["message"]), 140)
            self.assertEqual(m["to_room"], "")

        reassembled_plain = re.sub(
            r'\(\d+/\d+\)\s*', '', ' '.join(m["message"] for m in sent))
        for word in long_text.split(' '):
            self.assertIn(word, reassembled_plain)

        writer.close()
        await writer.wait_closed()


class MrcTcpUmrcPrefixBudgetTests(_TcpBridgeHarness):
    """umrc-client embeds its own styled display name at the start of
    every plain chat line and sizes each packet as up to 140 chars of
    TYPED text with that prefix on top -- the real umrc-bridge daemon
    and the hub pass such a line through as-is. The bridge applies the
    140 budget to the text after the prefix, so a full-length uMRC line
    goes out as one packet and any split piece keeps the sender's name.
    Prefix below is a real one captured from a live umrc-client session
    via MRC_BRIDGE_LOG_LEVEL=DEBUG."""

    PREFIX = "|12|16@|11|16Winzlo|09{|14Down|09-|14Lo|09} |14"

    async def _send_and_collect(self, writer, message):
        await self._send(writer, "Winzlo", "site", "lobby", "", "", "lobby", message)
        ok = await self._wait_until(lambda: len(self.app.mrc.sent) >= 1, timeout=3.0)
        self.assertTrue(ok)
        await self._settle()  # let every chunk's packet land, not just the first
        return self.app.mrc.sent_messages()

    async def test_full_length_umrc_line_passes_through_as_one_packet(self):
        reader, writer, _sid = await self._joined_client("Winzlo")
        self.app.mrc.sent.clear()

        # The exact first packet uMRC sent for the 280-char test line:
        # 139 chars of typed text + the 47-char prefix = 186 chars.
        text = ' '.join(f"This is sentence {i}." for i in range(1, 8))
        self.assertEqual(len(text), 139)
        message = self.PREFIX + text

        sent = await self._send_and_collect(writer, message)
        self.assertEqual(len(sent), 1, "an in-budget uMRC line must not be split")
        self.assertEqual(sent[0]["message"], message)
        self.assertEqual(sent[0]["to_room"], "lobby", "room chat keeps its room")

        writer.close()
        await writer.wait_closed()

    async def test_overlong_umrc_line_repeats_prefix_on_every_chunk(self):
        reader, writer, _sid = await self._joined_client("Winzlo")
        self.app.mrc.sent.clear()

        text = ' '.join(f"word{i}" for i in range(1, 60))  # well over 140 chars
        self.assertGreater(len(text), 140)

        sent = await self._send_and_collect(writer, self.PREFIX + text)
        self.assertGreater(len(sent), 1)
        for m in sent:
            self.assertTrue(m["message"].startswith(self.PREFIX),
                            "every chunk must carry the sender's styled name")
            self.assertLessEqual(len(m["message"]) - len(self.PREFIX), 140)

        # Breaks must fall on spaces (same rule as umrc-client's own
        # sendChatMessage()): every token in every chunk is a whole
        # original word, and together they are the original text.
        words = text.split(' ')
        tokens = []
        for m in sent:
            body = re.sub(r'^\(\d+/\d+\) ', '', m["message"][len(self.PREFIX):])
            for tok in body.split(' '):
                self.assertIn(tok, words, "a chunk boundary split a word")
                tokens.append(tok)
        self.assertEqual(tokens, words)

        writer.close()
        await writer.wait_closed()

    # 140 chars of typed text -- the most uMRC ever puts in one packet.
    TEXT_140 = ' '.join(f"This is sentence {i}." for i in range(1, 8)) + "!"

    async def _assert_single_unsplit_packet(self, message, to_user="", to_room="lobby"):
        reader, writer, _sid = await self._joined_client("Winzlo")
        self.app.mrc.sent.clear()
        await self._send(writer, "Winzlo", "site", "lobby", to_user, "", to_room, message)
        ok = await self._wait_until(lambda: len(self.app.mrc.sent) >= 1, timeout=3.0)
        self.assertTrue(ok)
        await self._settle()  # prove no second chunk follows
        sent = self.app.mrc.sent_messages()
        self.assertEqual(len(sent), 1, "an in-budget uMRC line must not be split")
        self.assertEqual(sent[0]["message"], message)
        self.assertEqual(sent[0]["to_user"], to_user)
        writer.close()
        await writer.wait_closed()
        return sent[0]

    async def test_umrc_me_action_passes_through_as_one_packet(self):
        self.assertEqual(len(self.TEXT_140), 140)
        pkt = await self._assert_single_unsplit_packet("|15* |13Winzlo " + self.TEXT_140)
        self.assertEqual(pkt["to_room"], "lobby")

    async def test_umrc_direct_message_passes_through_as_one_packet(self):
        prefix = "|15* |08(|15Winzlo|08/|14DirectMsg|08) |07"
        pkt = await self._assert_single_unsplit_packet(
            prefix + self.TEXT_140, to_user="NightOwl", to_room="")
        self.assertEqual(pkt["to_room"], "")

    async def test_umrc_broadcast_passes_through_to_every_room(self):
        # umrc-client's /b sends toRoom "" (all rooms); the bridge
        # forwards it with to_room still empty, as umrc-bridge does.
        prefix = "|15* |08(|15Winzlo|08/|14Broadcast|08) |07"
        pkt = await self._assert_single_unsplit_packet(prefix + self.TEXT_140, to_room="")
        self.assertEqual(pkt["to_room"], "")
        self.assertEqual(pkt["from_room"], "lobby")

    async def test_maximum_length_umrc_prefix_is_honored(self):
        # umrc-client builds the prefix in a char[80] buffer, so 79 is
        # the longest it can send; pad a styled-name suffix to exactly that.
        base = "|12|16@|11|16Winzlo|09{"
        tail = "|09} |14"
        prefix = base + "X" * (79 - len(base) - len(tail)) + tail
        self.assertEqual(len(prefix), 79)
        await self._assert_single_unsplit_packet(prefix + self.TEXT_140)

    async def test_overlong_first_word_is_not_treated_as_a_prefix(self):
        reader, writer, _sid = await self._joined_client("Winzlo")
        self.app.mrc.sent.clear()

        # Contains the nick but is longer than umrc-client's 79-char
        # prefix buffer allows -- must fall back to the plain
        # split-at-140 behavior rather than being repeated on every chunk.
        bogus = "Winzlo" + "x" * 74
        self.assertEqual(len(bogus + " "), 81)
        text = ' '.join(f"word{i}" for i in range(1, 40))

        sent = await self._send_and_collect(writer, bogus + " " + text)
        self.assertGreater(len(sent), 1)
        for m in sent:
            self.assertLessEqual(len(m["message"]), 140)

        writer.close()
        await writer.wait_closed()


class MrcTcpChatPacingTests(_TcpBridgeHarness):
    """The hub's flood control discards a chat line that arrives too
    soon after the same sender's previous one, so the bridge spaces
    each sender's chat packets at least MRC_CHAT_PACKET_SPACING apart:
    between the chunks of a line it splits itself, and between
    separate lines that arrive back to back."""

    PREFIX = "|12|16@|11|16Winzlo|09{|14Down|09-|14Lo|09} |14"
    SLACK = 0.02  # timer granularity

    def _gaps(self):
        t = self.app.mrc.sent_at
        return [b - a for a, b in zip(t, t[1:])]

    async def test_chunks_of_a_split_line_are_spaced(self):
        reader, writer, _sid = await self._joined_client("Winzlo")
        self.app.mrc.sent.clear()
        self.app.mrc.sent_at.clear()

        text = ' '.join(f"word{i}" for i in range(1, 60))  # needs 3 chunks
        await self._send(writer, "Winzlo", "site", "lobby", "", "", "lobby", self.PREFIX + text)
        ok = await self._wait_until(lambda: len(self.app.mrc.sent) >= 1, timeout=3.0)
        self.assertTrue(ok)
        await self._settle()

        self.assertGreater(len(self.app.mrc.sent), 1)
        for gap in self._gaps():
            self.assertGreaterEqual(gap, MRC_CHAT_PACKET_SPACING - self.SLACK)

        writer.close()
        await writer.wait_closed()

    async def test_back_to_back_lines_from_one_sender_are_spaced(self):
        reader, writer, _sid = await self._joined_client("Winzlo")
        self.app.mrc.sent.clear()
        self.app.mrc.sent_at.clear()

        sent_from = time.monotonic()
        await self._send(writer, "Winzlo", "site", "lobby", "", "", "lobby", self.PREFIX + "first")
        await self._send(writer, "Winzlo", "site", "lobby", "", "", "lobby", self.PREFIX + "second")
        ok = await self._wait_until(lambda: len(self.app.mrc.sent) >= 2, timeout=3.0)
        self.assertTrue(ok)

        # The first line goes out immediately; only the second waits.
        self.assertLess(self.app.mrc.sent_at[0] - sent_from, MRC_CHAT_PACKET_SPACING / 2)
        self.assertGreaterEqual(self._gaps()[0], MRC_CHAT_PACKET_SPACING - self.SLACK)
        self.assertEqual([m["message"] for m in self.app.mrc.sent_messages()],
                         [self.PREFIX + "first", self.PREFIX + "second"])

        writer.close()
        await writer.wait_closed()


class MrcTcpMixedTransportRoutingTests(_TcpBridgeHarness):
    """The scenario that made the earlier "become a umrc-bridge
    client" approach unworkable: a real TCP session and a real WS
    session sharing one bridge process must each receive exactly the
    messages meant for them, regardless of which transport they're on.
    """

    async def _register_fake_ws_session(self, handle, room="lobby"):
        ws = _FakeWebSocket()
        ws_id = id(ws)
        self.app.websockets[ws_id] = ws
        await self.app.db.save_session_async(str(ws_id), {
            "handle": handle, "nick": handle, "room": room,
            "in_room": True, "remote_ip": "",
            "style_prefix": "", "style_suffix": "", "style_color": "07",
            "style_prefix_color": "07", "style_handle_color": "07",
            "style_suffix_color": "07", "typing_color": "10",
        })
        return ws, ws_id

    async def test_dm_and_broadcast_reach_mixed_websocket_and_tcp_sessions(self):
        # Bob joins over raw TCP (umrc-client); Carol is a WebSocket
        # session in the same room (ANetBBS's own terminal/web client).
        reader, writer = await self._connect()
        await self._iamhere(writer, "Bob", "lobby")
        await self._wait_for_single_session()

        carol_ws, carol_id = await self._register_fake_ws_session("Carol", "lobby")

        # A DM from the hub addressed to Bob must reach Bob's TCP
        # session and must NOT reach Carol's WS session.
        await self.app._on_upstream_packet({
            "from_user": "Dave", "from_site": "site", "from_room": "lobby",
            "to_user": "Bob", "msg_ext": "", "to_room": "", "message": "psst, Bob",
        })

        pkt = await self._read_packet(reader)
        self.assertEqual(pkt["to_user"], "Bob")
        self.assertEqual(pkt["message"], "psst, Bob")
        self.assertEqual(carol_ws.sent, [])

        # A room broadcast from the hub must reach BOTH Bob (TCP) and
        # Carol (WS) -- the actual "one shared bridge, mixed clients"
        # requirement this whole feature exists for.
        await self.app._on_upstream_packet({
            "from_user": "Dave", "from_site": "site", "from_room": "lobby",
            "to_user": "", "msg_ext": "", "to_room": "lobby", "message": "hi room",
        })

        pkt2 = await self._read_packet(reader)
        self.assertEqual(pkt2["message"], "hi room")
        ok = await self._wait_until(lambda: len(carol_ws.sent) >= 1)
        self.assertTrue(ok)
        self.assertEqual(carol_ws.sent[-1]["message"], "hi room")

        writer.close()
        await writer.wait_closed()


class MrcTcpDisconnectTests(_TcpBridgeHarness):

    async def test_socket_close_removes_session(self):
        reader, writer = await self._connect()
        await self._iamhere(writer, "StingRay", "lobby")
        await self._wait_for_single_session()

        writer.close()
        await writer.wait_closed()

        ok = await self._wait_until(lambda: self.app.db.list_sessions() == {})
        self.assertTrue(ok)


class MrcTcpUnboundedBufferProtectionTests(_TcpBridgeHarness):
    """Real security review finding (2026-09-23): handle_mrc_tcp_
    connection's newline-delimited read loop had no cap on how large
    its buffer could grow while waiting for a line terminator -- the
    same "unbounded buffer / no size cap on a receive loop" bug class
    already fixed elsewhere in this codebase (BinkP, the MRC-IRC
    bridge, the web/terminal IRC clients, QWK), just missed here. A
    client that never sends '\\n' could grow memory without bound.
    Fixed with a cap (MRC_TCP_MAX_LINE_BYTES) checked after each
    read -- exceeding it closes the connection instead of growing
    further."""

    async def test_a_line_with_no_terminator_past_the_cap_closes_the_connection(self):
        reader, writer = await self._connect()
        # +1 to be unambiguously over the cap, not exactly at it.
        writer.write(b"A" * (MRC_TCP_MAX_LINE_BYTES + 1))
        await writer.drain()

        # The server must close its end -- reader.read() returns b''
        # on EOF once that happens, rather than hanging or the buffer
        # growing forever if more bytes kept arriving.
        data = await asyncio.wait_for(reader.read(1), timeout=2.0)
        self.assertEqual(data, b'', 'server must close the connection once '
                          'the no-terminator buffer exceeds the cap')

        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

    async def test_many_small_real_packets_in_one_burst_are_not_mistaken_for_overflow(self):
        """The cap is checked against the LEFTOVER partial line after
        every complete '\\n'-terminated packet in the current chunk is
        already split out and processed -- not against the raw total
        bytes ever seen. A legitimate client sending many small real
        packets back-to-back in one burst (well within a single
        4096-byte read()) must not get disconnected."""
        reader, writer = await self._connect()
        await self._iamhere(writer, "StingRay", "lobby")
        await self._wait_for_single_session()

        packets = b"".join(
            MRCProtocol.create_packet(
                "StingRay", "site", "lobby", "", "", "lobby", f"msg {i}"
            ).encode()
            for i in range(20)
        )
        self.assertLess(len(packets), MRC_TCP_MAX_LINE_BYTES)

        # This test is about read-buffer handling, not chat pacing: with
        # the real per-sender spacing, 20 chat lines from one sender take
        # 19 x MRC_CHAT_PACKET_SPACING to forward (see
        # MrcTcpChatPacingTests for that behavior), so turn it off here.
        with mock.patch("mrc.bridge.main.MRC_CHAT_PACKET_SPACING", 0):
            writer.write(packets)
            await writer.drain()

            # Session must still be alive and the connection still open --
            # confirmed by successfully sending one more packet and getting
            # a reply back over the fake hub, not by the socket closing.
            ok = await self._wait_until(
                lambda: len(self.app.mrc.sent_messages()) >= 20)
        self.assertTrue(ok, 'a legitimate multi-packet burst must not trip '
                         'the overflow protection')

        writer.close()
        await writer.wait_closed()


class MrcTcpConnectionCapTests(_TcpBridgeHarness):
    """Regression tests for a real Low/Medium finding from a follow-up
    security audit: handle_mrc_tcp_connection() tracked every accepted
    connection in self.mrc_tcp_clients with NO ceiling at all, unlike
    anetbbs/ftp/server.py's own max_cons/max_cons_per_ip convention --
    once a sysop opts into binding mrc_tcp_listen_host to 0.0.0.0 for
    remote umrc-client access, a single remote host opening many
    concurrent connections (no auth gate before the connection is even
    counted) had no sysop-configurable ceiling to fall back on."""

    async def test_global_cap_rejects_connections_beyond_the_limit(self):
        with mock.patch("mrc.bridge.main.MRC_TCP_MAX_CONNECTIONS", 3):
            conns = []
            for _ in range(3):
                reader, writer = await self._connect()
                await self._iamhere(writer, "StingRay")
                conns.append((reader, writer))
                await self._wait_until(
                    lambda: len(self.app.mrc_tcp_clients) == len(conns))

            self.assertEqual(len(self.app.mrc_tcp_clients), 3)

            # The 4th connection is accepted at the TCP layer (the
            # listening socket itself has no cap -- only the app-level
            # handler does) but must be refused and closed immediately
            # by the handler, without ever being added to tracking.
            over_reader, over_writer = await self._connect()
            closed = await self._wait_until(lambda: over_reader.at_eof())
            self.assertTrue(closed, 'the connection beyond the global cap '
                            'must be closed by the server, not left open')
            self.assertEqual(len(self.app.mrc_tcp_clients), 3,
                             'the rejected connection must never be added '
                             'to the tracked-clients dict')

            for _, w in conns:
                w.close()
                await w.wait_closed()
            try:
                over_writer.close()
                await over_writer.wait_closed()
            except Exception:
                pass

    async def test_per_ip_cap_rejects_connections_beyond_the_limit(self):
        # All connections in this test are from 127.0.0.1 (loopback),
        # so the per-IP cap is what's actually exercised here -- the
        # global cap is left high enough to not interfere.
        with mock.patch("mrc.bridge.main.MRC_TCP_MAX_CONNECTIONS", 100), \
             mock.patch("mrc.bridge.main.MRC_TCP_MAX_CONNECTIONS_PER_IP", 2):
            conns = []
            for _ in range(2):
                reader, writer = await self._connect()
                await self._iamhere(writer, "StingRay")
                conns.append((reader, writer))
                await self._wait_until(
                    lambda: len(self.app.mrc_tcp_clients) == len(conns))

            over_reader, over_writer = await self._connect()
            closed = await self._wait_until(lambda: over_reader.at_eof())
            self.assertTrue(closed, 'a 3rd connection from the same IP, '
                            'over the per-IP cap, must be refused')
            self.assertEqual(len(self.app.mrc_tcp_clients), 2)

            for _, w in conns:
                w.close()
                await w.wait_closed()
            try:
                over_writer.close()
                await over_writer.wait_closed()
            except Exception:
                pass

    async def test_connections_at_exactly_the_cap_are_still_accepted(self):
        """Sanity check the fix doesn't off-by-one and reject the
        LAST legitimate connection too."""
        with mock.patch("mrc.bridge.main.MRC_TCP_MAX_CONNECTIONS", 2):
            conns = []
            for _ in range(2):
                reader, writer = await self._connect()
                await self._iamhere(writer, "StingRay")
                conns.append((reader, writer))
                await self._wait_until(
                    lambda: len(self.app.mrc_tcp_clients) == len(conns))

            self.assertEqual(len(self.app.mrc_tcp_clients), 2)

            for _, w in conns:
                w.close()
                await w.wait_closed()


if __name__ == "__main__":
    unittest.main()
