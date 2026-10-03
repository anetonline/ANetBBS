# anetbbs/features/enhanced_protocol.py
"""
JSON message protocol for the ANetBBS Enhanced Client (browser-based,
Canvas + WebSocket -- see anetbbs/core/enhanced_server.py and
anetbbs/core/session.py's 'enhanced' term_mode branch in write()).

TEST VERSION ONLY: part of a feature that is disabled by default
(Config.ENHANCED_ENABLED), not yet pushed to GitHub or deployed to the
live install, per Jerry's explicit ask -- see the "ANetBBS Enhanced
Client" plan.

Plain JSON text frames, one complete message per WebSocket TEXT frame,
chosen for test-version debuggability (readable in browser devtools)
over a tighter binary format.

Server -> client: clear() / text() / menu() / cursor() / screen().
Client -> server: decode_client_message() turns one incoming frame into
the raw keystroke byte(s) it represents. This is the whole reason
menu_engine.py's existing hotkey-dispatch loop needed zero changes to
support mouse clicks: a "click" message is synthesized server-side,
before it ever reaches that code, into the exact same byte a real
keypress on that hotkey would have produced. The client never needs to
know what a hotkey "means" -- it just draws a button and reports a
click.
"""
import json


def encode_clear():
    return json.dumps({'type': 'clear'})


def encode_text(s, x=None, y=None, fg=None, bg=None):
    msg = {'type': 'text', 's': s}
    if x is not None:
        msg['x'] = x
    if y is not None:
        msg['y'] = y
    if fg is not None:
        msg['fg'] = fg
    if bg is not None:
        msg['bg'] = bg
    return json.dumps(msg)


def encode_menu(title, items):
    """items: an iterable of (hotkey, label, action_type, action_args)
    4-tuples -- the exact shape menu_engine.py's own item_list already
    builds from BbsMenuItem rows, so the common call site needs no
    reshaping beyond passing that list straight through. A click
    synthesizes `hotkey` as-is, matching a single real keypress --
    correct for every menu dispatched via read_key()/read_key_arrow()
    (one keystroke selects immediately).

    Also accepts (hotkey, label, action_type, action_args, send)
    5-tuples for menus dispatched via read_line() instead (e.g.
    games.py's door-menu category list, which accepts a multi-digit
    choice like "16" and waits for Enter to submit it) -- `send` is
    the raw text actually synthesized on click (e.g. "16\\r"),
    independent of `hotkey`, which stays the clean label shown on the
    button (e.g. "16", no visible control character). Defaults to
    `hotkey` itself when not given, so every existing single-keystroke
    caller is unaffected."""
    out_items = []
    for it in items:
        if len(it) >= 5:
            hk, lbl, _, _, send = it[0], it[1], it[2], it[3], it[4]
        else:
            hk, lbl, _, _ = it
            send = hk
        out_items.append({'hotkey': hk, 'label': lbl, 'send': send})
    return json.dumps({'type': 'menu', 'title': title, 'items': out_items})


def encode_cursor(x, y):
    return json.dumps({'type': 'cursor', 'x': x, 'y': y})


def encode_screen(cells, max_row, width=80, height=25):
    """A real colored ANSI-art screen (welcome banner, etc.) as a grid
    of per-row runs. `cells`/`max_row` are exactly what
    anetbbs.features.ansi_html._run_vt() already returns for any ANSI
    text -- that module already solves "parse ANSI cursor-positioning
    and SGR color codes into a 2-D cell grid" for the HTML ANSI-editor
    preview, so this reuses it instead of writing a second parser.
    `cells[(row,col)] = (char, fg_hex, bg_hex_or_None, bold_bool)`.

    session.py's _enhanced_feed() now calls this with a PERSISTENT,
    ever-growing cells dict (the whole point of carrying _run_vt()
    state across write() calls) -- `max_row` can keep climbing over a
    long session even with DECSTBM scroll-region support (anything
    printed outside an active scroll region, or before one is ever
    set, still just grows cur_row). Windowed to the last `height` rows
    (matching the client's fixed-height Canvas, COLS=80/ROWS=25 in
    anetbbs/enhanced_client/index.html) and remapped to 0-based row
    indices, so the payload stays bounded and the client always draws
    what's "on screen" at the bottom of the buffer -- a reasonable
    approximation of real terminal scrolling for content that grows
    past the scroll region (or has none set at all).

    Compacts consecutive same-style characters on each row into runs
    (not one message per character) and drops runs that are purely
    blank space with no background fill, since those render identically
    to an untouched canvas -- keeps a full-screen art payload a
    reasonable size instead of one JSON object per of 80x24 cells.
    """
    from .ansi_html import _bold_fg

    def _run_dict(col, text, fg, bg):
        d = {'col': col, 'text': text, 'fg': fg}
        if bg:
            d['bg'] = bg
        return d

    start_row = max(0, max_row - height + 1)
    rows = []
    for r in range(start_row, max_row + 1):
        runs = []
        cur_col = cur_text = cur_fg = cur_bg = None
        for c in range(width):
            cell = cells.get((r, c))
            if cell is None:
                ch, fg, bg = ' ', '#aaaaaa', None
            else:
                char, cfg, cbg, cb = cell
                ch, fg, bg = char, _bold_fg(cfg, cb), cbg
            if cur_text is not None and fg == cur_fg and bg == cur_bg:
                cur_text += ch
            else:
                if cur_text is not None and (cur_text.strip() or cur_bg):
                    runs.append(_run_dict(cur_col, cur_text, cur_fg, cur_bg))
                cur_col, cur_text, cur_fg, cur_bg = c, ch, fg, bg
        if cur_text is not None and (cur_text.strip() or cur_bg):
            runs.append(_run_dict(cur_col, cur_text, cur_fg, cur_bg))
        if runs:
            rows.append({'row': r - start_row, 'runs': runs})
    return json.dumps({'type': 'screen', 'rows': rows})


def decode_client_message(raw):
    """Parse one incoming client->server JSON text frame into the raw
    byte(s) it should synthesize into the session's input stream.
    Everything downstream of this (read_key(), menu hotkey dispatch,
    read_line() for typed text/passwords) stays completely unchanged --
    exactly like a real keypress arriving from any other terminal
    client.

    Returns b'' for anything unrecognized, malformed, or empty -- a
    stray or out-of-order client message is silently ignored, not an
    error that should end the session."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return b''
    if not isinstance(msg, dict):
        return b''
    mtype = msg.get('type')
    if mtype == 'key':
        ch = msg.get('ch', '')
        if not isinstance(ch, str) or not ch:
            return b''
        return ch.encode('utf-8', errors='replace')
    if mtype == 'click':
        hotkey = msg.get('hotkey', '')
        if not isinstance(hotkey, str) or not hotkey:
            return b''
        return hotkey.encode('utf-8', errors='replace')
    return b''
