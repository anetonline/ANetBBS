"""Entry point for the anetbbs-monitor terminal live node monitor.

Usage:
    python -m anetbbs.monitor
    anetbbs-monitor               (once installed via setup.py console_scripts)

A live, auto-refreshing view of every BBS node -- who's connected, on
what protocol, from where, and what they're currently doing -- the CLI
equivalent of Synchronet's uMonitor / Mystic's nodespy, run directly on
a shell (SSH into the box, no browser needed).

This is a new FRONT END, not new tracking: ANetBBS already runs a real
classic multinode architecture. core/session.py acquires a fixed slot
(1..BBS_NODES, env var, default 8) on login via
features/multinode.acquire_slot(), and heartbeats a NodeActivity row
(models.py) with slot/username/protocol/peer/page/action/started_at/
last_seen -- the exact same data web/control.py's NodeSpy panel
(nodespy_json) and the in-BBS terminal Node Monitor
(features/bbs_ui.py:_sysop_node_monitor) already read and, for kick,
write. This module is the third reader/writer of that same table, not
a fourth kind of tracking.

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
from anetbbs.cfg.ui import safe_curs_set, init_colors, prompt_text

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


def _bbs_nodes():
    """Same clamp as core/session.py's multinode slot acquisition
    (session.py:2536-2537), read from the same BBS_NODES env var, so
    the displayed slot count always matches the real running server's
    pool size."""
    return max(1, min(100, int(os.environ.get('BBS_NODES', '8'))))


def fetch_live_nodes():
    """NodeActivity rows within the online cutoff, keyed by slot.
    Mirrors web/control.py:nodespy_json's query."""
    from anetbbs.models import NodeActivity
    cutoff = datetime.utcnow() - timedelta(minutes=ONLINE_CUTOFF_MINUTES)
    rows = (NodeActivity.query
            .filter(NodeActivity.last_seen >= cutoff)
            .order_by(NodeActivity.slot).all())
    return {r.slot: r for r in rows}


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


def _draw(stdscr, nodes, total_slots, sel):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    online = len(nodes)
    now = datetime.utcnow()

    header = f" {TITLE} :: {online}/{total_slots} online :: {datetime.now().strftime('%H:%M:%S')} "
    _addstr(stdscr, 0, 0, header.ljust(w - 1), _attr(1, curses.A_REVERSE))

    col_header = (f"  {'Slot':>4}  {'User':<15} {'Proto':<6} {'Peer':<16} "
                  f"{'Doing':<25} {'Since':>7} {'Idle':>7}")
    _addstr(stdscr, 1, 0, col_header, curses.A_BOLD)

    visible = max(0, h - 3)
    for i in range(min(total_slots, visible)):
        slot = i + 1
        y = 2 + i
        row = nodes.get(slot)
        attr = _attr(2) if i == sel else 0
        if row is None:
            line = f"  {slot:>4}  -- waiting for call --"
            _addstr(stdscr, y, 0, line, attr or _attr(3, curses.A_DIM))
            continue
        page = (row.page or '')[:11]
        action = (row.action or page or '')[:25]
        since = _fmt_delta(now - row.started_at) if row.started_at else '?'
        idle = _fmt_delta(now - row.last_seen) if row.last_seen else '?'
        line = (f"  {slot:>4}  {(row.username or '?')[:15]:<15} "
                f"{(row.protocol or '?')[:6]:<6} {(row.peer or '')[:16]:<16} "
                f"{action:<25} {since:>7} {idle:>7}")
        _addstr(stdscr, y, 0, line, attr)

    footer = " [Up/Down] Select  [K] Kick  [R] Refresh  [Q] Quit "
    _addstr(stdscr, h - 1, 0, footer.ljust(w - 1), _attr(1, curses.A_REVERSE))
    stdscr.refresh()


def _run(stdscr):
    safe_curs_set(0)
    try:
        init_colors()
    except curses.error:
        pass
    stdscr.timeout(REFRESH_MS)
    sel = 0
    flash = None
    while True:
        nodes = fetch_live_nodes()
        total_slots = _bbs_nodes()
        h, _w = stdscr.getmaxyx()
        visible = max(1, min(total_slots, max(0, h - 3)))
        sel = max(0, min(sel, visible - 1))
        _draw(stdscr, nodes, total_slots, sel)
        if flash:
            from anetbbs.cfg.ui import show_message
            show_message(stdscr, flash)
            flash = None
            continue

        ch = stdscr.getch()
        if ch in (-1,):
            continue  # refresh timeout -- redraw with fresh data
        if ch in (ord('q'), ord('Q'), 27):
            return
        if ch in (ord('r'), ord('R'),):
            continue
        if ch == curses.KEY_UP:
            sel = max(0, sel - 1)
        elif ch == curses.KEY_DOWN:
            sel = min(visible - 1, sel + 1)
        elif ch in (ord('k'), ord('K')):
            slot = sel + 1
            if slot in nodes:
                reason = prompt_text(
                    stdscr, f"Kick reason for slot {slot} [Disconnected by sysop]: ") or ''
                _ok, msg = kick_node(slot, reason)
                flash = msg


def main():
    app = create_minimal_app()
    with app.app_context():
        try:
            curses.wrapper(_run)
        except KeyboardInterrupt:
            pass
    print("Goodbye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
