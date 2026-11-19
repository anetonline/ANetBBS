# anetbbs/mail/policy.py
"""Shared Internet Mail policy helpers.

Used by outbound send (outbound.py), the inbound receiver
(smtp_server.py), and every opt-in UI surface (web/profile.py, ANSI
bbs_ui.py, PETSCII petscii_ui.py) so the access-level gate and the
local-part/address rules aren't each duplicated three or four times.
"""
import re


# Role / system mailboxes no ordinary user may hold (audit finding F-09):
# RFC 5321 4.5.1 postmaster, RFC 2142 role addresses, conventional system
# accounts, and the CA/Browser Forum BR 3.2.2.4.4 domain-validation
# mailboxes (admin, administrator, webmaster, hostmaster, postmaster) --
# whoever receives those can obtain TLS certificates for the BBS domain.
RESERVED_LOCAL_PARTS = frozenset({
    'postmaster', 'abuse', 'security', 'noc', 'hostmaster', 'webmaster',
    'admin', 'administrator', 'root', 'mailer-daemon', 'nobody', 'daemon',
    'usenet', 'news', 'uucp', 'ftp', 'www',
})


# Mailbox-name format (audit F-09): dot-atom subset, ASCII only, 1-64
# chars, alnum at both ends, no '..'. Checked case-insensitively.
_LOCAL_PART_RE = re.compile(r'[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?', re.ASCII)


def is_valid_local_part(local_part):
    value = (local_part or '').lower()
    return bool(_LOCAL_PART_RE.fullmatch(value)) and '..' not in value


def is_reserved_local_part(local_part):
    """True if `local_part` is a role/system mailbox: one of
    RESERVED_LOCAL_PARTS, or the local part of the relay's own From
    address (SmtpConfig.from_address) when that address is on the BBS
    mail domain, since system notifications are sent from it.
    Case/whitespace-insensitive. Needs an app context."""
    wanted = (local_part or '').strip().lower()
    if wanted in RESERVED_LOCAL_PARTS:
        return True
    from ..models import MailConfig, SmtpConfig
    sys_local, _, sys_domain = (SmtpConfig.get().from_address or '').strip() \
        .lower().rpartition('@')
    domain = (MailConfig.get().domain or '').strip().lower()
    return bool(sys_local) and bool(domain) and sys_domain == domain \
        and wanted == sys_local


UNVERIFIED_SENDER_WARNING = ('WARNING: this message claims to be from this BBS '
                             'but arrived from the internet and could not be '
                             'verified. It may be forged.')


def sender_warning(message):
    """Warning text every Internet Mail reader shows for an inbound
    message stored as status='quarantine' (it claimed the BBS's own
    domain -- audit finding F-10), else ''."""
    if message.direction == 'inbound' and message.status == 'quarantine':
        return UNVERIFIED_SENDER_WARNING
    return ''


def mark_inbound_read(message, now):
    """Mark an inbound message read without discarding its quarantine
    flag, so the warning stays on every later view. Also clears the
    matching bell notification. Returns True if anything changed (caller
    commits)."""
    if message.direction != 'inbound':
        return False
    changed = False
    if message.read_at is None:
        message.read_at = now
        if message.status != 'quarantine':
            message.status = 'read'
        changed = True
    changed = bool(mark_inbound_notification_read(message)) or changed
    return changed


def mark_inbound_notification_read(message):
    """Clear the matching Internet Mail bell entry, if one exists."""
    if message.direction != 'inbound' or not message.id or not message.user_id:
        return False
    from ..models import Notification
    updated = Notification.query.filter_by(
        user_id=message.user_id, kind='internet_mail',
        target_url=f'/mail/{message.id}', is_read=False,
    ).update({'is_read': True}, synchronize_session=False)
    return bool(updated)


def slug_local_part(username):
    """Default mailbox local-part when User.email_local_part is blank.
    Permissive: letters/digits/dot/underscore/hyphen (RFC 5322 atext
    minus '+', which free mail providers often treat specially)."""
    s = (username or '').strip().lower()
    s = re.sub(r'[^a-z0-9._\-]', '', s)
    return s or 'user'


def mail_local_part(user):
    """The actual local-part a user's Internet Mail is addressed as."""
    explicit = (user.email_local_part or '').strip()
    return explicit or slug_local_part(user.username)


def is_plausible_email(value):
    """Address-shape check for the free-text To-address field on every
    compose screen (web/ANSI/PETSCII). Delegates to
    web/validators.py's regex rather than calling `email_validator`
    directly -- that library (as used by WTForms' stock `Email()`
    validator) rejects legitimate special-use TLDs like `.local` and
    FidoNet-style aliases, which is exactly why this codebase already
    built its own permissive regex instead of using it."""
    from ..web.validators import is_plausible_email as _check
    return _check(value)


def can_enable_mail(user):
    """True if `user` is currently allowed to self-enable Internet Mail
    (the feature is on, and this user's access_level clears the sysop's
    bar, or the user is an administrator). Admin status is a separate
    privilege from numeric access_level, so a sysop must not be locked
    out of testing/configuring a feature whose default threshold is 100.
    Gates whether the opt-in TOGGLE is available/editable -- does not look
    at user.email_enabled itself, and does not retroactively revoke an
    existing email_enabled=True user if the sysop raises the bar later
    (only who can newly opt in)."""
    from ..models import MailConfig
    cfg = MailConfig.get()
    if not cfg.enabled:
        return False
    return bool(user.is_admin or
                (user.access_level or 0) >= cfg.min_access_level)


def set_mail_optin(user, enabled, local_part):
    """Apply a user's Internet Mail opt-in/local-part change -- the one
    place every UI surface that can touch these two columns (web
    profile, ANSI/PETSCII terminal edit screens) goes through, so the
    gate and the mailbox-name uniqueness check aren't each duplicated
    per surface.

    Gate: the whole feature must be on, AND either this user is already
    opted in (so they can always turn it back off, even after a sysop
    raises the access bar) or can_enable_mail() clears it for a fresh
    opt-in.

    Uniqueness: a duplicate mailbox name would otherwise silently steal
    another user's mail, so this checks the EFFECTIVE local part (an
    explicit email_local_part, or the slugified username fallback) of
    every other email_enabled=True user, not just exact-column matches.

    Returns (ok: bool, error: str). Does not commit -- caller's
    responsibility, same as every other model-mutating helper in this
    codebase.
    """
    from ..models import MailConfig, User
    cfg = MailConfig.get()
    if not cfg.enabled or not (user.email_enabled or can_enable_mail(user)):
        return False, 'Internet Mail is not available for your account'

    local_part = (local_part or '').strip() or None
    if enabled:
        effective_new = (local_part or slug_local_part(user.username)).lower()
        if not is_valid_local_part(effective_new):
            return False, ('Invalid mailbox name: use 1-64 letters, digits, '
                           '".", "_" or "-", starting and ending with a letter '
                           'or digit' + ('' if local_part else
                                         ' (your username cannot be used; '
                                         'choose a mailbox name)'))
        if is_reserved_local_part(effective_new):
            return False, 'That mailbox name is reserved for the system'
        others = User.query.filter(User.id != user.id,
                                   User.email_enabled.is_(True)).all()
        # Same effective-address function lookup and display use, so a
        # whitespace-padded legacy value cannot dodge the check (F-01 PR-06b).
        conflict = any(mail_local_part(o).lower() == effective_new for o in others)
        if conflict:
            return False, 'That mailbox name is already taken by another user'
        user.email_enabled = True
        user.email_local_part = local_part
    else:
        user.email_enabled = False
        user.email_local_part = local_part
    return True, ''
