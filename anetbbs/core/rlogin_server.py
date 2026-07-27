# core/rlogin_server.py
"""
RFC 1282 rlogin server for ANetBBS.

rlogin is inherently insecure (no encryption). This server is disabled by
default (RLOGIN_ENABLED=false). Enable only on trusted networks.

Protocol summary (RFC 1282):
  1. Client connects and sends a NUL-terminated string: "<client-user>\0<server-user>\0<terminal/speed>\0"
  2. Server acknowledges with a single NUL byte.
  3. Normal bidirectional data flow follows.
"""
import asyncio
import logging

from .session import BBSSession

logger = logging.getLogger(__name__)

# Warning printed to stderr at startup when rlogin is enabled
_RLOGIN_WARNING = (
    'WARNING: rlogin server is enabled. '
    'rlogin provides NO encryption and is inherently insecure. '
    'Only enable on trusted, firewalled networks.'
)


class RloginServer:
    """Asyncio-based rlogin BBS server."""

    def __init__(self, config):
        self.config = config
        self.server = None
        self._shutdown_event = asyncio.Event()
        self.active_connections = set()

    async def handle_connection(self, reader, writer):
        addr = writer.get_extra_info('peername')
        # DEBUG, not INFO, at raw accept -- the SCC's own health-check
        # prober (anetbbs/web/control.py's _probe_tcp_uncached) hits this
        # port every ~5s from 127.0.0.1 with a bare connect-then-close and
        # no handshake data at all, which used to look IDENTICAL to a real
        # client at INFO level: "rlogin connection from ... / rlogin
        # connection closed for ..." with nothing in between. A sysop
        # reported this live as what looked like a sustained, unbannable
        # attack (no real external IP, because there genuinely isn't
        # one -- it's the BBS's own loopback health check). Real client
        # activity still gets its own INFO line once the handshake
        # actually completes below.
        logger.debug('rlogin connection from %s', addr)
        self.active_connections.add(writer)
        handshake_ok = False

        try:
            # rlogin handshake: read until three NUL-terminated strings
            header = await _read_rlogin_header(reader)
            logger.debug('rlogin header from %s: %s', addr, header)
            handshake_ok = True
            logger.info('rlogin session started for %s (login as %r)',
                       addr, header.get('server_user'))

            # Send the acknowledgement NUL byte
            writer.write(b'\x00')
            await writer.drain()

            # The handshake's server_user is who the client wants to log in as.
            # Pass it as prefill so the BBS session can skip the username prompt.
            session = BBSSession(reader, writer, self.config,
                                 prefill_username=header.get('server_user'))
            await session.start()
        except asyncio.IncompleteReadError:
            logger.debug('rlogin connection from %s closed during handshake', addr)
        except Exception as exc:
            logger.error('rlogin session error from %s: %s', addr, exc)
        finally:
            if not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            self.active_connections.discard(writer)
            if handshake_ok:
                logger.info('rlogin connection closed for %s', addr)
            else:
                logger.debug('rlogin connection closed for %s (no handshake)', addr)

    async def start(self):
        """Start the rlogin server and wait until shutdown."""
        import sys
        print(_RLOGIN_WARNING, file=sys.stderr, flush=True)
        logger.warning(_RLOGIN_WARNING)

        self.server = await asyncio.start_server(
            self.handle_connection,
            self.config.get('rlogin', {}).get('host', '0.0.0.0'),
            self.config.get('rlogin', {}).get('port', 513),
        )
        addr = self.server.sockets[0].getsockname()
        logger.info('rlogin server started on %s', addr)

        async with self.server:
            await self._shutdown_event.wait()

    def stop(self):
        if not self._shutdown_event.is_set():
            self._shutdown_event.set()
            if self.server:
                self.server.close()


async def _read_rlogin_header(reader):
    """
    Read the rlogin client header.

    Returns a dict with keys: client_user, server_user, terminal_speed.
    """
    # The very first byte from the client must be NUL (RFC 1282 §3)
    first = await reader.readexactly(1)
    if first != b'\x00':
        raise ValueError(f'Expected NUL, got {first!r}')

    # Real gap found in a full auth-security audit: this loop had no cap
    # on total accumulated size or iteration count -- a client that never
    # sends the 3 required NUL bytes could force unbounded buffer growth
    # (a DoS against this connection's memory) before IncompleteReadError/
    # EOF ever fires. A real rlogin header (two usernames + a terminal
    # speed string) is well under a few hundred bytes; 4096 is generous
    # headroom, not a realistic legitimate size.
    _MAX_HEADER_BYTES = 4096
    raw = b''
    while True:
        chunk = await reader.read(256)
        if not chunk:
            raise asyncio.IncompleteReadError(raw, None)
        raw += chunk
        if len(raw) > _MAX_HEADER_BYTES:
            raise ValueError(
                f'rlogin header exceeded {_MAX_HEADER_BYTES} bytes without '
                f'completing (missing NUL terminators?)')
        # We need three NUL-terminated strings
        parts = raw.split(b'\x00')
        if len(parts) >= 4:
            break

    client_user = parts[0].decode('ascii', errors='replace')
    server_user = parts[1].decode('ascii', errors='replace')
    terminal_speed = parts[2].decode('ascii', errors='replace')

    return {
        'client_user': client_user,
        'server_user': server_user,
        'terminal_speed': terminal_speed,
    }
