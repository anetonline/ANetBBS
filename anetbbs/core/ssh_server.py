# core/ssh_server.py
"""
Asyncssh-based SSH server for ANetBBS.

Bridges an SSH session to the same BBSSession the telnet/rlogin servers use.
Uses asyncssh's process_factory API (which gives real stdin/stdout streams +
PTY handling) instead of the low-level SSHServerSession callback interface —
the previous implementation mixed the two and caused clients (e.g. SyncTerm)
to see a blank screen because the BBS's first banner write never reached them.

A host key is auto-generated on first run if the key file is missing.
"""
import logging
import os
import asyncssh

from .session import BBSSession

logger = logging.getLogger(__name__)

# asyncssh's "socket.send() raised exception" is logged at WARNING
# whenever a peer half-closes mid-IAC — that's normal traffic for SCC
# probes, scanners, and rage-quitting clients. Demote it to DEBUG so
# the journal stays readable. Anything actually worth seeing
# (auth failures, key errors) is logged at ERROR by asyncssh, which
# we keep visible.
logging.getLogger('asyncssh').setLevel(logging.ERROR)


class _SshStreamReader:
    """Adapts an asyncssh SSHReader to the asyncio StreamReader interface
    BBSSession expects (read/readline/at_eof returning bytes)."""

    def __init__(self, ssh_reader):
        self._r = ssh_reader

    async def read(self, n=-1):
        try:
            data = await self._r.read(n if n > 0 else 4096)
        except (asyncssh.DisconnectError, asyncssh.ConnectionLost, asyncssh.BreakReceived):
            return b''
        if isinstance(data, str):
            data = data.encode('utf-8', errors='replace')
        return data

    def at_eof(self):
        try:
            return self._r.at_eof()
        except Exception:
            return False


class _SshStreamWriter:
    """Adapts an asyncssh SSHWriter to the asyncio StreamWriter interface."""

    def __init__(self, ssh_writer, peername):
        self._w = ssh_writer
        self._peername = peername
        self._closed = False

    def write(self, data):
        if self._closed:
            return
        if isinstance(data, bytes):
            # asyncssh SSHWriter accepts bytes when encoding=None on the server,
            # but for safety we decode here so it works in both modes.
            try:
                self._w.write(data)
            except TypeError:
                self._w.write(data.decode('utf-8', errors='replace'))
        else:
            self._w.write(data)

    def get_extra_info(self, key, default=None):
        if key == 'peername':
            return self._peername
        return default

    def is_closing(self):
        return self._closed

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._w.close()
            except Exception:
                pass

    async def wait_closed(self):
        try:
            await self._w.wait_closed()
        except Exception:
            pass

    async def drain(self):
        try:
            await self._w.drain()
        except Exception:
            pass


def _make_process_handler(bbs_config):
    """Returns an async coroutine asyncssh will spawn per shell connection."""
    async def _handle(process):
        peer = process.get_extra_info('peername')
        # Pull the SSH username + password. validate_password stashes the
        # password in two places (set_extra_info AND a dict on the server
        # instance) so we can recover it even if the asyncssh version
        # doesn't honor set_extra_info.
        ssh_user = process.get_extra_info('username')
        ssh_pass = None
        pubkey_user = None
        try:
            conn = process.get_extra_info('connection')
            if conn is not None:
                # Public-key auth (see validate_public_key()) already
                # resolved a full user dict -- if that's how this
                # connection authenticated, it takes priority and no
                # password is needed at all.
                pubkey_user = _PUBKEY_USER_CACHE.get(id(conn))
                if pubkey_user is None:
                    ssh_pass = conn.get_extra_info('bbs_password')
                    if not ssh_pass:
                        # Fallback: look up by connection id in the server-side cache.
                        ssh_pass = _PASSWORD_CACHE.get(id(conn))
        except Exception:
            pass
        logger.info('SSH session opened from %s as %s (auth=%s)',
                    peer, ssh_user,
                    'pubkey' if pubkey_user else ('password' if ssh_pass else 'none'))
        reader = _SshStreamReader(process.stdin)
        writer = _SshStreamWriter(process.stdout, peer)
        if pubkey_user is not None:
            session = BBSSession(reader, writer, bbs_config,
                                 prefill_authenticated_user=pubkey_user)
        else:
            session = BBSSession(reader, writer, bbs_config,
                                 prefill_username=ssh_user,
                                 prefill_password=ssh_pass)
        # Populate terminal info from the SSH PTY request so term_mode works.
        # SSH uses its own pty-req channel; telnet NAWS/TTYPE don't apply here.
        try:
            ttype = process.term_type  # e.g. 'xterm-256color', 'SyncTERM', etc.
            if ttype:
                session.terminal_type = ttype
        except Exception:
            pass
        try:
            ts = process.term_size   # (cols, rows, px_w, px_h)
            if ts and ts[0] > 0 and ts[1] > 0:
                session.window_size = (ts[0], ts[1])
        except Exception:
            pass
        try:
            await session.start()
        except Exception as exc:
            logger.exception('SSH BBS session error from %s: %s', peer, exc)
        finally:
            try:
                process.exit(0)
            except Exception:
                pass
            logger.info('SSH session closed for %s', peer)
    return _handle


# Connection-id -> password cache. Cleared on connection_lost. Lets the
# spawned process_factory recover the password even when asyncssh's
# set_extra_info is a no-op in some versions.
_PASSWORD_CACHE = {}

# Connection-id -> resolved user dict, set only when a connection
# authenticated via a registered SSH public key (validate_public_key()
# below) rather than a password. Cleared on connection_lost, same as
# _PASSWORD_CACHE.
_PUBKEY_USER_CACHE = {}


class _BBSSshServer(asyncssh.SSHServer):
    """Accepts all connections. We capture both the SSH username AND the
    SSH password the client sent and stash them on the connection object
    so the spawned process_factory can forward them to the BBS session.
    That way SSH's own username/password is enough — no BBS re-prompt.

    Public-key auth (validate_public_key() below) is a second, parallel
    path to the same goal: a registered key logs straight in with no
    password at all. Both stay enabled side by side — password_auth_
    supported() is unconditionally True throughout, so a user who never
    registers a key sees no change in behavior whatsoever."""

    def connection_made(self, conn):
        self._conn = conn
        peer = conn.get_extra_info('peername')
        logger.info('SSH connection from %s', peer)

    def connection_lost(self, exc):
        if exc:
            logger.debug('SSH connection lost: %s', exc)
        try:
            _PASSWORD_CACHE.pop(id(self._conn), None)
            _PUBKEY_USER_CACHE.pop(id(self._conn), None)
        except Exception:
            pass

    def begin_auth(self, username):
        # Require auth so the client actually sends its password/key.
        return True

    def password_auth_supported(self):
        return True

    def public_key_auth_supported(self):
        return True

    def validate_public_key(self, username, key):
        # asyncssh calls this to decide whether a CANDIDATE key is even
        # worth challenging the client to prove possession of -- the
        # actual cryptographic proof (a real signature over the SSH
        # wire protocol) happens inside asyncssh itself, independently
        # of this method, only after it returns True. This only checks
        # "is this specific fingerprint registered to this username,
        # and is the account otherwise allowed to log in" -- the same
        # active/locked/verified gates validate_password's downstream
        # authenticate() call applies, just without a password.
        try:
            from .user_manager import UserManager
            fingerprint = key.get_fingerprint()
            peer = self._conn.get_extra_info('peername')
            ip = peer[0] if peer else None
            user = UserManager().authenticate_by_public_key(
                username, fingerprint, ip=ip)
        except Exception:
            logger.exception('SSH public-key validation error for %s', username)
            return False
        if user is None:
            return False
        _PUBKEY_USER_CACHE[id(self._conn)] = user
        return True

    def validate_password(self, username, password):
        # Capture the client-supplied password — both via set_extra_info
        # (for asyncssh versions that honor it) AND in a server-side cache
        # keyed by connection id (works on every version). The spawned
        # process_factory reads it back by either path.
        try:
            self._conn.set_extra_info(bbs_password=password)
        except Exception:
            pass
        try:
            _PASSWORD_CACHE[id(self._conn)] = password
        except Exception:
            pass
        return True


def _ensure_host_key(key_file):
    """Generate an RSA host key if the file doesn't exist."""
    key_path = os.path.abspath(key_file)
    key_dir = os.path.dirname(key_path)
    if key_dir:
        os.makedirs(key_dir, exist_ok=True)
    if not os.path.exists(key_path):
        logger.info('Generating SSH host key at %s', key_path)
        key = asyncssh.generate_private_key('ssh-rsa', key_size=2048)
        key.write_private_key(key_path)
        os.chmod(key_path, 0o600)
    return key_path


async def start_ssh_server(host, port, key_file, bbs_config):
    """
    Start the asyncssh BBS SSH server.

    Args:
        host: Bind address (e.g. '0.0.0.0')
        port: Port number (e.g. 2234)
        key_file: Path to SSH host private key file
        bbs_config: Config dict passed to BBSSession (same format as telnet)

    Returns:
        The asyncssh server object.
    """
    key_path = _ensure_host_key(key_file)

    server = await asyncssh.create_server(
        _BBSSshServer,
        host=host,
        port=port,
        server_host_keys=[key_path],
        process_factory=_make_process_handler(bbs_config),
        encoding=None,
        x11_forwarding=False,
        agent_forwarding=False,
    )
    logger.info('SSH server started on %s:%d', host, port)
    return server
