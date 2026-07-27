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
import hmac
import logging
import os

from pyftpdlib.authorizers import DummyAuthorizer, AuthenticationFailed
from pyftpdlib.filesystems import AbstractedFS
from pyftpdlib.handlers import FTPHandler

logger = logging.getLogger(__name__)


def _check_ip_and_rate_limit(app, ip):
    """Return True if this IP must be blocked from even attempting a
    password check (already banned, or just tripped the rate limit and
    got auto-banned here). Mirrors core/user_manager.py's
    UserManager._check_ip_and_rate_limit() -- same AutoBanConfig/IpBan/
    IpWhitelist models and policy, kept as its own local copy (own
    'ftp_login:<ip>' rate-limit bucket, separate from 'login' (web) and
    'terminal_login:<ip>' (telnet/SSH/rlogin)) rather than importing
    across package boundaries. Fails open on any infrastructure error,
    same posture as every other caller of these models.
    """
    if not ip:
        return False
    import ipaddress as _ipa
    from datetime import datetime, timedelta
    from ..models import db, IpBan, IpWhitelist, AutoBanConfig
    from ..features.rate_limit import _check as _rl_check

    def _cidr_match(ip_str, cidr_list):
        try:
            addr = _ipa.ip_address(ip_str)
        except ValueError:
            return False
        for entry in cidr_list:
            try:
                if addr in _ipa.ip_network(entry, strict=False):
                    return True
            except ValueError:
                if entry == ip_str:
                    return True
        return False

    try:
        with app.app_context():
            whitelisted_cidrs = [r.cidr for r in
                                 IpWhitelist.query.with_entities(IpWhitelist.cidr).all()]
            if _cidr_match(ip, whitelisted_cidrs):
                return False

            now = datetime.utcnow()
            banned_cidrs = [r.cidr for r in IpBan.query.all()
                            if not (r.expires_at and r.expires_at < now)]
            if _cidr_match(ip, banned_cidrs):
                return True

            cfg = AutoBanConfig.get()
            if not cfg.enabled:
                return False
            if _rl_check(f'ftp_login:{ip}', cfg.attempt_limit, cfg.window_seconds):
                return False

            if IpBan.query.filter_by(cidr=ip).first():
                return True
            expires_at = (now + timedelta(hours=cfg.ban_duration_hours)
                         if cfg.ban_duration_hours else None)
            db.session.add(IpBan(
                cidr=ip, banned_by_id=None, expires_at=expires_at,
                reason=f'Auto-ban: FTP login rate limit exceeded '
                       f'({cfg.attempt_limit} attempts / '
                       f'{cfg.window_seconds // 60 or 1} min)'))
            db.session.commit()
            return True
    except Exception:
        logger.exception('FTP: IP ban/rate-limit check failed for %s', ip)
        return False


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
        # Real crash found live: pyftpdlib's ftp_USER stores self.username
        # verbatim from the wire (`self.username = line`, no case
        # normalization at all) -- but this method used to register the
        # DummyAuthorizer's user_table entry under a DIFFERENT case
        # (user.username, the DB's canonical stored case; or
        # node.packet_id.upper() for QWK nodes) than whatever case the
        # client actually typed. The DB lookups above are case-insensitive
        # (ilike), so a client typing "gatekeeper" against a DB/packet_id
        # of "GateKeeper"/"GATEKEEPER" authenticated fine here, but the
        # later get_home_dir(self.username) call -- keyed by the RAW
        # wire username, a plain dict lookup, case-sensitive -- raised an
        # unhandled KeyError and crashed the whole FTP session. Always
        # register under the exact `username` parameter (what the client
        # actually sent), never a normalized/canonical form, so the two
        # lookups always agree regardless of what case anyone typed.
        if username == 'anonymous':
            return super().validate_authentication(username, password, handler)

        # Brute-force protection -- FTP had zero rate-limiting/lockout,
        # unlike web (AutoBanConfig/IpBan) and terminal (same models via
        # UserManager._check_ip_and_rate_limit). Checked before any DB
        # lookup so a banned/rate-limited IP can't even probe usernames.
        remote_ip = getattr(handler, 'remote_ip', None)
        if _check_ip_and_rate_limit(self.app, remote_ip):
            raise AuthenticationFailed('Too many attempts. Try again later.')

        from ..models import User, QWKNode
        with self.app.app_context():
            # 1. Try BBS user.
            user = User.query.filter(
                User.username.ilike(username),
                User.is_active.is_(True),
            ).first()
            if user is not None and user.check_password(password):
                # Same account-standing gates as web/auth.py's login() and
                # core/user_manager.py's authenticate() -- FTP previously
                # only checked is_active, so a locked-out or not-yet-
                # verified (NUV) account could still fully authenticate
                # and read/write every non-sysop file area over FTP.
                if getattr(user, 'is_locked', False):
                    raise AuthenticationFailed('This account is locked.')
                if not getattr(user, 'is_verified', True) and not user.is_admin:
                    raise AuthenticationFailed('Account awaiting verification/approval.')
                home = self.admin_root if user.is_admin else self.user_root
                perm = 'elradfmwM' if user.is_admin else 'elradfmw'
                if self.has_user(username):
                    self.remove_user(username)
                self.add_user(username, password=password,
                              homedir=home, perm=perm)
                return

            # 2. Try QWK node (packet_id as username, plaintext password).
            # hmac.compare_digest instead of `==` -- constant-time, avoids
            # a (low-value but real) timing side channel on the stored
            # plaintext secret. Storage itself stays plaintext by design,
            # same as BinkPNode.password: both are shared secrets the
            # server must read back verbatim (QWK/BinkP session auth,
            # AreaFix passthrough), not hashable login credentials.
            node = (QWKNode.query
                    .filter_by(is_active=True)
                    .filter(QWKNode.packet_id.ilike(username))
                    .first())
            if node is not None and hmac.compare_digest(node.password, password):
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
                # (node.packet_id.upper() below is the QWK packet-naming
                # convention -- unrelated to the FTP user_table key, which
                # must stay the raw `username` per the note above.)
                node_home = os.path.join(self.qwk_root, node.packet_id.upper())
                os.makedirs(node_home, exist_ok=True)
                handler._qwk_node_id = node.id
                handler._qwk_packet_id = node.packet_id.upper()
                if self.has_user(username):
                    self.remove_user(username)
                self.add_user(username, password=password,
                              homedir=node_home, perm='elradfmw')
                return

            raise AuthenticationFailed('Invalid credentials.')

    def get_home_dir(self, username):
        # DummyAuthorizer raises KeyError on unknown users — for real users
        # that haven't yet been cached this is fine because validate_*
        # registers them before this is called (under the exact same
        # username key -- see validate_authentication's comment above).
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
                from ..echomail.qwk_hub_ftp import generate_node_packet, resolve_hub_id
                node = QWKNode.query.get(self._qwk_node_id)
                if node:
                    cfg = self.app.config
                    data_dir = cfg.get('DATA_DIR', 'data')
                    hub_id = resolve_hub_id(node)
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

                # Optional ClamAV scan -- the web upload routes
                # (files.py, file_areas.py) all scan on the way in;
                # this hook was the one path that let a file go
                # straight to disk (and become downloadable) with no
                # scan at all. Same fail-open posture as those routes:
                # a missing/broken scanner never blocks a legitimate
                # upload, only a positive match does.
                try:
                    from ..features.virus_scan import scan_path
                    result = scan_path(file)
                    if result.infected:
                        try:
                            os.remove(file)
                        except OSError:
                            pass
                        logger.warning(
                            'FTP: rejected upload %s by %s (virus detected: %s)',
                            os.path.basename(file), user.username, result.signature)
                        return
                except Exception:
                    logger.exception(
                        'FTP: virus scan crashed for %s; allowing file', file)

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

    def _area_write_allowed(self, path):
        """Guard for DELE/RNFR/RNTO/MKD/RMD -- pyftpdlib grants regular
        users a flat 'elradfmw' over the whole session home dir (see
        AnetbbsAuthorizer.validate_authentication), so without this,
        upload_permission='none'/'sysop' areas (meant to be sysop-
        curated/read-only for regular users) could still be deleted
        from, renamed within, or have directories created/removed by
        any user who could merely browse them -- on_file_received only
        ever gated STOR. Admin sessions and QWK-node sessions (which
        manage their own per-node hub dir, not a FileArea storage_path)
        are exempt, same as elsewhere in this handler.
        """
        if self.app is None or self._qwk_node_id is not None or self.username == 'anonymous':
            return True
        try:
            with self.app.app_context():
                from ..models import User, FileArea
                user = User.query.filter(
                    User.username.ilike(self.username)).first()
                if user is None or user.is_admin:
                    return True
                area_tag = self._parent_area_tag(path)
                if not area_tag:
                    return True
                area = FileArea.query.filter_by(tag=area_tag).first()
                if area is None:
                    return True
                if area.upload_permission == 'none' or (
                        area.upload_permission == 'sysop' and not user.is_admin):
                    logger.warning(
                        'FTP: blocked write op on %s by %s (area %s '
                        'permission=%s)', path, self.username, area.tag,
                        area.upload_permission)
                    return False
        except Exception:
            logger.exception('FTP: area write-permission check failed for %s',
                             self.username)
            return True  # fail-open, same posture as ftp_RETR's quota check
        return True

    def ftp_DELE(self, path):
        if not self._area_write_allowed(path):
            self.respond('550 Permission denied for this area.')
            return
        return super().ftp_DELE(path)

    def ftp_RNFR(self, path):
        if not self._area_write_allowed(path):
            self.respond('550 Permission denied for this area.')
            return
        return super().ftp_RNFR(path)

    def ftp_RNTO(self, path):
        if not self._area_write_allowed(path):
            self.respond('550 Permission denied for this area.')
            return
        return super().ftp_RNTO(path)

    def ftp_MKD(self, path):
        if not self._area_write_allowed(path):
            self.respond('550 Permission denied for this area.')
            return
        return super().ftp_MKD(path)

    def ftp_RMD(self, path):
        if not self._area_write_allowed(path):
            self.respond('550 Permission denied for this area.')
            return
        return super().ftp_RMD(path)

    def ftp_RETR(self, file):
        """Daily download quota (FR: Firehawke, 2026-07-24) -- checked
        here, before the transfer starts, unlike on_file_received's
        post-hoc upload check (a RETR can be rejected outright with a
        clean FTP error response; a STOR has already landed on disk by
        the time pyftpdlib calls its own post-transfer hook). Skipped
        for anonymous logins (no local User row to attribute usage to,
        same scope decision as web/files.py's anonymous-accessible
        download route) and QWK node sessions (bulk offline-mail packet
        retrieval, not the file-area leeching this feature targets).
        `file` is already an absolute filesystem path here, same as
        on_file_received()'s own `file` argument above."""
        if (self.app is not None and self.username != 'anonymous'
                and self._qwk_node_id is None):
            try:
                with self.app.app_context():
                    from ..models import User
                    from ..features.file_quota import check_quota
                    user = User.query.filter(
                        User.username.ilike(self.username)).first()
                    if user is not None:
                        try:
                            size = os.path.getsize(file)
                        except OSError:
                            size = 0
                        ok, msg = check_quota(user, size)
                        if not ok:
                            self.respond(f'550 {msg}')
                            return
            except Exception:
                logger.exception('FTP: quota check failed for %s', self.username)
        return super().ftp_RETR(file)

    def on_file_sent(self, file):
        """Companion to ftp_RETR's pre-check -- records usage after a
        successful RETR. See ftp_RETR's docstring for the anon/QWK skip."""
        if (self.app is None or self.username == 'anonymous'
                or self._qwk_node_id is not None):
            return
        try:
            with self.app.app_context():
                from ..models import User
                from ..features.file_quota import consume_quota
                user = User.query.filter(
                    User.username.ilike(self.username)).first()
                if user is not None:
                    try:
                        size = os.path.getsize(file)
                    except OSError:
                        size = 0
                    consume_quota(user, size)
        except Exception:
            logger.exception('FTP: on_file_sent quota hook failed')

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
