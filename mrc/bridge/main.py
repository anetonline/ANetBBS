"""
MRC Bridge WebSocket Server (aiohttp)

- Per-handle style (prefix/suffix/color) stored in BridgeDB profile
- Typing color persisted (local UI only)
- /me action: browser sends "* action"
  Bridge converts to: "|15* |13<nick> action|07"

PRIVACY + ROUTING (MRC-aware):
- True DMs delivered only to recipient + sender sessions
- CLIENT control packets delivered to sessions in that room
- NOTME join/part delivered to sessions in that room
- Room/public traffic delivered only to sessions in that room

- WebSocket keepalive: browser sends {"type":"ping"}; bridge replies {"type":"pong"}
- CTCP support via ctcp_echo_channel room
- room_changed emitted ONLY to the session that changed rooms
- handle_overhead and dm_overhead sent to client on join/style_updated
- Field7 never exceeds 140 chars (truncated server-side)
- Rate limiter only applies to actual user chat (send_message, server_cmd, direct_message)
  — NOT to join_room, leave_room, set_style, ping, pong
"""
import asyncio
import json
import logging
import os
import re
import ssl
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, Optional, Set, Tuple

import aiohttp
from aiohttp import web

from .db import BridgeDB
from .mrc_protocol import MRCProtocol

# MRC_BRIDGE_LOG_LEVEL=DEBUG enables full raw packet tracing (every
# outgoing send_packet() call and every incoming line, both directions,
# tagged "MRC RAW OUT"/"MRC RAW IN") -- deliberately NOT logged at the
# default INFO level, since that would mean every private chat message
# lands in plaintext in the server's own logs permanently. Meant to be
# switched on temporarily (systemd override or `MRC_BRIDGE_LOG_LEVEL=
# DEBUG python3 -m mrc.bridge.main`) to capture a full connect/
# identify/leave/rejoin transcript when static source/spec analysis
# alone isn't enough to root-cause a live wire-level issue.
_LOG_LEVEL = os.environ.get("MRC_BRIDGE_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("mrc_bridge")
# basicConfig() is a no-op if the root logger already has a handler
# (e.g. aiohttp or something else configured logging first under
# systemd) -- setting the level directly on this logger is what
# actually guarantees MRC_BRIDGE_LOG_LEVEL=DEBUG takes effect
# regardless of import order.
logger.setLevel(_LOG_LEVEL)

_BRIDGE_DIR = Path(__file__).parent
_WEB_DIR    = _BRIDGE_DIR.parent / "web"

MRC_MAX_MESSAGE_LEN = 140


def _find_config_path(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    local = _BRIDGE_DIR / "config.json"
    if local.exists():
        return str(local)
    example = _BRIDGE_DIR / "config.example.json"
    if example.exists():
        return str(example)
    raise FileNotFoundError(f"No config.json or config.example.json found in {_BRIDGE_DIR}")


def _load_config(explicit: Optional[str] = None) -> dict:
    path = _find_config_path(explicit)
    with open(path, "r") as f:
        return json.load(f)


def _redact_command_for_logs(cmd: str) -> str:
    c = (cmd or "").strip()
    if not c:
        return c
    upper = c.upper()
    if upper.startswith("IDENTIFY "): return "IDENTIFY ********"
    if upper.startswith("REGISTER "): return "REGISTER ********"
    if upper.startswith("UPDATE "):   return "UPDATE ********"
    if upper.startswith("ROOMPASS "): return "ROOMPASS ********"
    return c


def _format_template(tpl: str, **kwargs) -> str:
    out = tpl or ""
    for k, v in kwargs.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _resolve_message_template(sess: dict, tpl_key: str, global_default: str,
                              handle: str, extra: str = "") -> str:
    """Per-user enter/leave message template (see BridgeApp._session_prefs),
    falling back to the install-wide default when the user hasn't set
    their own. `extra`, if given (e.g. an explicit /quit message),
    is appended in parens -- matches the reference MRC client's
    "Name has left chat (message)" convention."""
    tpl = (sess.get(tpl_key) or "").strip() or global_default
    msg = _format_template(tpl, handle=handle)
    if extra:
        msg = f"{msg} ({extra})"
    return _truncate_wire_message(msg)


def _dm_wrapper(sender_display: str, message: str) -> str:
    sender_display = sender_display.strip()
    message        = message.strip()
    return f"|15* |08(|15{sender_display}|08/|14DirectMsg|08) |07{message}"


def _dm_wrapper_prefix(sender_display: str) -> str:
    """Return only the prefix portion of a DM wrapper (everything before the message text)."""
    return f"|15* |08(|15{sender_display.strip()}|08/|14DirectMsg|08) |07"


def _sanitize_no_tilde(s: str, max_len: int) -> str:
    return (s or "").replace("~", "")[:max_len]


def _clamp_tz_offset(value) -> int:
    """Minutes-from-UTC client display offset, clamped to the real range
    of UTC offsets in use (UTC-12 .. UTC+14) so a bad client payload can't
    push every rendered timestamp off into nonsense."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0
    return max(-720, min(840, v))


def _parse_userlist_text(msg: str) -> list:
    """Parse a `USERLIST:user1,user2@site,...` reply into a plain nick
    list. Wire format confirmed by two independent working parsers
    already in production against a real hub: the web client's
    tryParseUserListFromServerMessage (anetbbs/static/mrc/client.js/
    index.html) and the terminal client's own USERLIST:/CHATTERS:
    handling (anetbbs/features/mrc_chat.py) -- comma-separated, no
    whitespace, optional "@site" suffix per entry."""
    if ":" not in msg:
        return []
    raw = msg.split(":", 1)[1].strip()
    seen = set()
    users = []
    for entry in raw.split(","):
        nick = entry.split("@", 1)[0].strip()
        if nick and nick not in seen:
            seen.add(nick)
            users.append(nick)
    return users


def _norm_pipe_color(code: str) -> str:
    c = (code or "").strip()
    if len(c) == 1 and c.isdigit():
        c = "0" + c
    if len(c) == 2 and c.isdigit():
        n = int(c)
        if 0 <= n <= 15:
            return f"{n:02d}"
    return "07"


def _casefold(s: str) -> str:
    return (s or "").strip().casefold()


_PIPE_CODE_RE = re.compile(r'\|\d{2}')


def _strip_pipe_codes(s: str) -> str:
    """Strip MRC |NN pipe-color codes. The hub decorates its own notice
    text with these (e.g. "welcome back |10StingRay|07") -- confirmed
    live via a real capture -- so any text pulled out of a server
    notice for comparison against a plain stored value (a handle, a
    room name, ...) needs this first or it will never match."""
    return _PIPE_CODE_RE.sub('', s or '')


def _local_time_hhmm() -> str:
    return datetime.now().strftime("%m/%d/%y %H:%M")


def _ctcp_room_from_config(config: dict) -> str:
    return (config.get("ctcp_echo_channel") or "ctcp_echo_channel").strip() or "ctcp_echo_channel"


def _parse_ctcp_payload(body: str) -> Optional[Tuple[str, str]]:
    b = (body or "").strip()
    if not b.startswith("[CTCP] "):
        return None
    rest = b[len("[CTCP] "):].strip()
    if not rest:
        return None
    parts = rest.split(None, 1)
    if len(parts) != 2:
        return None
    from_user = parts[0].strip()
    data      = parts[1].strip()
    if not from_user or not data:
        return None
    return from_user, data


def _ctcp_build_reply(cmd: str, bbs_name: str = "ANetBBS") -> str:
    raw = (cmd or "").strip()
    parts = raw.split(None, 1)
    c = (parts[0] if parts else "").upper()
    rest = (parts[1] if len(parts) > 1 else "").strip()

    if c == "VERSION":
        # Identify ourselves with bbs_name + our version so peers querying
        # us can see who they're talking to, rather than a generic blob.
        return f"VERSION {bbs_name} (ANetBBS MRC Bridge v1.4[sr])"
    if c == "TIME":
        return f"TIME {_local_time_hhmm()}"
    if c == "PING":
        return f"PING {rest}".rstrip()
    if c == "CLIENTINFO":
        return "CLIENTINFO VERSION TIME PING CLIENTINFO"
    return f"{c} Unsupported CTCP command"


def _truncate_wire_message(body: str, max_len: int = MRC_MAX_MESSAGE_LEN) -> str:
    b = body or ""
    return b if len(b) <= max_len else b[:max_len]


class MRCConnection:
    def __init__(self, config: dict, status_callback=None):
        self.config          = config
        self.status_callback = status_callback

        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected  = False
        self._closing   = False

        self._rx_buf            = b""
        self._send_queue: Deque[str] = deque()
        self._send_queue_max    = int(self.config.get("mrc_send_queue_max", 500))

        self._reconnector_task: Optional[asyncio.Task] = None
        self._io_task:          Optional[asyncio.Task] = None

        self.message_callbacks = []

    async def start(self):
        self._closing = False
        if not self._reconnector_task or self._reconnector_task.done():
            self._reconnector_task = asyncio.create_task(self._reconnect_loop())

    async def stop(self):
        self._closing = True
        for t in (self._reconnector_task, self._io_task):
            if t:
                t.cancel()
        # Graceful shutdown notice, matching the reference multiplexer's
        # own send_shutdown() on exit -- lets the hub clean up our side
        # immediately (release the connection slot, drop any per-user
        # room state) instead of waiting out its own dead-peer timeout.
        # Best-effort: if the write itself fails because the socket is
        # already gone, disconnect() right after still runs regardless.
        if self.connected and self.writer:
            try:
                pkt = MRCProtocol.create_control_command(
                    "SHUTDOWN", bbs=self.config.get("bridge_bbs", ""))
                self.writer.write(pkt.encode())
                await self.writer.drain()
            except Exception:
                pass
        await self.disconnect()

    async def _set_connected(self, value: bool):
        if self.connected == value:
            return
        self.connected = value
        if self.status_callback:
            await self.status_callback("upstream_connected" if value else "upstream_disconnected")

    async def connect(self) -> bool:
        host      = self.config["mrc_host"]
        port      = int(self.config["mrc_port"])
        use_ssl   = bool(self.config.get("use_ssl", False))
        timeout_s = float(self.config.get("mrc_connect_timeout_seconds", 5))

        logger.info(f"Connecting to MRC server {host}:{port} (SSL: {use_ssl})")

        ssl_context = None
        if use_ssl:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode    = ssl.CERT_NONE

        try:
            coro = asyncio.open_connection(host, port, ssl=ssl_context)
            self.reader, self.writer = await asyncio.wait_for(coro, timeout=timeout_s)

            hs = MRCProtocol.create_handshake(self.config["bridge_bbs"], self.config.get("platform_info", ""))
            self.writer.write(hs.encode())
            await self.writer.drain()

            await self._set_connected(True)
            logger.info("Connected to MRC server")

            await self.send_capabilities()
            await self.send_bbsmeta()
            await self.send_info_fields()

            if not self._io_task or self._io_task.done():
                self._io_task = asyncio.create_task(self.receive_loop())

            await self._flush_queue()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to MRC server: {e}")
            await self._set_connected(False)
            await self.disconnect()
            return False

    async def _reconnect_loop(self):
        delay     = float(self.config.get("mrc_reconnect_initial_delay", 1))
        max_delay = float(self.config.get("mrc_reconnect_max_delay", 30))

        while not self._closing:
            if not self.connected:
                ok = await self.connect()
                if not ok:
                    await asyncio.sleep(delay)
                    delay = min(max_delay, delay * 2)
                    continue
                delay = float(self.config.get("mrc_reconnect_initial_delay", 1))
            await asyncio.sleep(1)

    async def send_capabilities(self):
        caps    = self.config.get("capabilities", []) or []
        cap_str = " ".join(caps).strip()
        cmd     = f"CAPABILITIES:{cap_str}" if cap_str else "CAPABILITIES:"
        pkt     = MRCProtocol.create_control_command(cmd, bbs=self.config["bridge_bbs"])
        await self.send_packet(pkt)

    async def send_bbsmeta(self):
        """Announce security level + sysop, matching the reference
        client's own connect sequence (mrc_send_bbsmeta, sent right
        before mrc_send_info_fields -- helper_protocol.c). ANetBBS never
        sent this at all. Added alongside the send_info_fields fixes
        above since both were live-suspected contributors to the BBS
        directory staying blank and couldn't be isolated without
        another live round-trip -- SecLevel(100) matches the reference
        client's own hardcoded constant, not sourced from config.
        Sysop name is sent with any pipe-color codes intact -- see
        send_info_fields for why this isn't stripped."""
        sysop = (self.config.get("bbs_sysop", "") or "").strip() or "SysOp"
        body = f"BBSMETA: SecLevel(100) Sysop({sysop})"
        pkt = MRCProtocol.create_packet(
            "CLIENT", self.config["bridge_bbs"], "", "SERVER",
            str(time.time()), "", body)
        logger.info("MRC outgoing BBSMETA packet: %r", pkt)
        await self.send_packet(pkt)

    async def send_info_fields(self):
        """Broadcast this BBS's own description/telnet/ssh/website/sysop
        so other clients on the network can look up "BBS info" for us --
        verified against the real reference C client
        (anetmrc_v1.3.9/src/helper_protocol.c's mrc_send_info_fields).
        ANetBBS never sent any of these fields at all, so this info was
        always empty for anyone looking us up from another client, even
        ones (like the Mystic Python client) that surface it -- reported
        live by the sysop as "the BBS info that is not shown with
        ANetBBS but is with the mystic mrc client." Each field is only
        sent if configured, matching the reference client's own
        per-field `if (...[0])` guards -- an unconfigured field is
        simply omitted, not sent blank.

        Pipe-color codes in these values are sent through untouched --
        confirmed with the sysop (2 years running this exact hub/
        protocol before ANetBBS existed) that coloring these fields is
        expected and supported, same as the BBS name itself (`bbs`
        below, from `bridge_bbs`) was never stripped either. An earlier
        version of this method defensively stripped pipe codes here,
        reasoning from the reference client's own plain-text MRCBBS.DAT
        example -- that was the wrong call; the identify-handle
        extraction fix (_extract_identified_handle) is the unrelated,
        still-necessary one (an internal string comparison against a
        plain-text stored handle, not anything ever displayed)."""
        bbs = self.config["bridge_bbs"]
        fields = (
            ("INFODSC", self.config.get("bbs_description", "")),
            ("INFOTEL", self.config.get("bbs_telnet", "")),
            ("INFOSSH", self.config.get("bbs_ssh", "")),
            ("INFOWEB", self.config.get("bbs_website", "")),
            ("INFOSYS", self.config.get("bbs_sysop", "")),
        )
        for prefix, value in fields:
            value = (value or "").strip()
            if not value:
                continue
            # Field order matches the reference client's actual sent
            # bytes exactly (helper_protocol.c's mrc_send_info_fields:
            # `"CLIENT", bbs_name, "", "SERVER", "ALL", msgext, body`) --
            # "ALL" in the msg_ext wire position, the epoch timestamp in
            # the to_room wire position, backwards from every other
            # broadcast in this protocol. NOTE: this alone did NOT fix
            # the live symptom (entry stayed blank even after this
            # shipped) -- kept as still-plausibly-correct since it
            # matches the reference byte-for-byte, but the real root
            # cause is still unconfirmed. See the logged raw packet
            # below for direct wire-level debugging.
            pkt = MRCProtocol.create_packet(
                "CLIENT", bbs, "", "SERVER", "ALL", str(time.time()),
                f"{prefix}: {value}")
            logger.info("MRC outgoing %s packet: %r", prefix, pkt)
            await self.send_packet(pkt)

    def _queue_packet(self, packet: str):
        if len(self._send_queue) >= self._send_queue_max:
            self._send_queue.popleft()
        self._send_queue.append(packet)

    async def _flush_queue(self):
        if not self.connected or not self.writer:
            return
        while self._send_queue:
            pkt = self._send_queue.popleft()
            try:
                self.writer.write(pkt.encode())
                await self.writer.drain()
            except Exception:
                self._send_queue.appendleft(pkt)
                await self._set_connected(False)
                await self.disconnect()
                return

    async def send_packet(self, packet: str):
        if not packet:
            return
        logger.debug("MRC RAW OUT: %r", packet)
        if not self.connected or not self.writer:
            self._queue_packet(packet)
            return
        try:
            self.writer.write(packet.encode())
            await self.writer.drain()
        except Exception:
            await self._set_connected(False)
            await self.disconnect()
            self._queue_packet(packet)

    @staticmethod
    def _looks_like_packet(line: str) -> bool:
        return bool(line) and line.count("~") >= 6

    async def receive_loop(self):
        self._rx_buf = b""
        while not self._closing and self.reader:
            try:
                chunk = await self.reader.read(4096)
                if not chunk:
                    logger.warning("Upstream connection closed by server")
                    await self._set_connected(False)
                    await self.disconnect()
                    break

                self._rx_buf += chunk
                while b"\n" in self._rx_buf:
                    raw, self._rx_buf = self._rx_buf.split(b"\n", 1)
                    line = raw.decode(errors="ignore").strip()
                    if not line:
                        continue
                    if not self._looks_like_packet(line):
                        logger.warning(f"Skipping non-packet line: {line!r}")
                        continue
                    logger.debug("MRC RAW IN: %r", line)
                    await self._handle_packet(line)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error receiving packet: {e}")
                await self._set_connected(False)
                await self.disconnect()
                break

    async def _handle_packet(self, line: str):
        try:
            parsed = MRCProtocol.parse_packet(line)
        except Exception as e:
            logger.error(f"Error parsing packet: {e}")
            return

        # Hub-pushed version-enforcement notices. Confirmed against the
        # reference Python multiplexer (mrc_client.py's deliver_mrc):
        # these arrive from_user=SERVER with no particular to_user, and
        # were never handled here at all -- they used to fall straight
        # through to message_callbacks and get broadcast into chat as
        # if they were regular room text. NEWUPDATE is informational
        # (a newer client exists); OLDVERSION means the hub has judged
        # our advertised platform_info too old and is about to drop
        # this connection -- log it loudly rather than let it become a
        # silent reconnect-every-few-seconds loop with zero diagnostic,
        # since every such reconnect starts a brand-new upstream
        # session and could plausibly look like "trust" lapsing far
        # more often than the real weeks-long window.
        if parsed.get("from_user") == "SERVER":
            top_msg = (parsed.get("message") or "").strip()
            if top_msg.upper().startswith("NEWUPDATE:"):
                logger.warning(
                    "MRC hub reports a newer client version is available: %s "
                    "(we advertise: %s)",
                    top_msg.split(":", 1)[1].strip() if ":" in top_msg else "",
                    self.config.get("platform_info", ""))
                return
            if top_msg.upper().startswith("OLDVERSION:"):
                logger.error(
                    "MRC hub says our advertised version (%s) is too old and "
                    "is disconnecting us -- hub wants: %s. The bridge will "
                    "keep retrying per its normal reconnect backoff, but the "
                    "hub will keep rejecting every attempt until "
                    "platform_info in mrc/bridge/config.json is updated. "
                    "This is a likely cause of chat trust seeming to lapse "
                    "far more often than expected -- every rejected "
                    "reconnect starts a brand-new upstream session.",
                    self.config.get("platform_info", ""),
                    top_msg.split(":", 1)[1].strip() if ":" in top_msg else "")
                return

        if parsed.get("from_user") == "SERVER" and parsed.get("to_user") == "CLIENT":
            msg = (parsed.get("message") or "").strip()
            if msg.upper() == "PING":
                pkt = MRCProtocol.create_imalive(
                    self.config["bridge_bbs"],
                    pid=str(os.getpid()),
                    msg_ext=str(time.time()),
                    bbsname=self.config["bridge_bbs"],
                )
                await self.send_packet(pkt)
                return
            if msg.upper().startswith("PONG"):
                return

        for cb in self.message_callbacks:
            await cb(parsed)

    def add_message_callback(self, cb):
        self.message_callbacks.append(cb)

    async def disconnect(self):
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        self.writer = None
        self.reader = None
        await self._set_connected(False)
        logger.info("Disconnected from MRC server")


class BridgeApp:
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path
        self.config      = _load_config(config_path)

        data_dir = self.config.get("data_dir", str(_BRIDGE_DIR / "data"))
        self.db  = BridgeDB(data_dir)

        self.websockets:   Dict[int, web.WebSocketResponse] = {}
        self.rate_limiter: Dict[int, float]                 = {}
        # Real caller IP per WebSocket connection, keyed by ws_id --
        # populated in handle_websocket() at accept time (for the web
        # client, from the incoming HTTP request, honoring nginx's
        # X-Forwarded-For since /mrcws is reverse-proxied) and possibly
        # overridden per-session by an explicit "ip" field in the
        # join_room message (for the terminal client, whose WebSocket
        # connection to this bridge always originates from localhost --
        # ws://127.0.0.1:8080 -- so the bridge has no way to observe the
        # real caller's IP itself for that path; the terminal door has
        # to tell us). See _handle_join_room and the real bug this
        # closes: sess["remote_ip"] was read in two places
        # (_send_join_payloads) but never written ANYWHERE, so the
        # USERIP: packet the reference client sends on every join was
        # never sent at all, for any user, on either client -- if the
        # hub uses USERIP to recognize a returning already-identified
        # connection, this would force a fresh /identify on every single
        # join with no way around it.
        self._ws_remote_ip: Dict[int, str] = {}

        # Default False: verified against the real reference client
        # (anetmrc_v1.3.9/src/helper_protocol.c) -- it sends NEWROOM and
        # joins the room unconditionally right after the handshake,
        # never waiting on identify; /identify is purely optional
        # ("MRC Trust" for a registered handle), never a requirement to
        # participate. This bridge's identify-gate defaulted to True
        # with no documented way to discover or disable it (not even in
        # config.example.json), silently blocking every caller on every
        # install from chatting at all -- including unregistered/casual
        # handles, which the real network lets chat freely -- until they
        # identified with a registered account. Reported live as "I
        # still have to identify every single time" / "blocked from
        # chatting entirely until identified".
        self.identify_required_mode  = bool(self.config.get("identify_required_mode", False))
        self.post_identify_auto_join = bool(self.config.get("post_identify_auto_join", False))

        self.announce_join_part                = bool(self.config.get("announce_join_part", True))
        self.request_banners_on_join           = bool(self.config.get("request_banners_on_join", True))
        self.request_motd_on_join              = bool(self.config.get("request_motd_on_join", True))
        self.userlist_refresh_on_server_events = bool(self.config.get("userlist_refresh_on_server_events", True))
        self.userlist_refresh_interval_seconds = float(self.config.get("userlist_refresh_interval_seconds", 0))

        self.join_packet_delay_ms = int(self.config.get("join_packet_delay_ms", 80))

        self.join_message_tpl = self.config.get("join_message", "|07- |11{handle} |03has arrived.")
        self.exit_message_tpl = self.config.get("exit_message", "|07- |12{handle} |04has left chat.")

        self.default_style_prefix = _sanitize_no_tilde(self.config.get("default_style_prefix", ""), 16)
        self.default_style_suffix = _sanitize_no_tilde(self.config.get("default_style_suffix", ""), 16)
        self.default_style_color  = _norm_pipe_color(self.config.get("default_style_color", "07"))

        self.ctcp_room = MRCProtocol.norm_room(_ctcp_room_from_config(self.config))

        self.mrc = MRCConnection(self.config, status_callback=self._broadcast_bridge_status)
        self.mrc.add_message_callback(self._on_upstream_packet)

        self.tasks = []
        self.ws_disconnect_grace_seconds      = float(self.config.get("ws_disconnect_grace_seconds", 12))
        self.pending_disconnects: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _sleep_delay(self):
        await asyncio.sleep(self.join_packet_delay_ms / 1000.0)

    def _session_effective_nick(self, sess: dict) -> str:
        return (sess.get("nick") or sess.get("handle") or "").strip()

    def _session_room(self, sess: dict) -> str:
        return MRCProtocol.norm_room(sess.get("room") or "lobby")

    def _session_style(self, sess: dict) -> dict:
        base = _norm_pipe_color(sess.get("style_color") or "07")
        return {
            "prefix":       (sess.get("style_prefix") or "").strip(),
            "suffix":       (sess.get("style_suffix") or "").strip(),
            "prefix_color": _norm_pipe_color(sess.get("style_prefix_color") or base),
            "handle_color": _norm_pipe_color(sess.get("style_handle_color") or base),
            "suffix_color": _norm_pipe_color(sess.get("style_suffix_color") or base),
            "typing_color": _norm_pipe_color(sess.get("typing_color") or "10"),
            "color":        base,
        }

    def _session_prefs(self, sess: dict) -> dict:
        """Chat preferences distinct from nick style (see _session_style) --
        kept as a separate request/response pair (set_prefs/prefs_updated,
        mirroring set_style/style_updated) rather than folded into style,
        since these are conceptually different settings groups, same as
        the reference MRC client's /SET splits fields into logical groups
        rather than one flat bag."""
        return {
            "twit_list":          list(sess.get("twit_list") or []),
            "twit_filter_enabled": bool(sess.get("twit_filter_enabled", True)),
            "broadcast_shield":   bool(sess.get("broadcast_shield", False)),
            "ticker_enabled":     bool(sess.get("ticker_enabled", True)),
            "enter_msg_tpl":      (sess.get("enter_msg_tpl") or "").strip(),
            "leave_msg_tpl":      (sess.get("leave_msg_tpl") or "").strip(),
            "quit_msg":           (sess.get("quit_msg") or "").strip(),
            # Room a client should land in on its *next* connect -- purely
            # a client-applied hint (see mrc_chat.py's _apply_prefs
            # auto-/join-on-initial-join logic), same as tz_offset below;
            # the bridge itself always still honors whatever room a
            # join_room message explicitly asks for.
            "default_room":       (sess.get("default_room") or "").strip(),
            # '12' or '24' -- purely a client rendering hint, same as
            # tz_offset; the bridge never interprets it.
            "clock_format":       (sess.get("clock_format") or "24").strip(),
            # Minutes offset from UTC for client-side timestamp display
            # (e.g. -300 for US Eastern). Purely a client rendering hint --
            # the bridge never interprets it, only stores/echoes it, same
            # as the message templates above.
            "tz_offset":          _clamp_tz_offset(sess.get("tz_offset", 0)),
        }

    def _session_display_handle(self, sess: dict) -> str:
        nick = self._session_effective_nick(sess)
        st   = self._session_style(sess)
        return f"|{st['prefix_color']}{st['prefix']}|{st['handle_color']}{nick}|{st['suffix_color']}{st['suffix']}|07"

    def _session_display_handle_wire_len(self, sess: dict) -> int:
        """
        Wire chars consumed by the styled display handle in a chat message.
        Field 7 = "{display_handle} {message}"  (+1 for the space separator).
        """
        return len(self._session_display_handle(sess)) + 1

    def _session_dm_overhead(self, sess: dict) -> int:
        """
        Wire chars consumed by the DM wrapper prefix.
        Field 7 = "{dm_wrapper_prefix}{message}" (wrapper already ends with a space).
        """
        return len(_dm_wrapper_prefix(self._session_display_handle(sess)))

    def _session_action_overhead(self, sess: dict) -> int:
        """
        Wire chars consumed by the /me action wrapper -- fixed |15/|13/|07
        colors, NOT the user's own style (see _handle_send_message's
        action branch). Field 7 = "|15* |13{nick} {action text}|07".
        Real gap this closes: neither client ever accounted for this
        wrapper at all when deciding how much of a /me action was safe
        to type -- they budgeted against the full 140-char wire limit
        with zero reservation, so any action text within ~12+len(nick)
        chars of that limit had its tail silently cut off by
        _truncate_wire_message() with no warning, the exact silent-
        truncation _chat_wire_cap()/_dm_wire_cap() already exist to
        prevent for plain chat and DMs.
        """
        nick = self._session_effective_nick(sess)
        return len(f"|15* |13{nick} ") + len("|07")

    def _is_action_body(self, message: str) -> bool:
        m = (message or "").lstrip()
        return m.startswith("|15*") or m.startswith("|15 *") or m.startswith("* ")

    def _session_matches_user(self, sess: dict, user: str) -> bool:
        u = _casefold(user)
        return u != "" and (u == _casefold(sess.get("handle")) or u == _casefold(sess.get("nick")))


    def _pending_disconnect_key(self, handle: str) -> str:
        return _casefold(handle)

    def _cancel_pending_disconnect(self, handle: str):
        key = self._pending_disconnect_key(handle)
        task = self.pending_disconnects.pop(key, None)
        if task and not task.done():
            task.cancel()
            logger.info(f"Cancelled pending disconnect grace for handle={handle}")

    async def _delayed_session_logoff(self, ws_id: int, eff_nick: str, room: str):
        key = self._pending_disconnect_key(eff_nick)
        try:
            await asyncio.sleep(max(0.0, self.ws_disconnect_grace_seconds))
            sess = self.db.get_session(str(ws_id))
            if not sess:
                return
            if not sess.get("in_room"):
                self.db.delete_session(str(ws_id))
                return
            if _casefold(self._session_effective_nick(sess)) != key:
                return
            if self.announce_join_part and eff_nick:
                # No `extra` message here -- this is an abrupt-disconnect
                # grace-timeout path, not an explicit /quit, so there's no
                # user-supplied quit message to append. A saved default
                # quit_msg still isn't appropriate here either: that's for
                # a deliberate /quit, not a dropped connection.
                exit_msg = _resolve_message_template(sess, "leave_msg_tpl", self.exit_message_tpl, eff_nick)
                await self.mrc.send_packet(MRCProtocol.create_message(eff_nick, self.config["bridge_bbs"], room, "NOTME", "", exit_msg))
            # LOGOFF deliberately NOT sent here -- see _handle_leave_room's
            # comment for why (real live evidence: sending it ends the
            # hub's MRC Trust state for this handle immediately, forcing
            # a fresh /identify on the very next join even though the
            # bridge's own connection to the hub never dropped).
            self.db.delete_session(str(ws_id))
            logger.info(f"Applied delayed disconnect logoff for handle={eff_nick} room={room}")
        except asyncio.CancelledError:
            logger.info(f"Delayed disconnect cancelled for handle={eff_nick}")
            raise
        finally:
            cur = self.pending_disconnects.get(key)
            if cur is asyncio.current_task():
                self.pending_disconnects.pop(key, None)

    def _sessions_for_user(self, user: str) -> Set[str]:
        out: Set[str] = set()
        u = _casefold(user)
        if not u:
            return out
        for ws_id_str, sess in self.db.list_sessions().items():
            if self._session_matches_user(sess, user):
                out.add(ws_id_str)
        return out

    def _sessions_in_room(self, room: str) -> Set[str]:
        r   = MRCProtocol.norm_room(room)
        out: Set[str] = set()
        for ws_id_str, sess in self.db.list_sessions().items():
            if not sess.get("in_room"):
                continue
            if self._session_room(sess) == r:
                out.add(ws_id_str)
        return out

    @staticmethod
    async def _safe_send(ws, payload: dict):
        """Every ws.send_json() call must go through this. A client can
        close its socket (browser tab closed, terminal door killed) in
        the brief window between us receiving its message and sending a
        reply -- aiohttp then raises ClientConnectionResetError out of
        send_json() itself (web_ws.py's _write_websocket_frame). Left
        unguarded, that exception propagates out of handle_websocket's
        `async for msg in ws:` loop and aiohttp logs it as "Error
        handling request" for that connection -- confirmed live via a
        real production traceback (_handle_leave_room's "left" reply
        during a race with the client already disconnecting). Harmless
        to the rest of the process either way (aiohttp catches it at the
        per-request level, it doesn't crash the whole service), but it's
        needless log noise and violates the guard pattern already used
        by the handful of call sites that happened to remember it."""
        try:
            await ws.send_json(payload)
        except Exception:
            pass

    async def _send_to_session(self, ws_id_str: str, payload: dict):
        try:
            ws_id = int(ws_id_str)
        except Exception:
            return
        ws = self.websockets.get(ws_id)
        if not ws:
            return
        await self._safe_send(ws, payload)

    async def _send_to_sessions(self, ws_ids: Set[str], payload: dict):
        for ws_id_str in ws_ids:
            await self._send_to_session(ws_id_str, payload)

    # ------------------------------------------------------------------
    # Upstream status / rejoin
    # ------------------------------------------------------------------

    async def _broadcast_bridge_status(self, status: str):
        payload = {"type": "bridge_status", "status": status}
        for ws in list(self.websockets.values()):
            await self._safe_send(ws, payload)
        if status == "upstream_connected":
            await self._rejoin_all_sessions()

    async def _rejoin_all_sessions(self):
        for _, sess in self.db.list_sessions().items():
            if not sess.get("in_room"):
                continue
            eff_nick = self._session_effective_nick(sess)
            room     = self._session_room(sess)
            if not eff_nick or not room:
                continue
            await self.mrc.send_packet(MRCProtocol.create_iamhere(eff_nick, self.config["bridge_bbs"], room, "ACTIVE"))
            await self._sleep_delay()
            await self.mrc.send_packet(MRCProtocol.create_newroom(eff_nick, self.config["bridge_bbs"], "", room))
            await self._sleep_delay()
            await self._send_userlist_control(room)

    def _rooms_with_active_sessions(self) -> Set[str]:
        rooms: Set[str] = set()
        for _, sess in self.db.list_sessions().items():
            if sess.get("in_room"):
                r = self._session_room(sess)
                if r:
                    rooms.add(r)
        return rooms

    async def _maybe_refresh_userlist_on_server_text(self, text: str):
        if not self.userlist_refresh_on_server_events:
            return
        triggers = ("Joining", "Leaving", "Timeout", "Rename", "Linked", "Unlink")
        if any(x in (text or "") for x in triggers):
            for r in self._rooms_with_active_sessions():
                await self._send_userlist_control(r)

    async def _send_userlist_control(self, room: str):
        pkt = MRCProtocol.create_control_command(
            "USERLIST",
            user="CLIENT",
            bbs=self.config["bridge_bbs"],
            room=room,
            msg_ext="ALL",
        )
        await self.mrc.send_packet(pkt)

    async def _send_stats_control(self, room: str):
        """Request server STATS -- mirrors _send_userlist_control's
        "as CLIENT" addressing exactly (proven to route/broadcast
        correctly for USERLIST; unlike USERLIST, the STATS reply text
        is NOT parsed into fields -- see the note on the ticker/banner
        text-feed plan for why: no reference implementation (the C
        client, the Mystic multiplexer's OWN upstream requests) parses
        a STATS reply into structured fields, it's opaque display text
        same as MOTD/BANNERS/CHANGELOG/ROUTING."""
        pkt = MRCProtocol.create_control_command(
            "STATS",
            user="CLIENT",
            bbs=self.config["bridge_bbs"],
            room=room,
        )
        await self.mrc.send_packet(pkt)

    async def _send_join_payloads(self, eff_nick: str, room: str, remote_ip: str = ""):
        if remote_ip:
            # Per the official MRC protocol spec, USERIP's documented
            # template is "user~bbs~~SERVER~msgext~~USERIP:ipaddress~"
            # -- fromRoom empty, unlike the generic command path
            # (create_server_command) which populates it. Built
            # directly rather than through that helper for this reason.
            pkt = MRCProtocol.create_packet(
                eff_nick, self.config["bridge_bbs"], '', 'SERVER', '', '',
                f"USERIP:{remote_ip}")
            await self.mrc.send_packet(pkt)
            await self._sleep_delay()
        if self.request_banners_on_join:
            await self.mrc.send_packet(MRCProtocol.create_server_command(eff_nick, self.config["bridge_bbs"], room, "BANNERS"))
            await self._sleep_delay()
        if self.request_motd_on_join:
            await self.mrc.send_packet(MRCProtocol.create_server_command(eff_nick, self.config["bridge_bbs"], room, "MOTD"))
            await self._sleep_delay()
        await self._send_userlist_control(room)
        await self._sleep_delay()
        await self.mrc.send_packet(MRCProtocol.create_server_command(eff_nick, self.config["bridge_bbs"], room, "CHATTERS"))

    async def _complete_join_after_identify(self, ws_id_str: str, sess: dict):
        eff_nick = self._session_effective_nick(sess)
        room     = self._session_room(sess)
        self._cancel_pending_disconnect(eff_nick)
        if not eff_nick or not room:
            return

        if self.announce_join_part:
            join_msg = _resolve_message_template(sess, "enter_msg_tpl", self.join_message_tpl, eff_nick)
            await self.mrc.send_packet(MRCProtocol.create_message(eff_nick, self.config["bridge_bbs"], room, "NOTME", "", join_msg))
            await self._sleep_delay()

        await self.mrc.send_packet(MRCProtocol.create_newroom(eff_nick, self.config["bridge_bbs"], "", room))
        await self._sleep_delay()

        await self._send_join_payloads(eff_nick, room, sess.get("remote_ip", ""))

        sess["waiting_for_identify"] = False
        sess["in_room"]              = True
        self.db.save_session(ws_id_str, sess)

    async def _broadcast_info(self, message: str):
        payload = {"type": "info", "message": message}
        for ws in list(self.websockets.values()):
            await self._safe_send(ws, payload)

    @staticmethod
    def _extract_identified_handle(server_msg: str) -> Optional[str]:
        # The hub decorates this notice with pipe-color codes around the
        # handle itself -- confirmed live: "...Successfully identified,
        # welcome back |10StingRay|07". Strip them before searching, or
        # the extracted "handle" comes out as "|10StingRay|07" and never
        # matches any real session's plain-text handle, silently
        # breaking both the strict-mode auto-join and the default-mode
        # self-heal that depend on this. Root cause of "still have to
        # identify" surviving the earlier fixes on the live server.
        msg = _strip_pipe_codes((server_msg or "").strip()).strip()
        low = msg.lower()
        idx = low.rfind("welcome back ")
        if idx == -1:
            return None
        tail = msg[idx + len("welcome back "):].strip()
        return (tail.split()[0].strip() or None) if tail else None

    # ------------------------------------------------------------------
    # CTCP
    # ------------------------------------------------------------------

    async def _handle_incoming_ctcp(self, parsed: dict):
        msg       = (parsed.get("message") or "").strip()
        to_user   = (parsed.get("to_user") or "").strip()
        from_room = MRCProtocol.norm_room((parsed.get("from_room") or "").strip())
        to_room   = MRCProtocol.norm_room((parsed.get("to_room") or "").strip())

        if from_room != self.ctcp_room and to_room != self.ctcp_room:
            return False

        req = _parse_ctcp_payload(msg)
        if not req:
            return False

        req_from_user, data = req
        parts = data.split(None, 1)
        if len(parts) < 2:
            return True

        target  = parts[0].strip()
        command = parts[1].strip()

        # Prevent self-CTCP loops/flooding
        if req_from_user and target and _casefold(req_from_user) == _casefold(target):
            return True

        should_respond = False

        if target == "*":
            should_respond = True
        elif to_user and _casefold(target) == _casefold(to_user):
            # only respond if the packet was actually routed to this local user
            should_respond = bool(self._sessions_for_user(to_user))

        if not should_respond:
            return False

        bbs_name = self.config.get('bridge_bbs', 'ANetBBS')
        reply_body = _truncate_wire_message(
            f"[CTCP-REPLY] {req_from_user} {_ctcp_build_reply(command, bbs_name)}".strip()
        )
        reply_from = (to_user or target or "SERVER").strip()
        pkt = MRCProtocol.create_message(
            reply_from,
            self.config["bridge_bbs"],
            self.ctcp_room,
            req_from_user,
            self.ctcp_room,
            reply_body
        )
        await self.mrc.send_packet(pkt)
        return True

    # ------------------------------------------------------------------
    # Upstream packet dispatch
    # ------------------------------------------------------------------

    async def _on_upstream_packet(self, parsed: dict):
        msg       = (parsed.get("message")  or "").strip()
        from_user = (parsed.get("from_user") or "").strip()
        to_user   = (parsed.get("to_user")   or "").strip()
        from_room = (parsed.get("from_room") or "").strip()
        to_room   = (parsed.get("to_room")   or "").strip()

        try:
            ctcp_handled = await self._handle_incoming_ctcp(parsed)
            if ctcp_handled:
                return
        except Exception as e:
            logger.warning(f"CTCP handler error: {e}")

        if from_user == "SERVER":
            low = msg.lower()
            # Temporary diagnostic: the "successfully identified"/
            # "welcome back " detection below predates this session and
            # has no confirmed-against-a-real-hub-reply verification --
            # log the raw text of anything identify/join-rejection
            # shaped so the actual wire wording can be confirmed rather
            # than assumed. Safe to remove once confirmed correct.
            if any(kw in low for kw in ("identif", "cannot join", "no route")):
                logger.info("MRC identify/join-related SERVER text: %r", msg)
            if "successfully identified" in low:
                identified_handle = self._extract_identified_handle(msg)
                if identified_handle:
                    for ws_id_str, sess in self.db.list_sessions().items():
                        if (sess.get("handle") or "").strip().lower() != identified_handle.lower():
                            continue
                        # Only act on sessions with a genuinely live
                        # WebSocket right now. self.db's session records
                        # can outlive the actual connection -- a hard
                        # process restart (systemctl restart, common
                        # during troubleshooting) doesn't run the
                        # graceful WS-close cleanup path, so stale
                        # records for the same handle can pile up across
                        # restarts. Without this check, one real
                        # /identify replayed the join for every stale
                        # record too -- reported live as MOTD/CHATTERS
                        # showing up 4 times after a single /identify.
                        if int(ws_id_str) not in self.websockets:
                            continue
                        if sess.get("waiting_for_identify"):
                            # Strict mode (identify_required_mode=True):
                            # unchanged, existing behavior.
                            if self.post_identify_auto_join:
                                await self._complete_join_after_identify(ws_id_str, sess)
                            else:
                                sess["waiting_for_identify"] = False
                                self.db.save_session(ws_id_str, sess)
                                await self._broadcast_info("Identified. Now use /join <room> to enter chat.")
                        else:
                            # Default (non-strict) mode: the session's
                            # initial optimistic join (_handle_join_room
                            # calling _complete_join_after_identify
                            # immediately) can be silently rejected by
                            # the hub for a REGISTERED-but-unidentified
                            # handle -- confirmed live: hub replies
                            # "Cannot join ROOM, please IDENTIFY to use
                            # this handle", and the bridge, still
                            # believing in_room=True, goes on to forward
                            # chat sends that the hub then bounces back
                            # with "No route to a room from your user,
                            # /join a room first." Re-send the join now
                            # that identify actually succeeded, so the
                            # caller doesn't also have to remember to
                            # manually /join again. Harmless no-op
                            # re-announce for a session whose join
                            # already genuinely succeeded.
                            await self._complete_join_after_identify(ws_id_str, sess)

        if from_user == "SERVER" and to_user == "CLIENT":
            u = msg.upper()
            if u.startswith("USERROOM:"):
                pass  # room_changed is emitted only to initiator in _handle_server_cmd
            elif u.startswith("USERNICK:"):
                nick = msg.split(":", 1)[1].strip()
                for ws_id_str, sess in self.db.list_sessions().items():
                    handle = (sess.get("handle") or "").strip()
                    if handle and nick and (sess.get("nick") in (None, "", handle) or nick.startswith(handle)):
                        sess["nick"] = nick
                        self.db.save_session(ws_id_str, sess)

        if from_user == "SERVER":
            await self._maybe_refresh_userlist_on_server_text(msg)

        payload = {
            "type":      "mrc_message",
            "from_user": parsed.get("from_user", ""),
            "from_site": parsed.get("from_site", ""),
            "from_room": parsed.get("from_room", ""),
            "to_user":   parsed.get("to_user",   ""),
            "to_room":   parsed.get("to_room",   ""),
            "message":   parsed.get("message",   ""),
            "timestamp": datetime.utcnow().isoformat(),
        }

        special = to_user.upper() if to_user else ""

        if special == "CLIENT":
            room = MRCProtocol.norm_room(to_room or from_room)
            if room:
                targets = self._sessions_in_room(room)
            else:
                targets = {sid for sid, s in self.db.list_sessions().items() if s.get("in_room")}
            await self._send_to_sessions(targets, payload)

            # Structured userlist event alongside the raw mrc_message
            # above -- both clients keep working off the raw text until
            # they migrate to consuming this (Phase B/D of the MRC
            # parity rework), it's purely additive. See
            # _parse_userlist_text for the wire-format justification.
            if from_user == "SERVER" and msg.upper().startswith("USERLIST:"):
                users = _parse_userlist_text(msg)
                if users:
                    await self._send_to_sessions(targets, {
                        "type":  "userlist",
                        "room":  room,
                        "users": users,
                    })
            return

        if special == "NOTME":
            room = MRCProtocol.norm_room(to_room or from_room)
            if room:
                await self._send_to_sessions(self._sessions_in_room(room), payload)
            return

        if to_user:
            targets = self._sessions_for_user(to_user) | self._sessions_for_user(from_user)
            if targets:
                await self._send_to_sessions(targets, payload)
            return

        room = MRCProtocol.norm_room(to_room or from_room)
        if room:
            await self._send_to_sessions(self._sessions_in_room(room), payload)
            return

        safe = {sid for sid, s in self.db.list_sessions().items() if s.get("in_room")}
        await self._send_to_sessions(safe, payload)

    # ------------------------------------------------------------------
    # HTTP handlers
    # ------------------------------------------------------------------

    async def handle_index(self, request):
        return web.FileResponse(_WEB_DIR / "index.html")

    async def handle_static(self, request):
        filename = request.match_info.get("filename", "")
        path     = (_WEB_DIR / filename).resolve()
        if not str(path).startswith(str(_WEB_DIR.resolve())):
            raise web.HTTPForbidden()
        if not path.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    # ------------------------------------------------------------------
    # Rate limiter — ONLY applies to actual user chat messages
    # ------------------------------------------------------------------

    def _rate_limit_ok(self, ws_id: int, msg_type: str) -> bool:
        # Control / housekeeping messages are never rate-limited
        if msg_type in ("ping", "pong", "join_room", "leave_room", "set_style", "set_prefs"):
            return True
        # Only rate-limit actual outgoing user content
        if msg_type not in ("send_message", "server_cmd", "direct_message"):
            return True
        rate = float(self.config.get("message_rate_seconds", 0.5))
        now  = time.time()
        last = self.rate_limiter.get(ws_id, 0.0)
        if now - last < rate:
            return False
        self.rate_limiter[ws_id] = now
        return True

    # ------------------------------------------------------------------
    # Command normalisation
    # ------------------------------------------------------------------

    def _normalize_server_cmd(self, current_room: str, command: str) -> str:
        cmd = (command or "").strip()
        if not cmd:
            return cmd

        parts = cmd.split()
        verb  = parts[0].upper()
        rest  = " ".join(parts[1:]).strip()

        if verb in ("JOIN", "J") and rest:
            return f"NEWROOM:{MRCProtocol.norm_room(current_room)}:{MRCProtocol.norm_room(rest)}"
        if verb == "TOPIC" and rest:
            return f"NEWTOPIC:{MRCProtocol.norm_room(current_room) or 'lobby'}:{rest}"
        if verb == "ROOMS":
            return "LIST"
        if verb == "CTCP" and rest:
            return f"CTCP {rest}"

        return cmd

    # ------------------------------------------------------------------
    # WebSocket handler
    # ------------------------------------------------------------------

    async def handle_websocket(self, request):
        # heartbeat=30: aiohttp sends a protocol-level ping every 30s and
        # closes the connection if no pong comes back. Without this, a
        # client that goes silent without a clean close (cable pull,
        # force-killed terminal/browser, dead network) leaves `async for
        # msg in ws:` below blocked forever -- the disconnect `finally:`
        # block (and the grace-period session cleanup / upstream LOGOFF it
        # triggers) never runs, so the session sits in sessions.json
        # looking permanently logged in, and the upstream hub still thinks
        # this BBS has the handle connected ("you can only be logged on
        # once" when the same user tries from elsewhere). Standard
        # WebSocket clients (browsers, aiohttp's own ws_connect() used by
        # the terminal MRC door) answer protocol-level pings automatically
        # -- no client-side change needed for this to take effect.
        ws    = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        ws_id = id(ws)
        self.websockets[ws_id] = ws
        # Best-effort real caller IP for the web client, read from the
        # incoming HTTP request. /mrcws is reverse-proxied by nginx (see
        # anetbbs/web/mrc_web.py's own docstring), so request.remote
        # alone would just be nginx's own loopback address -- honor
        # X-Forwarded-For (nginx sets this on every proxied request per
        # the shipped nginx config) first. The terminal client's
        # connection always originates from localhost regardless (it
        # dials ws://127.0.0.1:8080 itself), so for that path this is
        # expected to stay a loopback address -- _handle_join_room lets
        # an explicit "ip" field in the join_room message override it.
        xff = request.headers.get("X-Forwarded-For", "")
        self._ws_remote_ip[ws_id] = (
            xff.split(",")[0].strip() if xff else (request.remote or ""))
        logger.info(f"WebSocket connected: {ws_id}")

        try:
            await self._safe_send(ws, {"type": "welcome", "message": "Connected to MRC Bridge", "server": self.config["mrc_host"]})
            await self._safe_send(ws, {"type": "bridge_status", "status": "upstream_connected" if self.mrc.connected else "upstream_disconnected"})

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        await self._safe_send(ws, {"type": "error", "message": "Invalid JSON"})
                        continue
                    await self.handle_ws_message(ws_id, data)
        finally:
            self.websockets.pop(ws_id, None)
            self._ws_remote_ip.pop(ws_id, None)
            sess = self.db.get_session(str(ws_id))
            if sess:
                eff_nick = self._session_effective_nick(sess)
                room     = self._session_room(sess)
                if eff_nick and room and sess.get("in_room") and self.ws_disconnect_grace_seconds > 0:
                    key = self._pending_disconnect_key(eff_nick)
                    old = self.pending_disconnects.get(key)
                    if old and not old.done():
                        old.cancel()
                    self.pending_disconnects[key] = asyncio.create_task(self._delayed_session_logoff(ws_id, eff_nick, room))
                    logger.info(f"WebSocket disconnected: {ws_id} (grace {self.ws_disconnect_grace_seconds:.1f}s for handle={eff_nick} room={room})")
                else:
                    self.db.delete_session(str(ws_id))
                    logger.info(f"WebSocket disconnected: {ws_id}")
            else:
                logger.info(f"WebSocket disconnected: {ws_id}")

        return ws

    async def handle_ws_message(self, ws_id: int, data: dict):
        ws = self.websockets.get(ws_id)
        if not ws:
            return

        msg_type = data.get("type")
        if not self._rate_limit_ok(ws_id, msg_type):
            await self._safe_send(ws, {"type": "error", "message": "Rate limit: please slow down."})
            return

        if msg_type == "ping":
            await self._safe_send(ws, {"type": "pong", "t": data.get("t")})
            return

        handlers = {
            "join_room":      self._handle_join_room,
            "send_message":   self._handle_send_message,
            "direct_message": self._handle_direct_message,
            "server_cmd":     self._handle_server_cmd,
            "set_style":      self._handle_set_style,
            "set_prefs":      self._handle_set_prefs,
            "leave_room":     self._handle_leave_room,
        }
        handler = handlers.get(msg_type)
        if handler:
            await handler(ws_id, data)
        else:
            await self._safe_send(ws, {"type": "error", "message": f"Unknown message type: {msg_type}"})

    # ------------------------------------------------------------------
    # join_room
    # ------------------------------------------------------------------

    async def _handle_join_room(self, ws_id: int, data: dict):
        ws     = self.websockets.get(ws_id)
        handle = (data.get("handle") or "").strip()
        room   = MRCProtocol.norm_room(data.get("room") or "lobby")
        self._cancel_pending_disconnect(handle)

        if not ws:
            return
        if not MRCProtocol.validate_handle(handle):
            await self._safe_send(ws, {"type": "error", "message": "Invalid handle."})
            return

        prof = self.db.get_profile(handle) or {}

        style_prefix       = _sanitize_no_tilde(prof.get("style_prefix",       self.default_style_prefix), 16)
        style_suffix       = _sanitize_no_tilde(prof.get("style_suffix",       self.default_style_suffix), 16)
        style_color        = _norm_pipe_color(prof.get("style_color",           self.default_style_color))
        style_prefix_color = _norm_pipe_color(prof.get("style_prefix_color",   style_color))
        style_handle_color = _norm_pipe_color(prof.get("style_handle_color",   style_color))
        style_suffix_color = _norm_pipe_color(prof.get("style_suffix_color",   style_color))
        typing_color       = _norm_pipe_color(prof.get("typing_color",         "10"))

        # Chat prefs (see _session_prefs) -- loaded from the profile same
        # as style fields above, so per-user enter/leave/quit templates
        # and twit/shield settings are available without an extra DB
        # round-trip on every join/leave/disconnect.
        raw_twits    = prof.get("twit_list") or []
        twit_list    = [str(t).strip() for t in raw_twits if str(t).strip()][:64] \
                       if isinstance(raw_twits, list) else []

        # Real bug found investigating a live "I have to /identify every
        # single time" report: sess["remote_ip"] was read in two places
        # (_send_join_payloads, which sends the reference client's own
        # USERIP: packet on every join) but never written ANYWHERE in
        # this file -- so USERIP: was never sent at all, for any user,
        # on either the terminal or web client. If the hub uses USERIP
        # to recognize a returning already-identified connection (the
        # reference client sends it unconditionally on every join,
        # right alongside its own "/identify for MRC Trust" tip, which
        # strongly suggests it's load-bearing for that), never sending
        # it would force a fresh /identify on literally every join with
        # no way around it, regardless of how recently the same handle
        # last identified. An explicit "ip" field in the join_room
        # message (the terminal client's own real caller IP -- the
        # bridge can't observe it itself, since that connection always
        # originates from localhost) takes priority over the
        # server-observed WebSocket peer IP (the web client's real
        # path, via handle_websocket's X-Forwarded-For handling).
        remote_ip = (data.get("ip") or "").strip()[:64] or self._ws_remote_ip.get(ws_id, "")
        # Matches the reference client's own guard (helper_protocol.c:
        # only sends USERIP if it's non-empty AND not "127.0.0.1") --
        # loopback is never a meaningful caller identity, and for the
        # web path specifically it's also the tell-tale sign that
        # X-Forwarded-For isn't being forwarded correctly (nginx's own
        # proxy connection, not the real browser's address) -- sending
        # it anyway would be actively misleading, not just unhelpful,
        # since every caller behind that misconfiguration would appear
        # to share one identical IP.
        if remote_ip == "127.0.0.1":
            remote_ip = ""

        sess = {
            "handle":               handle,
            "nick":                 handle,
            "room":                 room,
            "remote_ip":            remote_ip,
            "joined_at":            datetime.utcnow().isoformat(),
            "waiting_for_identify": bool(self.identify_required_mode),
            "in_room":              False,
            "style_prefix":         style_prefix,
            "style_suffix":         style_suffix,
            "style_color":          style_color,
            "style_prefix_color":   style_prefix_color,
            "style_handle_color":   style_handle_color,
            "style_suffix_color":   style_suffix_color,
            "typing_color":         typing_color,
            "twit_list":            twit_list,
            "twit_filter_enabled":  bool(prof.get("twit_filter_enabled", True)),
            "broadcast_shield":     bool(prof.get("broadcast_shield", False)),
            "ticker_enabled":       bool(prof.get("ticker_enabled", True)),
            "enter_msg_tpl":        _sanitize_no_tilde(prof.get("enter_msg_tpl") or "", 200),
            "leave_msg_tpl":        _sanitize_no_tilde(prof.get("leave_msg_tpl") or "", 200),
            "quit_msg":             _sanitize_no_tilde(prof.get("quit_msg") or "", 200),
            "default_room":         MRCProtocol.norm_room(_sanitize_no_tilde(prof.get("default_room") or "", 20)),
            "clock_format":         "12" if str(prof.get("clock_format", "24")).strip() == "12" else "24",
            "tz_offset":            _clamp_tz_offset(prof.get("tz_offset", 0)),
        }
        self.db.save_session(str(ws_id), sess)

        # Normal case (identify_required_mode=False, the default): join
        # immediately, same as the reference client -- no reason to make
        # every caller wait on an optional trust check. post_identify_
        # auto_join only matters in the opt-in strict mode below, where
        # it decides whether a *successful* /identify auto-completes the
        # join or just clears the wait state and expects an explicit
        # /join (see _on_upstream_packet's "successfully identified"
        # handler).
        if not self.identify_required_mode:
            await self._complete_join_after_identify(str(ws_id), sess)
            sess = self.db.get_session(str(ws_id)) or sess

        await self._safe_send(ws, {
            "type":            "joined",
            "handle":          handle,
            "room":            room,
            "display_handle":  self._session_display_handle(sess),
            "style":           self._session_style(sess),
            "prefs":           self._session_prefs(sess),
            "handle_overhead": self._session_display_handle_wire_len(sess),
            "dm_overhead":     self._session_dm_overhead(sess),
            "action_overhead": self._session_action_overhead(sess),
            # The immediate-join message still mentions /identify --
            # purely informational, matching the reference client's own
            # permanent (never-blocking) "Use /identify password for MRC
            # Trust" connect notice (helper_protocol.c). A registered
            # handle's trust status isn't a one-time thing on the real
            # network -- it lapses after a stretch of weeks per real
            # user experience, so periodically re-running /identify
            # still matters even though it's never required to chat.
            "message":         (f"Joined #{room} as {handle}. Use /identify <pass> for MRC Trust."
                                 if not self.identify_required_mode
                                 else f"Ready as {handle}. If registered: /identify <pass> then /join {room}.")
        })

        # No separate userlist request here: in the immediate-join case
        # above, _complete_join_after_identify already sent one via
        # _send_join_payloads -- requesting it again would be a
        # duplicate. In strict mode (identify_required_mode=True) the
        # user isn't in the room yet; _send_join_payloads sends it once
        # they actually identify.

    # ------------------------------------------------------------------
    # set_style
    # ------------------------------------------------------------------

    async def _handle_set_style(self, ws_id: int, data: dict):
        ws   = self.websockets.get(ws_id)
        sess = self.db.get_session(str(ws_id))
        if not ws or not sess:
            return

        prefix       = _sanitize_no_tilde(data.get("prefix", ""), 16)
        suffix       = _sanitize_no_tilde(data.get("suffix", ""), 16)
        base_color   = _norm_pipe_color(data.get("color",        sess.get("style_color",  "07")))
        prefix_color = _norm_pipe_color(data.get("prefix_color", data.get("style_prefix_color", base_color)))
        handle_color = _norm_pipe_color(data.get("handle_color", data.get("style_handle_color", base_color)))
        suffix_color = _norm_pipe_color(data.get("suffix_color", data.get("style_suffix_color", base_color)))
        typing_color = _norm_pipe_color(data.get("typing_color", sess.get("typing_color", "10")))

        sess.update({
            "style_prefix":       prefix,
            "style_suffix":       suffix,
            "style_color":        base_color,
            "style_prefix_color": prefix_color,
            "style_handle_color": handle_color,
            "style_suffix_color": suffix_color,
            "typing_color":       typing_color,
        })
        self.db.save_session(str(ws_id), sess)

        handle = (sess.get("handle") or "").strip()
        if handle:
            existing = self.db.get_profile(handle) or {}
            existing.update({
                "style_prefix":       prefix,
                "style_suffix":       suffix,
                "style_color":        base_color,
                "style_prefix_color": prefix_color,
                "style_handle_color": handle_color,
                "style_suffix_color": suffix_color,
                "typing_color":       typing_color,
            })
            self.db.save_profile(handle, existing)

        await self._safe_send(ws, {
            "type":            "style_updated",
            "style":           self._session_style(sess),
            "display_handle":  self._session_display_handle(sess),
            "handle_overhead": self._session_display_handle_wire_len(sess),
            "dm_overhead":     self._session_dm_overhead(sess),
            "message":         "Style updated (applies to messages others see)."
        })

    # ------------------------------------------------------------------
    # set_prefs -- chat prefs distinct from nick style (see
    # _session_prefs): twit/ignore list, broadcast shield, ticker
    # toggle, custom enter/leave/quit messages. Same request/response
    # shape as set_style/style_updated, kept as a separate pair rather
    # than merged into style since they're a different settings group.
    # ------------------------------------------------------------------

    async def _handle_set_prefs(self, ws_id: int, data: dict):
        ws   = self.websockets.get(ws_id)
        sess = self.db.get_session(str(ws_id))
        if not ws or not sess:
            return

        updates = {}

        if "twit_list" in data:
            raw = data.get("twit_list")
            if isinstance(raw, list):
                seen = set()
                twits = []
                for entry in raw:
                    t = _casefold(str(entry).strip())
                    if not t or t.upper() in MRCProtocol.RESERVED_HANDLES or t in seen:
                        continue
                    seen.add(t)
                    twits.append(str(entry).strip()[:32])
                updates["twit_list"] = twits[:64]

        if "twit_filter_enabled" in data:
            updates["twit_filter_enabled"] = bool(data.get("twit_filter_enabled"))

        if "broadcast_shield" in data:
            updates["broadcast_shield"] = bool(data.get("broadcast_shield"))

        if "ticker_enabled" in data:
            updates["ticker_enabled"] = bool(data.get("ticker_enabled"))

        if "enter_msg_tpl" in data:
            updates["enter_msg_tpl"] = _sanitize_no_tilde(data.get("enter_msg_tpl") or "", 200)

        if "leave_msg_tpl" in data:
            updates["leave_msg_tpl"] = _sanitize_no_tilde(data.get("leave_msg_tpl") or "", 200)

        if "quit_msg" in data:
            updates["quit_msg"] = _sanitize_no_tilde(data.get("quit_msg") or "", 200)

        if "default_room" in data:
            updates["default_room"] = MRCProtocol.norm_room(
                _sanitize_no_tilde(data.get("default_room") or "", 20))

        if "clock_format" in data:
            updates["clock_format"] = "12" if str(data.get("clock_format")).strip() == "12" else "24"

        if "tz_offset" in data:
            updates["tz_offset"] = _clamp_tz_offset(data.get("tz_offset"))

        if not updates:
            await self._safe_send(ws, {"type": "error", "message": "No recognized preference fields in request."})
            return

        sess.update(updates)
        self.db.save_session(str(ws_id), sess)

        handle = (sess.get("handle") or "").strip()
        if handle:
            existing = self.db.get_profile(handle) or {}
            existing.update(updates)
            self.db.save_profile(handle, existing)

        await self._safe_send(ws, {
            "type":    "prefs_updated",
            "prefs":   self._session_prefs(sess),
            "message": "Preferences updated.",
        })

    # ------------------------------------------------------------------
    # send_message
    # ------------------------------------------------------------------

    async def _handle_send_message(self, ws_id: int, data: dict):
        ws   = self.websockets.get(ws_id)
        sess = self.db.get_session(str(ws_id))
        if not ws or not sess:
            return

        if not sess.get("in_room"):
            await self._safe_send(ws, {"type": "error", "message": "Not in a room yet. If registered: /identify <pass> then /join <room>."})
            return

        nick    = self._session_effective_nick(sess)
        room    = self._session_room(sess)
        message = (data.get("message") or "").strip()
        if not message:
            return

        if self._is_action_body(message):
            body = message.lstrip()
            if body.startswith("* "):
                body = f"|15* |13{nick} {body[2:].strip()}|07"
        else:
            body = f"{self._session_display_handle(sess)} {message}"

        body = _truncate_wire_message(body)
        pkt  = MRCProtocol.create_message(nick, self.config["bridge_bbs"], room, "", room, body)
        await self.mrc.send_packet(pkt)

    # ------------------------------------------------------------------
    # direct_message
    # ------------------------------------------------------------------

    async def _handle_direct_message(self, ws_id: int, data: dict):
        ws   = self.websockets.get(ws_id)
        sess = self.db.get_session(str(ws_id))
        if not ws or not sess:
            return

        nick    = self._session_effective_nick(sess)
        room    = self._session_room(sess)
        to_user = (data.get("to_user")  or "").strip()
        message = (data.get("message")  or "").strip()

        if not to_user or not message:
            await self._safe_send(ws, {"type": "error", "message": "Usage: /t <user> <message>"})
            return

        body = _truncate_wire_message(_dm_wrapper(self._session_display_handle(sess), message))
        pkt  = MRCProtocol.create_message(nick, self.config["bridge_bbs"], room, to_user, "", body)
        await self.mrc.send_packet(pkt)

    # ------------------------------------------------------------------
    # CTCP request
    # ------------------------------------------------------------------

    async def _send_ctcp_request(self, eff_nick: str, target: str, cmd: str):
        target = (target or "").strip()
        cmd    = (cmd    or "").strip()
        if not target or not cmd:
            return False

        body = _truncate_wire_message(f"[CTCP] {eff_nick} {target} {cmd}".strip())
        pkt  = MRCProtocol.create_message(
            eff_nick,
            self.config["bridge_bbs"],
            self.ctcp_room,
            ("" if target == "*" else target),
            self.ctcp_room,
            body
        )
        await self.mrc.send_packet(pkt)
        return True

    # ------------------------------------------------------------------
    # server_cmd
    # ------------------------------------------------------------------

    async def _handle_server_cmd(self, ws_id: int, data: dict):
        ws   = self.websockets.get(ws_id)
        sess = self.db.get_session(str(ws_id))
        if not ws or not sess:
            return

        eff_nick = self._session_effective_nick(sess)
        room     = self._session_room(sess)
        cmd      = (data.get("command") or "").strip()
        if not cmd:
            return

        normalized = self._normalize_server_cmd(room, cmd)
        logger.info(f"WS {ws_id} server_cmd -> {_redact_command_for_logs(normalized)} (room={room}, nick={eff_nick})")

        if normalized.upper().startswith("CTCP "):
            rest  = normalized[5:].strip()
            parts = rest.split(None, 1)
            if len(parts) < 2:
                await self._safe_send(ws, {"type": "error", "message": "Usage: /ctcp <target> <command> (VERSION/TIME/PING/CLIENTINFO)"})
                return
            target = parts[0].strip()
            if _casefold(target) == _casefold(eff_nick):
                await self._safe_send(ws, {"type": "error", "message": "CTCP to yourself is not supported."})
                return
            ok = await self._send_ctcp_request(eff_nick, target, parts[1].strip())
            if not ok:
                await self._safe_send(ws, {"type": "error", "message": "CTCP failed."})
            return

        new_room = None
        if normalized.upper().startswith("NEWROOM:"):
            try:
                _, _oldr, newr = normalized.split(":", 2)
                new_room = MRCProtocol.norm_room(newr)
            except Exception:
                new_room = None

        await self.mrc.send_packet(MRCProtocol.create_server_command(eff_nick, self.config["bridge_bbs"], room, normalized))

        if normalized.upper() == "WHOON":
            # WHOON's reply is a plain, hub-formatted cosmetic text dump --
            # no structural per-user markers a client can safely parse (see
            # _send_userlist_control below, the ONLY source clients should
            # ever build a tab-complete roster from). Piggyback a real
            # structured USERLIST push on every /who so a sysop who just
            # *looked* at who's in the room also gets a correct, fresh
            # tab-complete roster out of it, instead of the WHOON display
            # being purely cosmetic.
            await self._send_userlist_control(room)

        if normalized.upper().startswith("NEWROOM:") and new_room:
            sess["waiting_for_identify"] = False
            sess["in_room"]              = True
            sess["room"]                 = new_room
            self.db.save_session(str(ws_id), sess)

            # Only the initiator gets room_changed
            await self._send_to_session(str(ws_id), {"type": "room_changed", "room": new_room})
            await self._sleep_delay()

            if self.announce_join_part:
                join_msg = _truncate_wire_message(_format_template(self.join_message_tpl, handle=eff_nick))
                await self.mrc.send_packet(MRCProtocol.create_message(eff_nick, self.config["bridge_bbs"], new_room, "NOTME", "", join_msg))
                await self._sleep_delay()

            await self._send_join_payloads(eff_nick, new_room, sess.get("remote_ip", ""))
            await self._send_userlist_control(new_room)

    # ------------------------------------------------------------------
    # leave_room
    # ------------------------------------------------------------------

    async def _handle_leave_room(self, ws_id: int, data: dict = None):
        data = data or {}
        ws   = self.websockets.get(ws_id)
        sess = self.db.get_session(str(ws_id))
        if not ws or not sess:
            return

        eff_nick = self._session_effective_nick(sess)
        room     = self._session_room(sess)

        # Explicit per-quit message (client's /quit <message>) takes
        # priority over the saved default quit_msg pref, which in turn
        # only applies to a deliberate leave -- never the abrupt-
        # disconnect grace path in _delayed_session_logoff.
        quit_override = _sanitize_no_tilde((data.get("message") or "").strip(), 100) \
                       or (sess.get("quit_msg") or "").strip()

        if self.announce_join_part and eff_nick and room and sess.get("in_room"):
            exit_msg = _resolve_message_template(
                sess, "leave_msg_tpl", self.exit_message_tpl, eff_nick, extra=quit_override)
            await self.mrc.send_packet(MRCProtocol.create_message(eff_nick, self.config["bridge_bbs"], room, "NOTME", "", exit_msg))
            await self._sleep_delay()

        # LOGOFF is deliberately NOT sent on an individual caller leaving
        # a room. Real live evidence (a captured full packet transcript,
        # MRC_BRIDGE_LOG_LEVEL=DEBUG): sending LOGOFF ends the hub's MRC
        # Trust state for this handle immediately -- the very next join
        # got "Cannot join ROOM, please IDENTIFY to use this handle"
        # despite the bridge's own connection to the hub never having
        # dropped in between. This bridge holds ONE persistent shared
        # connection to the hub per BBS install across every local
        # caller's join/leave, so there's no need to tell the hub this
        # handle is "logging off" the way a single-session client would
        # -- NOTME's "has left chat" already covers the visible room-
        # presence announcement other users see. The one real cost:
        # the hub's own /who or CHATTERS listing may show this handle
        # lingering until the next reconnect's fresh join, or the hub's
        # own idle timeout, cleans it up.
        self.db.delete_session(str(ws_id))
        await self._safe_send(ws, {"type": "left", "message": "Left the room"})

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def keepalive_loop(self):
        interval = float(self.config.get("iamhere_interval_seconds", 60))
        while True:
            await asyncio.sleep(interval)
            if not self.mrc.connected:
                continue
            for _, sess in self.db.list_sessions().items():
                if not sess.get("in_room"):
                    continue
                eff_nick = self._session_effective_nick(sess)
                room     = self._session_room(sess)
                if eff_nick and room:
                    await self.mrc.send_packet(MRCProtocol.create_iamhere(eff_nick, self.config["bridge_bbs"], room, "ACTIVE"))

    async def _periodic_userlist_refresh(self):
        interval = float(self.config.get("userlist_refresh_interval_seconds", 30))
        while True:
            await asyncio.sleep(interval)
            if not self.mrc.connected:
                continue
            for _, sess in self.db.list_sessions().items():
                if not sess.get("in_room"):
                    continue
                room = self._session_room(sess)
                if room:
                    await self._send_userlist_control(room)

    async def _periodic_stats_refresh(self):
        """Feeds the ticker/banner text pool (see _send_stats_control's
        docstring for why this isn't parsed into structured fields).
        One request per room-with-active-sessions, same shape as
        _periodic_userlist_refresh, just a longer default interval --
        stats are far less time-sensitive than a room's user list."""
        interval = float(self.config.get("stats_refresh_interval_seconds", 120))
        while True:
            await asyncio.sleep(interval)
            if not self.mrc.connected:
                continue
            for _, sess in self.db.list_sessions().items():
                if not sess.get("in_room"):
                    continue
                room = self._session_room(sess)
                if room:
                    await self._send_stats_control(room)

    async def start_background_tasks(self, app):
        await self.mrc.start()
        self.tasks.append(asyncio.create_task(self.keepalive_loop()))
        self.tasks.append(asyncio.create_task(self._periodic_userlist_refresh()))
        self.tasks.append(asyncio.create_task(self._periodic_stats_refresh()))

    async def cleanup_background_tasks(self, app):
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.mrc.stop()


# ------------------------------------------------------------------
# App factory + entry point
# ------------------------------------------------------------------

def create_app(config_path: Optional[str] = None) -> web.Application:
    bridge = BridgeApp(config_path)
    app    = web.Application()
    app["bridge"] = bridge

    app.router.add_get("/ws",                bridge.handle_websocket)
    app.router.add_get("/mrcws",             bridge.handle_websocket)  # nginx proxy alias
    app.router.add_get("/",                  bridge.handle_index)
    app.router.add_get("/static/{filename}", bridge.handle_static)

    app.on_startup.append(bridge.start_background_tasks)
    app.on_cleanup.append(bridge.cleanup_background_tasks)
    return app


if __name__ == "__main__":
    cfg_path = os.environ.get("MRC_BRIDGE_CONFIG")
    app = create_app(cfg_path)
    cfg = _load_config(cfg_path)

    # Default to localhost-only so the bridge can't be reached except via
    # nginx's /mrcws proxy (which does auth_request). Users on the same VM
    # who explicitly want a public bind can set web_listen_host: "0.0.0.0"
    # in mrc/bridge/config.json.
    host = cfg.get("web_listen_host", "127.0.0.1")
    port = int(cfg.get("web_listen_port", 8080))

    logger.info(f"Starting MRC Bridge on {host}:{port}")
    web.run_app(app, host=host, port=port)
