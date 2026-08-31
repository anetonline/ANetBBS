# anetbbs/web/auth.py
"""
Authentication blueprint for user login, registration, and logout
"""
import random
import secrets
import threading
import time
from datetime import datetime, timedelta
from sqlalchemy.exc import IntegrityError

from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, current_app, session as flask_session)
from flask_login import login_user, logout_user, login_required, current_user
from wtforms import StringField, PasswordField, SubmitField, SelectField
from wtforms.validators import DataRequired, EqualTo, Length, Regexp, ValidationError
from flask_wtf import FlaskForm
from werkzeug.security import generate_password_hash, check_password_hash

from .validators import PermissiveEmail as Email
from ..models import (db, User, PasswordResetToken, RegistrationAttempt,
                      UserSecurityAnswer, SECURITY_QUESTIONS,
                      EmailVerifyToken, AutoBanConfig)
from ..features.rate_limit import rate_limit


# In-memory country-lookup cache: ip -> (country_code, expiry_timestamp)
_geoip_cache: dict = {}
_geoip_lock = threading.Lock()
# Real gap found in a security/performance audit: entries here were
# written on every cache miss but never evicted -- expiry is only
# checked on READ, so a stale entry for an IP that never comes back
# just sits in the dict forever. On a long-running production process
# with BLOCKED_COUNTRIES set (this feature exists specifically to
# reject scanner/credential-stuffing traffic, so every distinct
# attacking source IP gets its own permanent entry), this is the same
# "quiet unbounded creep on a long-running worker" shape as the
# v1.0.21 incident, and structurally the same bug
# features/rate_limit.py's own _buckets dict already has a fix for
# (probabilistic sweep on write). Same pattern here.
_GEOIP_SWEEP_PROBABILITY = 500  # ~1-in-500 writes triggers a sweep


def _sweep_stale_geoip_entries():
    now = time.time()
    stale_ips = [ip for ip, (_country, expiry) in _geoip_cache.items()
                if expiry < now]
    for ip in stale_ips:
        del _geoip_cache[ip]


def _client_ip():
    """Best-effort client IP.

    Real gap found in a full auth-security audit: this used to read the
    client-supplied X-Forwarded-For header directly, with no concept of
    whether the request actually came through a trusted reverse proxy --
    any direct connection (bypassing the sysop's real nginx, if any)
    could set an arbitrary value here to (a) spoof past the IP-ban and
    country-block checks below, or (b) worse, make the login-rate-limit
    auto-ban land on an arbitrary VICTIM IP instead of the attacker's
    own, since features/rate_limit.py's bucket is keyed on the real
    request.remote_addr while this function used to read the spoofable
    header instead -- a mismatch that meant the attacker's own
    throttling reset normally while an innocent IP got banned. Now just
    request.remote_addr, matching rate_limit.py's own key exactly.
    web_app.py's ProxyFix (opt-in via TRUST_PROXY_HEADERS, see its own
    comment there) is the ONLY place X-Forwarded-For is trusted, and
    only rewrites remote_addr itself when the sysop has explicitly
    confirmed Flask sits behind their own trusted proxy -- so this
    function doesn't need to know or care which case it's in.
    """
    return request.remote_addr or ''


def _cidr_match(ip_str, cidr_list):
    """Return True if ip_str matches any entry in cidr_list (IP strings or CIDR strings)."""
    import ipaddress as _ipa
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


def _ip_is_whitelisted(ip):
    """Return True if ip matches any IpWhitelist row. Whitelisted IPs bypass all blocks."""
    if not ip:
        return False
    try:
        from ..models import IpWhitelist
        rows = IpWhitelist.query.with_entities(IpWhitelist.cidr).all()
        return _cidr_match(ip, [r.cidr for r in rows])
    except Exception:
        return False


def _ip_is_banned(ip):
    """Return True if `ip` matches any active IpBan row. Whitelisted IPs always return False."""
    if not ip:
        return False
    if _ip_is_whitelisted(ip):
        return False
    try:
        from ..models import IpBan
        from datetime import datetime as _dt
        # Real gap found in a security audit: this used to load EVERY
        # IpBan row ever created (expired ones included) on every
        # single login/registration request, then filter expiry in
        # Python. Because _auto_ban_ip() below inserts a new row every
        # time the login rate limiter trips, and a public BBS is a
        # routine target for scanners/credential-stuffing bots, this
        # table only grows -- and so did the per-request cost, the
        # same "quiet unbounded creep on a long-running worker" shape
        # as this project's own v1.0.21 production incident. Filtering
        # expiry in SQL means an expired row costs nothing once it's
        # actually expired, instead of being fetched and discarded
        # forever.
        now = _dt.utcnow()
        rows = (IpBan.query
                .filter(db.or_(IpBan.expires_at.is_(None), IpBan.expires_at >= now))
                .with_entities(IpBan.cidr).all())
        return _cidr_match(ip, [r.cidr for r in rows])
    except Exception:
        return False


def _auto_ban_ip(ip):
    """Write an IpBan row for ip using the sysop-configured AutoBanConfig
    (see /admin/ip-bans). No-op if auto-ban is disabled, ip is empty, or
    ip is already banned/whitelisted. Duration comes from
    ban_duration_hours (0 = permanent, matching the manual-ban form's
    "TTL days, 0 = permanent" convention)."""
    if not ip or _ip_is_whitelisted(ip):
        return
    try:
        cfg = AutoBanConfig.get()
        if not cfg.enabled:
            return
        from ..models import IpBan
        if IpBan.query.filter_by(cidr=ip).first():
            return
        expires_at = (datetime.utcnow() + timedelta(hours=cfg.ban_duration_hours)
                      if cfg.ban_duration_hours else None)
        reason = (f'Auto-ban: login rate limit exceeded '
                  f'({cfg.attempt_limit} attempts / {cfg.window_seconds // 60 or 1} min)')
        db.session.add(IpBan(cidr=ip, reason=reason, banned_by_id=None,
                             expires_at=expires_at))
        db.session.commit()
        # Piggyback a low-frequency purge of long-expired rows onto the
        # "a new ban just happened" event -- keeps the table itself
        # bounded over time (not just the per-request query cost,
        # already fixed in _ip_is_banned() above) without needing a
        # separate cron job. Random 1-in-20 sample rather than every
        # call, since this is a cheap DELETE but there's no reason to
        # pay it on every single auto-ban.
        import random as _random
        if _random.randint(1, 20) == 1:
            try:
                cutoff = datetime.utcnow() - timedelta(days=7)
                IpBan.query.filter(IpBan.expires_at.isnot(None),
                                   IpBan.expires_at < cutoff).delete()
                db.session.commit()
            except Exception:
                db.session.rollback()
        try:
            current_app.logger.warning('Auto-banned IP %s: %s (expires %s)',
                                       ip, reason, expires_at or 'never')
        except RuntimeError:
            pass
    except Exception:
        db.session.rollback()


def _ip_country_blocked(ip):
    """Return True if ip's country is in BLOCKED_COUNTRIES.
    Uses ip-api.com (free, no registration). Results cached in-memory for 1 hour.
    Fails open — returns False on any network error so a downed lookup never locks out users."""
    if not ip:
        return False
    try:
        blocked = current_app.config.get('BLOCKED_COUNTRIES', '')
        if not blocked:
            return False
        countries = {c.strip().upper() for c in blocked.split(',') if c.strip()}
        if not countries:
            return False

        now = time.time()
        with _geoip_lock:
            entry = _geoip_cache.get(ip)
            if entry and entry[1] > now:
                return entry[0] in countries

        import urllib.request, json as _json
        url = f'http://ip-api.com/json/{ip}?fields=countryCode'
        with urllib.request.urlopen(url, timeout=2) as resp:  # nosec B310 -- hardcoded ip-api.com host, only the query param varies
            data = _json.loads(resp.read())
        country = (data.get('countryCode') or '').upper()

        with _geoip_lock:
            _geoip_cache[ip] = (country, now + 3600)
            if random.randint(1, _GEOIP_SWEEP_PROBABILITY) == 1:
                _sweep_stale_geoip_entries()

        return country in countries
    except Exception:
        return False


def _login_rate_exceeded():
    """Side-effect fired when the login rate limiter trips — permanently bans the source IP."""
    _auto_ban_ip(_client_ip())


def _log_activity(user_id, activity_type, details=None, caller_log_id=None):
    """Record a UserActivity row. Best-effort — never raise on failure.

    Thin, request-aware wrapper around the shared
    anetbbs.core.activity_log.log_activity() (which has no `request`
    dependency, so it can also be called from the asyncio terminal
    session code — see Session._log_activity()). `caller_log_id`
    defaults to whatever login session is active in flask_session
    (stashed by the login route below), so every other existing call
    site here (register, password reset, etc.) gets correlated to the
    active session automatically without passing it explicitly.
    """
    from ..core.activity_log import log_activity
    log_activity(
        user_id, activity_type, details,
        ip_address=_client_ip(),
        user_agent=request.headers.get('User-Agent') or '',
        service='web',
        caller_log_id=caller_log_id if caller_log_id is not None
                     else flask_session.get('caller_log_id'))

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# Real gap found in a security/performance audit: login()'s check used
# to be `if user is None or not user.check_password(...)`. Python's
# `or` short-circuits, so a NONEXISTENT username never reached
# check_password() at all -- a login for a real username with a wrong
# password took as long as the (deliberately slow) hash verification,
# while a nonexistent username returned almost instantly. Both cases
# already show the same generic "Invalid username or password"
# message, but the response TIMING itself is a distinguishable
# username-enumeration side channel regardless. Always running a real
# hash verification -- against this fixed dummy hash when there's no
# real user to check against -- makes the two cases statistically
# indistinguishable by timing. Computed once at import time (not per
# request) so a flood of login attempts against nonexistent usernames
# doesn't ALSO pay the cost of re-hashing this dummy value every time.
_DUMMY_PASSWORD_HASH = generate_password_hash('not-a-real-password-timing-normalization')

# Registration rate limit — at most this many attempts per IP per window.
REGISTER_LIMIT_COUNT = 3
REGISTER_LIMIT_WINDOW_MINUTES = 60

# Password-reset token TTL.
PASSWORD_RESET_TTL_HOURS = 2


class LoginForm(FlaskForm):
    """Login form"""
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=80)])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')


class RegisterForm(FlaskForm):
    """Registration form"""
    username = StringField('Username', validators=[
        DataRequired(),
        Length(min=3, max=80, message='Username must be between 3 and 80 characters'),
        # Real gap found in a full access-control audit: this had no
        # character restriction at all, unlike terminal registration's
        # equivalent (core/session.py's handle_registration(), which
        # has always required this exact charset) -- a web-registered
        # username containing '/'/'..'/CR/LF could break path
        # construction (features/anetcraft.py's save-file path,
        # separately fixed) or field-injection into DOS door dropfiles
        # (games/dropfile.py) and DOSBox/dosemu autoexec generation
        # (games/door_runner.py).
        Regexp(r"^[A-Za-z0-9][A-Za-z0-9 ._'\-]{1,79}$",
              message="Username must start with a letter or digit, and only "
                      "use letters, digits, spaces, dot, apostrophe, hyphen, "
                      "or underscore.")
    ])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=6, message='Password must be at least 6 characters')
    ])
    password2 = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match')
    ])
    question_1 = SelectField('Security Question 1', validators=[DataRequired()])
    answer_1   = StringField('Answer 1', validators=[DataRequired(), Length(min=2, max=200)])
    question_2 = SelectField('Security Question 2', validators=[DataRequired()])
    answer_2   = StringField('Answer 2', validators=[DataRequired(), Length(min=2, max=200)])
    question_3 = SelectField('Security Question 3', validators=[DataRequired()])
    answer_3   = StringField('Answer 3', validators=[DataRequired(), Length(min=2, max=200)])
    submit = SubmitField('Register')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(q, q) for q in SECURITY_QUESTIONS]
        self.question_1.choices = choices
        self.question_2.choices = choices
        self.question_3.choices = choices

    def validate_username(self, field):
        if User.query.filter(
            db.func.lower(User.username) == field.data.lower()
        ).first():
            raise ValidationError('Username already taken. Please choose a different one.')

    def validate_email(self, field):
        if User.query.filter(
            db.func.lower(User.email) == field.data.lower()
        ).first():
            raise ValidationError('Email already registered. Please use a different one.')

    def validate_question_2(self, field):
        if field.data and field.data == self.question_1.data:
            raise ValidationError('Please choose a different question.')

    def validate_question_3(self, field):
        if field.data and (field.data == self.question_1.data
                           or field.data == self.question_2.data):
            raise ValidationError('Please choose a different question.')


@auth_bp.route('/login', methods=['GET', 'POST'])
@rate_limit('login',
           limit=lambda: AutoBanConfig.get().attempt_limit,
           window=lambda: AutoBanConfig.get().window_seconds,
           on_exceed=_login_rate_exceeded)
def login():
    """Login page"""
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    ip = _client_ip()
    # IP ban check — refuse the login attempt entirely.
    if _ip_is_banned(ip):
        flash('Your IP has been banned. Contact the sysop if you believe this is a mistake.', 'danger')
        return redirect(url_for('auth.login'))
    # Country block check.
    if _ip_country_blocked(ip):
        flash('Access from your region is not permitted.', 'danger')
        return redirect(url_for('auth.login'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter(
            db.func.lower(User.username) == form.username.data.lower()
        ).first()

        if user is None:
            # Always run a real hash verification -- see
            # _DUMMY_PASSWORD_HASH's own comment above -- so this path
            # takes statistically the same time as a real username
            # with a wrong password, not a short-circuited instant return.
            check_password_hash(_DUMMY_PASSWORD_HASH, form.password.data)
            flash('Invalid username or password', 'danger')
            return redirect(url_for('auth.login'))
        if not user.check_password(form.password.data):
            flash('Invalid username or password', 'danger')
            return redirect(url_for('auth.login'))

        if not user.is_active:
            flash('Your account has been deactivated. Please contact an administrator.', 'warning')
            return redirect(url_for('auth.login'))

        if getattr(user, 'is_locked', False):
            flash('This account is locked. Contact the sysop.', 'danger')
            return redirect(url_for('auth.login'))

        # Block unverified accounts.
        if not getattr(user, 'is_verified', True) and not user.is_admin:
            from ..mailer import email_verify_enabled
            if email_verify_enabled():
                flash('Please verify your email address before logging in. '
                      'Check your inbox for the verification link.', 'warning')
            elif current_app.config.get('NUV_ENABLED'):
                flash('Your account is awaiting sysop approval. '
                      'Try again later.', 'warning')
            else:
                flash('Your account has not been verified. Contact the sysop.', 'warning')
            return redirect(url_for('auth.login'))
        
        # Update login information
        user.update_login()

        login_user(user, remember=True)
        # Caller log row — best effort, never block the login. Created
        # BEFORE _log_activity('login') and its id stashed in
        # flask_session so every UserActivity event logged for the rest
        # of this login (here and at every other _log_activity() call
        # site: register, password reset, etc.) correlates back to this
        # session for the admin drill-down view.
        try:
            from ..models import CallerLog
            _cl = CallerLog(
                user_id=user.id, username=user.username,
                service='web', ip_address=_client_ip())
            db.session.add(_cl)
            db.session.commit()
            flask_session['caller_log_id'] = _cl.id
            try:
                from ..echomail.interbbs_sync import post_lastcaller_to_interbbs
                post_lastcaller_to_interbbs(_cl)
            except Exception:
                pass
        except Exception:
            db.session.rollback()
        _log_activity(user.id, 'login')
        try:
            from ..features.webhooks import fire
            fire('login', {'user': user.username, 'service': 'web'})
        except Exception:
            pass
        # Real-time "X just logged in" alert for every other online user
        # (terminal and web) -- see models.PresenceEvent's docstring.
        try:
            from ..models import PresenceEvent
            db.session.add(PresenceEvent(
                user_id=user.id, username=user.username,
                kind='login', protocol='web'))
            db.session.commit()
        except Exception:
            db.session.rollback()
        # Random MOTD pick.
        try:
            from ..models import MotdEntry
            import random
            pool = MotdEntry.query.filter_by(is_active=True).all()
            if pool:
                weighted = []
                for m in pool:
                    weighted.extend([m.text] * max(1, m.weight or 1))
                flash(f'☆ {random.choice(weighted)}', 'info')
        except Exception:
            pass
        # Achievement check — runs all rules and flashes any new awards.
        try:
            from ..features.achievements import check_for_user
            for code in check_for_user(user):
                flash(f'🏆 New achievement unlocked: {code}', 'success')
        except Exception:
            db.session.rollback()
        flash(f'Welcome back, {user.username}!', 'success')
        
        # Redirect to next page or home
        # Validate the post-login redirect target to prevent open-redirects.
        # Reject anything that isn't a same-origin local path: must start with
        # '/', must NOT start with '//' (protocol-relative URL: //evil.com),
        # must NOT start with '/\\' (Windows-path-like trick), and the parsed
        # URL must have an empty netloc (no host, no scheme).
        next_page = request.args.get('next') or ''
        from urllib.parse import urlparse
        parsed = urlparse(next_page)
        is_safe = (
            next_page.startswith('/')
            and not next_page.startswith('//')
            and not next_page.startswith('/\\')
            and not parsed.netloc
            and not parsed.scheme
        )
        if not is_safe:
            next_page = url_for('main.index')
        
        return redirect(next_page)
    
    return render_template('auth/login.html', form=form)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """Registration page — rate-limited per IP."""
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    ip = _client_ip()
    if _ip_is_banned(ip):
        flash('Registrations from your IP are not permitted.', 'danger')
        return redirect(url_for('auth.login'))
    if _ip_country_blocked(ip):
        flash('Registrations from your region are not permitted.', 'danger')
        return redirect(url_for('auth.login'))
    form = RegisterForm()

    # Rate limit BEFORE form validation so even invalid payloads count.
    if request.method == 'POST':
        window_start = datetime.utcnow() - timedelta(
            minutes=REGISTER_LIMIT_WINDOW_MINUTES)
        recent = RegistrationAttempt.query.filter(
            RegistrationAttempt.ip_address == ip,
            RegistrationAttempt.created_at >= window_start,
        ).count()
        if recent >= REGISTER_LIMIT_COUNT:
            db.session.add(RegistrationAttempt(
                ip_address=ip,
                username_attempted=request.form.get('username', '')[:80],
                success=False,
                error_reason='rate_limited',
                user_agent=(request.headers.get('User-Agent') or '')[:255],
            ))
            db.session.commit()
            flash(
                f'Too many registration attempts from this IP — please wait '
                f'{REGISTER_LIMIT_WINDOW_MINUTES} minutes and try again.',
                'danger')
            return render_template('auth/register.html', form=form)

    if form.validate_on_submit():
        # Determine whether the new account should start unverified.
        from ..mailer import email_verify_enabled as _ev_enabled
        nuv_on = bool(current_app.config.get('NUV_ENABLED'))
        ev_on = _ev_enabled()
        start_unverified = nuv_on or ev_on
        user = User(
            username=form.username.data,
            email=form.email.data,
            is_verified=(not start_unverified),
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.add(RegistrationAttempt(
            ip_address=ip,
            username_attempted=form.username.data[:80],
            success=True,
            user_agent=(request.headers.get('User-Agent') or '')[:255],
        ))
        # Real gap found in a full auth-security audit: no try/except
        # existed around this flush -- validate_username()/
        # validate_email() above already pre-check for a collision, but
        # on a genuine race (two concurrent registrations of the same
        # username/email, both passing that pre-check before either
        # commits) the flush here raised an unhandled IntegrityError,
        # surfacing as a raw 500 instead of a friendly "already taken"
        # message. Same fix already made for the identical gap in
        # core/user_manager.py's create_user() (the terminal
        # registration path).
        try:
            db.session.flush()   # get user.id before saving answers
        except IntegrityError as exc:
            db.session.rollback()
            if 'username' in str(exc.orig).lower():
                flash('That username is already taken.', 'danger')
            else:
                flash('That email address is already registered.', 'danger')
            return render_template('auth/register.html', form=form)

        # Save security questions chosen during registration
        for i, (q_field, a_field) in enumerate([
            (form.question_1, form.answer_1),
            (form.question_2, form.answer_2),
            (form.question_3, form.answer_3),
        ], 1):
            q = (q_field.data or '').strip()
            a = (a_field.data or '').strip()
            if q and a and q in SECURITY_QUESTIONS:
                db.session.add(UserSecurityAnswer(
                    user_id=user.id,
                    question=q,
                    answer_hash=UserSecurityAnswer.hash_answer(a),
                ))

        db.session.commit()
        _log_activity(user.id, 'register')

        try:
            from ..features.social_queue import maybe_queue_user_milestone
            maybe_queue_user_milestone(User.query.count())
        except Exception:
            current_app.logger.exception('maybe_queue_user_milestone failed')

        if nuv_on:
            # NUV needs an actual sysop click to resolve -- unlike email
            # verification (self-service, user clicks their own emailed
            # link, is_verified flips with zero admin involvement), an
            # NUV-gated account just sits in the queue until someone
            # goes looking for it under Admin -> Pending Users.
            from ..features.notify import notify_admins
            notify_admins(
                'nuv_pending',
                title=f'New user pending approval: {user.username}',
                body=f'{user.username} ({user.email}) registered and is '
                     f'waiting for NUV sysop approval.',
                target_url='/admin/pending-users')

        # Email verification path — send link and redirect to "check your email" page.
        if ev_on:
            from ..mailer import send_verification_email
            token = secrets.token_urlsafe(32)
            db.session.add(EmailVerifyToken(
                user_id=user.id,
                token=token,
                expires_at=datetime.utcnow() + timedelta(hours=24),
            ))
            db.session.commit()
            verify_url = url_for('auth.verify_email', token=token, _external=True)
            ok, err = send_verification_email(user, verify_url)
            if not ok:
                current_app.logger.warning(
                    'Verification email send failed for %s: %s', user.username, err)
            return render_template('auth/verify_sent.html', email=user.email)

        if start_unverified:
            # Real access-control gap found in a full audit: with NUV_ENABLED
            # on and email verification off, execution used to fall straight
            # through to login_user() below -- logging the brand-new,
            # still-unverified account in immediately and completely
            # bypassing the sysop-approval queue this whole code path exists
            # to enforce. login()'s own is_verified check (above) only ever
            # runs on a LATER, separate login -- never here.
            return render_template('auth/pending_approval.html', username=user.username)

        flash(f'Account created successfully! Welcome, {user.username}!', 'success')
        login_user(user, remember=True)

        # Real-time "X just logged in" alert for every other online user
        # (terminal and web) -- see models.PresenceEvent's docstring. This
        # block is separate from login()'s identical one: a brand-new
        # account that registers straight into a session (no NUV/email
        # verification gate) never passes through login(), so without this
        # a new-user registration silently never notified anyone -- the
        # real gap reported live (2026-08-27).
        try:
            from ..models import PresenceEvent
            db.session.add(PresenceEvent(
                user_id=user.id, username=user.username,
                kind='login', protocol='web'))
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Welcome PM from the sysop. Best-effort, never blocks registration.
        try:
            from ..models import User as _U, PrivateMessage as _PM
            sysop = _U.query.filter_by(is_admin=True).order_by(_U.id).first()
            if sysop and sysop.id != user.id:
                bbs_name = current_app.config.get('BBS_NAME', 'this BBS')
                content = (
                    f"Welcome to {bbs_name}, {user.username}!\n\n"
                    "Take the 60-second tour: /tour\n\n"
                    "A few places to check out first:\n"
                    "  • Tools → Statistics — see what's happening\n"
                    "  • Tools → Bulletins — the sysop's news\n"
                    "  • Tools → Calendar — upcoming events\n"
                    "  • Chat → MRC / IRC — real-time chat with users\n"
                    "  • Game Center — door games + built-ins\n"
                    "  • Profile → Edit — set up your tagline + AKAs\n\n"
                    "Need anything? Reply to this message and I'll see it. — Sysop")
                db.session.add(_PM(
                    sender_id=sysop.id,
                    recipient_id=user.id,
                    subject=f'Welcome to {bbs_name}!',
                    content=content,
                    created_at=datetime.utcnow()))
                db.session.commit()
        except Exception:
            db.session.rollback()
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        # Validation failed — record the failed attempt for rate-limit accounting
        db.session.add(RegistrationAttempt(
            ip_address=ip,
            username_attempted=request.form.get('username', '')[:80],
            success=False,
            error_reason='validation_failed',
            user_agent=(request.headers.get('User-Agent') or '')[:255],
        ))
        db.session.commit()

    return render_template('auth/register.html', form=form)


@auth_bp.route('/logout')
@login_required
def logout():
    """Logout the current user"""
    uid = current_user.id
    uname = current_user.username
    # Fix for a real bug found live: CallerLog.duration_seconds was
    # declared on the model and shown in two admin templates but never
    # written anywhere -- every row showed 0s. Written here, before
    # popping the correlation id, using the same flask_session-stashed
    # CallerLog.id the login route above set.
    try:
        _cl_id = flask_session.get('caller_log_id')
        if _cl_id:
            from ..models import CallerLog
            _cl = CallerLog.query.get(_cl_id)
            if _cl is not None:
                _cl.duration_seconds = int(
                    (datetime.utcnow() - _cl.started_at).total_seconds())
                db.session.commit()
    except Exception:
        db.session.rollback()
    # Delete this browser session's presence row -- now that UserSession
    # supports multiple rows per user (session_key, see model docstring),
    # leaving it around after logout would just be a permanently stale
    # row instead of self-overwriting on the next login the way the old
    # unique=True design implicitly did.
    try:
        _sk = flask_session.get('presence_session_key')
        if _sk:
            from ..models import UserSession
            UserSession.query.filter_by(session_key=_sk).delete()
            db.session.commit()
    except Exception:
        db.session.rollback()
    # Real-time "X just logged out" alert -- see models.PresenceEvent's
    # docstring. Recorded before logout_user() invalidates current_user,
    # using the uid/uname already captured above for the same reason.
    try:
        from ..models import PresenceEvent
        db.session.add(PresenceEvent(
            user_id=uid, username=uname, kind='logout', protocol='web'))
        db.session.commit()
    except Exception:
        db.session.rollback()
    logout_user()
    _log_activity(uid, 'logout')
    flask_session.pop('caller_log_id', None)
    flask_session.pop('presence_session_key', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.index'))


# ---------------------------------------------------------------------------
# Password reset flow
# ---------------------------------------------------------------------------

class ForgotPasswordForm(FlaskForm):
    """Step 1: user submits their email/username; we issue a token."""
    identifier = StringField('Username or Email', validators=[
        DataRequired(), Length(min=3, max=120)])
    submit = SubmitField('Send Reset Link')


class ResetPasswordForm(FlaskForm):
    """Step 2: user submits a new password along with the token."""
    password = PasswordField('New Password', validators=[
        DataRequired(), Length(min=6)])
    password2 = PasswordField('Confirm Password', validators=[
        DataRequired(), EqualTo('password', message='Passwords must match')])
    submit = SubmitField('Set Password')


@auth_bp.route('/forgot', methods=['GET', 'POST'])
@rate_limit('forgot_password', limit=10, window=300)
def forgot_password():
    """Request a password-reset token by username or email.

    If the account has security questions, the user is redirected to the
    self-service verify page.  Otherwise the token is logged to the journal
    and the sysop passes the link out-of-band.

    SECURITY: found in a full auth-security audit -- this used to redirect
    to the security-question verify page ONLY when a real, active account
    with security answers matched, and fall through to a generic "if that
    account exists" message otherwise. Since registration requires 3
    security questions, that redirect target was itself a reliable
    username/email enumeration oracle for almost every real account. Now
    EVERY submission with a syntactically plausible identifier redirects
    to the SAME verify page -- a nonexistent account (or one with no
    security questions on file) gets a random DECOY question that can
    never be answered correctly, indistinguishable from the real flow by
    redirect target or page content. An account that genuinely has no
    security questions still gets its real reset token issued/emailed in
    the background here, same as before -- only the visible page changes.

    SECURITY (round 2): a RESIDUAL timing oracle remained even after the
    above -- the "no security questions on file" branch did a real DB
    insert + commit + (when SMTP is configured) a synchronous email
    send before responding, while the "no such account" and "has
    security questions" branches did none of that. An attacker timing
    responses could still distinguish "real account with no security
    questions" from the other two cases purely by latency, even though
    the redirect target and page content were already identical. The
    email send -- real network I/O to a remote mail server, by far the
    slowest and most attacker-measurable part of this branch's cost,
    and unboundedly so against a greylisting server -- is now
    backgrounded (same fire-and-forget pattern already used for
    poll_now()/poll_binkp_node() elsewhere in this codebase). The
    token generation + DB write stay synchronous: a local SQLite
    commit is fast and low-variance enough that it isn't a
    meaningfully exploitable signal on its own, and keeping it
    synchronous avoids changing this route's "token is issued by the
    time the response comes back" contract that other code (and
    tests) depend on.
    """
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        ident = form.identifier.data.strip()
        user = (User.query.filter_by(username=ident).first()
                or User.query.filter_by(email=ident).first())

        real_answers = list(user.security_answers) if (user and user.is_active) else []

        if user and user.is_active and not real_answers:
            # No security questions on file -- this account's real
            # recovery path is the email/journal token, same as always.
            # Still shown the decoy verify page below so the existence
            # of this account isn't distinguishable from one with real
            # security questions or one that doesn't exist at all.
            #
            # Token generation + the DB insert/commit stay synchronous
            # here (fast, low-variance -- a local SQLite write, not a
            # source of meaningfully exploitable timing signal, and
            # keeping it synchronous means the token is reliably
            # findable the instant this request returns, same as
            # before). Only the SMTP send is backgrounded below (see
            # the SECURITY round-2 docstring note above): it's real
            # network I/O to a remote mail server, whose latency is
            # both the dominant and the most attacker-measurable part
            # of this branch's cost -- a slow/greylisting mail server
            # could otherwise hang the whole request for seconds or
            # even minutes, which was ALSO a real (non-security)
            # responsiveness problem independent of the timing-oracle
            # angle.
            token = secrets.token_urlsafe(32)
            db.session.add(PasswordResetToken(
                user_id=user.id,
                token=token,
                expires_at=datetime.utcnow() + timedelta(hours=PASSWORD_RESET_TTL_HOURS),
                requested_ip=_client_ip(),
            ))
            db.session.commit()
            reset_url = url_for('auth.reset_password', token=token,
                                _external=True)
            from ..mailer import smtp_enabled, send_password_reset_email
            if smtp_enabled():
                # Re-query by ID inside the thread's own app context
                # rather than passing the live `user` ORM object
                # across threads -- it's bound to this request's
                # scoped session, which gets torn down once this
                # request ends, matching the same
                # pass-an-ID-not-a-live-object pattern already used by
                # poll_network_now()/poll_binkp_node() elsewhere in
                # this codebase for the exact same reason.
                app = current_app._get_current_object()
                user_id = user.id

                def _send_reset_email(app, user_id, reset_url):
                    with app.app_context():
                        u = User.query.get(user_id)
                        if u:
                            send_password_reset_email(u, reset_url)

                threading.Thread(target=_send_reset_email,
                                 args=(app, user_id, reset_url),
                                 daemon=True).start()
            else:
                current_app.logger.info(
                    'Password reset requested for user %s — reset URL: %s',
                    user.username, reset_url)
            _log_activity(user.id, 'password_reset_requested')

        nonce = secrets.token_urlsafe(24)
        flask_session['sq_nonce'] = nonce
        flask_session['sq_attempts'] = 0
        if real_answers:
            import random
            chosen = random.choice(real_answers)
            flask_session['sq_user_id'] = user.id
            flask_session['sq_answer_id'] = chosen.id
            flask_session.pop('sq_decoy_question', None)
        else:
            # No real account/question to bind to -- a fixed-pool decoy
            # question that can never be answered correctly. Deterministic
            # per identifier (not re-randomized on every submit) so a
            # user who mistypes and resubmits sees a consistent question,
            # same as the real flow always shows the same chosen question.
            import hashlib
            idx = int(hashlib.sha256(ident.encode('utf-8', errors='replace'))
                      .hexdigest(), 16) % len(SECURITY_QUESTIONS)
            flask_session['sq_user_id'] = None
            flask_session['sq_answer_id'] = None
            flask_session['sq_decoy_question'] = SECURITY_QUESTIONS[idx]
        return redirect(url_for('auth.security_question_verify'))

    return render_template('auth/forgot_password.html', form=form)


def _clear_sq_session():
    for key in ('sq_nonce', 'sq_user_id', 'sq_answer_id', 'sq_decoy_question',
               'sq_attempts'):
        flask_session.pop(key, None)


@auth_bp.route('/forgot/verify', methods=['GET', 'POST'])
@rate_limit('security_question_verify', limit=10, window=300)
def security_question_verify():
    """Self-service password recovery via security question.

    SECURITY: found in a full auth-security audit -- no rate limit or
    attempt cap existed, and session state was never cleared on a wrong
    guess, so the SAME question could be brute-forced indefinitely in
    one browser session with no throttle beyond typing speed. Now caps
    at 5 guesses per /forgot session (tracked in flask_session, reset by
    a fresh /forgot submission) on top of the route-level rate limit,
    and also handles the decoy-question case forgot_password() sets up
    for a nonexistent/answerless account -- see that function's own
    docstring for why.
    """
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    nonce = flask_session.get('sq_nonce')
    if not nonce:
        flash('Session expired. Please try again.', 'warning')
        return redirect(url_for('auth.forgot_password'))

    user_id = flask_session.get('sq_user_id')
    answer_id = flask_session.get('sq_answer_id')
    decoy_question = flask_session.get('sq_decoy_question')

    qa = None
    if user_id and answer_id:
        qa = UserSecurityAnswer.query.get(answer_id)
        if qa is None or qa.user_id != user_id:
            _clear_sq_session()
            flash('Session expired. Please try again.', 'warning')
            return redirect(url_for('auth.forgot_password'))
        question = qa.question
    elif decoy_question:
        question = decoy_question
    else:
        _clear_sq_session()
        flash('Session expired. Please try again.', 'warning')
        return redirect(url_for('auth.forgot_password'))

    error = None
    if request.method == 'POST':
        attempts = flask_session.get('sq_attempts', 0) + 1
        flask_session['sq_attempts'] = attempts
        if attempts > 5:
            _clear_sq_session()
            flash('Too many attempts. Please start over.', 'danger')
            return redirect(url_for('auth.forgot_password'))

        raw = request.form.get('answer', '').strip()
        if not raw:
            error = 'Please enter your answer.'
        elif qa is not None and qa.check_answer(raw):
            # Correct — issue reset token and send user straight to reset page
            _clear_sq_session()
            token = secrets.token_urlsafe(32)
            db.session.add(PasswordResetToken(
                user_id=user_id,
                token=token,
                expires_at=datetime.utcnow() + timedelta(hours=PASSWORD_RESET_TTL_HOURS),
                requested_ip=_client_ip(),
            ))
            db.session.commit()
            _log_activity(user_id, 'password_reset_via_security_question')
            return redirect(url_for('auth.reset_password', token=token))
        else:
            # Decoy questions (qa is None) always land here -- there is
            # no correct answer, by design.
            error = 'Incorrect answer. Please try again.'

    return render_template('auth/security_question_verify.html',
                           question=question, error=error)


@auth_bp.route('/reset/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Apply a password-reset token and let the user set a new password."""
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    rt = PasswordResetToken.query.filter_by(token=token).first()
    if rt is None or not rt.is_valid:
        flash('Reset link is invalid or has expired.', 'danger')
        return redirect(url_for('auth.forgot_password'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        rt.user.set_password(form.password.data)
        rt.used_at = datetime.utcnow()
        db.session.commit()
        _log_activity(rt.user.id, 'password_reset_applied')
        flash('Password updated. You can log in now.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html', form=form, token=token)


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

@auth_bp.route('/verify/<token>')
def verify_email(token):
    """Click-through email verification link."""
    vt = EmailVerifyToken.query.filter_by(token=token).first()
    if vt is None or not vt.is_valid:
        flash('Verification link is invalid or has expired. '
              'Contact the sysop or try registering again.', 'danger')
        return redirect(url_for('auth.login'))

    user = vt.user
    vt.used_at = datetime.utcnow()
    user.is_verified = True
    db.session.commit()
    _log_activity(user.id, 'email_verified')
    login_user(user, remember=True)
    return render_template('auth/verified.html', username=user.username)


@auth_bp.route('/verify/resend', methods=['GET', 'POST'])
@rate_limit('resend_verification', limit=10, window=300)
def resend_verification():
    """Let an unverified user request a new verification email."""
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    error = None
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        pw = request.form.get('password', '')
        user = (User.query.filter(
                    db.func.lower(User.username) == identifier.lower()).first()
                or User.query.filter(
                    db.func.lower(User.email) == identifier.lower()).first())
        if user and user.check_password(pw) and not user.is_verified:
            from ..mailer import send_verification_email, email_verify_enabled
            if not email_verify_enabled():
                flash('Email verification is not enabled. Contact the sysop.', 'warning')
                return redirect(url_for('auth.login'))
            token = secrets.token_urlsafe(32)
            db.session.add(EmailVerifyToken(
                user_id=user.id,
                token=token,
                expires_at=datetime.utcnow() + timedelta(hours=24),
            ))
            db.session.commit()
            verify_url = url_for('auth.verify_email', token=token, _external=True)
            send_verification_email(user, verify_url)
            return render_template('auth/verify_sent.html', email=user.email)
        error = 'Invalid credentials or account already verified.'

    return render_template('auth/resend_verification.html', error=error)
