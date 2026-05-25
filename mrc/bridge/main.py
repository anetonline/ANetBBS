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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("mrc_bridge")

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


def _dm_wrapper(sender_display: str, message: str) -> str:
    sender_display = sender_display.strip()
    message        = message.strip()
    return f"|15* |08(|15{sender_display}|08/|14DirectMsg|08) |07{message}"


def _dm_wrapper_prefix(sender_display: str) -> str:
    """Return only the prefix portion of a DM wrapper (everything before the message text)."""
    return f"|15* |08(|15{sender_display.strip()}|08/|14DirectMsg|08) |07"


def _sanitize_no_tilde(s: str, max_len: int) -> str:
    return (s or "").replace("~", "")[:max_len]


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
        return f"VERSION {bbs_name} (ANetBBS MRC Bridge)"
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

        self.identify_required_mode  = bool(self.config.get("identify_required_mode", True))
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
                exit_msg = _truncate_wire_message(_format_template(self.exit_message_tpl, handle=eff_nick))
                await self.mrc.send_packet(MRCProtocol.create_message(eff_nick, self.config["bridge_bbs"], room, "NOTME", "", exit_msg))
            if eff_nick and room:
                await self.mrc.send_packet(MRCProtocol.create_logoff(eff_nick, self.config["bridge_bbs"], room))
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

    async def _send_to_session(self, ws_id_str: str, payload: dict):
        try:
            ws_id = int(ws_id_str)
        except Exception:
            return
        ws = self.websockets.get(ws_id)
        if not ws:
            return
        try:
            await ws.send_json(payload)
        except Exception:
            pass

    async def _send_to_sessions(self, ws_ids: Set[str], payload: dict):
        for ws_id_str in ws_ids:
            await self._send_to_session(ws_id_str, payload)

    # ------------------------------------------------------------------
    # Upstream status / rejoin
    # ------------------------------------------------------------------

    async def _broadcast_bridge_status(self, status: str):
        payload = {"type": "bridge_status", "status": status}
        for ws in list(self.websockets.values()):
            try:
                await ws.send_json(payload)
            except Exception:
                pass
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

    async def _send_join_payloads(self, eff_nick: str, room: str, remote_ip: str = ""):
        if remote_ip:
            await self.mrc.send_packet(MRCProtocol.create_server_command(eff_nick, self.config["bridge_bbs"], room, f"USERIP:{remote_ip}"))
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
            join_msg = _truncate_wire_message(_format_template(self.join_message_tpl, handle=eff_nick))
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
            try:
                await ws.send_json(payload)
            except Exception:
                pass

    @staticmethod
    def _extract_identified_handle(server_msg: str) -> Optional[str]:
        msg = (server_msg or "").strip()
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
            if "successfully identified" in low:
                identified_handle = self._extract_identified_handle(msg)
                if identified_handle:
                    for ws_id_str, sess in self.db.list_sessions().items():
                        if (sess.get("handle") or "").strip().lower() != identified_handle.lower():
                            continue
                        if not sess.get("waiting_for_identify"):
                            continue
                        if self.post_identify_auto_join:
                            await self._complete_join_after_identify(ws_id_str, sess)
                        else:
                            sess["waiting_for_identify"] = False
                            self.db.save_session(ws_id_str, sess)
                            await self._broadcast_info("Identified. Now use /join <room> to enter chat.")

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
                await self._send_to_sessions(self._sessions_in_room(room), payload)
            else:
                safe = {sid for sid, s in self.db.list_sessions().items() if s.get("in_room")}
                await self._send_to_sessions(safe, payload)
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
        if msg_type in ("ping", "pong", "join_room", "leave_room", "set_style"):
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
        ws    = web.WebSocketResponse()
        await ws.prepare(request)

        ws_id = id(ws)
        self.websockets[ws_id] = ws
        logger.info(f"WebSocket connected: {ws_id}")

        try:
            await ws.send_json({"type": "welcome", "message": "Connected to MRC Bridge", "server": self.config["mrc_host"]})
            await ws.send_json({"type": "bridge_status", "status": "upstream_connected" if self.mrc.connected else "upstream_disconnected"})

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        await ws.send_json({"type": "error", "message": "Invalid JSON"})
                        continue
                    await self.handle_ws_message(ws_id, data)
        finally:
            self.websockets.pop(ws_id, None)
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
            await ws.send_json({"type": "error", "message": "Rate limit: please slow down."})
            return

        if msg_type == "ping":
            await ws.send_json({"type": "pong", "t": data.get("t")})
            return

        handlers = {
            "join_room":      self._handle_join_room,
            "send_message":   self._handle_send_message,
            "direct_message": self._handle_direct_message,
            "server_cmd":     self._handle_server_cmd,
            "set_style":      self._handle_set_style,
            "leave_room":     lambda wid, _d: self._handle_leave_room(wid),
        }
        handler = handlers.get(msg_type)
        if handler:
            await handler(ws_id, data)
        else:
            await ws.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})

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
            await ws.send_json({"type": "error", "message": "Invalid handle."})
            return

        prof = self.db.get_profile(handle) or {}

        style_prefix       = _sanitize_no_tilde(prof.get("style_prefix",       self.default_style_prefix), 16)
        style_suffix       = _sanitize_no_tilde(prof.get("style_suffix",       self.default_style_suffix), 16)
        style_color        = _norm_pipe_color(prof.get("style_color",           self.default_style_color))
        style_prefix_color = _norm_pipe_color(prof.get("style_prefix_color",   style_color))
        style_handle_color = _norm_pipe_color(prof.get("style_handle_color",   style_color))
        style_suffix_color = _norm_pipe_color(prof.get("style_suffix_color",   style_color))
        typing_color       = _norm_pipe_color(prof.get("typing_color",         "10"))

        sess = {
            "handle":               handle,
            "nick":                 handle,
            "room":                 room,
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
        }
        self.db.save_session(str(ws_id), sess)

        if not self.identify_required_mode and self.post_identify_auto_join:
            await self._complete_join_after_identify(str(ws_id), sess)

        await ws.send_json({
            "type":            "joined",
            "handle":          handle,
            "room":            room,
            "display_handle":  self._session_display_handle(sess),
            "style":           self._session_style(sess),
            "handle_overhead": self._session_display_handle_wire_len(sess),
            "dm_overhead":     self._session_dm_overhead(sess),
            "message":         f"Ready as {handle}. If registered: /identify <pass> then /join {room}."
        })

        # Only send userlist if the user is being placed into the room immediately
        # (identify_required_mode=True means they are NOT in the room yet — the
        # userlist will be requested by _send_join_payloads after identification).
        if not self.identify_required_mode:
            await self._send_userlist_control(room)

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

        await ws.send_json({
            "type":            "style_updated",
            "style":           self._session_style(sess),
            "display_handle":  self._session_display_handle(sess),
            "handle_overhead": self._session_display_handle_wire_len(sess),
            "dm_overhead":     self._session_dm_overhead(sess),
            "message":         "Style updated (applies to messages others see)."
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
            await ws.send_json({"type": "error", "message": "Not in a room yet. If registered: /identify <pass> then /join <room>."})
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
            await ws.send_json({"type": "error", "message": "Usage: /t <user> <message>"})
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
                await ws.send_json({"type": "error", "message": "Usage: /ctcp <target> <command> (VERSION/TIME/PING/CLIENTINFO)"})
                return
            target = parts[0].strip()
            if _casefold(target) == _casefold(eff_nick):
                await ws.send_json({"type": "error", "message": "CTCP to yourself is not supported."})
                return
            ok = await self._send_ctcp_request(eff_nick, target, parts[1].strip())
            if not ok:
                await ws.send_json({"type": "error", "message": "CTCP failed."})
            return

        new_room = None
        if normalized.upper().startswith("NEWROOM:"):
            try:
                _, _oldr, newr = normalized.split(":", 2)
                new_room = MRCProtocol.norm_room(newr)
            except Exception:
                new_room = None

        await self.mrc.send_packet(MRCProtocol.create_server_command(eff_nick, self.config["bridge_bbs"], room, normalized))

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

    async def _handle_leave_room(self, ws_id: int):
        ws   = self.websockets.get(ws_id)
        sess = self.db.get_session(str(ws_id))
        if not ws or not sess:
            return

        eff_nick = self._session_effective_nick(sess)
        room     = self._session_room(sess)

        if self.announce_join_part and eff_nick and room and sess.get("in_room"):
            exit_msg = _truncate_wire_message(_format_template(self.exit_message_tpl, handle=eff_nick))
            await self.mrc.send_packet(MRCProtocol.create_message(eff_nick, self.config["bridge_bbs"], room, "NOTME", "", exit_msg))
            await self._sleep_delay()

        if eff_nick and room and sess.get("in_room"):
            await self.mrc.send_packet(MRCProtocol.create_logoff(eff_nick, self.config["bridge_bbs"], room))
            await self._sleep_delay()

        self.db.delete_session(str(ws_id))
        await ws.send_json({"type": "left", "message": "Left the room"})

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

    async def start_background_tasks(self, app):
        await self.mrc.start()
        self.tasks.append(asyncio.create_task(self.keepalive_loop()))
        self.tasks.append(asyncio.create_task(self._periodic_userlist_refresh()))

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
