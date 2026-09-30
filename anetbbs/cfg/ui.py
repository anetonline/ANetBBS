"""Reusable curses widgets for anetbbs-cfg.

Four building blocks, in increasing order of composition:

- ``run_menu``    -- a vertical list of choices (main menu, section pickers)
- ``run_list``    -- a scrollable table with Add/Edit/Delete/Reorder hotkeys
- ``run_form``    -- a field-driven editor (turns a dict of values into a
                      screen without any per-model curses layout code)
- ``confirm`` / ``show_message`` -- small modal helpers

Every screen here takes ``stdscr`` (or a sub-window) as its first argument
and is a blocking call that returns once the user backs out or confirms --
callers just chain these together, no separate event loop to manage.
"""
import curses
import textwrap
from curses.textpad import Textbox

APP_TITLE = "ANetBBS Terminal Configuration"


def safe_curs_set(visibility):
    """curs_set() raises curses.error ("curs_set() returned ERR")
    whenever the terminfo entry for the current $TERM has no cursor-
    visibility capability (civis/cnorm) -- real bug found live: doors
    launched via door_runner.py inherit TERM=ansi (a minimal terminfo
    entry meant for doors that emit raw ANSI escapes directly, which is
    every OTHER door this launch path has ever run -- anetbbs-cfg is
    the first curses-based program to go through it, and 'ansi' simply
    doesn't define civis/cnorm). Cursor visibility is cosmetic, not
    functional, so failing here should never crash the whole tool --
    same reasoning as _safe_addstr below for the equivalent addstr
    edge case."""
    try:
        curses.curs_set(visibility)
    except curses.error:
        pass


def _safe_addstr(win, y, x, text, attr=0):
    """addstr silently raises curses.error at the bottom-right corner cell
    (writing there advances the cursor past the window's last legal
    position) -- harmless, but would otherwise crash every screen on a
    resized/small terminal."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    try:
        win.addstr(y, x, text[: max(0, w - x - 1)], attr)
    except curses.error:
        pass


def init_colors(theme='dark'):
    """theme='dark' (default, original palette) or 'light' -- added for
    anetbbs-monitor's theme toggle (2026-09-29). Shared by anetbbs-cfg
    and anetbbs-monitor (the latter can now launch the former directly),
    so a theme choice made in one carries over to the other rather than
    needing two independent palettes to stay in sync by hand."""
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    if theme == 'light':
        # Real terminal light backgrounds are usually white/light-gray,
        # not something curses' 8-color palette can address directly --
        # approximate with black-on-white bars and dark-ish foreground
        # text colors, which reads correctly against a light terminal
        # background/theme the same way a light GUI theme would.
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)   # header/footer bars (unchanged -- still readable on light bg)
        curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLUE)   # selected row
        curses.init_pair(3, curses.COLOR_BLUE, bg)                   # hints/help
        curses.init_pair(4, curses.COLOR_RED, bg)                    # errors
        curses.init_pair(5, curses.COLOR_GREEN, bg)                  # good/online/positive
        curses.init_pair(6, curses.COLOR_MAGENTA, bg)                # section labels/accents
        curses.init_pair(7, curses.COLOR_BLUE, bg)                   # box-drawing borders
        curses.init_pair(8, curses.COLOR_GREEN, curses.COLOR_BLUE)   # status dot on the header bar
    else:
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)   # header/footer bars
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)   # selected row
        curses.init_pair(3, curses.COLOR_YELLOW, bg)                 # hints/help
        curses.init_pair(4, curses.COLOR_RED, bg)                    # errors
        curses.init_pair(5, curses.COLOR_GREEN, bg)                  # good/online/positive
        curses.init_pair(6, curses.COLOR_MAGENTA, bg)                # section labels/accents
        curses.init_pair(7, curses.COLOR_CYAN, bg)                   # box-drawing borders
        curses.init_pair(8, curses.COLOR_GREEN, curses.COLOR_BLUE)   # status dot on the header bar


def _attr(pair, fallback=0):
    if curses.has_colors():
        return curses.color_pair(pair)
    return fallback


def draw_header(win, title):
    h, w = win.getmaxyx()
    bar = f" {APP_TITLE} :: {title} ".ljust(w - 1)
    _safe_addstr(win, 0, 0, bar, _attr(1, curses.A_REVERSE))


def draw_footer(win, hints):
    h, w = win.getmaxyx()
    bar = (" " + hints).ljust(w - 1)
    _safe_addstr(win, h - 1, 0, bar, _attr(1, curses.A_REVERSE))


def show_message(stdscr, text, error=False):
    """Blocking modal -- any key dismisses."""
    lines = text.split("\n")
    h = len(lines) + 4
    w = max(len(l) for l in lines) + 6
    h = min(h, curses.LINES - 2) if curses.LINES > 4 else h
    w = min(w, curses.COLS - 2) if curses.COLS > 4 else w
    win = curses.newwin(h, w, max(0, (curses.LINES - h) // 2), max(0, (curses.COLS - w) // 2))
    win.box()
    attr = _attr(4, curses.A_BOLD) if error else 0
    for i, line in enumerate(lines[: h - 3]):
        _safe_addstr(win, 1 + i, 2, line, attr)
    _safe_addstr(win, h - 2, 2, "Press any key to continue...", _attr(3, curses.A_DIM))
    win.refresh()
    win.getch()


def confirm(stdscr, text, default_no=True):
    """Blocking Y/N modal. Returns True only on an explicit 'y'."""
    lines = text.split("\n")
    prompt = "[y/N]" if default_no else "[Y/n]"
    lines = lines + [prompt]
    # Real gap found in a security/performance audit: h/w were never
    # bounds-clamped against the actual screen size (unlike every other
    # curses call in this module -- see safe_curs_set()/_safe_addstr()'s
    # own docstrings for the same class of gap already fixed there).
    # curses.newwin() raises curses.error whenever a requested window
    # doesn't fit the terminal (a long confirmation string on a narrow
    # PTY, or a mid-session resize), which would otherwise crash the
    # whole anetbbs-cfg tool on an unhandled exception. Clamp both
    # dimensions to fit the current screen, and fail soft (auto-answer
    # the default) if even a minimal window can't be created at all.
    h = min(len(lines) + 4, max(1, curses.LINES))
    w = min(max(len(l) for l in lines) + 6, max(1, curses.COLS))
    try:
        win = curses.newwin(h, w, max(0, (curses.LINES - h) // 2), max(0, (curses.COLS - w) // 2))
    except curses.error:
        return not default_no
    win.box()
    for i, line in enumerate(lines):
        _safe_addstr(win, 1 + i, 2, line)
    win.refresh()
    while True:
        ch = win.getch()
        if ch in (ord("y"), ord("Y")):
            return True
        if ch in (ord("n"), ord("N"), 27, 10, 13):
            return False


def _wrap_help_line(line, width):
    """Word-wrap one help_lines entry to `width` columns instead of
    letting _safe_addstr hard-truncate it at the terminal edge.

    Real bug found live (2026-09-15, reported by a screen-reader user
    on an 80-col SSH client): a single long HELP string -- e.g.
    cfg/sections/login_modules.py's "Params by type: ..." line, one
    unbroken string well over 200 characters -- got clipped dead at
    column 79 with no indication anything was cut off, since
    _safe_addstr silently truncates rather than wrapping. Widening the
    terminal isn't a workable fix for a screen-reader user, per the
    report -- the content itself needs to actually fit. Splits on
    embedded newlines first so a caller can still force an explicit
    line break between logically separate tips, matching the general
    word-wrap convention used for real terminal output elsewhere in
    this codebase."""
    out = []
    for segment in line.split('\n'):
        out.extend(textwrap.wrap(segment, width=max(10, width)) or [''])
    return out


def run_menu(stdscr, title, items, footer="[Up/Down] Move  [Enter] Select  [Esc] Back"):
    """items: list of (key, label) tuples. Returns the selected key, or
    None if the user backed out.

    Scrolls the same way run_list()/run_form() do -- no fixed menu here
    happens to overflow the standard 80x24 today, but this is the exact
    same "drawn at a fixed row with no bounds check" shape that made
    run_form() silently swallow anything past row 24 (real bug fixed
    2026-09-27), so it's fixed here too rather than left as a latent
    trap for the next menu that grows past ~20 items."""
    idx = 0
    top = 0
    stdscr.keypad(True)
    safe_curs_set(0)
    while True:
        h, _w = stdscr.getmaxyx()
        visible_h = max(1, (h - 2) - 2 + 1)   # rows 2..h-2, footer on h-1
        if idx < top:
            top = idx
        if idx >= top + visible_h:
            top = idx - visible_h + 1
        top = max(0, min(top, max(0, len(items) - visible_h)))

        stdscr.erase()
        draw_header(stdscr, title)
        for row_i, (_key, label) in enumerate(items[top: top + visible_h]):
            i = top + row_i
            attr = _attr(2, curses.A_REVERSE) if i == idx else 0
            _safe_addstr(stdscr, 2 + row_i, 4, label, attr)
        if len(items) > visible_h:
            _safe_addstr(stdscr, 0, max(0, _w - 12), f"[{idx + 1}/{len(items)}]",
                         _attr(1, curses.A_REVERSE))
        draw_footer(stdscr, footer)
        stdscr.refresh()
        ch = stdscr.getch()
        if ch in (curses.KEY_UP, ord("k")):
            idx = (idx - 1) % len(items)
        elif ch in (curses.KEY_DOWN, ord("j")):
            idx = (idx + 1) % len(items)
        elif ch in (10, 13, curses.KEY_ENTER):
            return items[idx][0]
        elif ch in (27, ord("q")):
            return None


def _edit_line(stdscr, y, x, width, initial=""):
    """Single-line text editor in-place. Enter confirms, Esc cancels
    (returns None, leaving the caller's prior value untouched)."""
    width = max(4, width)
    win = curses.newwin(1, width, y, x)
    win.erase()
    win.addstr(0, 0, initial[: width - 1])
    win.move(0, min(len(initial), width - 1))
    safe_curs_set(1)
    box = Textbox(win, insert_mode=True)
    cancelled = []

    def validator(ch):
        if ch in (10, 13, curses.KEY_ENTER):
            return 7  # Textbox's own stop character (Ctrl-G)
        if ch == 27:
            cancelled.append(True)
            return 7
        if ch in (curses.KEY_BACKSPACE, 127):
            return 8
        return ch

    box.edit(validator)
    safe_curs_set(0)
    if cancelled:
        return None
    return box.gather().strip()


# Sentinel "kind" for run_form()'s two on-screen action rows -- see
# their docstring note below for why these exist alongside F2/Esc.
_ACTION_SAVE = {"key": "__save__", "label": "[ Save ]", "kind": "action"}
_ACTION_CANCEL = {"key": "__cancel__", "label": "[ Cancel (discard changes) ]", "kind": "action"}


def run_form(stdscr, title, fields, values, help_lines=None,
             footer="[Up/Down] Field  [Enter] Edit/Activate  [Space] Toggle  "
                    "[Left/Right] Cycle  [F2] Save  [Esc] Cancel"):
    """fields: list of dicts, each with at minimum:
        {'key': 'name', 'label': 'Name', 'kind': 'text'}
    kind is one of: text, text_nullable, int, int_nullable, bool, choice
    (choice fields also need 'choices': [...]).

    values: dict of key -> current value.

    Returns the edited dict on save, or None if the user cancelled the
    whole form (Esc, or selecting Cancel, while not editing a field).

    F2 (save) and Esc (cancel) remain the fast path for terminals where
    they work, but they're both control/function-key sequences whose
    exact wire encoding varies by client and isn't always what this
    tool's terminfo expects -- real bug found live (2026-09-15,
    reported by a screen-reader user on an SSH client where F2
    apparently sent a bare ESC instead of a recognized F2 sequence,
    silently discarding ~3 hours of edits with no save and no warning).
    A keyboard-shortcut-only fix (e.g. adding Ctrl-Z) has the same
    fundamental problem -- it still depends on the terminal correctly
    delivering a specific byte, and Ctrl-Z specifically also collides
    with the terminal driver's own job-control SUSPEND character
    (confirmed live: it never reaches curses as a literal keystroke in
    this tool's current input mode at all, it triggers job-control
    processing instead). Explicit, always-focusable [Save]/[Cancel]
    rows at the end of every form sidestep the whole class of problem
    -- they only need the arrow keys + Enter, which every terminal
    already reliably sends and which this tool already depends on for
    every other kind of navigation.
    """
    data = dict(values)
    nav_items = list(fields) + [_ACTION_SAVE, _ACTION_CANCEL]
    idx = 0
    top = 0  # scroll offset, in CONTENT rows (not nav-item index)
    safe_curs_set(0)
    label_w = max(len(f["label"]) for f in fields) + 2

    def _build_rows(width):
        """Flattens fields + blank separators + Save/Cancel + help_lines
        into one linear content list, one entry per screen row. Real bug
        found live (2026-09-27, reported: editing/managing a door game
        made the lower fields and the Save button itself unreachable
        over SSH at the standard 80x24 -- this form has enough
        conditional fields, especially for door games, to run past row
        24 with no way to scroll to them at all): every OTHER screen in
        this module (run_list) already scrolls; this one drew every
        field/action/help row at a fixed absolute y with no bounds
        check against the real terminal height, so anything past the
        bottom just silently never rendered -- including Save itself.
        Returns (rows, nav_row_of) where nav_row_of maps a nav_items
        index (a field index, or len(fields)/len(fields)+1 for Save/
        Cancel) to its row index within `rows`, so the caller can keep
        whichever item is currently selected scrolled into view."""
        rows = []
        nav_row_of = {}
        for i in range(len(fields)):
            nav_row_of[i] = len(rows)
            rows.append(('field', i))
        rows.append(('blank',))
        for action_i in range(2):
            nav_row_of[len(fields) + action_i] = len(rows)
            rows.append(('action', action_i))
        if help_lines:
            rows.append(('blank',))
            help_width = max(10, width - 4)
            for line in help_lines:
                for wrapped_line in _wrap_help_line(line, help_width):
                    rows.append(('help', wrapped_line))
        return rows, nav_row_of

    while True:
        h, w = stdscr.getmaxyx()
        content_top = 2
        content_bottom = h - 2       # last row before the footer
        visible_h = max(1, content_bottom - content_top + 1)

        rows, nav_row_of = _build_rows(w)
        total = len(rows)
        sel_row = nav_row_of[idx]

        # Keep the selected nav item (field or Save/Cancel) scrolled
        # into view -- identical clamping logic to run_list()'s own
        # top/body_h handling just above, applied to content rows
        # instead of table rows.
        if sel_row < top:
            top = sel_row
        if sel_row >= top + visible_h:
            top = sel_row - visible_h + 1
        top = max(0, min(top, max(0, total - visible_h)))

        stdscr.erase()
        draw_header(stdscr, title)
        for screen_i in range(visible_h):
            row_i = top + screen_i
            if row_i >= total:
                break
            entry = rows[row_i]
            y = content_top + screen_i
            if entry[0] == 'blank':
                continue
            elif entry[0] == 'field':
                i = entry[1]
                f = fields[i]
                attr = _attr(2, curses.A_REVERSE) if i == idx else 0
                val = data.get(f["key"])
                if f["kind"] == "bool":
                    shown = "[X]" if val else "[ ]"
                elif val is None:
                    shown = "(none)"
                else:
                    shown = str(val)
                _safe_addstr(stdscr, y, 2, f["label"].ljust(label_w) + ": ", attr)
                _safe_addstr(stdscr, y, 2 + label_w + 2, shown, attr)
            elif entry[0] == 'action':
                action_i = entry[1]
                action = (_ACTION_SAVE, _ACTION_CANCEL)[action_i]
                nav_i = len(fields) + action_i
                attr = _attr(2, curses.A_REVERSE) if nav_i == idx else _attr(3, curses.A_BOLD)
                _safe_addstr(stdscr, y, 2, action["label"], attr)
            elif entry[0] == 'help':
                _safe_addstr(stdscr, y, 2, entry[1], _attr(3, curses.A_DIM))

        # Scroll position indicator -- the only hint (besides the arrow
        # keys just working) that there's more content above/below.
        if total > visible_h:
            _safe_addstr(stdscr, 0, max(0, w - 12), f"[{sel_row + 1}/{total}]",
                         _attr(1, curses.A_REVERSE))

        draw_footer(stdscr, footer)
        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (curses.KEY_UP,):
            idx = (idx - 1) % len(nav_items)
        elif ch in (curses.KEY_DOWN,):
            idx = (idx + 1) % len(nav_items)
        elif ch == 27:
            return None
        elif ch == curses.KEY_F2:
            return data
        elif idx >= len(fields):
            # On the [Save]/[Cancel] rows -- only Enter/Space activates.
            if ch in (ord(" "), 10, 13, curses.KEY_ENTER):
                return data if idx == len(fields) else None
        else:
            f = fields[idx]
            key, kind = f["key"], f["kind"]
            if kind == "bool" and ch in (ord(" "), 10, 13, curses.KEY_ENTER):
                data[key] = not data.get(key)
            elif kind == "choice" and ch in (curses.KEY_LEFT, curses.KEY_RIGHT):
                choices = f["choices"]
                cur = data.get(key)
                i = choices.index(cur) if cur in choices else 0
                i = (i + (1 if ch == curses.KEY_RIGHT else -1)) % len(choices)
                data[key] = choices[i]
            elif kind in ("text", "text_nullable", "int", "int_nullable") and \
                    ch in (10, 13, curses.KEY_ENTER):
                # On-screen row for the currently selected field, using
                # the SAME scroll offset just drawn with above -- not
                # the old fixed "2 + idx", which assumed no scrolling.
                fy = content_top + (sel_row - top)
                fx = 2 + label_w + 2
                fw = curses.COLS - fx - 2
                new = _edit_line(stdscr, fy, fx, fw, "" if data.get(key) is None else str(data.get(key)))
                if new is not None:
                    if kind in ("int", "int_nullable"):
                        if new == "" and kind == "int_nullable":
                            data[key] = None
                        else:
                            try:
                                data[key] = int(new)
                            except ValueError:
                                show_message(stdscr, f"{f['label']} must be a whole number.", error=True)
                    else:
                        data[key] = None if (kind == "text_nullable" and new == "") else new


def prompt_text(stdscr, prompt, initial=""):
    """One-off text prompt on the bottom status line -- e.g. a search box.
    Returns the entered string, or None if the user pressed Esc."""
    h, w = stdscr.getmaxyx()
    y = h - 2
    _safe_addstr(stdscr, y, 0, " " * max(0, w - 1), _attr(1, curses.A_REVERSE))
    _safe_addstr(stdscr, y, 2, prompt, _attr(1, curses.A_REVERSE))
    stdscr.refresh()
    x = 2 + len(prompt) + 1
    return _edit_line(stdscr, y, x, max(4, w - x - 1), initial)


def run_list(stdscr, title, columns, fetch_rows, on_add=None, on_edit=None,
             on_delete=None, on_reorder=None, extra_actions=None,
             empty_hint="(none yet -- press A to add one)"):
    """columns: list of (header, width, getter) where getter(row) -> str.
    fetch_rows(): called fresh every time the list redraws/reacts, so it
    always reflects the current DB state after an add/edit/delete.
    on_add(stdscr), on_edit(stdscr, row), on_delete(stdscr, row),
    on_reorder(stdscr, row, direction) where direction is -1 (up) or +1 (down)
    are all optional; omitting one just disables that hotkey.
    extra_actions: dict of single-char-str -> (hint_label, callback(stdscr, row)).
    """
    idx = 0
    top = 0
    safe_curs_set(0)
    hint_parts = []
    if on_add:
        hint_parts.append("[A]dd")
    if on_edit:
        hint_parts.append("[E]dit/Enter")
    if on_delete:
        hint_parts.append("[D]elete")
    if on_reorder:
        hint_parts.append("[+/-]Move")
    if extra_actions:
        hint_parts.extend(f"[{k.upper()}]{label}" for k, (label, _cb) in extra_actions.items())
    hint_parts.append("[Esc]Back")
    footer = "  ".join(hint_parts)

    while True:
        rows = fetch_rows()
        if idx >= len(rows):
            idx = max(0, len(rows) - 1)
        h, w = stdscr.getmaxyx()
        body_h = h - 4  # header + column row + footer + margin
        if idx < top:
            top = idx
        if idx >= top + body_h:
            top = idx - body_h + 1

        stdscr.erase()
        draw_header(stdscr, title)
        x = 2
        for header, width, _getter in columns:
            _safe_addstr(stdscr, 1, x, header.ljust(width), curses.A_BOLD)
            x += width + 1
        if not rows:
            _safe_addstr(stdscr, 3, 2, empty_hint, _attr(3, curses.A_DIM))
        for row_i, row in enumerate(rows[top: top + body_h]):
            y = 2 + row_i
            attr = _attr(2, curses.A_REVERSE) if (top + row_i) == idx else 0
            x = 2
            for _header, width, getter in columns:
                _safe_addstr(stdscr, y, x, str(getter(row)).ljust(width), attr)
                x += width + 1
        draw_footer(stdscr, footer)
        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            idx = min(max(0, len(rows) - 1), idx + 1)
        elif ch in (27, ord("q")):
            return
        elif ch in (ord("a"), ord("A")) and on_add:
            on_add(stdscr)
        elif ch in (10, 13, curses.KEY_ENTER, ord("e"), ord("E")) and on_edit and rows:
            on_edit(stdscr, rows[idx])
        elif ch in (ord("d"), ord("D")) and on_delete and rows:
            on_delete(stdscr, rows[idx])
        elif ch in (ord("+"), ord("=")) and on_reorder and rows:
            on_reorder(stdscr, rows[idx], -1)
        elif ch in (ord("-"), ord("_")) and on_reorder and rows:
            on_reorder(stdscr, rows[idx], 1)
        elif extra_actions and rows:
            for k, (_label, cb) in extra_actions.items():
                if ch in (ord(k.lower()), ord(k.upper())):
                    cb(stdscr, rows[idx])
                    break
