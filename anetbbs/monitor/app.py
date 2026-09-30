"""Entry point for the anetbbs-monitor terminal live node monitor.

Usage:
    python -m anetbbs.monitor
    anetbbs-monitor               (once installed via setup.py console_scripts)

A live, auto-refreshing dashboard for the whole BBS -- who's connected,
on what protocol, from where, and what they're currently doing, plus
Today/Total activity stats, a log viewer, and one-key access to
anetbbs-cfg -- the CLI equivalent of Synchronet's uMonitor / Mystic's
nodespy, run directly on a shell (SSH into the box, no browser needed).

This is a new FRONT END, not new tracking: ANetBBS already runs a real
classic multinode architecture. core/session.py acquires a fixed slot
(1..BBS_NODES, env var, default 8) on login via
features/multinode.acquire_slot(), and heartbeats a NodeActivity row
(models.py) with slot/username/protocol/peer/page/action/started_at/
last_seen -- the exact same data web/control.py's NodeSpy panel
(nodespy_json) and the in-BBS terminal Node Monitor
(features/bbs_ui.py:_sysop_node_monitor) already read and, for kick,
write. This module is the third reader/writer of that same table, not
a fourth kind of tracking. The Today/Total stats panel is the same
story: every field maps to a real, already-tracked column (CallerLog,
User, Post, EchomailMessage, PrivateMessage, InstantMessage,
FileUpload, UserActivity) -- see fetch_stats()'s own docstring for the
one real gap found and fixed while building this (file_download events
were never actually logged anywhere despite being a documented
UserActivity.activity_type value).

Deliberately duplicates the 5-minute online cutoff and the kick
mutation shape (kick_requested/kick_reason + a UserActivity audit row)
locally rather than importing them from web/control.py or
features/bbs_ui.py -- the same reasoning both of those already apply to
each other: each surface is a different process (web, telnet/SSH,
and now this standalone CLI), and this codebase's established pattern
for keeping duplicated cross-process logic honest is a same-repo
regression test asserting the constants/behavior match, not sharing
code across the process boundary (see
tests/test_terminal_node_monitor.py's own docstring, and
tests/test_node_monitor_cli.py here for this module's version of that
guard).

Deliberately no internal auth check of its own, same reasoning as
anetbbs/cfg/app.py: whoever can run this already has a real shell on
the box, i.e. already-equivalent-or-greater privilege than anything
this tool could grant.

Known, pre-existing limitation (not something this tool regresses):
a connection is invisible to ANetBBS's tracking until login succeeds --
there is no guest/anonymous browsing path, so an empty slot here reads
"waiting for call" whether nobody has dialed in at all, or someone is
sitting at the login prompt right now. The web NodeSpy panel and the
in-BBS Node Monitor have this exact same gap today.
"""
import curses
import os
import sys
from datetime import datetime, timedelta

from anetbbs.cfg.db_bootstrap import create_minimal_app
from anetbbs.cfg.ui import safe_curs_set, init_colors, prompt_text, show_message

TITLE = "ANetBBS Node Monitor"

# Matches web/control.py:nodespy_json's and features/bbs_ui.py's
# _sysop_node_monitor's cutoff exactly. See this module's docstring for
# why it's a local constant rather than a shared import.
ONLINE_CUTOFF_MINUTES = 5

# How often the screen redraws with no keypress. Passed to
# stdscr.timeout(), which is what makes getch() return -1 on expiry
# instead of blocking forever -- none of anetbbs/cfg/ui.py's existing
# loop helpers (run_menu, run_list) do this, since a static menu has no
# reason to redraw itself; a live monitor is the whole reason this file
# exists rather than being a new anetbbs-cfg section.
REFRESH_MS = 1000

# Reserved screen rows around the scrollable node list: header, column
# header, the boxed stats panel (STATS_ROWS lines -- a top border, 3
# content lines, a bottom border), and the footer/hint bar. Kept as
# named constants so _draw()'s row-position math and the "how many
# node rows actually fit" calc in _run() can't drift apart from
# hand-editing one but not the other.
STATS_ROWS = 5
RESERVED_ROWS = 2 + STATS_ROWS + 1  # header+colhdr, boxed stats panel, footer


def _bbs_nodes():
    """Same clamp as core/session.py's multinode slot acquisition
    (session.py:2536-2537), read from the same BBS_NODES env var, so
    the displayed slot count always matches the real running server's
    pool size."""
    return max(1, min(100, int(os.environ.get('BBS_NODES', '8'))))


# Action-label prefixes core/session.py's AFK detection actually writes
# (session.py:618,629,662) -- checked with a plain prefix match rather
# than an exact set, since "Away From Keyboard (screensaver)" carries a
# suffix. Used to color AFK rows distinctly from actively-navigating
# ones instead of a "top notch" monitor giving them identical styling.
_AFK_ACTION_PREFIXES = ('Possibly AFK', 'Away From Keyboard')

# How close to the 5-minute online cutoff a row has to be before this
# tool flags it as "about to age out" -- a real, connected session that
# just hasn't heartbeated in a while (see core/session.py's
# _start_kick_watchdog docstring for the bug this guards against: that
# watchdog now refreshes last_seen every 5s independent of activity, so
# a healthy connected session should essentially never sit in this
# window; seeing one here for more than a poll or two is itself worth a
# sysop's attention -- e.g. a session whose asyncio task is wedged).
STALE_WARNING_SECONDS = 60


def fetch_live_nodes():
    """NodeActivity rows within the online cutoff, keyed by slot.
    Mirrors web/control.py:nodespy_json's query."""
    from anetbbs.models import NodeActivity
    cutoff = datetime.utcnow() - timedelta(minutes=ONLINE_CUTOFF_MINUTES)
    rows = (NodeActivity.query
            .filter(NodeActivity.last_seen >= cutoff)
            .order_by(NodeActivity.slot).all())
    return {r.slot: r for r in rows}


def fetch_stats():
    """Today/Total activity stats for the dashboard panel. Every field
    maps to a real, already-tracked column -- no new tracking needed,
    confirmed by reading models.py directly rather than assumed:

      Logons / Time    -- CallerLog.started_at/duration_seconds
      New Users        -- User.created_at
      Posts            -- Post + EchomailMessage (outbound only -- see
                           below for why inbound/imported FTN traffic
                           is deliberately excluded)
      E-mail           -- PrivateMessage + InstantMessage
      Uploads today    -- FileUpload.created_at (count + summed bytes)
      Downloads today  -- UserActivity(activity_type='file_download')

    Posts counts EchomailMessage.direction == 'outbound' only, not
    every row: EchomailMessage.created_at also covers messages this BBS
    merely *received* from the network (imported FTN traffic), which
    isn't this BBS's own posting activity and would inflate the number
    for any install with heavy inbound echomail -- outbound is the
    correct filter to match what "Posts" actually means here.

    Real gap found and fixed while building this panel: 'file_download'
    is listed in UserActivity's own docstring as a common activity_type
    value but was never actually written anywhere -- FileUpload.
    download_count / SharedFileLink.download_count are lifetime
    cumulative counters incremented in place, with no per-event
    timestamped row, so there was previously no way to compute a real
    per-day download count at all. Fixed at the three real download
    call sites (web/files.py, web/file_areas.py, features/bbs_ui.py's
    terminal ZMODEM path) rather than worked around here.
    """
    from anetbbs.models import (
        db, CallerLog, User, Post, EchomailMessage, PrivateMessage,
        InstantMessage, FileUpload, UserActivity)
    from sqlalchemy import func

    today_start = datetime.combine(datetime.utcnow().date(), datetime.min.time())

    logons_today = CallerLog.query.filter(CallerLog.started_at >= today_start).count()
    logons_total = CallerLog.query.count()
    time_today = (db.session.query(func.sum(CallerLog.duration_seconds))
                  .filter(CallerLog.started_at >= today_start).scalar() or 0)
    time_total = db.session.query(func.sum(CallerLog.duration_seconds)).scalar() or 0

    new_users_today = User.query.filter(User.created_at >= today_start).count()
    new_users_total = User.query.count()

    posts_today = (Post.query.filter(Post.created_at >= today_start).count()
                   + EchomailMessage.query.filter(
                       EchomailMessage.direction == 'outbound',
                       EchomailMessage.created_at >= today_start).count())
    posts_total = (Post.query.count()
                   + EchomailMessage.query.filter_by(direction='outbound').count())

    email_today = (PrivateMessage.query.filter(
                       PrivateMessage.created_at >= today_start).count()
                   + InstantMessage.query.filter(
                       InstantMessage.received_at >= today_start).count())
    email_total = PrivateMessage.query.count() + InstantMessage.query.count()

    upload_rows_today = FileUpload.query.filter(
        FileUpload.created_at >= today_start).all()
    uploads_today_files = len(upload_rows_today)
    uploads_today_bytes = sum(r.file_size or 0 for r in upload_rows_today)

    downloads_today = UserActivity.query.filter(
        UserActivity.activity_type == 'file_download',
        UserActivity.created_at >= today_start).count()

    return {
        'logons_today': logons_today, 'logons_total': logons_total,
        'time_today': int(time_today), 'time_total': int(time_total),
        'new_users_today': new_users_today, 'new_users_total': new_users_total,
        'posts_today': posts_today, 'posts_total': posts_total,
        'email_today': email_today, 'email_total': email_total,
        'uploads_today_files': uploads_today_files,
        'uploads_today_bytes': uploads_today_bytes,
        'downloads_today': downloads_today,
    }


def kick_node(slot, reason):
    """Sets the same kick_requested/kick_reason flag NodeSpy's web kick
    button and the in-BBS Node Monitor set -- picked up by
    core/session.py's existing 5s cross-process watchdog, no session.py
    changes needed. user_id is None (nullable "for anon events" per
    models.UserActivity's own docstring): this tool has no logged-in
    identity of its own, same as anetbbs-cfg -- whoever ran this already
    had shell access. Returns (ok, message)."""
    from anetbbs.models import db, NodeActivity, UserActivity
    row = NodeActivity.query.filter_by(slot=slot).first()
    if row is None:
        return False, f'No live session at slot {slot}.'
    reason = ((reason or '').strip() or 'Disconnected by sysop')[:200]
    target = row.username or '?'
    row.kick_requested = True
    row.kick_reason = reason
    db.session.add(UserActivity(
        user_id=None, activity_type='kick_node',
        details=f'slot {slot} ({target}): {reason}',
        service='cli'))
    db.session.commit()
    return True, f'Kick requested for {target} (slot {slot}) -- disconnects within ~5s.'


def _fmt_delta(td):
    secs = max(0, int(td.total_seconds()))
    hours, rem = divmod(secs, 3600)
    mins, s = divmod(rem, 60)
    if hours:
        return f"{hours}:{mins:02d}:{s:02d}"
    return f"{mins}:{s:02d}"


def _fmt_hms(total_seconds):
    """Same shape as _fmt_delta but takes a raw second count (the stats
    panel's summed CallerLog.duration_seconds), not a timedelta."""
    secs = max(0, int(total_seconds))
    hours, rem = divmod(secs, 3600)
    mins, s = divmod(rem, 60)
    return f"{hours}:{mins:02d}:{s:02d}"


def _fmt_bytes(n):
    n = max(0, int(n))
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f"{n:,} {unit}" if unit == 'bytes' else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _theme_state_path(app):
    return os.path.join(app.config['DATA_DIR'], 'monitor_theme.txt')


def _load_theme(app):
    try:
        with open(_theme_state_path(app)) as f:
            theme = f.read().strip()
        return theme if theme in ('dark', 'light') else 'dark'
    except OSError:
        return 'dark'


def _save_theme(app, theme):
    try:
        os.makedirs(app.config['DATA_DIR'], exist_ok=True)
        with open(_theme_state_path(app), 'w') as f:
            f.write(theme)
    except OSError:
        pass  # cosmetic persistence only -- never worth crashing over


def _attr(pair, extra=0):
    if curses.has_colors():
        return curses.color_pair(pair) | extra
    return extra


def _addstr(win, y, x, text, attr=0):
    """Bounds-checked addstr -- same rationale as cfg/ui.py's private
    _safe_addstr (avoids crashing on the bottom-right cell or a
    resized/small terminal); kept local rather than importing another
    module's underscore-prefixed helper."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    try:
        win.addstr(y, x, text[:max(0, w - x - 1)], attr)
    except curses.error:
        pass


# Column widths as named constants, shared by the header and every row,
# so the two can't drift out of alignment the way two independently
# hand-formatted f-strings can. USER/PROTO/PEER/ACTION widths are sized
# for real values seen live: PROTO must fit "petscii40"/"petscii80" (9
# chars, per models.PresenceEvent's own protocol column comment) not
# just "telnet"/"rlogin" (6); ACTION must fit phrases like "Away From
# Keyboard (screensaver)" (33 chars, core/session.py's own AFK label)
# without truncating mid-word.
W_SLOT, W_USER, W_PROTO, W_PEER, W_ACTION, W_TIME = 4, 15, 10, 20, 34, 8


def _fmt_row(slot_s, user_s, proto_s, peer_s, action_s, since_s, idle_s):
    return (f"  {slot_s:>{W_SLOT}}  {user_s:<{W_USER}} {proto_s:<{W_PROTO}} "
            f"{peer_s:<{W_PEER}} {action_s:<{W_ACTION}} {since_s:>{W_TIME}} {idle_s:>{W_TIME}}")


# Menu hint bar hotkeys, in display order -- shared by the header hint
# line and mouse-click hit-testing (_hotkey_hit_test below) so a click
# on a label always maps to the exact same key the label itself names,
# rather than keeping two independently-hand-maintained lists in sync.
MENU_HOTKEYS = [
    ('C', 'Cfg'), ('L', 'Logs'), ('T', 'Theme'),
    ('K', 'Kick'), ('R', 'Refresh'), ('Q', 'Quit'),
]


def _menu_hint_line():
    return '  '.join(f"[{key}]{label}" for key, label in MENU_HOTKEYS)


def _hotkey_hit_test(x, line_text):
    """Given a click x-position on the row _menu_hint_line() was drawn
    into (starting at column 0), return the hotkey letter it landed on,
    or None. Walks the same MENU_HOTKEYS list the hint line itself was
    built from, so the two can never disagree about where a label is."""
    pos = 0
    for key, label in MENU_HOTKEYS:
        token = f"[{key}]{label}"
        end = pos + len(token)
        if pos <= x < end:
            return key
        pos = end + 2  # the '  ' separator _menu_hint_line() joins with
    return None


def _draw(stdscr, nodes, total_slots, sel, stats, db_error=None):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    online = len(nodes)
    now = datetime.utcnow()

    header = f" {TITLE} :: {online}/{total_slots} online :: {datetime.now().strftime('%H:%M:%S')} "
    if db_error:
        # A DB hiccup (locked file, transient connection error) must not
        # take the whole curses screen down -- last known rows just keep
        # showing (possibly stale) with a visible warning in the header
        # until a later tick succeeds again, same "best-effort, keep
        # going" posture as every other DB touch point in this
        # codebase's session.py. Folded into the header bar rather than
        # its own row so no row position below ever shifts.
        header = f" {TITLE} :: DB ERROR: {db_error} -- showing last known state "
    _addstr(stdscr, 0, 0, header.ljust(w - 1),
            _attr(4 if db_error else 1, curses.A_REVERSE | curses.A_BOLD))
    if not db_error:
        # A colored status dot right after the leading space -- solid
        # green when at least one node is live, blending into the bar
        # (plain white-on-blue, pair 1) when the board is quiet -- gives
        # an at-a-glance "is anything happening" read without parsing
        # the online count text. Pair 8 is dedicated to this (green
        # foreground pinned to the header bar's own blue background)
        # rather than reusing pair 5 + A_REVERSE, which would fight
        # with the header row's own already-reversed white-on-blue bar.
        dot = '●' if online else '○'  # ● / ○
        dot_attr = _attr(8, curses.A_BOLD) if online else _attr(1, curses.A_REVERSE)
        _addstr(stdscr, 0, 0, dot, dot_attr)

    col_header = _fmt_row('Slot', 'User', 'Proto', 'Peer', 'Action', 'Since', 'Idle')
    _addstr(stdscr, 1, 0, col_header, curses.A_BOLD)

    visible = max(0, h - RESERVED_ROWS - 2)  # -2 for header + column header
    list_end_row = 2
    for i in range(min(total_slots, visible)):
        slot = i + 1
        y = 2 + i
        list_end_row = y + 1
        row = nodes.get(slot)
        attr = _attr(2) if i == sel else 0
        if row is None:
            line = f"  {slot:>{W_SLOT}}  -- waiting for call --"
            _addstr(stdscr, y, 0, line, attr or _attr(3, curses.A_DIM))
            continue
        page = (row.page or '')[:W_ACTION]
        action = (row.action or page or '')[:W_ACTION]
        since = _fmt_delta(now - row.started_at) if row.started_at else '?'
        idle_td = (now - row.last_seen) if row.last_seen else None
        idle = _fmt_delta(idle_td) if idle_td is not None else '?'
        line = _fmt_row(str(slot), (row.username or '?')[:W_USER],
                        (row.protocol or '?')[:W_PROTO], (row.peer or '')[:W_PEER],
                        action, since, idle)
        if not attr:
            # Selection highlight (attr already set above) always wins;
            # otherwise flag AFK sessions and rows nearing the online
            # cutoff distinctly rather than styling every online row
            # identically regardless of what it's actually telling a
            # sysop.
            is_afk = (row.action or '').startswith(_AFK_ACTION_PREFIXES)
            is_stale_warning = (idle_td is not None and
                                idle_td.total_seconds() >= STALE_WARNING_SECONDS)
            if is_stale_warning:
                attr = _attr(4)
            elif is_afk:
                attr = _attr(3)
        _addstr(stdscr, y, 0, line, attr)

    # Stats panel: a boxed "Activity" panel (top border with an
    # embedded label, 3 content lines, bottom border) -- Today's
    # numbers in green (the "happening right now" figures), Total's in
    # the border's own cyan/blue (lifetime/historical, deliberately
    # less emphasized), with unicode up/down triangles marking
    # uploads/downloads. Box-drawing is already confirmed safe on this
    # tool's terminals -- the plain '─' separator this replaces was
    # already rendering correctly live -- and this is an SSH-shell
    # console tool, not a CP437 BBS terminal session, so UTF-8 is the
    # safe assumption here.
    box_row0 = list_end_row
    inner_w = max(0, w - 3)  # width between the two vertical bars

    def _box_top(y):
        label = ' Activity '
        left = '┌─'  # ┌─
        fill_len = max(0, w - 1 - len(left) - len(label) - 1)
        line = (left + label + ('─' * fill_len) + '┐')[:w - 1]
        _addstr(stdscr, y, 0, line, _attr(7))

    def _box_bottom(y):
        line = ('└' + ('─' * max(0, w - 3)) + '┘')[:w - 1]
        _addstr(stdscr, y, 0, line, _attr(7))

    def _box_line(y, segments):
        """segments: [(text, attr), ...] drawn left-to-right inside the
        box, space-padded out to inner_w before the right border."""
        _addstr(stdscr, y, 0, '│', _attr(7))
        x, used = 1, 0
        for text, attr in segments:
            _addstr(stdscr, y, x, text, attr)
            x += len(text)
            used += len(text)
        pad = max(0, inner_w - used)
        if pad:
            _addstr(stdscr, y, x, ' ' * pad)
        _addstr(stdscr, y, w - 2, '│', _attr(7))

    _box_top(box_row0)
    if stats is not None:
        _box_line(box_row0 + 1, [
            (' Today   ', _attr(6, curses.A_BOLD)),
            (f"Logons {stats['logons_today']:<4} ", _attr(5, curses.A_BOLD)),
            (f"Time {_fmt_hms(stats['time_today']):<9} ", _attr(5, curses.A_BOLD)),
            (f"New {stats['new_users_today']:<4} ", _attr(5, curses.A_BOLD)),
            (f"Posts {stats['posts_today']:<4} ", _attr(5, curses.A_BOLD)),
            (f"Mail {stats['email_today']:<4}", _attr(5, curses.A_BOLD)),
        ])
        _box_line(box_row0 + 2, [
            (' Total   ', _attr(6, curses.A_BOLD)),
            (f"Logons {stats['logons_total']:<4} ", _attr(7)),
            (f"Time {_fmt_hms(stats['time_total']):<9} ", _attr(7)),
            (f"New {stats['new_users_total']:<4} ", _attr(7)),
            (f"Posts {stats['posts_total']:<4} ", _attr(7)),
            (f"Mail {stats['email_total']:<4}", _attr(7)),
        ])
        _box_line(box_row0 + 3, [
            (' ▲ Uploads ', _attr(5, curses.A_BOLD)),
            (f"{stats['uploads_today_files']} files, "
             f"{_fmt_bytes(stats['uploads_today_bytes'])}    ", 0),
            ('▼ Downloads ', _attr(6, curses.A_BOLD)),
            (f"{stats['downloads_today']} files", 0),
        ])
    else:
        _box_line(box_row0 + 1, [(' (stats unavailable -- DB error above)', _attr(4))])
        _box_line(box_row0 + 2, [('', 0)])
        _box_line(box_row0 + 3, [('', 0)])
    _box_bottom(box_row0 + 4)

    _draw_footer(stdscr, h - 1, w)
    stdscr.refresh()


def _draw_footer(stdscr, y, w):
    """Draws the same text _menu_hint_line() returns (so
    _hotkey_hit_test's column math -- computed against that plain
    string -- stays valid), but segment-by-segment so each hotkey
    letter gets its own bright color against the bar instead of the
    whole footer being one flat reverse-video block."""
    bar_attr = _attr(1, curses.A_REVERSE)
    _addstr(stdscr, y, 0, ' '.ljust(w - 1), bar_attr)
    x = 1
    for key, label in MENU_HOTKEYS:
        _addstr(stdscr, y, x, '[', bar_attr)
        x += 1
        _addstr(stdscr, y, x, key, _attr(8, curses.A_REVERSE | curses.A_BOLD))
        x += 1
        _addstr(stdscr, y, x, f']{label}', bar_attr)
        x += 1 + len(label)
        _addstr(stdscr, y, x, '  ', bar_attr)
        x += 2
    tail = f":: yellow=AFK  red=not heartbeating >{STALE_WARNING_SECONDS}s "
    _addstr(stdscr, y, x, tail, bar_attr)


def _default_log_path(app):
    from anetbbs.config import get_config
    return getattr(get_config(), 'LOG_FILE', None) or os.path.join(
        app.config.get('BASE_DIR', '.'), 'bbs.log')


def tail_lines(log_path, target_lines=4000, chunk_size=65536):
    """Read the last ~target_lines lines of log_path, seeking backwards
    in chunk_size-byte chunks -- never the whole file into memory. Real
    lesson already learned once in this exact codebase (v1.0.54): an
    unbounded readlines() on a multi-GB bbs.log is a genuine OOM risk on
    a live server, not a theoretical one. Pure function (no curses),
    kept separate from _view_log's interactive loop so the seek-from-end
    correctness is directly unit-testable.

    Returns a list of decoded lines (most recent last), or raises
    OSError if the file can't be opened -- caller's job to catch it.
    """
    size = os.path.getsize(log_path)
    with open(log_path, 'rb') as f:
        pos = size
        buf = b''
        while pos > 0 and buf.count(b'\n') < target_lines:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + buf
        text = buf.decode('utf-8', errors='replace')
        return text.splitlines()


def _view_log(stdscr, app):
    """Scrollable pager over the real bbs.log, opened at the END (most
    recent entries) -- see tail_lines()'s own docstring for the actual
    seek-from-end file reading this is built on."""
    log_path = _default_log_path(app)
    try:
        lines = tail_lines(log_path)
    except OSError as exc:
        show_message(stdscr, f"Could not open log: {exc}", error=True)
        return

    if not lines:
        show_message(stdscr, "Log is empty.")
        return

    safe_curs_set(0)
    h, w = stdscr.getmaxyx()
    body_h = max(1, h - 2)
    top = max(0, len(lines) - body_h)
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        body_h = max(1, h - 2)
        _addstr(stdscr, 0, 0,
                f" bbs.log :: lines {top + 1}-{min(len(lines), top + body_h)} of {len(lines)} "
                .ljust(w - 1), _attr(1, curses.A_REVERSE))
        for i in range(body_h):
            idx = top + i
            if idx >= len(lines):
                break
            _addstr(stdscr, 1 + i, 0, lines[idx])
        _addstr(stdscr, h - 1, 0,
                " [Up/Down/PgUp/PgDn] Scroll  [G] End  [Home] Start  [Q/Esc] Back "
                .ljust(w - 1), _attr(1, curses.A_REVERSE))
        stdscr.refresh()
        ch = stdscr.getch()
        max_top = max(0, len(lines) - body_h)
        if ch in (ord('q'), ord('Q'), 27):
            return
        elif ch == curses.KEY_UP:
            top = max(0, top - 1)
        elif ch == curses.KEY_DOWN:
            top = min(max_top, top + 1)
        elif ch == curses.KEY_PPAGE:
            top = max(0, top - body_h)
        elif ch == curses.KEY_NPAGE:
            top = min(max_top, top + body_h)
        elif ch in (ord('g'), ord('G')):
            top = max_top
        elif ch == curses.KEY_HOME:
            top = 0


def _launch_cfg(stdscr):
    """Hands the real terminal off to anetbbs-cfg and cleanly resumes
    this screen on return.

    Real bug found live (2026-09-30): this originally shelled out to
    `[sys.executable, '-m', 'anetbbs.cfg.app']` on the theory that
    reusing sys.executable was "guaranteed" to resolve the same
    package this process itself came from. That's wrong -- `-m`
    re-resolves sys.path from scratch in the child process, inserting
    the child's OWN current working directory ahead of everything
    else. If that cwd happens to contain anything shaped like an
    `anetbbs` path segment (e.g. the install directory itself sitting
    at ~/anetbbs), Python's namespace-package fallback can resolve
    `anetbbs` to that unrelated directory instead of the real
    installed package, crashing with a confusing "cannot import name
    '__version__' from 'anetbbs' (unknown location)" ImportError --
    unrelated to whatever this process already has loaded.

    Since `anetbbs.cfg.app` is already importable right here (this
    process already imported `anetbbs` successfully to get this far),
    calling its main() directly in-process sidesteps the whole
    problem -- no new sys.path resolution happens at all. main() is a
    plain function (only `if __name__ == "__main__"` calls sys.exit),
    and curses.wrapper() inside it does its own init/teardown safely
    nested after our own curses.endwin() below.
    """
    curses.endwin()
    try:
        from anetbbs.cfg.app import main as _cfg_main
        _cfg_main()
    finally:
        # Re-enter curses mode and force a full redraw -- stdscr itself
        # is still the same window object curses.wrapper() gave us, it
        # just needs the terminal put back into curses mode.
        stdscr.clear()
        safe_curs_set(0)
        curses.doupdate()


def _run(stdscr, app):
    """Every DB read/write below opens its OWN fresh `with
    app.app_context():` block per call, rather than one context wrapping
    this whole function -- real bug found live (2026-08-28): a single
    long-lived app context keeps one SQLAlchemy session open for the
    entire curses session, and without an explicit refresh, its ORM
    identity map kept serving the FIRST query's cached row objects
    forever -- "Doing"/"Idle" looked frozen the instant this tool
    started, even though core/session.py's heartbeat was genuinely
    updating the underlying row the whole time (confirmed: restarting
    the tool immediately showed the current value). Every other poller
    in this codebase (core/presence.py's _relay_loop, core/session.py's
    own kick/presence-alert watchdogs) already opens app_context fresh
    per poll for exactly this reason -- this just brings the monitor in
    line with that established pattern instead of introducing a new one.
    """
    theme = _load_theme(app)
    safe_curs_set(0)
    try:
        init_colors(theme)
    except curses.error:
        pass
    # Mouse support (2026-09-29): real, standard ncurses capability --
    # works over any client that does xterm-style mouse reporting
    # (SyncTerm, most modern terminals). A client that doesn't simply
    # never sends KEY_MOUSE; keyboard-only use is entirely unaffected,
    # which is the correct fallback, not a bug to chase.
    try:
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
    except curses.error:
        pass
    stdscr.timeout(REFRESH_MS)
    sel = 0
    flash = None
    nodes = {}
    stats = None
    stats_tick = 0
    while True:
        # A transient DB error (e.g. "database is locked" during a
        # write-heavy moment elsewhere) must not crash the whole curses
        # screen out from under a sysop mid-diagnosis -- keep showing
        # the last known rows with a visible warning instead, and keep
        # retrying every tick, same best-effort posture the rest of
        # this codebase's DB touch points already use.
        db_error = None
        try:
            with app.app_context():
                nodes = fetch_live_nodes()
                # Stats change slowly (per-day aggregates) -- recompute
                # every 5th tick (~5s) rather than every single 1s
                # redraw, to avoid re-running several COUNT/SUM queries
                # a sysop will never actually see change that fast.
                if stats is None or stats_tick % 5 == 0:
                    stats = fetch_stats()
        except Exception as exc:
            db_error = str(exc)[:120] or type(exc).__name__
        stats_tick += 1
        total_slots = _bbs_nodes()
        h, _w = stdscr.getmaxyx()
        visible = max(1, min(total_slots, max(0, h - RESERVED_ROWS - 2)))
        sel = max(0, min(sel, visible - 1))
        _draw(stdscr, nodes, total_slots, sel, stats, db_error=db_error)
        if flash:
            show_message(stdscr, flash)
            flash = None
            continue

        ch = stdscr.getch()
        if ch in (-1,):
            continue  # refresh timeout -- redraw with fresh data
        if ch == curses.KEY_MOUSE:
            try:
                _mid, mx, my, _mz, _bstate = curses.getmouse()
            except curses.error:
                continue
            if my == h - 1:
                key = _hotkey_hit_test(mx - 1, _menu_hint_line())  # -1: footer has a leading space
                if key:
                    ch = ord(key.lower())
            elif 2 <= my < 2 + visible:
                sel = my - 2
                continue
            else:
                continue
        if ch in (ord('q'), ord('Q'), 27):
            return
        if ch in (ord('r'), ord('R'),):
            continue
        if ch == curses.KEY_UP:
            sel = max(0, sel - 1)
        elif ch == curses.KEY_DOWN:
            sel = min(visible - 1, sel + 1)
        elif ch in (ord('c'), ord('C')):
            _launch_cfg(stdscr)
            try:
                init_colors(theme)
            except curses.error:
                pass
            try:
                curses.mousemask(curses.ALL_MOUSE_EVENTS)
            except curses.error:
                pass
        elif ch in (ord('l'), ord('L')):
            _view_log(stdscr, app)
        elif ch in (ord('t'), ord('T')):
            theme = 'light' if theme == 'dark' else 'dark'
            try:
                init_colors(theme)
            except curses.error:
                pass
            _save_theme(app, theme)
        elif ch in (ord('k'), ord('K')):
            slot = sel + 1
            if slot in nodes:
                reason = prompt_text(
                    stdscr, f"Kick reason for slot {slot} [Disconnected by sysop]: ") or ''
                try:
                    with app.app_context():
                        _ok, msg = kick_node(slot, reason)
                    flash = msg
                except Exception as exc:
                    flash = f'Kick failed: {str(exc)[:100] or type(exc).__name__}'


def main():
    app = create_minimal_app()
    try:
        curses.wrapper(_run, app)
    except KeyboardInterrupt:
        pass
    print("Goodbye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
