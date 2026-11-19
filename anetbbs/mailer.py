# anetbbs/mailer.py
"""
SMTP relay helper for ANetBBS.

Sends email via the sysop-configured relay server (SmtpConfig DB row).
Uses only Python stdlib smtplib — no extra deps.
Returns (ok: bool, error: str) so callers decide whether to flash/log.
"""
import ipaddress
import logging
import smtplib
import ssl
import unicodedata
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

from flask import current_app, has_app_context

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


def _require_verified_tls():
    return has_app_context() and current_app.config.get('REGISTRATION_REQUIRE_VERIFIED_EMAIL', False)


def _is_literal_loopback(host):
    """True only for a literal loopback IP address (127.0.0.0/8, ::1).
    Host names -- 'localhost' included -- are never trusted: what a name
    resolves to is not proof that the connection stays on this machine."""
    try:
        return ipaddress.ip_address(host or '').is_loopback
    except ValueError:
        return False


def plaintext_auth_refused(cfg):
    """Relay credentials may only cross verified TLS, or stay on a literal
    loopback address. Returns '' or the reason the configuration is refused."""
    if cfg.username and not (cfg.use_ssl or cfg.use_tls) and not _is_literal_loopback(cfg.host):
        return ('SMTP relay authentication requires STARTTLS or SSL/TLS; '
                'plaintext login is only allowed to a loopback IP address')
    return ''


def email_verify_enabled():
    """True if SMTP is ready AND email verification is turned on."""
    cfg = _get_config()
    return bool(cfg and cfg.email_verify_enabled
                and (not _require_verified_tls() or cfg.use_ssl or cfg.use_tls))


def header_line_break_error(**values):
    """Explicit CR/LF rejection for every value that becomes a header or
    SMTP envelope argument. Rejected, never stripped (stripping silently
    joins the injected text onto the header). Returns '' or an error."""
    for name, value in values.items():
        if value and ('\r' in value or '\n' in value):
            return f'Invalid {name}: it contains a line break (CR/LF)'
    return ''


def send_email_as(from_addr, from_name, to_address, subject, body_text, body_html=None):
    """Send one email via the configured SMTP relay, using an explicit
    From identity instead of SmtpConfig.from_address/from_name. The
    relay connection is still the sysop's own configured one -- only the
    message's From header changes. Used by Internet Mail outbound
    (anetbbs/mail/outbound.py) so a user's mail is From
    <local_part>@<MailConfig.domain>, not the sysop's verification/reset
    address; the relay itself doesn't need to know or authorize that
    address, it just carries the message like any other.

    Args:
        from_addr:   The From address to put on the message.
        from_name:   Display name for the From header (may be blank).
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
    # Main-branch rule (REGISTRATION_REQUIRE_VERIFIED_EMAIL): strict signup
    # refuses a plaintext relay outright.
    if _require_verified_tls() and not (cfg.use_ssl or cfg.use_tls):
        return False, 'The SMTP relay must use TLS'
    # #12 (F-02 review): never send relay credentials in plaintext to a
    # non-loopback relay. Refused before any connection is opened.
    auth_err = plaintext_auth_refused(cfg)
    if auth_err:
        logger.warning('SMTP send refused for relay %s: %s', cfg.host, auth_err)
        return False, auth_err

    # Display name is not checked here: user display names are rejected by
    # outbound.send_internet_mail (the user-controlled path); the sysop's
    # SmtpConfig.from_name keeps the F-03 control-character removal below
    # so a misconfiguration cannot silently stop verification/reset mail.
    crlf_err = header_line_break_error(sender=from_addr, recipient=to_address,
                                       subject=subject)
    if crlf_err:
        logger.warning('SMTP send refused: %s', crlf_err)
        return False, crlf_err

    from_addr = from_addr.strip()
    display_name = ''.join(
        char for char in (from_name or '')
        if unicodedata.category(char) not in {'Cc', 'Cf'}
    ).strip()
    # Address-like names can still mislead readers even when RFC-quoted.
    if any(char in display_name for char in '@<>'):
        display_name = ''
    from_header = formataddr((display_name, from_addr), charset='utf-8')

    if body_html:
        msg = MIMEMultipart('alternative')
        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    else:
        msg = MIMEText(body_text, 'plain', 'utf-8')

    # External providers such as Gmail reject messages without a valid
    # RFC 5322 Message-ID. Generate one for every outbound message,
    # including Internet Mail and account notifications.
    sender_domain = (from_addr.rsplit('@', 1)[1]
                     if '@' in from_addr else None)
    msg['Message-ID'] = make_msgid(domain=sender_domain)
    msg['Subject'] = subject
    msg['From'] = from_header
    msg['To'] = to_address

    server = None
    try:
        # Audit F-02: whenever TLS is configured the relay certificate and
        # hostname are verified (system trust store), not only in strict
        # signup mode -- an unverified context would hand the relay
        # credentials to any on-path attacker. Plaintext AUTH is refused
        # above unless the relay is a literal loopback IP; strict signup
        # additionally refuses any plaintext relay.
        tls_context = ssl.create_default_context() if cfg.use_ssl or cfg.use_tls else None
        if cfg.use_ssl:
            server = smtplib.SMTP_SSL(cfg.host, cfg.port or 465, timeout=15, context=tls_context)
        else:
            server = smtplib.SMTP(cfg.host, cfg.port or 587, timeout=15)
            if cfg.use_tls:
                server.ehlo()
                server.starttls(context=tls_context)
                server.ehlo()
        if cfg.username:
            server.login(cfg.username.strip(), cfg.password or '')
        server.sendmail(from_addr, [to_address], msg.as_string())
        server.quit()
        logger.info('Email sent to %s', to_address)   # no subject (audit F-20)
        return True, ''
    except Exception as exc:
        logger.warning('SMTP send failed to %s: %s', to_address, exc)
        return False, str(exc)
    finally:
        if server is not None:
            server.close()


def send_email(to_address, subject, body_text, body_html=None):
    """Send one email via the configured SMTP relay, From the sysop's
    own configured SmtpConfig.from_address/from_name.

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
    return send_email_as(cfg.from_address, cfg.from_name, to_address,
                          subject, body_text, body_html)


def send_verification_email(user, verify_url):
    """Send an email verification link to a newly registered user."""
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
