# anetbbs/features/fsxnet_ibol.py
"""fsxNet IBOL (InterBBS Oneliners) -- terminal feature.

Real wire-format cross-BBS oneliner wall, speaking the actual
Synchronet mod protocol over fsxNet's FSX_DAT echo area (see
anetbbs/echomail/fsxnet_sync.py for the wire format and attribution).
Deliberately separate from wall.py's own Graffiti Wall/ANET_WALL --
this reads/writes FsxnetOneliner rows, never WallPost.

Layout/behavior modeled directly on the real ibol.js (header box text
from its own shipped ibolhead.msg; the right-justified 20-char Author/
Source field pair + wrapped text + horizontal rule per entry; the
"(A) Add, (V) View All, (Q) Quit" prompt shown immediately below
whatever fits on screen, never hidden behind forced pagination; "View
All" as a true scrollable viewer, not a chunked pager) -- a real sysop
screenshot comparison (2026-10-07) found the first version of this
screen looked generic and buried the Add prompt behind a forced
20-line-at-a-time pager.
"""
from __future__ import annotations

import re

from .ansi_ui import ui_width, ui_height
from .bbs_ui import _app

_PIPE_FG = {
    '00': '30', '01': '34', '02': '32', '03': '36',
    '04': '31', '05': '35', '06': '33', '07': '37',
    '08': '1;30', '09': '1;34', '10': '1;32', '11': '1;36',
    '12': '1;31', '13': '1;35', '14': '1;33', '15': '1;37',
}
_PIPE_BG = {
    '16': '40', '17': '44', '18': '42', '19': '46',
    '20': '41', '21': '45', '22': '43', '23': '47',
}
_PIPE_RE = re.compile(r'\|(\d{2})')

# Same ANSI-injection/control-byte stripping discipline as wall.py's own
# _strip_untrusted() -- FsxnetOneliner rows are materialized straight
# from inbound FSX_DAT echomail bodies relayed by OTHER BBS software on
# the real fsxNet network (anetbbs/echomail/fsxnet_sync.py's
# sync_fsxnet_inbound), so a malicious/compromised peer could otherwise
# post a raw ANSI/CSI escape sequence that renders straight to every
# local caller's real terminal.
_INJECTED_ANSI_RE = re.compile(
    r'\x1b(?:\[[0-9;?]*[A-Za-z]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-Za-z]|[A-Za-z0-9=><~])')
# Excludes \x01 (SOH/Ctrl-A) from the stripped range -- _translate_ctrla()
# below needs to see it first. Every OTHER control byte (including raw
# ESC, 0x1b, already handled by _INJECTED_ANSI_RE above) is still
# stripped.
_CONTROL_RE = re.compile(r'[\x00\x02-\x1f\x7f]')

# Synchronet Ctrl-A (\x01<letter>) -> ANSI, ported from
# anetbbs/games/synchronet_compat.py's own _CTRLA_MAP (there as a JS
# string for the Node-compat door shim, unusable directly from Python --
# same table, re-expressed here). Defensive: real IBOL posts pipe codes
# on the wire (confirmed reading ibol.js -- getInputWithPreview() posts
# the raw typed string, pipe codes intact, converting to Ctrl-A only
# for Synchronet's own LOCAL display), but a different fsxNet client
# could conceivably post literal Ctrl-A bytes, so decode those too
# rather than leaving them as visible garbage.
_CTRLA_MAP = {
    'N': '\x1b[0m', 'H': '\x1b[1m', 'I': '\x1b[5m',
    'K': '\x1b[30m', 'R': '\x1b[31m', 'G': '\x1b[32m', 'Y': '\x1b[33m',
    'B': '\x1b[34m', 'M': '\x1b[35m', 'C': '\x1b[36m', 'W': '\x1b[37m',
    '0': '\x1b[40m', '1': '\x1b[44m', '2': '\x1b[42m', '3': '\x1b[46m',
    '4': '\x1b[41m', '5': '\x1b[45m', '6': '\x1b[43m', '7': '\x1b[47m',
    '[': '\r', ']': '\n',
}
_CTRLA_RE = re.compile('\x01(.)')

_GRY = '\x1b[1;30m'   # bold black -- renders as gray, matches Synchronet's \x01h\x01k
_CY = '\x1b[1;36m'
_CY_N = '\x1b[36m'
_WH = '\x1b[1;37m'
_YE = '\x1b[1;33m'
_MA = '\x1b[1;35m'
_GR = '\x1b[1;32m'
_RST = '\x1b[0m'


def _strip_untrusted(s: str) -> str:
    if not s:
        return ''
    return _CONTROL_RE.sub('', _INJECTED_ANSI_RE.sub('', s))


def _translate_ctrla(s: str) -> str:
    if not s or '\x01' not in s:
        return s
    return _CTRLA_RE.sub(lambda m: _CTRLA_MAP.get(m.group(1).upper(), ''), s)


def _pipe_to_ansi(s: str) -> str:
    if not s:
        return s
    s = _translate_ctrla(_strip_untrusted(s))
    if '|' not in s:
        return s
    def sub(m):
        c = m.group(1)
        sgr = _PIPE_FG.get(c) or _PIPE_BG.get(c)
        return f'\x1b[{sgr}m' if sgr else m.group(0)
    return _PIPE_RE.sub(sub, s)


def _visible_len(s: str) -> int:
    """Length ignoring ANSI SGR sequences -- for padding/wrap math."""
    return len(re.sub(r'\x1b\[[0-9;]*m', '', s))


def _header_box(width: int) -> str:
    """CP437 box-drawing header, modeled on the real ibol.js's own
    shipped ibolhead.msg (">>> InterBBS Oneliners <<<" in a bordered
    box) -- same visual language, re-expressed in plain ANSI rather
    than embedding the original Ctrl-A-coded file."""
    inner = width - 2
    title = '>>> InterBBS Oneliners <<<'
    pad_total = max(0, inner - len(title))
    left_pad = pad_total // 2
    right_pad = pad_total - left_pad
    return (
        f'{_GRY}+{"-" * inner}+{_RST}\r\n'
        f'{_GRY}|{_RST}{" " * left_pad}{_WH}>>> {_YE}InterBBS Oneliners {_WH}<<<{_RST}'
        f'{" " * right_pad}{_GRY}|{_RST}\r\n'
        f'{_GRY}+{"-" * inner}+{_RST}\r\n'
    )


def _word_wrap(text: str, width: int) -> list[str]:
    """Plain-text word-wrap (ignores ANSI/pipe codes for the width
    math, since those are translated to color, not visible chars) --
    mirrors ibol.js's own word-by-word wrap at a fixed column."""
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


def _format_entry(author: str, source_bbs: str, body: str, field_w: int, text_w: int) -> list[str]:
    """One oneliner formatted exactly like ibol.js's readmsgbase():
    author right-justified in a field_w-wide column (bold cyan),
    first line of wrapped text beside it; source BBS right-justified
    the same way on the next line (plain cyan), remaining wrapped
    text beside/under it; a horizontal rule after."""
    # Wrap on the pipe-code-stripped plain text so width math is right
    # (pipe codes become invisible color escapes, not display chars),
    # then colorize each resulting line's content afterward.
    plain_lines = _word_wrap(_strip_untrusted(body).replace('\n', ' '), text_w) if body.strip() else ['']
    out = []
    out.append(f'{" " * max(0, field_w - len(author))}{_CY}{author}{_GRY}: {_RST}{_WH}'
               f'{_pipe_to_ansi(plain_lines[0])}{_RST}')
    out.append(f'{" " * max(0, field_w - len(source_bbs))}{_CY_N}{source_bbs}{_GRY}: {_RST}{_WH}'
               f'{_pipe_to_ansi(plain_lines[1]) if len(plain_lines) > 1 else ""}{_RST}')
    for extra in plain_lines[2:]:
        out.append(f'{" " * field_w}{_GRY}: {_RST}{_WH}{_pipe_to_ansi(extra)}{_RST}')
    out.append(f'{_GRY}{"-" * (field_w + text_w + 2)}{_RST}')
    return out


async def _scroll_pager(session, lines, header_lines, hint_str):
    """Real scrollable text viewer -- up/down/pgup/pgdn/home/end with a
    percentage indicator, matching the real ibol.js's Scroller-module
    "View All" behavior (image reference: arrow-key scroll, [100%] at
    bottom-right). Ported from BBSMenuUI._rss_pager() in bbs_ui.py
    (self-contained copy, same per-file-fixture convention already
    used throughout this codebase -- that method is only ever
    self-called within bbs_ui.py, never designed for cross-module
    reuse)."""
    from .ansi_ui import FG
    EOL = '\x1b[K'
    hdr_rows = len(header_lines)
    rows = ui_height(session)
    body_visible = max(4, rows - hdr_rows - 3)
    body_start = 2 + hdr_rows
    sep_row = body_start + body_visible
    hint_row = sep_row + 1

    offset = max(0, len(lines) - body_visible)  # start at the bottom (newest)

    async def _draw():
        await session.write('\x1b[2J\x1b[H')
        for r, hl in enumerate(header_lines, 2):
            await session.write(f'\x1b[{r};1H{hl}{EOL}')
        for i in range(body_visible):
            li = offset + i
            txt = lines[li] if li < len(lines) else ''
            await session.write(f'\x1b[{body_start + i};1H{txt}{EOL}')
        pct = ''
        if lines:
            pct_val = min(100, int((offset + body_visible) * 100 / len(lines)))
            pct = f'{FG["gry"]} [{pct_val}%]{_RST}'
        await session.write(f'\x1b[{sep_row};1H{_GRY}{"-" * ui_width(session)}{_RST}{EOL}')
        await session.write(f'\x1b[{hint_row};1H{hint_str}{pct}{EOL}')

    await _draw()
    while True:
        key = await session.read_key_arrow()
        max_offset = max(0, len(lines) - body_visible)
        if key == 'UP' and offset > 0:
            offset -= 1; await _draw()
        elif key == 'DOWN' and offset < max_offset:
            offset += 1; await _draw()
        elif key == 'PGUP' and offset > 0:
            offset = max(0, offset - body_visible); await _draw()
        elif key == 'PGDN' and offset < max_offset:
            offset = min(max_offset, offset + body_visible); await _draw()
        elif key == 'HOME' and offset != 0:
            offset = 0; await _draw()
        elif key == 'END' and offset != max_offset:
            offset = max_offset; await _draw()
        elif key in ('Q', 'ENTER', 'ESC', 'CTRL_C'):
            return


async def show_oneliner_wall(session, args=None) -> None:
    """Entry point -- matches wall.show_wall(session, ...)'s own
    signature convention."""
    width = ui_width(session)
    field_w = 20
    text_w = max(20, width - field_w - 2)

    while True:
        with _app().app_context():
            from flask import current_app
            from ..models import FsxnetOneliner
            rows = (FsxnetOneliner.query
                    .order_by(FsxnetOneliner.created_at.desc())
                    .limit(200).all())
            ibol_enabled = current_app.config.get('FSXNET_IBOL_ENABLED', False)

        await session.write('\x1b[2J\x1b[H')
        header = _header_box(width)
        await session.write(header)

        if not ibol_enabled:
            await session.write(
                "  IBOL is not enabled on this BBS. Ask your sysop to turn it on\r\n"
                "  at Admin -> Messages -> fsxNet IBOL/IBLC.\r\n")
            await session.read_line("\r\nPress Enter...")
            return

        rows = list(reversed(rows))  # oldest-first, newest at the bottom
        if not rows:
            await session.write(f"\r\n{_GRY}  (nothing posted yet){_RST}\r\n\r\n")
        else:
            # Render every entry, then show only the LAST screenful --
            # same "tail of the wall" behavior as the real ibol.js.
            all_lines = []
            for row in rows:
                all_lines.extend(_format_entry(row.author, row.source_bbs, row.body, field_w, text_w))
            avail = max(4, ui_height(session) - 6)
            shown = all_lines[-avail:]
            for line in shown:
                await session.write(line + '\r\n')
            await session.write('\r\n')

        prompt = (f'{_WH}({_YE}A{_WH}) {_MA}Add, {_WH}({_YE}V{_WH}) {_MA}View All, '
                 f'{_WH}({_YE}Q{_WH}) {_MA}Quit {_RST}')
        choice = (await session.read_line(prompt) or '').strip().upper()
        if choice == 'A':
            await _post_oneliner(session)
        elif choice == 'V':
            await _view_all(session, rows, header, field_w, text_w)
        else:
            return


async def _view_all(session, rows, header, field_w, text_w) -> None:
    all_lines = []
    for row in rows:
        all_lines.extend(_format_entry(row.author, row.source_bbs, row.body, field_w, text_w))
    if not all_lines:
        return
    header_lines = header.rstrip('\r\n').split('\r\n')
    hint = f'{_WH}Up/Dn PgUp/PgDn/Home/End {_GRY}scroll, {_WH}Q{_GRY} quit'
    await _scroll_pager(session, all_lines, header_lines, hint)


async def _post_oneliner(session) -> None:
    await session.write(
        f'\r\n{_CY}Post a oneliner{_RST} (up to 10 lines, blank line to finish):\r\n')
    lines = []
    for i in range(10):
        line = (await session.read_line(f'Line {i + 1}: ') or '').strip()
        if not line:
            break
        lines.append(line)
    if not lines:
        return

    await session.write(f'\r\n{_YE}Preview:{_RST}\r\n')
    for line in lines:
        await session.write(f'  {_pipe_to_ansi(line)}{_RST}\r\n')
    confirm = (await session.read_line('\r\nPost this? (y/N): ') or '').strip().lower()
    if confirm != 'y':
        await session.write('Cancelled.\r\n')
        return

    user = getattr(session, 'user', None) or {}
    alias = user.get('username', '?') if isinstance(user, dict) else getattr(user, 'username', '?')
    with _app().app_context():
        from ..echomail.fsxnet_sync import post_oneliner_to_fsxnet
        post_oneliner_to_fsxnet(lines, alias)
    await session.write(f'{_GR}Posted!{_RST}\r\n')
    await session.read_line('Press Enter...')
