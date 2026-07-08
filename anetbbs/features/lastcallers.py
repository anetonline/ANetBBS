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

PAGE_SIZE = 20


async def show_last_callers(session, args=None):
    """Entry point -- takes `session` directly (not the full BBSMenuUI),
    matching wall.py's show_wall(session, ...) signature, since this is
    called both from menu_engine.py's _act_lastcallers (which has a `ui`
    to pull .session off of) and login_modules.py's _dispatch (which
    only ever has `session`, no `ui`)."""
    from ..models import CallerLog

    with _app().app_context():
        callers = (CallerLog.query
                  .order_by(CallerLog.started_at.desc())
                  .limit(200).all())
        rows = [
            (c.username or '?', c.service or '?', c.started_at, c.origin_bbs)
            for c in callers
        ]

    if not rows:
        await session.write("\r\n\x1b[1;36m=== Last Callers ===\x1b[0m\r\n"
                            "  (none yet)\r\n")
        await session.read_line("\r\nPress Enter...")
        return

    lines = ["\x1b[1;36m=== Last Callers ===\x1b[0m"]
    for username, service, started_at, origin_bbs in rows:
        when = started_at.strftime('%m-%d %H:%M') if started_at else '?'
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
