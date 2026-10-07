# anetbbs/features/fsxnet_iblc.py
"""fsxNet IBLC (InterBBS Last Callers) -- terminal feature.

Real wire-format cross-BBS last-caller feed + BBS directory, speaking
the actual Synchronet mod protocol over fsxNet's FSX_DAT echo area
(see anetbbs/echomail/fsxnet_sync.py for the wire format and
attribution). Deliberately separate from lastcallers.py's own local
Last Callers/ANET_LASTCALLERS -- this reads FsxnetLastCaller rows,
never CallerLog.

Column layout/colors modeled directly on the real iblc.js's own
showLastCallers() (view mode 0: Alias(17) BBS(26) Date(8) Time(5)
Location(19) = 79 chars; view mode 1, toggled with M: Alias(15)
BBS(25) "Telnet Address"(28) OS(8) = 79 chars -- both fit inside 80
columns with zero wrapping). Date/Time are always derived from each
row's own created_at (the LOCAL arrival time of the echomail message),
never from the embedded remote_date_str/remote_time_str fields -- the
real script does the same, since a remote BBS's own date/time text
carries no reliable timezone/locale info (see fsxnet_sync.py's module
docstring). A real sysop screenshot comparison (2026-10-07) found the
first version of this screen combined Location+OS into one row and
let the Date/Time overflow 80 columns, causing every entry to wrap
onto two lines.

The BBS-directory view (press [B] from the main feed) is a real
arrow-key scrollable/selectable lightbar, matching the real iblc.js's
own showBBSes() (a DDLightbarMenu) -- confirmed directly against that
source after a sysop screenshot comparison (2026-10-07) showed our
first version's static one-page numbered list had no way to reach
entries past the first screenful. BBSMenuUI._rss_lightbar (bbs_ui.py)
is a method tied to that class's own fixed menu-chrome row anchors, so
_scroll_lightbar below is a standalone port with row anchors computed
from ui_height(session) instead -- same reasoning _scroll_pager
(fsxnet_ibol.py) already established for porting _rss_pager. It also
deliberately does NOT use _rss_lightbar's reverse-video SEL highlight:
that technique is already confirmed (bbs_ui.py's own Message Boards
render_row, live on SyncTERM) to not render visibly on a real client,
and every existing _rss_lightbar caller in this codebase works around
it by cancelling SEL and drawing a plain bright ">" marker instead --
baked in here from the start rather than re-discovering the same dead
end.

Real column widths/glyphs for the directory, pinned from iblc.js's own
showBBSes(): marker(1) + BBS(24) + Address(30) + Calls(5) +
"Last Call"(16) = 79 chars, fits 80 cols with zero wrapping (our first
version summed to 85 and visibly wrapped mid-field on a live SyncTERM
screenshot). The "this BBS" marker (real ascii(175)) and the hint-bar
separator (real ascii(236)) are '»'/'∞' here -- both confirmed
cp437-encodable, unlike a couple of glyphs the first redesign pass
shipped that weren't (see fsxnet_sync.py's wire-format docstring for
the parallel ROT47/encoding-precedent discipline this project holds
itself to).
"""
from __future__ import annotations

import re

from .ansi_ui import ui_width, ui_height
from .bbs_ui import _app

_GRY = '\x1b[1;30m'
_WH = '\x1b[1;37m'
_WH_N = '\x1b[0;37m'
_YE = '\x1b[1;33m'
_BLU = '\x1b[1;34m'
_BLU_N = '\x1b[0;34m'
_RD = '\x1b[1;31m'
_RST = '\x1b[0m'

VERSION = '1.0'


def _ascii_mode(session) -> bool:
    return getattr(session, 'term_mode', 'ansi') == 'ascii'


def _cap(session) -> str:
    """Small corner-mark glyph for the title divider -- real CP437
    ascii(254) on a capable client, a plain '+' fallback otherwise,
    same ascii_mode convention wall.py already established."""
    return '+' if _ascii_mode(session) else '■'   # cp437 0xFE


def _divider(session, width: int) -> str:
    cap = _cap(session)
    line = '-' * (width - 2) if _ascii_mode(session) else '─' * (width - 2)
    return f'{_BLU}{cap}{_BLU_N}{line}{_BLU}{cap}{_RST}'


def _title_block(session, title: str, width: int) -> str:
    pad = max(0, (width - len(title)) // 2)
    return (f'{" " * pad}{_WH}{title}{_RST}\r\n'
           f'{_divider(session, width)}\r\n')


def _visible_len(s: str) -> int:
    """Length ignoring ANSI SGR sequences -- for wrap math. Ported
    alongside _word_wrap below (same small self-contained-per-file
    convention fsxnet_ibol.py already established for this pair)."""
    return len(re.sub(r'\x1b\[[0-9;]*m', '', s))


def _word_wrap(text: str, width: int) -> list[str]:
    """Plain-text word-wrap for a single paragraph (no embedded
    newlines) at a fixed column."""
    words = text.split(' ')
    lines, cur = [], ''
    for w in words:
        candidate = (cur + ' ' + w).strip() if cur else w
        if _visible_len(candidate) > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = candidate
    if cur or not lines:
        lines.append(cur)
    return lines


def _word_wrap_paragraphs(paragraphs: list[str], width: int) -> list[str]:
    """Word-wrap each paragraph independently and join with a blank
    line between -- splitting on paragraph boundaries BEFORE wrapping
    (rather than relying on terminal auto-wrap, or word-wrapping one
    long string with embedded \\r\\n paragraph breaks) is the fix this
    project's pty-terminal-verification discipline calls for: a
    wrap loop that tokenizes on whitespace alone treats an embedded
    newline as just another space, so its internal column count
    silently drifts from where the terminal's own cursor actually
    resets to column 0 after a real line break."""
    out: list[str] = []
    for i, para in enumerate(paragraphs):
        if i:
            out.append('')
        out.extend(_word_wrap(para, width))
    return out


async def _scroll_lightbar(session, rows, render_header, render_row,
                            render_hint, initial_sel=0):
    """Standalone arrow-key row-selection scroller -- see this module's
    docstring for why this is a fresh port rather than a reuse of
    BBSMenuUI._rss_lightbar, and why it never applies reverse-video.

    render_header()           -- async: writes title+divider+column
                                  header; cursor ends at the row items
                                  start on (LB_START below).
    render_row(idx, row, sel) -- sync: returns ONE already-fully-styled
                                  line (selected rows must do their own
                                  highlight -- nothing is layered on
                                  top of what this returns).
    render_hint(sel, total)   -- sync: returns the status+hints string.
    Returns ('enter', idx) | ('key', char, idx) | ('quit',)
    """
    if not rows:
        return ('quit',)

    EOL = '\x1b[K'
    LB_START = 4
    height = ui_height(session)
    visible = max(4, height - LB_START - 3)
    sep_row = LB_START + visible
    hint_row = sep_row + 1

    def _at(r):
        return f'\x1b[{r};1H'

    sel = min(initial_sel, len(rows) - 1)
    scroll = max(0, sel - visible + 1)

    async def _draw_full():
        await session.write('\x1b[2J\x1b[H')
        await render_header()
        for i in range(visible):
            idx = scroll + i
            row_txt = (render_row(idx, rows[idx], idx == sel)
                       if idx < len(rows) else '')
            await session.write(f'{_at(LB_START + i)}{row_txt}{EOL}')
        await session.write(
            f'{_at(sep_row)}{_divider(session, ui_width(session))}{EOL}')
        await session.write(
            f'{_at(hint_row)}{render_hint(sel, len(rows))}{EOL}')
        await session.write('\x1b[24;1H')

    async def _draw_row(idx):
        if scroll <= idx < scroll + visible:
            row_num = LB_START + (idx - scroll)
            await session.write(
                f'{_at(row_num)}{render_row(idx, rows[idx], idx == sel)}{EOL}')

    async def _refresh_hint():
        await session.write(
            f'{_at(hint_row)}{render_hint(sel, len(rows))}{EOL}\x1b[24;1H')

    await _draw_full()

    while True:
        key = await session.read_key_arrow()

        if key == 'UP':
            if sel > 0:
                old, sel = sel, sel - 1
                if sel < scroll:
                    scroll = sel
                    await _draw_full()
                else:
                    await _draw_row(old)
                    await _draw_row(sel)
                    await _refresh_hint()

        elif key == 'DOWN':
            if sel < len(rows) - 1:
                old, sel = sel, sel + 1
                if sel >= scroll + visible:
                    scroll = sel - visible + 1
                    await _draw_full()
                else:
                    await _draw_row(old)
                    await _draw_row(sel)
                    await _refresh_hint()

        elif key == 'PGUP':
            if scroll > 0:
                scroll = max(0, scroll - visible)
                sel = scroll
                await _draw_full()

        elif key == 'PGDN':
            max_scroll = max(0, len(rows) - visible)
            if scroll < max_scroll:
                scroll = min(max_scroll, scroll + visible)
                sel = scroll
                await _draw_full()

        elif key == 'HOME':
            if sel != 0:
                sel, scroll = 0, 0
                await _draw_full()

        elif key == 'END':
            if sel != len(rows) - 1:
                sel = len(rows) - 1
                scroll = max(0, len(rows) - visible)
                await _draw_full()

        elif key == 'ENTER':
            return ('enter', sel)

        elif key in ('Q', 'ESC', 'CTRL_C'):
            return ('quit',)

        else:
            return ('key', key, sel)


async def show_last_callers(session, args=None) -> None:
    """Entry point -- matches lastcallers.show_last_callers(session,
    args)'s own signature convention."""
    width = ui_width(session)
    view_mode = 0

    while True:
        with _app().app_context():
            from flask import current_app
            from ..models import FsxnetLastCaller
            rows = (FsxnetLastCaller.query
                    .order_by(FsxnetLastCaller.created_at.desc())
                    .limit(200).all())
            iblc_enabled = current_app.config.get('FSXNET_IBLC_ENABLED', False)

        await session.write('\x1b[2J\x1b[H')
        await session.write(_title_block(session, 'Inter-BBS Last Callers', width))

        if not iblc_enabled:
            await session.write(
                "  IBLC is not enabled on this BBS. Ask your sysop to turn it on\r\n"
                "  at Admin -> Messages -> fsxNet IBOL/IBLC.\r\n")
            await session.read_line("\r\nPress Enter...")
            return

        avail = max(4, ui_height(session) - 6)
        shown = rows[:avail]

        if view_mode == 0:
            await session.write(
                f"{_GRY}{'Alias':<17} {'BBS':<26} {'Date':<8} {'Time':<5} {'Location':<19}{_RST}\r\n")
            for row in shown:
                date_s = row.created_at.strftime('%m/%d/%y') if row.created_at else '?'
                time_s = row.created_at.strftime('%H:%M') if row.created_at else '?'
                await session.write(
                    f"{_WH}{row.alias[:17]:<17}{_RST} "
                    f"{_WH_N}{row.bbs_name[:26]:<26}{_RST} "
                    f"{_WH}{date_s:<8}{_RST} "
                    f"{_WH_N}{time_s:<5}{_RST} "
                    f"{_WH}{(row.location or '')[:19]:<19}{_RST}\r\n")
        else:
            await session.write(
                f"{_GRY}{'Alias':<15} {'BBS':<25} {'Telnet Address':<28} {'OS':<8}{_RST}\r\n")
            for row in shown:
                await session.write(
                    f"{_WH}{row.alias[:15]:<15}{_RST} "
                    f"{_WH_N}{row.bbs_name[:25]:<25}{_RST} "
                    f"{_WH}{(row.address or '')[:28]:<28}{_RST} "
                    f"{_WH_N}{(row.os or '')[:8]:<8}{_RST}\r\n")

        if not rows:
            await session.write(f"{_GRY}  (none yet){_RST}\r\n")

        await session.write(f'\r\n{_divider(session, width)}\r\n')
        hint = (f'{_WH}Q{_GRY}> {_WH}Quit  {_YE}·{_RST}  '
               f'{_WH}B{_GRY}> {_WH}BBSes  {_YE}·{_RST}  '
               f'{_WH}M{_GRY}> {_WH}More info  {_YE}·{_RST}  '
               f'{_WH}A{_GRY}> {_WH}About{_RST}')
        choice = (await session.read_line(hint + '  ') or '').strip().upper()
        if choice == 'B':
            await _show_bbs_directory(session)
        elif choice == 'M':
            view_mode = 1 - view_mode
        elif choice == 'A':
            await _show_about(session, width)
        else:
            return


async def _show_about(session, width: int) -> None:
    await session.write('\x1b[2J\x1b[H')
    await session.write(_title_block(session, f'About Inter-BBS Last Callers (v{VERSION})', width))
    paragraphs = [
        f"{_WH_N}This feature shares last-caller data with other BBSes over "
        f"{_WH}fsxNet{_RST}{_WH_N}. It lists the most recent callers on "
        f"participating BBSes, with the option to browse known BBSes and "
        f"connect directly.{_RST}",
        f"{_WH_N}Speaks the real wire format of the Synchronet mod this was "
        f"ported from -- InterBBS Last Callers (IBLC) by {_WH}Craig Hendricks"
        f"{_RST}{_WH_N} (codefenix), ConstructiveChaos BBS.{_RST}",
    ]
    for line in _word_wrap_paragraphs(paragraphs, max(20, width - 2)):
        await session.write(line + '\r\n')
    await session.read_line('\r\nPress Enter...')


def _aggregate_bbses(rows):
    """GROUP BY bbs_name over a list of FsxnetLastCaller rows --
    calls count, last-call timestamp, most-recently-seen address/OS.
    Matches the real iblc.js's own iblc_bbs.json aggregate, derived
    here at render time instead of persisted (see fsxnet_sync.py's
    module docstring / the plan this was built from for why)."""
    agg = {}
    for row in rows:
        entry = agg.get(row.bbs_name)
        if entry is None:
            agg[row.bbs_name] = {
                'bbs_name': row.bbs_name, 'address': row.address,
                'os': row.os, 'lastcall': row.created_at, 'calls': 1,
            }
            continue
        entry['calls'] += 1
        if row.created_at and (entry['lastcall'] is None or row.created_at > entry['lastcall']):
            entry['lastcall'] = row.created_at
            entry['address'] = row.address
            entry['os'] = row.os
    return list(agg.values())


async def _show_bbs_directory(session) -> None:
    """Real scrollable/selectable BBS directory -- see this module's
    docstring for the _scroll_lightbar design and the real-column-width
    citation from iblc.js's own showBBSes()."""
    sort_mode = 0
    # Same order as the real sortCols/sortColNames in iblc.js.
    sort_labels = ['Name', 'Calls', 'Last Call']
    width = ui_width(session)

    with _app().app_context():
        from flask import current_app
        my_bbs_name = (
            (current_app.config.get('FSXNET_SYSTEM_NAME') or '').strip()
            or current_app.config.get('BBS_NAME', 'ANetBBS'))

    sel = 0
    while True:
        with _app().app_context():
            from ..models import FsxnetLastCaller
            rows = FsxnetLastCaller.query.all()
        bbses = _aggregate_bbses(rows)
        if sort_mode == 0:
            bbses.sort(key=lambda b: (b['bbs_name'] or '').upper())
        elif sort_mode == 1:
            bbses.sort(key=lambda b: b['calls'], reverse=True)
        else:
            from datetime import datetime as _dt
            bbses.sort(key=lambda b: b['lastcall'] or _dt.min, reverse=True)

        if not bbses:
            await session.write('\x1b[2J\x1b[H')
            await session.write(_title_block(session, 'fsxNet BBS Directory', width))
            await session.write(f"{_GRY}  (none yet){_RST}\r\n")
            await session.read_line("\r\nPress Enter...")
            return

        async def render_header():
            await session.write(_title_block(session, 'fsxNet BBS Directory', width))
            await session.write(
                f"{_GRY} {'BBS':<24} {'Address':<30} {'Calls':<5} {'Last Call':<16}{_RST}\r\n")

        def render_row(idx, b, selected):
            marker = '»' if b['bbs_name'] == my_bbs_name else ' '
            when = b['lastcall'].strftime('%m/%d/%y %H:%M') if b['lastcall'] else '?'
            line = (f"{marker}{b['bbs_name'][:24]:<24} "
                    f"{(b['address'] or '')[:30]:<30} "
                    f"{b['calls']:<5} {when:<16}")
            # _rss_lightbar's reverse-video SEL highlight is confirmed
            # (bbs_ui.py's own Message Boards render_row) to not render
            # visibly on a real SyncTERM client -- use a plain bright
            # marker instead, same workaround every other caller in
            # this codebase already applies.
            if selected:
                return f"\x1b[0m{_YE}>{line[1:]}{_RST}"
            return f"{_WH}{line}{_RST}"

        next_mode = (sort_mode + 1) % len(sort_labels)

        def render_hint(s, tot):
            return (f'{_WH}Enter{_GRY}> {_WH}connect  {_YE}∞{_RST}  '
                    f'{_WH}S{_GRY}> {_WH}sort by {sort_labels[next_mode]}  {_YE}∞{_RST}  '
                    f'{_WH}Q{_GRY}> {_WH}quit{_RST}')

        result = await _scroll_lightbar(
            session, bbses, render_header, render_row, render_hint,
            initial_sel=min(sel, len(bbses) - 1))

        if result[0] == 'quit':
            return
        elif result[0] == 'enter':
            sel = result[1]
            await _connect_to_bbs(session, bbses[sel])
        else:
            key, sel = result[1], result[2]
            if key == 'S':
                sort_mode = next_mode


async def _connect_to_bbs(session, bbs) -> None:
    address = bbs.get('address') or ''
    if not address:
        await session.write(f'\r\n{_RD}No known address for this BBS.{_RST}\r\n')
        await session.read_line('Press Enter...')
        return
    host, _, port_s = address.partition(':')
    try:
        port = int(port_s) if port_s else 23
    except ValueError:
        port = 23

    from .dialout import DialoutMenu
    await DialoutMenu(session)._connect(bbs['bbs_name'], host, port, 'telnet')
