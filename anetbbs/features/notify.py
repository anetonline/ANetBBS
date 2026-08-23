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
        # Live push to any already-connected browser tab for this user --
        # piggybacks on the same default-namespace socket base.html
        # already opens for sysop_broadcast (see web_app.py's connect
        # handler, which joins every authenticated socket to a room named
        # after its own user id).
        #
        # Deliberately does NOT `from ..web_app import socketio` -- that
        # module calls eventlet.monkey_patch() unconditionally at import
        # time, and monkey_patch() doesn't raise on failure: it just logs
        # warnings and leaves already-created threading primitives (locks,
        # conditions) half-patched. A process that was never meant to run
        # under eventlet -- background pollers, CLI tools, and
        # binkp_server.py's plain-asyncio inbound listener, exactly the
        # "don't always want eventlet/SocketIO pulled in" contexts this
        # function already anticipated -- would "succeed" at that import,
        # permanently corrupt its own SQLAlchemy connection pool ("cannot
        # notify on un-acquired lock" on every commit after), and a plain
        # try/except around the import would never see anything to catch,
        # since the corruption is a side effect of a successful import,
        # not a raised exception. Confirmed as a real production incident
        # on bbs.a-net.fyi: an inbound BinkP session delivering netmail to
        # a real local user triggered this exact failure the first time
        # it happened in a 4-day-old anetbbs-binkp process, then broke
        # every later DB write in that process for the rest of its life.
        #
        # Checking current_app.extensions instead (same pattern already
        # used safely by msp/server.py's own live-toast push) never
        # imports web_app.py at all -- 'socketio' is only registered
        # there if THIS app is the real one web_app.create_app() built,
        # so it's a safe no-op everywhere else. The Notification row
        # above is written either way, so the recipient still sees it via
        # the in-app bell/terminal banner, just without the instant live
        # toast.
        try:
            from flask import current_app
            sio = current_app.extensions.get('socketio')
            if sio is not None:
                sio.emit('user_notification', {
                    'kind': kind, 'title': title, 'body': body,
                    'target_url': target_url,
                }, room=str(user_id))
        except Exception:
            pass
    except Exception:
        db.session.rollback()


async def check_new_notifications(session):
    """Print any Notification rows created since the last check THIS
    terminal session made (tracked via session._last_notif_id) -- the
    "while already online" half of session.py's login-time
    _show_notification_summary() banner (which only covers what was
    already unread AT login). Called once per menu redraw from both
    menu_engine.py's run_menu() and BBSMenuUI.show_main()'s hard-coded
    fallback loop, the same granularity the existing sysop-reply
    pop_messages() check already uses elsewhere in those same loops.

    The first call in a session only establishes the baseline (whatever
    was already unread at login was already announced by
    _show_notification_summary(), so it must not repeat here) -- it
    prints nothing until a notification arrives AFTER that baseline.
    Best-effort: never raises, silent if nothing new.
    """
    try:
        uid = session.user.get('id') if isinstance(session.user, dict) else None
        if not uid:
            return
        from ..models import Notification
        last_id = getattr(session, '_last_notif_id', None)
        if last_id is None:
            top = (Notification.query.filter_by(user_id=uid)
                   .order_by(Notification.id.desc()).first())
            session._last_notif_id = top.id if top else 0
            return
        new_rows = (Notification.query
                    .filter(Notification.user_id == uid,
                            Notification.id > last_id)
                    .order_by(Notification.id)
                    .all())
        if not new_rows:
            return
        session._last_notif_id = new_rows[-1].id
        await session.write('\r\n')
        for n in new_rows:
            line = f'\x1b[1;33m*** New: \x1b[0m{n.title}'
            if n.body:
                line += f' \x1b[36m({n.body})\x1b[0m'
            await session.write(line + '\r\n')
    except Exception:
        pass


def notify_admins(kind, title='', body='', target_url=''):
    """Notify every admin user of something needing their review --
    a new MSP federation join request, a new QWK node application, a
    new user pending verification, a new bad-area echomail entry, etc.
    Each admin still gets their own per-kind notify_prefs toggle honored
    via the normal notify() call."""
    try:
        admin_ids = [u.id for u in User.query.filter_by(is_admin=True).all()]
    except Exception:
        db.session.rollback()
        return
    for admin_id in admin_ids:
        notify(admin_id, kind, title=title, body=body, target_url=target_url)


def notify_mentions(text, sender_name, target_url='', min_access_level=None):
    """Scan `text` for @mentions and notify each mentioned user.
    Skips self-mentions, unknown usernames, and users who've blocked
    the sender.

    Real gap found in a full message-boards security audit: this had no
    concept of the mentioned text's own access gating -- a post in a
    sysop-only/VIP-restricted board that @-mentions someone below that
    level pushed them a live notification (including up to 280 chars of
    the restricted content in `body`) despite them never being able to
    read the board itself. `min_access_level`, when given, is checked
    against each mentioned user via the same shared `evaluate_access()`
    gate every read route in this project already uses -- callers with
    no access-gated context (PMs, the public shoutbox) simply omit it
    and get the old unfiltered behavior.
    """
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
        if min_access_level is not None:
            from .access_control import evaluate_access
            if not evaluate_access(u, min_access_level):
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
