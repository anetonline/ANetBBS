# anetbbs/core/petscii_server.py
"""
Dedicated PETSCII-only telnet listener for Commodore 64/128 users (see
the "PETSCII Terminal Support (Phase 1)" plan). Modeled directly on
anetbbs/core/rlogin_server.py -- but simpler, since there's no handshake
to parse: every connection on this port IS PETSCII, at a fixed screen
width (40 or 80), unconditionally. Real C64 telnet clients (Novaterm,
CCGMS, 64NIC+, etc.) mostly don't announce themselves usefully via
telnet TTYPE negotiation, so this sidesteps auto-detection entirely --
one dedicated port per screen width, same convention Synchronet itself
uses (confirmed via its own SCFG screen: separate "40 Column PETSCII
Support" / "80 Column PETSCII Support" port settings).

Two instances of this server run side by side (see anetbbs/main.py),
one per width, each independently opt-in via PETSCII40_ENABLED /
PETSCII80_ENABLED.
"""
import asyncio
import logging

from .session import BBSSession

logger = logging.getLogger(__name__)


class PetsciiServer:
    """Asyncio-based PETSCII-only BBS listener, fixed at one screen width."""

    def __init__(self, config, width):
        self.config = config
        self.width = width
        self.server = None
        self._shutdown_event = asyncio.Event()
        self.active_connections = set()

    async def handle_connection(self, reader, writer):
        addr = writer.get_extra_info('peername')
        logger.info('PETSCII (%d-col) connection from %s', self.width, addr)
        self.active_connections.add(writer)

        try:
            session = BBSSession(reader, writer, self.config,
                                 forced_term_mode='petscii',
                                 forced_width=self.width)
            # Real bug found on first hardware test: a real C64 keyboard's
            # PETSCII byte code for a letter key depends on which charset
            # ROM is currently selected -- this is a KERNAL keyboard-decode
            # difference, not just a display/rendering choice. In the
            # default power-on "graphics" charset, unshifted letters send
            # the UPPERCASE byte range regardless of what was actually
            # typed; only after selecting the upper/lowercase charset
            # (0x0E) do unshifted letters send the lowercase range. This
            # MUST happen before session.start() (and therefore before
            # login_screen()) -- sending it only after a successful login
            # (the original approach) meant every password typed during
            # login arrived silently case-flattened to uppercase,
            # authentication always failed, and it looked exactly like a
            # broken user database.
            from ..features.petscii_codec import LOWERCASE_CHARSET
            await session.write(LOWERCASE_CHARSET)
            await session.start()
        except Exception as exc:
            logger.error('PETSCII (%d-col) session error from %s: %s',
                        self.width, addr, exc)
        finally:
            if not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            self.active_connections.discard(writer)
            logger.info('PETSCII (%d-col) connection closed for %s', self.width, addr)

    async def start(self):
        """Start the PETSCII listener and wait until shutdown."""
        self.server = await asyncio.start_server(
            self.handle_connection,
            self.config.get('petscii', {}).get('host', '0.0.0.0'),
            self.config.get('petscii', {}).get('port'),
        )
        addr = self.server.sockets[0].getsockname()
        logger.info('PETSCII (%d-col) server started on %s', self.width, addr)

        async with self.server:
            await self._shutdown_event.wait()

    def stop(self):
        if not self._shutdown_event.is_set():
            self._shutdown_event.set()
            if self.server:
                self.server.close()
