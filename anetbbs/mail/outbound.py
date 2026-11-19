# anetbbs/mail/outbound.py
"""Outbound Internet Mail: a user composing mail to an arbitrary
external address, sent through the existing SmtpConfig relay (see
anetbbs/mailer.py) under a From address of their own
<local_part>@<MailConfig.domain>. Writes one InternetMail row on every
attempt that got far enough to try the network, so a user's Sent view
matches what actually happened -- gating/validation failures before
that point write nothing, since nothing was attempted.
"""
import logging
from datetime import datetime, timedelta

from .. import mailer
from ..models import db, InternetMail, MailConfig
from .limits import lock_mailboxes, outbound_quota_error, stored_bytes_for
from .policy import is_plausible_email, is_reserved_local_part, mail_local_part

logger = logging.getLogger(__name__)


def _outbound_limit_error(user, cfg):
    """Per-user rolling-window send limits through the shared sysop relay
    (audit F-05 release acceptance). Every outbound row counts -- 'failed'
    attempts reached the relay too. Returns an error string, or ''."""
    now = datetime.utcnow()
    for window, limit, label in ((timedelta(hours=1), cfg.outbound_per_hour, 'hour'),
                                 (timedelta(days=1), cfg.outbound_per_day, 'day')):
        limit = 0 if limit is None else limit
        used = (InternetMail.query
                .filter(InternetMail.user_id == user.id,
                        InternetMail.direction == 'outbound',
                        InternetMail.created_at > now - window)
                .count())
        if used >= limit:
            return (f'Outbound mail limit reached ({limit} per {label}); '
                    'try again later')
    return ''


def send_internet_mail(user, to_address, subject, body_text, received_via='web'):
    """Compose + send one outbound Internet Mail message as `user`.

    Returns (ok: bool, error: str).
    """
    cfg = MailConfig.get()
    if not cfg.enabled:
        return False, 'Internet Mail is not enabled on this BBS'
    if not mailer.smtp_enabled():
        return False, 'No outbound mail relay is configured'
    if not user.email_enabled:
        return False, 'Internet Mail is not enabled on your account'
    if user.is_active is False or getattr(user, 'is_locked', False):
        return False, 'Your account is suspended'
    if is_reserved_local_part(mail_local_part(user)):
        # Legacy row holding a role/system mailbox (F-09).
        return False, ('Your mailbox name is reserved for the system; '
                       'choose a different one')

    # Validation failure, nothing attempted -> no row (module docstring).
    crlf_err = mailer.header_line_break_error(
        recipient=to_address, subject=subject,
        display_name=user.display_name or user.username)
    if crlf_err:
        return False, crlf_err

    to_address = (to_address or '').strip()
    if not is_plausible_email(to_address):
        return False, f'Invalid recipient address: {to_address!r}'

    from_addr = f'{mail_local_part(user)}@{cfg.domain}'
    from_name = user.display_name or user.username
    user_id, username = user.id, user.username

    # Atomic per-user rate limit: the window count and the reservation
    # row are one locked step (limits.lock_mailboxes serializes writers
    # for this user's rows across web/terminal processes), so concurrent
    # sends cannot all pass the same "N used" check. The relay call
    # itself happens after the lock is released.
    lock_mailboxes([user_id])
    limit_err = (_outbound_limit_error(user, cfg)
                 # Per-user storage quota also covers Sent copies (limits.py).
                 or outbound_quota_error(user_id, body_text, cfg))
    if limit_err:
        db.session.rollback()                     # release the lock
        logger.info('Internet Mail send refused for user %s: %s', username, limit_err)
        return False, limit_err
    row = InternetMail(
        user_id=user_id,
        direction='outbound',
        status='sending',             # counted by the limiter immediately
        from_addr=from_addr,
        from_name=from_name,
        to_addr=to_address,
        subject=(subject or '')[:998],
        body=body_text,
        stored_bytes=stored_bytes_for(b'', body_text),
        received_via=received_via,
    )
    db.session.add(row)
    db.session.commit()

    ok, err = False, 'Internal error while sending'
    try:
        ok, err = mailer.send_email_as(from_addr, from_name, to_address,
                                       subject, body_text)
    finally:
        # Same final row shape as before the reservation existed: every
        # attempt that reached the relay is recorded sent/failed.
        row.status = 'sent' if ok else 'failed'
        row.sent_at = datetime.utcnow() if ok else None
        db.session.commit()

    if not ok:
        logger.warning('Internet Mail send failed for user %s to %s: %s',
                       username, to_address, err)
    return ok, err
