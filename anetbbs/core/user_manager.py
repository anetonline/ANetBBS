# anetbbs/core/user_manager.py
"""
Telnet/SSH/rlogin user manager — talks to the SAME database the Flask web app uses.

Previously this was a JSON-file UserManager (data/users.json) that was completely
disconnected from the web's `User` table. Result: a user registered on the web
couldn't log in via telnet, and vice-versa. This module now uses raw SQLAlchemy
(no Flask app context required) against the same User table, with the same
Werkzeug password hash format the web app uses, so accounts are unified.

The public dict shape returned by `authenticate()` is preserved for compatibility
with `core/session.py`.
"""
import os
from datetime import datetime, timedelta
from typing import Optional, Dict
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from werkzeug.security import check_password_hash, generate_password_hash


def _resolve_db_uri() -> str:
    """Resolve the database URI the same way the Flask app does."""
    # Prefer the env var (which is what the systemd EnvironmentFile loads)
    uri = os.environ.get('DATABASE_URL')
    if uri:
        return uri
    # Fall back to whatever the Flask config picks for the active env
    try:
        from anetbbs.config import get_config
        cfg = get_config(os.environ.get('FLASK_ENV', 'production'))
        return cfg.SQLALCHEMY_DATABASE_URI
    except Exception:
        return 'sqlite:///data/anetbbs.db'


# Module-level engine — one connection pool shared across all sessions
_DB_URI = _resolve_db_uri()
_engine = create_engine(_DB_URI, future=True)
_Session = sessionmaker(bind=_engine, future=True, expire_on_commit=False)

# Real gap found in a security/performance audit: authenticate() below
# used to `return None` for a nonexistent username BEFORE ever calling
# check_password_hash() -- the same short-circuit-timing gap fixed in
# web/auth.py's login() (see that module's own longer comment on
# _DUMMY_PASSWORD_HASH for the full reasoning). A telnet/SSH/rlogin
# login for a real username with a wrong password took as long as the
# real hash verification, while a nonexistent username returned almost
# instantly -- a distinguishable timing side channel independent of
# whatever error message either path shows.
_DUMMY_PASSWORD_HASH = generate_password_hash('not-a-real-password-timing-normalization')


class UserManager:
    """SQLAlchemy-backed user manager. Same User table as the web app."""

    def __init__(self, data_dir: str = "data"):
        # data_dir kept for backward-compat with the old signature; unused now
        self.data_dir = data_dir

    @staticmethod
    def _check_ip_and_rate_limit(ip: str) -> bool:
        """Return True if this IP must be blocked from even attempting a
        password check (already banned, or just tripped the rate limit
        and got auto-banned here). Mirrors web/auth.py's
        _ip_is_banned()/_login_rate_exceeded()/_auto_ban_ip() -- same
        AutoBanConfig/IpBan/IpWhitelist models, same policy, kept as its
        own local copy rather than importing from anetbbs.web.auth to
        avoid a core-package-depends-on-web-package layering inversion
        (core/ has no other dependency on web/ anywhere in this
        codebase). Uses the same sliding-window bucket primitive
        (features/rate_limit.py's _check()) the web login route uses,
        just called directly instead of through its Flask-route
        decorator wrapper.
        """
        import ipaddress as _ipa
        from anetbbs.models import db, IpBan, IpWhitelist, AutoBanConfig
        from anetbbs.features.bbs_ui import _app
        from anetbbs.features.rate_limit import _check as _rl_check

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
            with _app().app_context():
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
                if _rl_check(f'terminal_login:{ip}', cfg.attempt_limit,
                            cfg.window_seconds):
                    return False

                # Rate limit tripped -- auto-ban, same policy as
                # web/auth.py's _auto_ban_ip().
                if IpBan.query.filter_by(cidr=ip).first():
                    return True
                expires_at = (now + timedelta(hours=cfg.ban_duration_hours)
                             if cfg.ban_duration_hours else None)
                db.session.add(IpBan(
                    cidr=ip, banned_by_id=None, expires_at=expires_at,
                    reason=f'Auto-ban: terminal login rate limit exceeded '
                           f'({cfg.attempt_limit} attempts / '
                           f'{cfg.window_seconds // 60 or 1} min)'))
                db.session.commit()
                return True
        except Exception:
            # Fail open on infrastructure trouble (DB hiccup, etc.) --
            # matches web/auth.py's own _ip_is_banned()/_auto_ban_ip()
            # stance of never locking out real logins over a transient
            # error in the ban-checking machinery itself.
            return False

    @staticmethod
    def _user_to_dict(user) -> Dict:
        """Convert a User row into the dict shape session.py expects."""
        return {
            "id": user.id,
            "username": user.username,
            "display_name": getattr(user, 'display_name', None) or '',
            "location": getattr(user, 'location', None) or '',
            "email": user.email,
            "is_admin": bool(user.is_admin),
            "is_active": bool(user.is_active),
            "is_locked": bool(getattr(user, 'is_locked', False)),
            "access_level": getattr(user, 'access_level', None) or 10,
            "codepage": getattr(user, 'codepage', None) or 'cp437',
            "cursor_style": getattr(user, 'cursor_style', None) or 'default',
            "real_name": getattr(user, 'real_name', None) or '',
            "echomail_name_pref": getattr(user, 'echomail_name_pref', None) or 'handle',
            "language": getattr(user, 'language', None) or 'en',
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "login_count": user.login_count or 0,
        }

    def username_exists(self, username: str) -> bool:
        """Case-insensitive check for an existing username."""
        from anetbbs.models import User
        with _Session() as s:
            row = s.execute(
                select(User).where(func.lower(User.username) == username.lower())
            ).scalar_one_or_none()
            return row is not None

    def email_exists(self, email: str) -> bool:
        """Case-insensitive check for an existing email."""
        from anetbbs.models import User
        with _Session() as s:
            row = s.execute(
                select(User).where(func.lower(User.email) == email.lower())
            ).scalar_one_or_none()
            return row is not None

    def create_user(self, username: str, password: str, email: str) -> str:
        """Create a new user. Returns 'ok', 'ok_pending', 'username_taken',
        or 'email_taken'. 'ok_pending' means the account was created but
        is_verified=False (NUV_ENABLED) -- the caller must NOT treat this
        the same as 'ok' by logging the new account straight in; it needs
        sysop approval first, exactly like the web registration path.

        Real access-control gap found in a full audit: this used to
        always create is_verified=True (the model default) regardless of
        NUV_ENABLED, so a NUV-gated BBS's sysop-approval queue only ever
        applied to accounts created via the web -- anyone could dial in
        over telnet/SSH/rlogin, self-register, and skip the queue
        entirely. Mirrors web/auth.py's register() route.
        """
        from anetbbs.models import User

        nuv_on = False
        try:
            from anetbbs.config import get_config
            cfg = get_config(os.environ.get('FLASK_ENV', 'production'))
            nuv_on = bool(getattr(cfg, 'NUV_ENABLED', False))
        except Exception:
            pass

        with _Session() as s:
            if s.execute(
                select(User).where(func.lower(User.username) == username.lower())
            ).scalar_one_or_none() is not None:
                return 'username_taken'
            if s.execute(
                select(User).where(func.lower(User.email) == email.lower())
            ).scalar_one_or_none() is not None:
                return 'email_taken'
            user = User(
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                is_active=True,
                is_admin=False,
                is_verified=(not nuv_on),
                created_at=datetime.utcnow(),
                login_count=0,
            )
            s.add(user)
            try:
                s.commit()
            except IntegrityError as exc:
                s.rollback()
                # Real bug found in a full auth-security audit: this
                # unconditionally reported 'email_taken' regardless of
                # WHICH unique constraint actually fired -- on a genuine
                # race (two concurrent registrations of the same
                # username, both passing the pre-checks above before
                # either commits), the username collision surfaced here
                # instead, and the second registrant was told an account
                # with their EMAIL already existed, which was false.
                # Both username and email have their own unique
                # constraint (models.py), and the underlying DB driver's
                # error text names which one fired.
                if 'username' in str(exc.orig).lower():
                    return 'username_taken'
                return 'email_taken'
            if nuv_on:
                try:
                    from anetbbs.features.notify import notify_admins
                    from anetbbs.features.bbs_ui import _app
                    with _app().app_context():
                        notify_admins(
                            'nuv_pending',
                            title=f'New user pending approval: {user.username}',
                            body=f'{user.username} ({user.email}) registered '
                                 f'via terminal and is waiting for NUV sysop '
                                 f'approval.',
                            target_url='/admin/pending-users')
                except Exception:
                    pass
                return 'ok_pending'
            return 'ok'

    def get_user_dict_by_username(self, username: str) -> Optional[Dict]:
        """Internal lookup with NO password/lock/verification gating --
        for post-registration bookkeeping (security questions, newuser
        questionnaire) on an account this same request just created.
        Never use this to grant session access; use authenticate()."""
        from anetbbs.models import User
        with _Session() as s:
            user = s.execute(
                select(User).where(func.lower(User.username) == username.lower())
            ).scalar_one_or_none()
            return self._user_to_dict(user) if user else None

    def authenticate(self, username: str, password: str,
                     ip: Optional[str] = None) -> Optional[Dict]:
        """Return user dict on success, None on failure.

        Real gap found in a full auth-security audit: web/auth.py's
        /login route has always been protected by an IP ban check and a
        configurable rate-limit-triggered auto-ban (AutoBanConfig) --
        this telnet/SSH/rlogin/PETSCII path (every terminal transport
        calls into this one function) had ZERO equivalent. A client
        could retry username/password combinations forever with no
        delay, counter, or lockout at all -- worse on SSH specifically,
        since asyncssh's own validate_password() always returns True
        (by design, to capture the password for the prefill flow) so
        even the SSH layer itself never rejects a login attempt.
        `ip` is optional (best-effort) so a caller that genuinely can't
        resolve a peer address doesn't lose the ability to authenticate
        at all -- but every real caller in core/session.py now passes
        one.
        """
        from anetbbs.models import User

        if ip:
            blocked = self._check_ip_and_rate_limit(ip)
            if blocked:
                return None

        with _Session() as s:
            user = s.execute(
                select(User).where(func.lower(User.username) == username.lower())
            ).scalar_one_or_none()
            if user is None:
                # Always run a real hash verification -- see
                # _DUMMY_PASSWORD_HASH's own comment above -- so this
                # path takes statistically the same time as a real
                # username with a wrong password.
                check_password_hash(_DUMMY_PASSWORD_HASH, password)
                return None
            # Password verified BEFORE the is_active check (unlike a
            # naive "check state, then hash" ordering) -- same timing-
            # normalization reasoning as the user-is-None case above:
            # an existing-but-deactivated account would otherwise
            # short-circuit past the hash check and return faster than
            # an active account with a wrong password, leaking which
            # existing usernames are currently deactivated purely by
            # response timing.
            if not check_password_hash(user.password_hash, password):
                return None
            if not user.is_active:
                return None
            # Real access-control gap found in a full audit: neither of
            # these was ever checked here, unlike web/auth.py's login()
            # -- a sysop-locked account, or one still awaiting NUV/email
            # approval, logged in over telnet/SSH/rlogin exactly as if
            # nothing were wrong.
            if getattr(user, 'is_locked', False):
                return None
            if not getattr(user, 'is_verified', True) and not user.is_admin:
                return None
            # Update login bookkeeping (same fields the web app maintains)
            user.last_login = datetime.utcnow()
            user.login_count = (user.login_count or 0) + 1
            s.commit()
            return self._user_to_dict(user)

    def save_security_answers(self, user_id: int, qa_pairs: list) -> None:
        """Persist password-recovery security Q&A. qa_pairs: list of (question, answer) tuples."""
        from anetbbs.models import UserSecurityAnswer
        with _Session() as s:
            for question, answer in qa_pairs:
                s.add(UserSecurityAnswer(
                    user_id=user_id,
                    question=question,
                    answer_hash=UserSecurityAnswer.hash_answer(answer),
                ))
            s.commit()

    def get_user(self, username: str) -> Optional[Dict]:
        """Look up a user by username without authentication."""
        from anetbbs.models import User
        with _Session() as s:
            user = s.execute(
                select(User).where(func.lower(User.username) == username.lower())
            ).scalar_one_or_none()
            return self._user_to_dict(user) if user else None
