# anetbbs/web/auth.py
"""
Authentication blueprint for user login, registration, and logout
"""
import secrets
import threading
import time
from datetime import datetime, timedelta

from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, current_app, session as flask_session)
from flask_login import login_user, logout_user, login_required, current_user
from wtforms import StringField, PasswordField, SubmitField, SelectField
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError
from flask_wtf import FlaskForm

from .validators import PermissiveEmail as Email
from ..models import (db, User, PasswordResetToken, RegistrationAttempt,
                      UserActivity, UserSecurityAnswer, SECURITY_QUESTIONS,
                      EmailVerifyToken, AutoBanConfig)
from ..features.rate_limit import rate_limit


# In-memory country-lookup cache: ip -> (country_code, expiry_timestamp)
_geoip_cache: dict = {}
_geoip_lock = threading.Lock()


def _client_ip():
    """Best-effort client IP — honors X-Forwarded-For if behind a proxy."""
    fwd = request.headers.get('X-Forwarded-For', '')
    if fwd:
        return fwd.split(',')[0].strip()
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
        rows = IpBan.query.all()
        active = [r.cidr for r in rows
                  if not (r.expires_at and r.expires_at < _dt.utcnow())]
        return _cidr_match(ip, active)
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
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = _json.loads(resp.read())
        country = (data.get('countryCode') or '').upper()

        with _geoip_lock:
            _geoip_cache[ip] = (country, now + 3600)

        return country in countries
    except Exception:
        return False


def _login_rate_exceeded():
    """Side-effect fired when the login rate limiter trips — permanently bans the source IP."""
    _auto_ban_ip(_client_ip())


def _log_activity(user_id, activity_type, details=None):
    """Record a UserActivity row. Best-effort — never raise on failure."""
    try:
        db.session.add(UserActivity(
            user_id=user_id,
            activity_type=activity_type,
            details=details,
            ip_address=_client_ip(),
            user_agent=(request.headers.get('User-Agent') or '')[:255],
            service='web',
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

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
        Length(min=3, max=80, message='Username must be between 3 and 80 characters')
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

        if user is None or not user.check_password(form.password.data):
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
        _log_activity(user.id, 'login')
        # Caller log row — best effort, never block the login.
        try:
            from ..models import CallerLog
            _cl = CallerLog(
                user_id=user.id, username=user.username,
                service='web', ip_address=_client_ip())
            db.session.add(_cl)
            db.session.commit()
            try:
                from ..echomail.interbbs_sync import post_lastcaller_to_interbbs
                post_lastcaller_to_interbbs(_cl)
            except Exception:
                pass
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
        db.session.flush()   # get user.id before saving answers

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

        flash(f'Account created successfully! Welcome, {user.username}!', 'success')
        login_user(user, remember=True)

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
    logout_user()
    _log_activity(uid, 'logout')
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
def forgot_password():
    """Request a password-reset token by username or email.

    If the account has security questions, the user is redirected to the
    self-service verify page.  Otherwise the token is logged to the journal
    and the sysop passes the link out-of-band.
    """
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        ident = form.identifier.data.strip()
        user = (User.query.filter_by(username=ident).first()
                or User.query.filter_by(email=ident).first())
        if user and user.is_active:
            answers = list(user.security_answers)
            if answers:
                import random
                chosen = random.choice(answers)
                nonce = secrets.token_urlsafe(24)
                flask_session['sq_nonce'] = nonce
                flask_session['sq_user_id'] = user.id
                flask_session['sq_answer_id'] = chosen.id
                return redirect(url_for('auth.security_question_verify'))

            # No security questions — send email or fall back to sysop-copy-link
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
            # Try sending by email; fall back to journal log.
            from ..mailer import smtp_enabled, send_password_reset_email
            if smtp_enabled():
                send_password_reset_email(user, reset_url)
            else:
                current_app.logger.info(
                    'Password reset requested for user %s — reset URL: %s',
                    user.username, reset_url)
            _log_activity(user.id, 'password_reset_requested')

        from ..mailer import smtp_enabled as _se
        if _se():
            flash('If that account exists, a password-reset link has been sent to the registered email address.',
                  'success')
        else:
            flash('If that account exists, a password-reset link has been issued. '
                  'Contact the sysop to receive your reset link.',
                  'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/forgot_password.html', form=form)


@auth_bp.route('/forgot/verify', methods=['GET', 'POST'])
def security_question_verify():
    """Self-service password recovery via security question."""
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))

    nonce = flask_session.get('sq_nonce')
    user_id = flask_session.get('sq_user_id')
    answer_id = flask_session.get('sq_answer_id')
    if not nonce or not user_id or not answer_id:
        flash('Session expired. Please try again.', 'warning')
        return redirect(url_for('auth.forgot_password'))

    qa = UserSecurityAnswer.query.get(answer_id)
    if qa is None or qa.user_id != user_id:
        flask_session.pop('sq_nonce', None)
        flask_session.pop('sq_user_id', None)
        flask_session.pop('sq_answer_id', None)
        flash('Session expired. Please try again.', 'warning')
        return redirect(url_for('auth.forgot_password'))

    error = None
    if request.method == 'POST':
        raw = request.form.get('answer', '').strip()
        if not raw:
            error = 'Please enter your answer.'
        elif qa.check_answer(raw):
            # Correct — issue reset token and send user straight to reset page
            flask_session.pop('sq_nonce', None)
            flask_session.pop('sq_user_id', None)
            flask_session.pop('sq_answer_id', None)
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
            error = 'Incorrect answer. Please try again.'

    return render_template('auth/security_question_verify.html',
                           question=qa.question, error=error)


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
