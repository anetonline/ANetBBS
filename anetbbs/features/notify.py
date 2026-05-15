# anetbbs/features/notify.py
"""
In-app notification helper. Used by @mention scanning, sysop replies, etc.
"""
import re

from ..models import db, Notification, User


# Match @username mentions in text. Allow letters, digits, dots, dashes,
# underscores. Min 2 chars to avoid false positives.
_MENTION_RE = re.compile(r'(?:^|[^a-zA-Z0-9_])@([a-zA-Z0-9_.-]{2,40})')


def notify(user_id, kind, title='', body='', target_url=''):
    """Insert a Notification row. Best-effort — never raises.

    Honors the recipient's notify_prefs JSON (per-kind on/off toggle).
    """
    if user_id is None:
        return
    try:
        target = User.query.get(user_id)
        if target is not None and getattr(target, 'notify_prefs', None):
            try:
                import json as _json
                prefs = _json.loads(target.notify_prefs) or {}
                if prefs.get(kind, True) is False:
                    return
            except (ValueError, TypeError):
                pass
        db.session.add(Notification(
            user_id=user_id, kind=kind, title=title[:200],
            body=body, target_url=target_url[:500]))
        db.session.commit()
    except Exception:
        db.session.rollback()


def notify_mentions(text, sender_name, target_url=''):
    """Scan `text` for @mentions and notify each mentioned user.
    Skips self-mentions, unknown usernames, and users who've blocked
    the sender."""
    if not text:
        return 0
    found = set(m.lower() for m in _MENTION_RE.findall(text))
    if not found:
        return 0

    # Look up the sender once for block-list filtering.
    sender = None
    try:
        sender = User.query.filter(
            User.username.ilike(sender_name or '')).first()
    except Exception:
        db.session.rollback()

    n = 0
    for name in found:
        if name == (sender_name or '').lower():
            continue
        try:
            u = User.query.filter(User.username.ilike(name)).first()
        except Exception:
            db.session.rollback()
            continue
        if u is None:
            continue
        # Suppress if this user has blocked the sender.
        if sender is not None:
            try:
                from ..web.blocks import is_blocked
                if is_blocked(u.id, sender.id):
                    continue
            except Exception:
                pass
        notify(u.id, 'mention',
               title=f'{sender_name} mentioned you',
               body=text[:280],
               target_url=target_url)
        n += 1
    return n
