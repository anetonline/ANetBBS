# anetbbs/mail/limits.py
"""Inbound Internet Mail storage limits: per-user mailbox quota and
per-message attachment limits, plus the row lock that makes the quota
check and the insert one atomic step.

Mailbox quota -- what is counted
--------------------------------
Each delivered InternetMail row charges its owner exactly

    len(raw) + len(body encoded as UTF-8)

i.e. the stored RFC 5322 payload plus the stored decoded text body. No
field is counted twice: `raw_size` duplicates len(raw) and is never
added; headers extracted into subject/from/cc are small, bounded copies
and are not charged.

Scope: the quota caps ALL InternetMail storage owned by one user --
received/quarantined inbound rows AND the user's own outbound Sent rows
(body only, raw is NULL there). It is enforced at both creation points:
inbound SMTP delivery (452 4.2.2) and outbound send (refused before the
relay is contacted, nothing stored). Other users' rows never count.
Deleting mail (web delete is a hard delete) frees its bytes immediately.

Race safety
-----------
`lock_mailboxes()` issues a no-op UPDATE on each recipient's users row
as the FIRST statement of the delivery transaction. On SQLite that takes
the database write lock before usage is read (the first write statement
of a transaction gets a fresh snapshot, waiting on busy_timeout if
another writer holds the lock), so two concurrent deliveries -- other
SMTP sessions, other processes -- cannot both read the same "room left"
and both insert. On row-locking databases it takes the users-row locks
(sorted order, so two multi-recipient deliveries cannot deadlock) and
the following READ COMMITTED reads see every earlier delivery's commit.
"""
import logging

from sqlalchemy import LargeBinary, cast, func, text

logger = logging.getLogger(__name__)


class DeliveryRefused(Exception):
    """A message that must not be stored; `reply` is the SMTP response."""

    def __init__(self, reply, reason=''):
        super().__init__(reason or reply)
        self.reply = reply
        self.reason = reason or reply


class MailboxFull(DeliveryRefused):
    pass


class AttachmentLimitExceeded(DeliveryRefused):
    pass


MAILBOX_FULL_RCPT_REPLY = '452 4.2.2 Mailbox full: {addr}'
MAILBOX_FULL_DATA_REPLY = ('452 4.2.2 Mailbox full for {count} recipient(s); '
                           'message not accepted, try again later')


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------

def stored_bytes_for(raw_bytes, body):
    """Quota charge for one stored row (see module docstring)."""
    return len(raw_bytes or b'') + len((body or '').encode('utf-8', 'surrogatepass'))


def _stored_bytes_expr(dialect_name):
    from ..models import InternetMail as M
    if dialect_name == 'sqlite':
        # length() of a BLOB is bytes; CAST(text AS BLOB) is its UTF-8 bytes
        # (length() of TEXT would count characters).
        raw_len = func.length(M.raw)
        body_len = func.length(cast(M.body, LargeBinary))
    else:
        raw_len = func.octet_length(M.raw)
        body_len = func.octet_length(M.body)
    return func.coalesce(M.stored_bytes,
                         func.coalesce(raw_len, 0) + func.coalesce(body_len, 0))


def mailbox_usage_bytes(user_id):
    """Bytes currently charged to `user_id` (all of their InternetMail rows,
    inbound and outbound -- module docstring)."""
    from ..models import db, InternetMail as M
    dialect = db.session.get_bind().dialect.name
    total = (db.session.query(func.coalesce(func.sum(_stored_bytes_expr(dialect)), 0))
             .filter(M.user_id == user_id)
             .scalar())
    return int(total or 0)


OUTBOUND_QUOTA_ERROR = ('Your mailbox storage quota is full; delete some messages '
                        '(inbox or sent) and try again')


def outbound_quota_error(user_id, body, cfg):
    """With lock_mailboxes() held: '' if a Sent copy of `body` fits in the
    user's quota, else the user-facing refusal."""
    if mailbox_usage_bytes(user_id) + stored_bytes_for(b'', body) > mailbox_quota(cfg):
        return OUTBOUND_QUOTA_ERROR
    return ''


def mailbox_quota(cfg):
    """The configured quota, never unlimited: a missing/invalid value falls
    back to the finite default."""
    from ..models import MAILBOX_QUOTA_DEFAULT_BYTES
    try:
        quota = int(getattr(cfg, 'mailbox_quota_bytes', None) or 0)
    except (TypeError, ValueError):
        quota = 0
    return quota if quota > 0 else MAILBOX_QUOTA_DEFAULT_BYTES


def mailbox_is_full(user_id, cfg):
    """Advisory RCPT-time check: True when not even one more byte fits.
    The binding check is the locked one in check_quota_locked()."""
    return mailbox_usage_bytes(user_id) >= mailbox_quota(cfg)


def lock_mailboxes(user_ids):
    """Serialize writers for these users' mailboxes until the current
    transaction ends (module docstring). Must be the first statement of
    the transaction."""
    from ..models import db
    stmt = text('UPDATE users SET email_enabled = email_enabled WHERE id = :uid')
    for uid in sorted({int(u) for u in user_ids}):
        db.session.execute(stmt, {'uid': uid})


def check_quota_locked(targets, charge, cfg):
    """With lock_mailboxes() held: raise MailboxFull unless EVERY target
    user can take `charge` more bytes. All-or-nothing on purpose -- one
    SMTP reply covers the whole transaction, so storing a subset and
    answering 452 would duplicate those copies when the sender retries."""
    quota = mailbox_quota(cfg)
    full = [uid for uid in {uid for uid, _ in targets}
            if mailbox_usage_bytes(uid) + charge > quota]
    if full:
        raise MailboxFull(MAILBOX_FULL_DATA_REPLY.format(count=len(full)),
                          f'mailbox quota exceeded for {len(full)} recipient(s)')


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

_B64_ALPHABET = b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
_B64_NON_ALPHABET = bytes(b for b in range(256) if b not in _B64_ALPHABET)


def _is_attachment(part):
    """Leaf MIME part counted against the attachment limits: anything
    with an attachment disposition or a filename, or any non-text leaf
    (images, application/*, audio, ...). Plain/HTML body text is not an
    attachment; it is bounded by the message size limit and the quota.
    A part whose headers cannot be read counts as an attachment."""
    try:
        if part.get_content_disposition() == 'attachment' or part.get_filename():
            return True
        return part.get_content_maintype() != 'text'
    except Exception:
        return True


def decoded_size(part):
    """Upper bound on the decoded size of one leaf part, robust to
    malformed transfer encodings:

      * base64: max(Python's lenient decode, 3/4 of the number of base64
        alphabet characters). Lenient decoders skip invalid characters and
        some keep going past misplaced '=' padding, so the alphabet count
        bounds what ANY client could decode -- a part cannot sneak under
        the limit by being "broken" for our decoder only.
      * quoted-printable / uuencode / 7bit / 8bit / binary / unknown:
        decoding never expands, so the decoded length is used; if
        decoding raises, the encoded length is the (larger) bound.
    """
    try:
        cte = str(part.get('content-transfer-encoding') or '').strip().lower()
    except Exception:
        cte = ''
    try:
        encoded = part.get_payload(decode=False)
    except Exception:
        encoded = ''
    if isinstance(encoded, str):
        encoded = encoded.encode('utf-8', 'surrogateescape')
    elif not isinstance(encoded, (bytes, bytearray)):
        encoded = b''
    try:
        decoded = part.get_payload(decode=True)
    except Exception:
        decoded = None
    size = len(decoded) if isinstance(decoded, (bytes, bytearray)) else len(encoded)
    if cte == 'base64':
        size = max(size, len(bytes(encoded).translate(None, _B64_NON_ALPHABET)) * 3 // 4)
    return size


def _attachment_limits(cfg):
    from ..models import MAX_ATTACHMENTS_DEFAULT, MAX_ATTACHMENT_BYTES_DEFAULT
    count = getattr(cfg, 'max_attachments', None)
    size = getattr(cfg, 'max_attachment_bytes', None)
    count = MAX_ATTACHMENTS_DEFAULT if count is None or int(count) < 0 else int(count)
    size = MAX_ATTACHMENT_BYTES_DEFAULT if not size or int(size) <= 0 else int(size)
    return count, size


def _is_attached_container(part):
    """A container part (is_multipart() is True) that is itself ONE
    attachment: any message/* part (message/rfc822 and friends -- the
    stdlib represents their payload as a list, so a leaf-only walk never
    saw them: the reproduced attached-message bypass), or any multipart
    with an attachment disposition or a filename. Ordinary body wrappers
    (multipart/mixed, /alternative, /related without disposition) are not
    attachments; their children are examined instead."""
    try:
        if part.get_content_maintype() == 'message':
            return True
        return part.get_content_disposition() == 'attachment' or bool(part.get_filename())
    except Exception:
        return True


def container_size(part):
    """Size charged for an attached container: its serialized content
    (headers + bodies of the attached message/multipart, i.e. what a client
    saves as the .eml). If it cannot be serialized, the decoded sizes of
    every leaf inside it are summed instead -- never less than its content."""
    from email.policy import compat32
    try:
        payload = part.get_payload()
        if isinstance(payload, list) and part.get_content_maintype() == 'message':
            return sum(len(m.as_bytes(policy=compat32)) for m in payload)
        return len(part.as_bytes(policy=compat32))
    except Exception:
        return sum(decoded_size(p) for p in part.walk() if not p.is_multipart())


def check_attachment_limits(msg, cfg):
    """Raise AttachmentLimitExceeded if `msg` (an email.message.Message)
    has more attachments than allowed or any one is larger than allowed.
    Attachment units: leaf parts per _is_attachment(), and attached
    containers per _is_attached_container() -- an attached message counts
    as exactly ONE attachment whose size is its serialized content, and
    its inner parts are not counted again. Runs before anything is
    stored. A message whose MIME tree cannot be walked is refused rather
    than stored unchecked; the total message-size cap is enforced
    separately while reading DATA."""
    max_count, max_bytes = _attachment_limits(cfg)
    if msg is None:
        raise AttachmentLimitExceeded(
            '554 5.6.0 Message structure could not be checked; not accepted',
            'MIME structure unreadable')
    count = 0

    def charge(size):
        nonlocal count
        count += 1
        if count > max_count:
            raise AttachmentLimitExceeded(
                f'552 5.3.4 Message rejected: more than {max_count} attachment(s)',
                f'attachment count over {max_count}')
        if size > max_bytes:
            raise AttachmentLimitExceeded(
                f'552 5.3.4 Message rejected: an attachment exceeds the '
                f'{max_bytes}-byte size limit',
                f'attachment over {max_bytes} bytes')

    try:
        # Iterative depth-first walk: deeply nested body wrappers must stay
        # deliverable (audit MIME-08, 300 levels) without recursion limits;
        # total work is bounded by the message-size cap.
        stack = [(msg, True)]
        while stack:
            part, is_root = stack.pop()
            if part.is_multipart():
                if not is_root and _is_attached_container(part):
                    charge(container_size(part))
                    continue
                stack.extend((child, False) for child in reversed(part.get_payload()))
            elif _is_attachment(part):
                charge(decoded_size(part))
    except DeliveryRefused:
        raise
    except Exception:
        raise AttachmentLimitExceeded(
            '554 5.6.0 Message structure could not be checked; not accepted',
            'MIME walk failed')
    return count
