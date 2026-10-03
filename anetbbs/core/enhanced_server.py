# anetbbs/core/enhanced_server.py
"""
ANetBBS Enhanced Client -- browser-based, mouse-driven, auto-graphical
client just for ANetBBS (see the "ANetBBS Enhanced Client" plan).

TEST VERSION ONLY: disabled by default (Config.ENHANCED_ENABLED), not
yet pushed to GitHub or deployed to the live install, per Jerry's
explicit ask. Purely additive -- every existing client (telnet/SSH/
rlogin/PETSCII/SyncTerm/NetRunner/MagiTerm) is completely unaffected
whether this is enabled or not.

Uses aiohttp for the WebSocket transport rather than adding a new
`websockets` dependency: aiohttp is already a hard requirement
(requirements.txt) for the MRC bridge's own downstream WebSocket-to-
browser path (mrc/bridge/main.py's handle_websocket), so this reuses an
already-vendored library and an already-established in-repo pattern
(web.WebSocketResponse(heartbeat=...), await ws.prepare(request),
`async for msg in ws:`) instead of introducing a second WebSocket
library for no reason.

Modeled on anetbbs/core/petscii_server.py's minimal shape (one
dedicated listener, no negotiation -- every connection on this port IS
the Enhanced Client protocol), but an aiohttp web.Application instead
of a bare asyncio.start_server(), since a browser can only open a
WebSocket via an HTTP upgrade handshake, not a raw TCP connect. Runs
inside the SAME asyncio event loop as the existing telnet/SSH/rlogin/
PETSCII servers (anetbbs/main.py), not inside anetbbs-web.service's
eventlet-monkey-patched Flask process -- running real asyncio
BBSSession objects inside an eventlet process would be a real cross-
event-loop risk for no benefit.

The one genuinely novel, risk-bearing piece here is _WSReaderAdapter /
_WSWriterAdapter: bridging aiohttp's message-oriented, exception-
signaled WebSocketResponse API into the byte-stream read(n)/write(data)
+ stored-exception (.exception()) contract anetbbs/core/session.py's
BBSSession already depends on (_reader_stored_exception()/
_drain_protected()) -- see the deadconn-freeze-audit skill. Getting
this wrong risks reintroducing the exact "dead connection replays a
stored exception forever, mistaken for a fresh timeout, spins the
session" bug class that caused a real documented 16-minute/8.4GB-RSS
incident elsewhere in this codebase (ANEView's _read_key()).
"""
import asyncio
import logging
import os

from .session import BBSSession
from ..features import enhanced_protocol

logger = logging.getLogger(__name__)

try:
    from aiohttp import web, WSMsgType
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False


_CLIENT_HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'enhanced_client', 'index.html')


class _WSReaderAdapter:
    """Reader-shaped adapter wrapping an aiohttp WebSocketResponse so
    BBSSession can use it completely unmodified via its existing
    asyncio.StreamReader-shaped contract:

      - async read(n) -> up to n bytes, b'' on clean EOF
      - exception() -> None normally; once the connection has genuinely
        died, the SAME stored exception object on every subsequent
        call, forever (matches real asyncio.StreamReader.exception()
        exactly -- see session.py's _reader_stored_exception()
        docstring for the full incident this contract exists to avoid
        repeating).

    Deliberately never stores a bare TimeoutError as the stored
    exception (wraps it in ConnectionResetError instead) -- storing one
    would make a replayed dead-connection error indistinguishable BY
    TYPE from a genuine asyncio.wait_for() timeout elsewhere in
    session.py, which is the exact ambiguity that caused the ANEView
    incident this adapter is deliberately designed not to repeat.

    Each incoming WebSocket TEXT frame is one complete client->server
    JSON message (enhanced_protocol.decode_client_message); it's
    decoded into the raw keystroke byte(s) it represents and queued
    into an internal buffer that read(n) drains like any ordinary byte
    stream -- menu_engine.py's hotkey dispatch and read_key()/
    read_line() never need to know a WebSocket is involved at all.

    Real bug found live on first Pi3 test pass: BBSSession's callers
    routinely wrap read_raw()/reader.read() in their own short
    asyncio.wait_for() (e.g. mrc_chat.py's _enter_split_screen() polls
    read_raw(32) in a tight wait_for(timeout=0.2) loop while probing
    for a terminal-size CPR response that an Enhanced Client browser
    can never send). Each timeout CANCELS whatever read() was doing --
    and aiohttp's WebSocketResponse.receive() does not tolerate being
    cancelled mid-await and then called again: the next receive() call
    can raise "RuntimeError: Concurrent call to receive() is not
    allowed" even though nothing was actually concurrent from this
    code's point of view, because aiohttp's own internal waiting-future
    bookkeeping doesn't always finish unwinding before the next call
    starts. Observed live: entering MRC chat (which calls that exact
    CPR-probe loop) reliably crashed the whole session with that error.

    Fixed by never letting any OUTER caller's wait_for()/cancellation
    touch ws.receive() at all: _pump() is the ONE caller of
    ws.receive() for this connection's entire lifetime, running as its
    own independent task. read() only ever waits on the queue _pump()
    feeds, so cancelling a read() (an outer timeout) only ever
    abandons a queue.get() -- never anything aiohttp itself is in the
    middle of.
    """

    def __init__(self, ws):
        self._ws = ws
        self._buf = bytearray()
        self._exc = None
        self._queue = asyncio.Queue()
        self._pump_task = asyncio.ensure_future(self._pump())

    def exception(self):
        return self._exc

    def _store(self, exc):
        if self._exc is None:
            if isinstance(exc, TimeoutError):
                exc = ConnectionResetError(str(exc) or 'websocket closed')
            self._exc = exc
        return self._exc

    async def _pump(self):
        """Runs for the life of the connection; never cancelled by a
        caller's own read() timeout (see class docstring)."""
        try:
            while True:
                try:
                    msg = await self._ws.receive()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self._queue.put(('exc', self._store(exc)))
                    return
                if msg.type == WSMsgType.TEXT:
                    data = enhanced_protocol.decode_client_message(msg.data)
                    if data:
                        await self._queue.put(('data', data))
                    continue
                if msg.type == WSMsgType.BINARY:
                    # Protocol is JSON text only -- a stray binary frame
                    # is ignored, not treated as a connection error.
                    continue
                if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING,
                                WSMsgType.CLOSED):
                    await self._queue.put(
                        ('exc', self._store(ConnectionResetError('websocket closed'))))
                    return
                if msg.type == WSMsgType.ERROR:
                    await self._queue.put((
                        'exc',
                        self._store(self._ws.exception()
                                    or ConnectionResetError('websocket error'))))
                    return
                # PING/PONG/etc: aiohttp's receive() answers these
                # internally (autoping=True, the default) and shouldn't
                # normally surface here at all -- ignored defensively
                # if they ever do, rather than treated as data or an
                # error.
        except asyncio.CancelledError:
            pass

    async def read(self, n=1):
        if self._exc is not None:
            raise self._exc
        while not self._buf:
            kind, payload = await self._queue.get()
            if kind == 'exc':
                raise payload
            self._buf.extend(payload)
        chunk = bytes(self._buf[:n])
        del self._buf[:n]
        return chunk

    def close(self):
        if self._pump_task is not None:
            self._pump_task.cancel()


class _WSWriterAdapter:
    """Writer-shaped adapter, the other half of _WSReaderAdapter.

    write() stays synchronous (matching asyncio.StreamWriter) by
    scheduling the real aiohttp send as a background task chained after
    any send still in flight -- aiohttp's own send_str() is a single
    atomic async call with no separate buffer-then-drain split, so this
    fakes that split rather than changing BBSSession's write()/drain()
    calling convention.

    drain() awaits that chain. session.py's own _drain_protected()
    already wraps every drain() call in asyncio.wait_for(timeout=...)
    (Shape 2 of the deadconn-freeze-audit skill: neither
    asyncio.StreamWriter.drain() nor this adapter's drain() has any
    built-in timeout, so an unresponsive peer could otherwise hang it
    forever) -- a stuck client here just times out exactly like every
    other transport already handles, with no extra protection needed in
    this file.

    close() cancels any still-pending send so a timed-out drain()
    doesn't leave an orphaned task quietly awaiting a dead socket
    forever in the background.
    """

    def __init__(self, ws, peername=''):
        self._ws = ws
        self._peername = peername
        self._pending = None
        self._closed = False

    def write(self, data):
        if self._closed:
            return
        if isinstance(data, (bytes, bytearray)):
            text = bytes(data).decode('utf-8', errors='replace')
        else:
            text = data
        prev = self._pending

        async def _send():
            if prev is not None:
                try:
                    await prev
                except Exception:
                    pass
            await self._ws.send_str(text)

        self._pending = asyncio.ensure_future(_send())

    async def drain(self):
        pending = self._pending
        if pending is not None:
            await pending

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._pending is not None:
            self._pending.cancel()
        try:
            asyncio.ensure_future(self._ws.close())
        except Exception:
            pass

    def is_closing(self):
        return self._closed

    def get_extra_info(self, name, default=None):
        if name == 'peername':
            return self._peername
        return default


class EnhancedServer:
    """aiohttp-based listener for the Enhanced Client -- serves the
    static client page at / and the WebSocket upgrade at /ws."""

    def __init__(self, config, host, port):
        self.config = config
        self.host = host
        self.port = port
        self.runner = None
        self.active_connections = set()

    async def _handle_index(self, request):
        try:
            with open(_CLIENT_HTML_PATH, 'rb') as f:
                body = f.read()
        except OSError:
            return web.Response(status=404, text='Enhanced Client page not found')
        # No cache-control was set before -- a real gap found live: a
        # plain reload after copying over an updated index.html kept
        # showing the OLD client's rendering (font-gap fix included),
        # because the browser silently served its own cached copy
        # instead of re-fetching. TEST VERSION, under active iteration
        # -- always fetch fresh rather than ever risk re-chasing an
        # already-fixed bug because of a stale cache.
        return web.Response(
            body=body, content_type='text/html',
            headers={'Cache-Control': 'no-cache, no-store, must-revalidate'})

    async def _handle_ws(self, request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        peer = request.remote or ''
        logger.info('Enhanced Client connection from %s', peer)
        self.active_connections.add(ws)

        reader = _WSReaderAdapter(ws)
        try:
            writer = _WSWriterAdapter(ws, peername=peer)
            session = BBSSession(reader, writer, self.config,
                                 forced_term_mode='enhanced')
            await session.start()
        except Exception as exc:
            logger.error('Enhanced Client session error from %s: %s', peer, exc)
        finally:
            reader.close()
            self.active_connections.discard(ws)
            try:
                if not ws.closed:
                    await ws.close()
            except Exception:
                pass
            logger.info('Enhanced Client connection closed for %s', peer)

        return ws

    async def start(self):
        if not _AIOHTTP_AVAILABLE:
            logger.error(
                'ENHANCED_ENABLED is true but aiohttp is not installed -- '
                'Enhanced Client server disabled. Install with: '
                'pip install aiohttp>=3.14.3')
            return
        app = web.Application()
        app.router.add_get('/', self._handle_index)
        app.router.add_get('/ws', self._handle_ws)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        logger.info('Enhanced Client server started on %s:%d', self.host, self.port)
        # Keep this task alive for as long as the runner exists --
        # stop() tears the runner down, which is what actually ends
        # this wait (mirrors PetsciiServer's own shutdown-event shape).
        self._shutdown_event = asyncio.Event()
        await self._shutdown_event.wait()

    def stop(self):
        """Sync, matching every other *Server.stop() in this file's
        siblings (anetbbs/main.py's signal_handler calls these via
        loop.call_soon_threadsafe(), which cannot await) -- schedules
        the real async cleanup instead of awaiting it directly."""
        if getattr(self, '_stopped', False):
            return
        self._stopped = True
        ev = getattr(self, '_shutdown_event', None)
        if ev is not None:
            ev.set()
        if self.runner is not None:
            asyncio.ensure_future(self.runner.cleanup())
