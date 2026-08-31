# anetbbs/features/file_quota.py
"""
Per-security-level daily file-download quota (FR: Firehawke, 2026-07-24).

"I only have 3TB of upstream each month... need to be able to block
people from just tag-leeching the entire file base overnight." Admin
configures a list of (access_level, daily_quota_bytes) tiers via
FileQuotaTier; a user's quota is whichever tier has the HIGHEST
min_access_level they still qualify for (not their exact level -- a
level-75 user with tiers set at 50 and 100 gets the level-50 tier).
No tier at or below the user's level = unlimited (opt-in per band, not
a default cap). Admins always bypass, same convention as
evaluate_access()'s bypass_admin default.

Enforced on web, terminal (ANSI + PETSCII), and FTP downloads. Works
against both a plain ORM User row (web/FTP) and a session.user dict
(terminal) -- same reason access_control.py's evaluate_access() takes
the same shape, see its own module docstring.
Deliberately does NOT import that module's private _get() helper;
duplicated here rather than reaching into another module's underscore-
prefixed internals.
"""
from datetime import datetime


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _today_str():
    from ..core.tz import EASTERN
    return datetime.now(EASTERN).date().isoformat()


def resolve_quota_bytes(user):
    """Return the user's daily quota in bytes, or None if unlimited
    (no tier configured at or below their level, or they're an admin)."""
    from ..models import FileQuotaTier

    if _get(user, 'is_admin', False):
        return None
    user_level = _get(user, 'access_level', 10) or 10
    tier = (FileQuotaTier.query
           .filter(FileQuotaTier.min_access_level <= user_level)
           .order_by(FileQuotaTier.min_access_level.desc())
           .first())
    return tier.daily_quota_bytes if tier else None


def _get_or_reset_usage_row(user_id):
    """Return this user's FileQuotaUsage row, creating it or resetting
    it to 0 if the stored day doesn't match today's Eastern date."""
    from ..models import db, FileQuotaUsage

    today = _today_str()
    row = FileQuotaUsage.query.filter_by(user_id=user_id).first()
    if row is None:
        row = FileQuotaUsage(user_id=user_id, day=today, bytes_used_today=0)
        db.session.add(row)
        db.session.commit()
    elif row.day != today:
        row.day = today
        row.bytes_used_today = 0
        db.session.commit()
    return row


def check_quota(user, size_bytes):
    """Pure check (no mutation): can this user download `size_bytes`
    more right now? Returns (ok: bool, message: str). message is empty
    when ok=True, otherwise a user-facing explanation."""
    quota = resolve_quota_bytes(user)
    if quota is None:
        return True, ''
    user_id = _get(user, 'id')
    if user_id is None:
        return True, ''
    row = _get_or_reset_usage_row(user_id)
    if row.bytes_used_today + size_bytes > quota:
        remaining = max(0, quota - row.bytes_used_today)
        return False, (
            f'Daily download quota exceeded. You have {remaining:,} of '
            f'{quota:,} bytes remaining today (resets at Eastern midnight).')
    return True, ''


def consume_quota(user, size_bytes):
    """Record `size_bytes` against this user's daily usage. Call only
    after check_quota() has returned ok=True -- optimistic counting,
    same convention as FileRatio's existing bump-before-send pattern in
    web/file_areas.py's download() route (counted at send-initiation,
    not confirmed delivery -- BinkP/ZMODEM/HTTP all lack a reliable
    post-hoc completion signal cheap enough to gate on here).

    Real gap found in a security/performance audit: this used to be a
    Python-side read-then-write (`row.bytes_used_today = (row.
    bytes_used_today or 0) + size_bytes`) -- two concurrent downloads
    both reading the same starting value and both writing back
    `start + their_own_size` lose one of the two increments (a classic
    read-modify-write race), silently undercounting usage under any
    real concurrent traffic. Rewritten as a single atomic SQL UPDATE
    (`bytes_used_today = bytes_used_today + :size`), so the database
    itself serializes concurrent increments instead of losing one to
    whichever request's write lands last."""
    from ..models import db, FileQuotaUsage

    if _get(user, 'is_admin', False):
        return
    user_id = _get(user, 'id')
    if user_id is None:
        return
    # Ensure the row exists (and is reset for a new day) before the
    # atomic increment -- this part still has a benign race (two
    # concurrent first-downloads-of-the-day could both try to create
    # the row), but _get_or_reset_usage_row() already handles that via
    # the DB's own unique constraint / idempotent creation, and it
    # doesn't lose any byte-count data either way since the increment
    # below is what actually matters.
    _get_or_reset_usage_row(user_id)
    today = _today_str()
    db.session.query(FileQuotaUsage).filter_by(
        user_id=user_id, day=today
    ).update(
        {FileQuotaUsage.bytes_used_today: FileQuotaUsage.bytes_used_today + size_bytes},
        synchronize_session=False)
    db.session.commit()
