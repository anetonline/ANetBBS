"""Alternate MRCConnection backend: supervises the real vendored
mrc_client.py (mrc/mystic_client/vendor/, see PROVENANCE.md there) as
a subprocess against a synthetic Mystic BBS directory tree, instead of
opening our own raw socket to the upstream MRC hub.

Exposes the exact same public interface BridgeApp already uses on
MRCConnection -- start()/stop()/send_packet()/add_message_callback()/
.connected -- plus one addition, sync_active_rooms(), that the mystic
subprocess's file-based IPC needs and the native socket backend
doesn't (BridgeApp calls it defensively via getattr(), a no-op for the
native backend). Every other piece of BridgeApp -- session tracking,
identify-gating, CTCP, DM routing, userlist -- is completely unchanged
regardless of which backend is active; only the lowest-level upstream
transport differs.

Outbound: send_packet(pkt) writes `pkt` as a new file under
data/mrc/ -- the subprocess's own send_mrc() polls that directory
every ~10ms, forwards the raw packet text to the real hub verbatim,
and deletes the file.

Inbound: the subprocess's own deliver_mrc() writes one packet per file
into temp<room>/*.mrc (temp + room name concatenated with no
separator -- NOT a temp/<room>/ subdirectory, see fake_bbs.py's
room_dir() docstring for how this was confirmed) for any room with a
temp<room>/tchat.inuse marker present. A background task here polls
those directories (only
for rooms BridgeApp has told us are actually active, via
sync_active_rooms), parses each file with the same wire-format parser
the native backend uses, deletes it, and invokes message_callbacks --
identical shape to MRCConnection's own receive_loop() -> _handle_packet.
"""
import asyncio
import logging
import re
import signal
import sys
import time
from pathlib import Path
from typing import Optional, Set

from .latency import LatencyTracker
from .mrc_protocol import MRCProtocol
from ..mystic_client.fake_bbs import (
    ensure_fake_bbs_tree,
    room_dir,
    chat_dat_path,
    inuse_marker_path,
    mrc_outbound_dir,
)

logger = logging.getLogger("mrc_bridge.mystic")

_BRIDGE_DIR = Path(__file__).resolve().parent

# Exact substrings the vendored client's own logger() prints (see
# mrc_client.py's mainproc()) -- used to infer connection state without
# touching the vendored file at all.
_CONNECTED_MARKERS = ("Connected (",)
_DISCONNECTED_MARKERS = ("Unable to connect", "Lost connection", "Connection error")


def _safe_room(room: str) -> str:
    return re.sub(r'[^A-Za-z0-9_-]', '', room or '')[:32] or 'lobby'


class MysticMultiplexerConnection:
    def __init__(self, config: dict, status_callback=None, latency_callback=None):
        self.config = config
        self.status_callback = status_callback
        self.latency_callback = latency_callback

        self.connected = False
        self._closing = False
        self.message_callbacks = []

        data_dir = Path(config.get('data_dir', str(_BRIDGE_DIR / 'data')))
        self.bbspath = Path(config.get('mystic_bbspath', str(data_dir / 'mystic_mrc')))
        self._script_path: Optional[Path] = None

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._stdout_task: Optional[asyncio.Task] = None
        self._inbound_task: Optional[asyncio.Task] = None
        self._active_rooms: Set[str] = set()
        self._outbound_seq = 0
        self._latency = LatencyTracker(int(config.get('mrc_latency_registry_max', 200)))

    @property
    def latency_ms(self) -> Optional[float]:
        return self._latency.latency_ms

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        self._closing = False
        self._script_path = ensure_fake_bbs_tree(self.bbspath, self.config)
        await self._spawn()
        if not self._inbound_task or self._inbound_task.done():
            self._inbound_task = asyncio.create_task(self._inbound_loop())

    async def _spawn(self):
        logger.info("Starting mystic mrc_client subprocess (bbspath=%s)", self.bbspath)
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, str(self._script_path),
            cwd=str(self.bbspath),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._stdout_task = asyncio.create_task(self._stdout_loop())

    async def _stdout_loop(self):
        proc = self._proc
        if not proc or not proc.stdout:
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode(errors='ignore').strip()
                if not text:
                    continue
                # INFO, not debug: unlike the native backend's raw wire
                # packet logging (deliberately debug-only since a chat
                # message could appear in plaintext there), the vendored
                # client's own stdout is purely connection-status text
                # ("Attempting connection...", "Connected...",
                # "Reconnecting in..." -- see mrc_client.py's logger())
                # -- actual chat content only ever flows through the
                # file-based IPC, never through here. Real gap this
                # closes: with this at debug and MRC_BRIDGE_LOG_LEVEL
                # defaulting to INFO, a sysop had no way to see whether
                # the mystic backend ever actually reached the hub
                # without a redeploy or a temporary log-level override.
                logger.info("mystic mrc_client: %s", text)
                if any(m in text for m in _CONNECTED_MARKERS):
                    await self._set_connected(True)
                elif any(m in text for m in _DISCONNECTED_MARKERS):
                    await self._set_connected(False)
        except asyncio.CancelledError:
            return

        await self._set_connected(False)
        if self._closing:
            return
        returncode = proc.returncode
        logger.warning(
            "mystic mrc_client subprocess exited unexpectedly (code=%s), "
            "respawning in 5s", returncode)
        await asyncio.sleep(5)
        if not self._closing:
            await self._spawn()

    async def _set_connected(self, value: bool):
        if self.connected == value:
            return
        self.connected = value
        if self.status_callback:
            await self.status_callback("upstream_connected" if value else "upstream_disconnected")

    async def stop(self):
        self._closing = True
        if self._inbound_task:
            self._inbound_task.cancel()
        if self._proc and self._proc.returncode is None:
            # SIGINT (not terminate/SIGTERM) so the vendored script's own
            # `except KeyboardInterrupt` branch runs -- its graceful
            # send_shutdown() notice to the hub and its own pidfile
            # cleanup, matching MRCConnection.stop()'s equivalent
            # best-effort SHUTDOWN packet before disconnecting.
            try:
                self._proc.send_signal(signal.SIGINT)
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.terminate()
                    await asyncio.wait_for(self._proc.wait(), timeout=3)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
            except Exception:
                pass
        if self._stdout_task:
            self._stdout_task.cancel()
        await self._set_connected(False)

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send_packet(self, packet: str):
        if not packet:
            return
        logger.debug("MYSTIC RAW OUT: %r", MRCProtocol.redact_packet_for_logs(packet))
        out_dir = mrc_outbound_dir(self.bbspath)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._outbound_seq += 1
        stem = f"{int(time.time() * 1000):013d}_{self._outbound_seq:04d}"
        final_path = out_dir / f"{stem}.mrc"
        tmp_path = out_dir / f"{stem}.tmp"
        try:
            # Write-then-rename: the subprocess globs *.mrc every ~10ms
            # and opens whatever it finds directly -- a direct write to
            # the final name risks it reading a partial line mid-write.
            # .tmp is invisible to its glob until the rename lands.
            tmp_path.write_text(packet, encoding='utf-8', errors='replace')
            # Registered right as the packet becomes visible to the
            # subprocess (the rename), not earlier -- same reasoning as
            # MRCConnection.send_packet's identical placement.
            self._latency.note_sent(packet)
            tmp_path.rename(final_path)
        except OSError:
            logger.exception("Failed to write outbound mystic packet")

    # ------------------------------------------------------------------
    # Room activity -> file-marker sync
    # ------------------------------------------------------------------

    async def sync_active_rooms(self, rooms: Set[str]):
        """Called by BridgeApp whenever room membership changes (mirrors
        its own _rooms_with_active_sessions()). The subprocess only
        delivers inbound packets for a room into temp<room>/ while that
        room's tchat.inuse marker exists -- this keeps that marker (and
        the room's own chat<room>.dat registration) in sync with who's
        actually listening, so we're not endlessly polling directories
        with nobody home."""
        wanted = {_safe_room(r) for r in rooms if r}
        added = wanted - self._active_rooms
        removed = self._active_rooms - wanted
        for room in added:
            d = room_dir(self.bbspath, room)
            d.mkdir(parents=True, exist_ok=True)
            chat_dat_path(self.bbspath, room).parent.mkdir(parents=True, exist_ok=True)
            chat_dat_path(self.bbspath, room).touch(exist_ok=True)
            inuse_marker_path(self.bbspath, room).touch(exist_ok=True)
        for room in removed:
            try:
                inuse_marker_path(self.bbspath, room).unlink(missing_ok=True)
            except OSError:
                pass
        self._active_rooms = wanted

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    async def _inbound_loop(self):
        poll_interval = float(self.config.get('mystic_poll_interval_seconds', 0.15))
        while not self._closing:
            try:
                for room in list(self._active_rooms):
                    d = room_dir(self.bbspath, room)
                    if not d.is_dir():
                        continue
                    for f in sorted(d.glob('*.mrc')):
                        try:
                            raw = f.read_text(encoding='utf-8', errors='replace')
                        except OSError:
                            continue
                        try:
                            f.unlink()
                        except OSError:
                            pass
                        line = raw.strip()
                        if not line:
                            continue
                        logger.debug("MYSTIC RAW IN: %r", MRCProtocol.redact_packet_for_logs(line))
                        if self._latency.check_received(line) and self.latency_callback:
                            await self.latency_callback(self._latency.latency_ms)
                        try:
                            parsed = MRCProtocol.parse_packet(line)
                        except Exception as e:
                            logger.warning("Unparsable inbound mystic packet %r: %s", line, e)
                            continue
                        for cb in self.message_callbacks:
                            await cb(parsed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("mystic backend inbound loop error")
            await asyncio.sleep(poll_interval)

    def add_message_callback(self, cb):
        self.message_callbacks.append(cb)
