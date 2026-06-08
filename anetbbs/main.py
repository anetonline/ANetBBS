# anetbbs/main.py
"""Telnet/SSH/rlogin entry point for ANetBBS.

Console script: `anetbbs` -> anetbbs.main:main
"""
import asyncio
import logging
import signal
import sys
import os

# Pin DATABASE_URL to an absolute path derived from this file's own location
# before any anetbbs imports — mirroring what deploy/serve.py does for the
# web process. Without this, the telnet/SSH service falls back to whatever
# DATABASE_URL is in the environment. If it's absent (e.g. wizard install
# wrote SQLALCHEMY_DATABASE_URI instead) or wrong, anetbbs.config resolves
# DATA_DIR from config.py's location in the venv site-packages, which doesn't
# exist, causing "unable to open database file" on every DB query.
_INSTALL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ['DATABASE_URL'] = (
    os.environ.get('ANETBBS_DB_URL')          # explicit operator override
    or os.environ.get('DATABASE_URL')          # already-correct EnvironmentFile value
    or f'sqlite:///{_INSTALL_DIR}/data/anetbbs.db'
)

from anetbbs.core.session import BBSSession
from anetbbs.core.service_locator import ServiceLocator
from anetbbs.features.chat import ChatManager
from anetbbs.config import get_config

# Set up logging once. We do this at import time so a fresh
# `anetbbs --version` or `anetbbs --help` doesn't crash on a missing
# logger — but the FileHandler is the tricky bit. Past versions hard-
# coded `FileHandler('bbs.log')`, which resolves relative to the
# CWD at process start. Running the CLI from /tmp left
# `/tmp/bbs.log` lingering owned by the wrong user, and every later
# CLI invocation from /tmp blew up with PermissionError before the
# argparse even saw the args. We now:
#   1. Resolve the log path to an absolute location next to the
#      installed package (or honour an explicit ANETBBS_LOG_FILE env
#      override, useful for journald-only setups).
#   2. Treat the FileHandler as best-effort — if we can't open the
#      file, log only to stdout. The service's logs still hit the
#      systemd journal regardless.
def _build_log_handlers():
    handlers = [logging.StreamHandler(sys.stdout)]
    log_path = os.environ.get('ANETBBS_LOG_FILE') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'bbs.log')
    try:
        handlers.append(logging.FileHandler(log_path))
    except (OSError, PermissionError):
        # Common cases: read-only filesystem, leftover root-owned
        # /tmp/bbs.log, install dir mounted noexec. Falling back to
        # stdout-only is harmless — systemd captures it.
        sys.stderr.write(
            f'anetbbs: warning: cannot open log file {log_path!r}; '
            f'logging to stdout only.\n')
    return handlers

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=_build_log_handlers(),
)
logger = logging.getLogger(__name__)


class BBSServer:
    def __init__(self, config=None):
        self.config = config or {
            'server': {'host': '0.0.0.0', 'port': 2233}
        }
        self.server = None
        self.running = True
        self.active_connections = set()
        self._shutdown_event = asyncio.Event()

    async def handle_connection(self, reader, writer):
        addr = writer.get_extra_info('peername')
        logger.info(f'New connection from {addr}')

        self.active_connections.add(writer)

        session = BBSSession(reader, writer, self.config)
        chat_manager = ChatManager(session)
        ServiceLocator.register('chat_manager', chat_manager)

        try:
            await session.start()
        except Exception as e:
            logger.error(f"Unhandled exception in client_connected_cb\n{writer.transport}")
            logger.exception(e)
        finally:
            if not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception as e:
                    logger.error(f"Error closing connection for {addr}: {e}")

            self.active_connections.discard(writer)
            ServiceLocator.clear()
            logger.info(f'Connection closed for {addr}')

    async def close_all_connections(self):
        if self.active_connections:
            logger.info(f"Closing {len(self.active_connections)} active connections...")
            close_tasks = []
            for writer in self.active_connections:
                if not writer.is_closing():
                    writer.close()
                    close_tasks.append(writer.wait_closed())
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)
            self.active_connections.clear()

    def stop(self):
        if not self._shutdown_event.is_set():
            logger.info("Shutting down server...")
            self._shutdown_event.set()
            self.running = False
            if self.server:
                self.server.close()

    async def shutdown(self):
        self.stop()
        await self.close_all_connections()
        if self.server:
            await self.server.wait_closed()
        logger.info("Server shutdown complete")

    async def start(self):
        try:
            self.server = await asyncio.start_server(
                self.handle_connection,
                self.config['server']['host'],
                self.config['server']['port']
            )

            addr = self.server.sockets[0].getsockname()
            logger.info(f'BBS server started on {addr}')

            async with self.server:
                await self._shutdown_event.wait()

        except Exception as e:
            logger.error(f"Server error: {e}")
        finally:
            await self.shutdown()


def main():
    """Main entry point — starts telnet, SSH, and optionally rlogin servers."""
    # --version / -V: print the running version and exit. Available so
    # sysops can confirm what's deployed without grepping config.
    if any(a in ('--version', '-V') for a in sys.argv[1:]):
        from anetbbs.version import VERSION
        print(f'ANetBBS {VERSION}')
        return

    env = os.environ.get('FLASK_ENV', 'development')
    config_class = get_config(env)

    telnet_enabled = config_class.TELNET_ENABLED
    ssh_enabled = config_class.SSH_ENABLED
    rlogin_enabled = config_class.RLOGIN_ENABLED
    ftp_enabled = getattr(config_class, 'FTP_ENABLED', False)

    if not telnet_enabled and not ssh_enabled and not rlogin_enabled \
            and not ftp_enabled:
        logger.info("All BBS servers are disabled. Use 'anetbbs-web' to run the web interface.")
        return

    # FTP server runs in a daemon thread alongside the asyncio servers.
    # pyftpdlib has its own selectors-based loop and doesn't natively mix
    # with asyncio; a thread is the cleanest integration. CRITICAL: we
    # build a MINIMAL Flask app here (just SQLAlchemy bound to the same
    # DB), NOT the full web_app.create_app() — that one registers 50
    # blueprints and starts the echomail/RSS pollers, all of which use
    # threading primitives. Pulling them in turns this pure-asyncio
    # process into a mixed-concurrency mess and the telnet/SSH login
    # flow's threading.Condition breaks with "cannot notify on
    # un-acquired lock".
    if ftp_enabled:
        try:
            import threading
            from anetbbs.ftp.server import run as _ftp_run, build_minimal_app
            flask_app = build_minimal_app()

            def _ftp_thread_wrapper():
                """Wrap the FTP entry point so a crash inside it actually
                lands in the journal. Without this wrapper the thread
                dies silently — every traceback inside ftp.server.run
                (TLS init failure, port-bind failure, anything) was lost,
                which is what masked the cert-permission bug for a week.
                """
                try:
                    _ftp_run(flask_app)
                except Exception:
                    logger.exception(
                        "FTP server thread crashed — listener will NOT "
                        "be available until the BBS is restarted. "
                        "Common causes: TLS cert unreadable by the "
                        "service user, port 21 already bound, missing "
                        "CAP_NET_BIND_SERVICE on the unit.")

            ftp_thread = threading.Thread(
                target=_ftp_thread_wrapper,
                name='ftp-server', daemon=True)
            ftp_thread.start()
            logger.info("Starting ANetBBS FTP Server on %s:%d",
                        config_class.FTP_HOST, config_class.FTP_PORT)
        except ImportError as exc:
            logger.warning("pyftpdlib not installed — FTP server disabled. "
                           "Install with: pip install pyftpdlib>=2.0.0 (%s)", exc)
        except Exception:
            logger.exception("Failed to start FTP server")

    bbs_config = {
        'server': {
            'host': config_class.TELNET_HOST,
            'port': config_class.TELNET_PORT,
        }
    }
    rlogin_cfg = {
        'rlogin': {
            'host': config_class.RLOGIN_HOST,
            'port': config_class.RLOGIN_PORT,
        }
    }

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    servers_to_stop = []

    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        if not loop.is_closed():
            for s in servers_to_stop:
                loop.call_soon_threadsafe(s.stop)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    async def run_all():
        tasks = []

        if telnet_enabled:
            telnet_server = BBSServer(bbs_config)
            servers_to_stop.append(telnet_server)
            logger.info("Starting ANetBBS Telnet Server on %s:%d",
                        config_class.TELNET_HOST, config_class.TELNET_PORT)
            tasks.append(asyncio.ensure_future(telnet_server.start()))

        if ssh_enabled:
            try:
                from anetbbs.core.ssh_server import start_ssh_server
                logger.info("Starting ANetBBS SSH Server on %s:%d",
                            config_class.SSH_HOST, config_class.SSH_PORT)
                ssh_srv = await start_ssh_server(
                    config_class.SSH_HOST,
                    config_class.SSH_PORT,
                    config_class.SSH_HOST_KEY_FILE,
                    bbs_config,
                )
                async def _ssh_keepalive():
                    async with ssh_srv:
                        await asyncio.get_event_loop().create_future()
                tasks.append(asyncio.ensure_future(_ssh_keepalive()))
            except ImportError:
                logger.warning("asyncssh not installed — SSH server disabled. "
                               "Install with: pip install asyncssh>=2.14.0")
            except Exception as exc:
                logger.error("Failed to start SSH server: %s", exc)

        if rlogin_enabled:
            from anetbbs.core.rlogin_server import RloginServer
            rlogin_cfg_merged = {**bbs_config, **rlogin_cfg}
            rlogin_server = RloginServer(rlogin_cfg_merged)
            servers_to_stop.append(rlogin_server)
            logger.info("Starting ANetBBS rlogin Server on %s:%d",
                        config_class.RLOGIN_HOST, config_class.RLOGIN_PORT)
            tasks.append(asyncio.ensure_future(rlogin_server.start()))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    try:
        loop.run_until_complete(run_all())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        try:
            tasks = asyncio.all_tasks(loop)
            for task in tasks:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        logger.info("Server shutdown complete")


if __name__ == '__main__':
    main()
