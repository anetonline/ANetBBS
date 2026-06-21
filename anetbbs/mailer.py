# anetbbs/mailer.py
"""
SMTP relay helper for ANetBBS.

Sends email via the sysop-configured relay server (SmtpConfig DB row).
Uses only Python stdlib smtplib — no extra deps.
Returns (ok: bool, error: str) so callers decide whether to flash/log.
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def _get_config():
    """Return the SmtpConfig row, or None if SMTP is disabled/unconfigured."""
    try:
        from .models import SmtpConfig
        cfg = SmtpConfig.query.first()
        if cfg and cfg.enabled and cfg.host and cfg.from_address:
            return cfg
    except Exception:
        pass
    return None


def smtp_enabled():
    """Fast check — True if SMTP relay is configured and enabled."""
    return _get_config() is not None


def email_verify_enabled():
    """True if SMTP is ready AND email verification is turned on."""
    cfg = _get_config()
    return bool(cfg and cfg.email_verify_enabled)


def send_email(to_address, subject, body_text, body_html=None):
    """Send one email via the configured SMTP relay.

    Args:
        to_address:  Recipient email address string.
        subject:     Subject line.
        body_text:   Plain-text body (always sent).
        body_html:   Optional HTML alternative body.

    Returns:
        (True, '') on success or (False, error_message) on failure.
    """
    cfg = _get_config()
    if not cfg:
        return False, 'SMTP relay not configured or disabled'

    from_addr = cfg.from_address.strip()
    from_header = (f'{cfg.from_name} <{from_addr}>'
                   if cfg.from_name else from_addr)

    if body_html:
        msg = MIMEMultipart('alternative')
        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    else:
        msg = MIMEText(body_text, 'plain', 'utf-8')

    msg['Subject'] = subject
    msg['From'] = from_header
    msg['To'] = to_address

    try:
        if cfg.use_ssl:
            server = smtplib.SMTP_SSL(cfg.host, cfg.port or 465, timeout=15)
        else:
            server = smtplib.SMTP(cfg.host, cfg.port or 587, timeout=15)
            if cfg.use_tls:
                server.ehlo()
                server.starttls()
                server.ehlo()
        if cfg.username:
            server.login(cfg.username.strip(), cfg.password or '')
        server.sendmail(from_addr, [to_address], msg.as_string())
        server.quit()
        logger.info('Email sent to %s: %s', to_address, subject)
        return True, ''
    except Exception as exc:
        logger.warning('SMTP send failed to %s: %s', to_address, exc)
        return False, str(exc)


def send_verification_email(user, verify_url):
    """Send an email verification link to a newly registered user."""
    from flask import current_app
    bbs_name = current_app.config.get('BBS_NAME', 'This BBS')
    subject = f'Verify your email — {bbs_name}'
    text = (
        f'Hi {user.username},\n\n'
        f'Thanks for registering at {bbs_name}!\n\n'
        f'Please verify your email address by clicking the link below:\n\n'
        f'{verify_url}\n\n'
        f'This link expires in 24 hours.\n\n'
        f'If you did not register, ignore this email.\n\n'
        f'— {bbs_name} Sysop'
    )
    html = (
        f'<p>Hi <strong>{user.username}</strong>,</p>'
        f'<p>Thanks for registering at <strong>{bbs_name}</strong>!</p>'
        f'<p>Please verify your email address by clicking the link below:</p>'
        f'<p><a href="{verify_url}" style="font-size:1.1em;">{verify_url}</a></p>'
        f'<p>This link expires in <strong>24 hours</strong>.</p>'
        f'<p>If you did not register, ignore this email.</p>'
        f'<p>— {bbs_name} Sysop</p>'
    )
    return send_email(user.email, subject, text, html)


def send_password_reset_email(user, reset_url):
    """Send a password reset link."""
    from flask import current_app
    bbs_name = current_app.config.get('BBS_NAME', 'This BBS')
    subject = f'Password reset — {bbs_name}'
    text = (
        f'Hi {user.username},\n\n'
        f'A password reset was requested for your account at {bbs_name}.\n\n'
        f'Click the link below to set a new password:\n\n'
        f'{reset_url}\n\n'
        f'This link expires in 2 hours.\n\n'
        f'If you did not request a password reset, ignore this email.\n\n'
        f'— {bbs_name} Sysop'
    )
    html = (
        f'<p>Hi <strong>{user.username}</strong>,</p>'
        f'<p>A password reset was requested for your account at <strong>{bbs_name}</strong>.</p>'
        f'<p>Click the link below to set a new password:</p>'
        f'<p><a href="{reset_url}" style="font-size:1.1em;">{reset_url}</a></p>'
        f'<p>This link expires in <strong>2 hours</strong>.</p>'
        f'<p>If you did not request a password reset, ignore this email.</p>'
        f'<p>— {bbs_name} Sysop</p>'
    )
    return send_email(user.email, subject, text, html)
