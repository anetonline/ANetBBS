# anetbbs/features/lastcallers.py
"""Last Callers -- read-only paginated list of recent BBS logins.

Deliberately mirrors menu_engine.py's existing `_act_oneliners` inline
"Last 10 Callers" block (plain fixed-width list, not Wall's box-drawing
chrome) since the sysop already has a visual precedent on this BBS for
what a callers list looks like -- this screen is meant to coexist with
that inline block as a fuller, paginated superset (filterable by
service, more rows), not a differently-styled reskin of the same data.

Can optionally show entries imported via InterBBS Last Callers sharing
(anetbbs/echomail/interbbs_sync.py) tagged with their origin BBS name.
"""
from .bbs_ui import _app
from ..core.tz import fmt_eastern

PAGE_SIZE = 20


def recent_callers_query(limit=200):
    """Shared query builder for every user-facing Last Callers display
    (terminal full view, terminal inline block, web oneliners page --
    NOT the admin audit view, which always shows everything).

    Applies the LASTCALLERS_HIDE_SYSOP filter: excludes CallerLog rows
    for a local login by an is_admin user. Uses an outer join so rows
    with no local user_id at all (InterBBS-imported entries, which
    never carry a real user_id -- see interbbs_sync.py's
    sync_lastcallers_inbound) are never affected by this filter; there's
    no local User to check for those, and a sysop hiding their own
    frequent local logins says nothing about a remote BBS's callers.
    """
    from flask import current_app
    from ..models import db, CallerLog, User

    query = CallerLog.query.order_by(CallerLog.started_at.desc())
    if current_app.config.get('LASTCALLERS_HIDE_SYSOP'):
        # isnot(True), not is_(False) -- a legacy/migrated User row with
        # is_admin IS NULL (nullable column, no DB-level default) must
        # read as "not admin", not get excluded by SQL's three-valued
        # NULL logic.
        query = (query.outerjoin(User, CallerLog.user_id == User.id)
                 .filter(db.or_(User.is_admin.isnot(True), User.id.is_(None))))
    return query.limit(limit)


async def show_last_callers(session, args=None):
    """Entry point -- takes `session` directly (not the full BBSMenuUI),
    matching wall.py's show_wall(session, ...) signature, since this is
    called both from menu_engine.py's _act_lastcallers (which has a `ui`
    to pull .session off of) and login_modules.py's _dispatch (which
    only ever has `session`, no `ui`)."""
    with _app().app_context():
        from flask import current_app
        display_count = current_app.config.get('LASTCALLERS_DISPLAY_COUNT', 20)
        callers = recent_callers_query(display_count).all()
        rows = [
            (c.username or '?', c.service or '?', c.started_at, c.origin_bbs)
            for c in callers
        ]

    await session.write('\x1b[2J\x1b[H')

    if not rows:
        await session.write("\x1b[1;36m=== Last Callers ===\x1b[0m\r\n"
                            "  (none yet)\r\n")
        await session.read_line("\r\nPress Enter...")
        return

    lines = ["\x1b[1;36m=== Last Callers ===\x1b[0m"]
    for username, service, started_at, origin_bbs in rows:
        when = fmt_eastern(started_at, '%m-%d %H:%M', '?')
        origin_tag = f" \x1b[36m(via {origin_bbs})\x1b[0m" if origin_bbs else ""
        lines.append(
            f"  \x1b[1;33m{username:<20}\x1b[0m \x1b[1;30m{service:<8}\x1b[0m "
            f"{when}{origin_tag}")

    await _page_lines(session, lines, page_size=PAGE_SIZE,
                      end_message='--- end of last callers ---')


async def _page_lines(session, lines, page_size=22, end_message='--- end ---'):
    """Standalone pager, same shape as BBSMenuUI._page_lines() in
    bbs_ui.py, but usable without a `ui` instance (only `session`)."""
    YEL = '\x1b[1;33m'; DIM = '\x1b[37m'; RESET = '\x1b[0m'
    total = len(lines)
    if not total:
        return
    page = 0
    while page * page_size < total:
        chunk = lines[page * page_size:(page + 1) * page_size]
        for line in chunk:
            await session.write(line + '\r\n')
        page += 1
        if page * page_size >= total:
            await session.read_line(
                f'{YEL}{end_message}{RESET}  Press Enter to continue: ')
            return
        shown = min(page * page_size, total)
        prompt = (f'{YEL}--MORE--{RESET}  '
                 f'({shown}/{total} lines)  '
                 f'{DIM}[Enter]=more  [Q]=quit:{RESET} ')
        ans = (await session.read_line(prompt) or '').strip().upper()
        if ans == 'Q':
            return
