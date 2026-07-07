"""ANetBBS FTP server.

Maps each `FileArea` row onto a subdirectory under a single symlink-tree
root. Anonymous users see only public areas (active + not sysop-only);
authenticated users see all permitted areas and can upload to those whose
`upload_permission` allows it. Uploaded files get a `FileUpload` row
recorded so the web UI's file-area browser surfaces them too — which is
what gets you the FTN nodelist `IFC` flag (Internet File transfer
Capability) once you start advertising the service.

Auth uses the existing `User.check_password` (bcrypt) so the FTP login is
the same as the web/telnet/SSH login. Optional FTPS (AUTH TLS) reuses
whatever cert nginx already has, keyed off `FTP_TLS_CERTFILE` /
`FTP_TLS_KEYFILE` in the app config.

This module is `serve_forever`-style, intended to run inside the main
`anetbbs.service` process — the entry point `run()` is started in a
daemon thread by `anetbbs.main` when `FTP_ENABLED=true`.
"""
import logging
import os
import shutil
from datetime import datetime

from pyftpdlib.authorizers import DummyAuthorizer, AuthenticationFailed
from pyftpdlib.filesystems import AbstractedFS
from pyftpdlib.handlers import FTPHandler

logger = logging.getLogger(__name__)


def _public_area_filter(area):
    """Anonymous users see only active, non-sysop-only areas."""
    return area.is_active and not area.is_sysop_only


def _user_area_filter(area, include_sysop_only=False):
    """Authenticated users see anything active that isn't sysop-only.
    Pass `include_sysop_only=True` for the admin variant of the tree."""
    if not area.is_active:
        return False
    if area.is_sysop_only and not include_sysop_only:
        return False
    return True


class SymlinkAwareFS(AbstractedFS):
    """Default pyftpdlib AbstractedFS resolves symlinks via os.path.realpath()
    and then refuses any path that escapes the user's home dir. Our setup
    is intentionally a symlink tree pointing at FileArea.storage_path
    directories all over the filesystem — so every CWD/RETR/STOR through
    a symlink would error with "outside the user's root directory".

    We override realpath() to use abspath() instead (no symlink
    resolution), and validpath() to compare against the abspath root.
    Together those preserve the safety guarantee (no `..` traversal
    above the FTP root) while letting our deliberately-placed symlinks
    work as intended.

    We also override lstat() to use os.stat() — the default behavior
    leaks the absolute server-side path to clients via the LIST output
    (e.g. `lrwxrwxrwx ... FILES.GAMES -> /opt/anetbbs/.../data/files/games`).
    Resolving through the symlink makes each entry look like an
    ordinary directory in `ls -l`.
    """
    def realpath(self, path):
        return os.path.abspath(path)

    def validpath(self, path):
        root = os.path.abspath(self.root)
        p = os.path.abspath(path)
        return p == root or p.startswith(root + os.sep)

    def lstat(self, path):
        # Resolve symlinks so clients see "drwxr-xr-x ... FILES.GAMES"
        # instead of "lrwxrwxrwx ... FILES.GAMES -> /abs/server/path".
        # Falls back to the real lstat if the target is broken so the
        # listing still renders something (just with link mode bits).
        try:
            return os.stat(path)
        except (OSError, ValueError):
            return os.lstat(path)


def _build_symlink_tree(root_dir, areas, mode='user'):
    """Build (or rebuild) the symlink tree under `root_dir`.

    mode='anon'  → only public areas (active + not sysop-only)
    mode='user'  → all non-sysop-only active areas
    mode='admin' → every active area (sysop-only included)

    Each `FileArea` that passes the filter becomes a symlink
    `root_dir/<TAG>` pointing at `area.storage_path`. Re-runs are safe —
    we wipe existing symlinks but never the targets.
    """
    os.makedirs(root_dir, exist_ok=True)
    for name in os.listdir(root_dir):
        full = os.path.join(root_dir, name)
        if os.path.islink(full):
            try:
                os.unlink(full)
            except PermissionError as exc:
                # Leftover from a previous FTP run that executed as a
                # different user — usually root, before the BBS was
                # converted to run as the sysop's own service account.
                # We can't remove the stale symlink but we can still operate
                # around it (worst case the link points at a dead
                # target). Log + continue rather than crash the whole
                # FTP thread, which is what kept FTP silently dead
                # for a week pre-v1.0a2.32. Fix on the sysop side:
                # `sudo chown -R <service_user>:<group> data/ftp_root`.
                logger.warning(
                    'FTP: cannot remove stale symlink %s (%s) — likely '
                    'a leftover owned by a different user. Fix with: '
                    'sudo chown -R <service-user>:<group> %s ; then '
                    'restart anetbbs.', full, exc, root_dir)
            except OSError as exc:
                logger.warning('FTP: unlink %s failed: %s', full, exc)
    linked = 0
    for area in areas:
        if mode == 'anon' and not _public_area_filter(area):
            continue
        if mode == 'user' and not _user_area_filter(area,
                                                    include_sysop_only=False):
            continue
        if mode == 'admin' and not _user_area_filter(area,
                                                     include_sysop_only=True):
            continue
        if not area.storage_path or not os.path.isdir(area.storage_path):
            continue
        link_name = area.tag.replace('/', '_')
        link_path = os.path.join(root_dir, link_name)
        try:
            os.symlink(area.storage_path, link_path)
            linked += 1
        except OSError as exc:
            logger.warning('FTP: could not symlink %s -> %s: %s',
                           link_path, area.storage_path, exc)
    return linked


class AnetbbsAuthorizer(DummyAuthorizer):
    """Authorizer backed by the User table *and* the QWKNode table.

    BBS users authenticate as before.  QWK nodes (packet_id / password)
    get a per-node home dir under `<data_dir>/qwk-hub/<PACKET_ID>/` where
    they can download their ANET.qwk and upload their <ID>.rep.
    """

    def __init__(self, app, anon_root, user_root, admin_root,
                 anon_enabled=True, qwk_root=''):
        super().__init__()
        self.app = app
        self.anon_root = anon_root
        self.user_root = user_root
        self.admin_root = admin_root
        self.qwk_root = qwk_root  # data/qwk-hub
        if anon_enabled:
            # pyftpdlib quirk: the anonymous user is registered as
            # username 'anonymous' with an empty password.
            self.add_anonymous(homedir=anon_root, perm='elr')

    def validate_authentication(self, username, password, handler):
        if username == 'anonymous':
            return super().validate_authentication(username, password, handler)
        from ..models import User, QWKNode
        with self.app.app_context():
            # 1. Try BBS user.
            user = User.query.filter(
                User.username.ilike(username),
                User.is_active.is_(True),
            ).first()
            if user is not None and user.check_password(password):
                home = self.admin_root if user.is_admin else self.user_root
                perm = 'elradfmwM' if user.is_admin else 'elradfmw'
                if self.has_user(user.username):
                    self.remove_user(user.username)
                self.add_user(user.username, password=password,
                              homedir=home, perm=perm)
                return

            # 2. Try QWK node (packet_id as username, plaintext password).
            node = (QWKNode.query
                    .filter_by(is_active=True)
                    .filter(QWKNode.packet_id.ilike(username))
                    .first())
            if node is not None and node.password == password:
                # self.qwk_root is ALREADY <DATA_DIR>/qwk-hub (set at
                # construction, see run() below) -- do NOT pass it to
                # ensure_node_dir(), which appends its own 'qwk-hub'
                # segment and expects the bare DATA_DIR instead. Doing
                # so here doubled the path (.../qwk-hub/qwk-hub/<ID>),
                # landing the FTP session's home dir one level away
                # from where on_login()'s generate_node_packet() (which
                # correctly receives the bare DATA_DIR) actually writes
                # the packet -- the client always saw an empty
                # directory no matter how successfully login went.
                node_home = os.path.join(self.qwk_root, node.packet_id.upper())
                os.makedirs(node_home, exist_ok=True)
                handler._qwk_node_id = node.id
                handler._qwk_packet_id = node.packet_id.upper()
                reg_name = node.packet_id.upper()
                if self.has_user(reg_name):
                    self.remove_user(reg_name)
                self.add_user(reg_name, password=password,
                              homedir=node_home, perm='elradfmw')
                return

            raise AuthenticationFailed('Invalid credentials.')

    def get_home_dir(self, username):
        # DummyAuthorizer raises KeyError on unknown users — for real users
        # that haven't yet been cached this is fine because validate_*
        # registers them before this is called.
        return super().get_home_dir(username)


class AnetbbsFTPHandler(FTPHandler):
    """Adds upload-tracking + QWK-hub integration on top of the stock
    FTPHandler. Uploaded files get a `FileUpload` row whose `file_area_id`
    is resolved from the parent directory's tag (which matches the
    symlink name in the FTP root tree).  QWK node sessions additionally
    get their packet generated on login and their .rep processed on upload.
    """

    # Wired by `run()` at startup.
    app = None

    # Set by the authorizer during validate_authentication for QWK sessions.
    _qwk_node_id = None
    _qwk_packet_id = None

    def on_connect(self):
        logger.info('FTP: connect from %s', self.remote_ip)

    def on_login(self, username):
        logger.info('FTP: login %s from %s', username, self.remote_ip)
        if self._qwk_node_id is None or self.app is None:
            return
        # QWK node logged in — generate their packet now so it's ready for RETR.
        try:
            with self.app.app_context():
                from ..models import QWKNode
                from ..echomail.qwk_hub_ftp import generate_node_packet
                node = QWKNode.query.get(self._qwk_node_id)
                if node:
                    cfg = self.app.config
                    data_dir = cfg.get('DATA_DIR', 'data')
                    hub_id = (os.environ.get('QWK_HUB_ID', '')
                              or cfg.get('QWK_HUB_ID', 'ANET'))
                    generate_node_packet(node, data_dir, hub_id)
        except Exception:
            logger.exception('FTP: QWK packet generation failed for %s',
                             self._qwk_packet_id)

    def on_login_failed(self, username, password):
        logger.info('FTP: login failed for %r from %s',
                    username, self.remote_ip)

    def on_file_received(self, file):
        """Called after a successful STOR."""
        if self.app is None:
            return

        # QWK node uploaded a .rep file — process it.
        if self._qwk_node_id is not None:
            if file.upper().endswith('.REP'):
                try:
                    from ..echomail.qwk_hub_ftp import process_rep_upload
                    process_rep_upload(self._qwk_node_id, file, self.app)
                except Exception:
                    logger.exception('FTP: REP processing failed for %s', file)
            return

        # Regular BBS user upload — track in FileUpload.
        try:
            with self.app.app_context():
                from ..models import db, User, FileArea, FileUpload

                area_tag = self._parent_area_tag(file)
                if not area_tag:
                    return

                area = FileArea.query.filter_by(tag=area_tag).first()
                if area is None:
                    return

                # Enforce upload permission post-hoc — pyftpdlib already
                # wrote the file, but if policy says NO we delete it and
                # log the violation. Anonymous gets 'elr' permission via
                # the authorizer so anon can never reach this hook.
                allowed = area.upload_permission
                user = User.query.filter(
                    User.username.ilike(self.username)).first()
                if user is None:
                    return
                if allowed == 'none' or (allowed == 'sysop' and not user.is_admin):
                    try:
                        os.remove(file)
                    except OSError:
                        pass
                    logger.warning(
                        'FTP: rejected upload %s by %s (area %s '
                        'permission=%s)',
                        os.path.basename(file), user.username, area.tag, allowed)
                    return

                size = 0
                try:
                    size = os.path.getsize(file)
                except OSError:
                    pass
                up = FileUpload(
                    uploader_id=user.id,
                    filename=os.path.basename(file),
                    original_filename=os.path.basename(file),
                    file_path=file,
                    file_size=size,
                    description='Uploaded via FTP',
                    is_public=not area.is_sysop_only,
                    file_area_id=area.id,
                )
                db.session.add(up)
                db.session.commit()
                logger.info(
                    'FTP: %s uploaded %s (%d bytes) into area %s',
                    user.username, os.path.basename(file), size, area.tag)
        except Exception:
            logger.exception('FTP: on_file_received hook failed')

    def _parent_area_tag(self, file_path):
        """The parent directory of an uploaded file, RELATIVE to the
        session's home dir, is the area's symlink name (== `FileArea.tag`
        with `/` swapped to `_`). Return the original tag form so the
        lookup matches the DB.
        """
        try:
            rel = os.path.relpath(file_path, self.fs.root)
        except ValueError:
            return None
        parts = rel.split(os.sep, 1)
        if not parts or parts[0] in ('.', '..', ''):
            return None
        # We stored the link with `/` -> `_`; reverse for the lookup. If the
        # original tag never had a slash this is a no-op.
        return parts[0].replace('_', '/')


def build_server(app, host, port, anon_enabled=True,
                 tls_certfile='', tls_keyfile='',
                 pasv_ports_range='40000-40050',
                 root_dir='data/ftp_root',
                 banner='ANetBBS FTP'):
    """Wire up an FTP server bound to (host, port). Returns the server
    instance — caller does `serve_forever()` or `close_all()`.
    """
    from ..models import FileArea
    from pyftpdlib.servers import FTPServer

    handler_cls = AnetbbsFTPHandler

    # FTPS soft-fail: we need both files to *exist AND be readable*. The
    # previous `os.path.exists` check passed for /etc/letsencrypt/live/
    # symlinks even when the linked archive/*.pem files were 0600 root-
    # only — TLS_FTPHandler then crashed during SSL context init and the
    # FTP listener died silently (the whole daemon thread, not just one
    # session). Now we test readability up front, log a clear warning,
    # and fall back to plain FTP instead of taking the listener down.
    def _tls_ready():
        if not (tls_certfile and tls_keyfile):
            return False
        for path, label in ((tls_certfile, 'cert'), (tls_keyfile, 'key')):
            if not os.path.exists(path):
                logger.warning('FTP: TLS %s missing (%s) — falling back '
                               'to plain FTP', label, path)
                return False
            if not os.access(path, os.R_OK):
                logger.warning(
                    'FTP: TLS %s unreadable by service user — falling '
                    'back to plain FTP. Fix: add this user to the '
                    'ssl-cert group OR copy the cert to a path the '
                    'service can read. (%s)', label, path)
                return False
            # Belt-and-braces: open the file. Catches stat-vs-open
            # divergence on weird filesystems (overlayfs, NFS root-squash).
            try:
                with open(path, 'rb') as _:
                    pass
            except OSError as exc:
                logger.warning('FTP: TLS %s open failed (%s) — falling '
                               'back to plain FTP', label, exc)
                return False
        return True

    if _tls_ready():
        # FTPS — AUTH TLS on the same port. Implicit FTPS would need 990.
        # ``TLS_FTPHandler`` is only exposed by pyftpdlib when pyOpenSSL
        # is importable; missing that dep used to crash the whole FTP
        # thread with ``ImportError: cannot import name 'TLS_FTPHandler'``.
        # Catch + downgrade to plain FTP with an actionable warning.
        try:
            from pyftpdlib.handlers import TLS_FTPHandler  # noqa: WPS433
        except ImportError as exc:
            logger.warning(
                'FTP: TLS_FTPHandler unavailable (%s) — pyOpenSSL is '
                'probably not installed in the venv. Run '
                '`venv/bin/pip install pyopenssl` and restart anetbbs '
                'to enable FTPS. Falling back to plain FTP for now.',
                exc)
        else:
            class _TLSAnetbbsHandler(TLS_FTPHandler, AnetbbsFTPHandler):
                pass
            handler_cls = _TLSAnetbbsHandler
            handler_cls.certfile = tls_certfile
            handler_cls.keyfile = tls_keyfile
            handler_cls.tls_control_required = False  # AUTH TLS optional
            handler_cls.tls_data_required = False
            logger.info('FTP: TLS enabled (%s)', tls_certfile)
    elif tls_certfile or tls_keyfile:
        # Either set, but soft-fail check kicked in — make the
        # downgrade explicit so the sysop sees one line per startup.
        logger.warning('FTP: starting without TLS (AUTH TLS clients '
                       'will fall back to plain). Clear FTP_TLS_CERTFILE/'
                       'FTP_TLS_KEYFILE in .env to silence this warning.')

    # Build three symlink trees — anon (read-only public), users (auth,
    # non-sysop-only), admin (sysop-only included). The authorizer maps
    # the right tree to each session at login.
    anon_root = os.path.join(root_dir, 'anon')
    user_root = os.path.join(root_dir, 'users')
    admin_root = os.path.join(root_dir, 'admin')
    with app.app_context():
        areas = FileArea.query.all()
        n_anon = _build_symlink_tree(anon_root, areas, mode='anon')
        n_user = _build_symlink_tree(user_root, areas, mode='user')
        n_admin = _build_symlink_tree(admin_root, areas, mode='admin')

    logger.info('FTP: linked %d anon / %d user / %d admin visible areas',
                n_anon, n_user, n_admin)

    qwk_root = os.path.join(app.config.get('DATA_DIR', 'data'), 'qwk-hub')
    os.makedirs(qwk_root, exist_ok=True)
    authorizer = AnetbbsAuthorizer(app, anon_root, user_root, admin_root,
                                   anon_enabled=anon_enabled,
                                   qwk_root=qwk_root)
    handler_cls.authorizer = authorizer
    handler_cls.abstracted_fs = SymlinkAwareFS
    handler_cls.app = app
    handler_cls.banner = banner

    # Passive-mode port range parsing — `start-end` form.
    try:
        lo, hi = pasv_ports_range.split('-', 1)
        handler_cls.passive_ports = range(int(lo), int(hi) + 1)
    except (ValueError, AttributeError):
        handler_cls.passive_ports = range(40000, 40051)

    server = FTPServer((host, port), handler_cls)
    server.max_cons = 256
    server.max_cons_per_ip = 8
    return server


def build_minimal_app():
    """Create a stripped-down Flask app — JUST enough for SQLAlchemy
    `app_context()` and User/FileArea queries. Skips blueprint
    registration, echomail/RSS pollers, MSP/SYSTAT bind attempts, and
    every other background subsystem.

    CRITICAL for FTP integration: the full `web_app.create_app()` starts
    threads that conflict with the asyncio loop in `anetbbs.main` and
    breaks telnet/SSH login with "cannot notify on un-acquired lock"
    errors. This minimal app sidesteps all of that.
    """
    from flask import Flask
    from ..config import get_config
    from ..models import db

    env = os.environ.get('FLASK_ENV', 'production')
    app = Flask(__name__)
    app.config.from_object(get_config(env))
    db.init_app(app)
    # The User model's bcrypt check + FileUpload write only need
    # `db.session`, which `db.init_app` is enough to bind. Inside the
    # FTP request handlers we wrap calls in `app.app_context():`.
    return app


def run(app):
    """Daemon-thread entry point. Pulls config off `app`, builds the
    server, and blocks on `serve_forever()`. Designed to be invoked from
    `anetbbs.main` when `FTP_ENABLED` is true."""
    cfg = app.config
    host = cfg.get('FTP_HOST', '0.0.0.0')
    port = int(cfg.get('FTP_PORT', 21))
    root = cfg.get('FTP_ROOT_DIR', 'data/ftp_root')
    if not os.path.isabs(root):
        root = os.path.join(cfg.get('BASE_DIR', '.'), root)

    server = build_server(
        app, host, port,
        anon_enabled=cfg.get('FTP_ANON_ENABLED', True),
        tls_certfile=cfg.get('FTP_TLS_CERTFILE', ''),
        tls_keyfile=cfg.get('FTP_TLS_KEYFILE', ''),
        pasv_ports_range=cfg.get('FTP_PASV_PORTS', '40000-40050'),
        root_dir=root,
        banner=cfg.get('FTP_BANNER', 'ANetBBS FTP'),
    )
    logger.info('FTP: serving %s:%d (root=%s, anon=%s)',
                host, port, root, cfg.get('FTP_ANON_ENABLED', True))
    try:
        server.serve_forever()
    finally:
        try: server.close_all()
        except Exception: pass
