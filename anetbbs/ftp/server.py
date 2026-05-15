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
    (e.g. `lrwxrwxrwx ... FILES.GAMES -> /home/stingray/.../data/files/games`).
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
            os.unlink(full)
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
    """Authorizer backed by the User table. The DummyAuthorizer's in-memory
    user dict is repurposed as a cache for the anonymous user plus admin
    overrides; real authentication delegates to `User.check_password`.
    """

    def __init__(self, app, anon_root, user_root, admin_root,
                 anon_enabled=True):
        super().__init__()
        self.app = app
        self.anon_root = anon_root
        self.user_root = user_root
        self.admin_root = admin_root
        if anon_enabled:
            # pyftpdlib quirk: the anonymous user is registered as
            # username 'anonymous' with an empty password.
            self.add_anonymous(homedir=anon_root, perm='elr')

    def validate_authentication(self, username, password, handler):
        if username == 'anonymous':
            return super().validate_authentication(username, password, handler)
        from ..models import User
        with self.app.app_context():
            user = User.query.filter(
                User.username.ilike(username),
                User.is_active.is_(True),
            ).first()
            if user is None or not user.check_password(password):
                raise AuthenticationFailed('Invalid credentials.')
            # Pick the right home dir based on role. Admins see sysop-only
            # areas; everyone else sees the non-sysop tree.
            home = self.admin_root if user.is_admin else self.user_root
            # Permission `elradfmwM` = read + write + delete + rename + mkdir
            # + change-dir + append + chmod. Per-area upload-permission
            # enforcement still happens via on_file_received post-hoc.
            perm = 'elradfmwM' if user.is_admin else 'elradfmw'
            if self.has_user(user.username):
                self.remove_user(user.username)
            self.add_user(user.username, password=password,
                          homedir=home, perm=perm)

    def get_home_dir(self, username):
        # DummyAuthorizer raises KeyError on unknown users — for real users
        # that haven't yet been cached this is fine because validate_*
        # registers them before this is called.
        return super().get_home_dir(username)


class AnetbbsFTPHandler(FTPHandler):
    """Adds upload-tracking + presence integration on top of the stock
    FTPHandler. Uploaded files get a `FileUpload` row whose `file_area_id`
    is resolved from the parent directory's tag (which matches the
    symlink name in the FTP root tree).
    """

    # Wired by `run()` at startup.
    app = None

    def on_connect(self):
        logger.info('FTP: connect from %s', self.remote_ip)

    def on_login(self, username):
        logger.info('FTP: login %s from %s', username, self.remote_ip)

    def on_login_failed(self, username, password):
        logger.info('FTP: login failed for %r from %s',
                    username, self.remote_ip)

    def on_file_received(self, file):
        """Called after a successful STOR. Walk the parent directory back
        through the FTP virtual root to find the FileArea tag, then create
        a FileUpload row."""
        if self.app is None:
            return
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
    if tls_certfile and tls_keyfile and \
            os.path.exists(tls_certfile) and os.path.exists(tls_keyfile):
        # FTPS — AUTH TLS on the same port. Implicit FTPS would need 990.
        from pyftpdlib.handlers import TLS_FTPHandler
        class _TLSAnetbbsHandler(TLS_FTPHandler, AnetbbsFTPHandler):
            pass
        handler_cls = _TLSAnetbbsHandler
        handler_cls.certfile = tls_certfile
        handler_cls.keyfile = tls_keyfile
        handler_cls.tls_control_required = False  # AUTH TLS upgrade is optional
        handler_cls.tls_data_required = False
        logger.info('FTP: TLS enabled (%s)', tls_certfile)

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

    authorizer = AnetbbsAuthorizer(app, anon_root, user_root, admin_root,
                                   anon_enabled=anon_enabled)
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
