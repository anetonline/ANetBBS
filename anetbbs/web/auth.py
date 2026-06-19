# anetbbs/web/auth.py
"""
Authentication blueprint for user login, registration, and logout
"""
import secrets
from datetime import datetime, timedelta

from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, current_app, session as flask_session)
from flask_login import login_user, logout_user, login_required, current_user
from wtforms import StringField, PasswordField, SubmitField, SelectField
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError
from flask_wtf import FlaskForm

from .validators import PermissiveEmail as Email
from ..models import (db, User, PasswordResetToken, RegistrationAttempt,
                      UserActivity, UserSecurityAnswer, SECURITY_QUESTIONS)
from ..features.rate_limit import rate_limit


def _client_ip():
    """Best-effort client IP — honors X-Forwarded-For if behind a proxy."""
    fwd = request.headers.get('X-Forwarded-For', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.remote_addr or ''


def _ip_is_banned(ip):
    """Return True if `ip` matches any active IpBan row (including CIDR
    netmask). Best-effort — silently returns False on error."""
    if not ip:
        return False
    try:
        from ..models import IpBan
        from datetime import datetime as _dt
        import ipaddress as _ipa
        try:
            addr = _ipa.ip_address(ip)
        except ValueError:
            return False
        rows = IpBan.query.all()
        for r in rows:
            if r.expires_at and r.expires_at < _dt.utcnow():
                continue
            try:
                net = _ipa.ip_network(r.cidr, strict=False)
                if addr in net:
                    return True
            except ValueError:
                if r.cidr == ip:
                    return True
    except Exception:
        return False
    return False


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
        if User.query.filter_by(username=field.data).first():
            raise ValidationError('Username already taken. Please choose a different one.')

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError('Email already registered. Please use a different one.')

    def validate_question_2(self, field):
        if field.data and field.data == self.question_1.data:
            raise ValidationError('Please choose a different question.')

    def validate_question_3(self, field):
        if field.data and (field.data == self.question_1.data
                           or field.data == self.question_2.data):
            raise ValidationError('Please choose a different question.')


@auth_bp.route('/login', methods=['GET', 'POST'])
@rate_limit('login', limit=10, window=300)
def login():
    """Login page"""
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    
    # IP ban check — refuse the login attempt entirely.
    if _ip_is_banned(_client_ip()):
        flash('Your IP has been banned. Contact the sysop if you believe this is a mistake.', 'danger')
        return redirect(url_for('auth.login'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()

        if user is None or not user.check_password(form.password.data):
            flash('Invalid username or password', 'danger')
            return redirect(url_for('auth.login'))

        if not user.is_active:
            flash('Your account has been deactivated. Please contact an administrator.', 'warning')
            return redirect(url_for('auth.login'))

        if getattr(user, 'is_locked', False):
            flash('This account is locked. Contact the sysop.', 'danger')
            return redirect(url_for('auth.login'))

        # New User Verification — pending sysop approval.
        if (current_app.config.get('NUV_ENABLED')
                and not getattr(user, 'is_verified', True)
                and not user.is_admin):
            flash('Your account is awaiting sysop approval. '
                  'Try again later.', 'warning')
            return redirect(url_for('auth.login'))
        
        # Update login information
        user.update_login()

        login_user(user, remember=True)
        _log_activity(user.id, 'login')
        # Caller log row — best effort, never block the login.
        try:
            from ..models import CallerLog
            db.session.add(CallerLog(
                user_id=user.id, username=user.username,
                service='web', ip_address=_client_ip()))
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
        # Honor NUV — newly-registered users are pending until a sysop
        # approves them.
        nuv_on = bool(current_app.config.get('NUV_ENABLED'))
        user = User(
            username=form.username.data,
            email=form.email.data,
            is_verified=(not nuv_on),
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

        flash(f'Account created successfully! Welcome, {user.username}!', 'success')
        login_user(user, remember=True)
        _log_activity(user.id, 'register')

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

            # No security questions — fall back to sysop-copy-link flow
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
            current_app.logger.info(
                'Password reset requested for user %s — reset URL: %s',
                user.username, reset_url)
            _log_activity(user.id, 'password_reset_requested')

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
