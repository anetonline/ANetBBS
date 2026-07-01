"""Graffiti Wall — terminal feature.

Displays a paginated, pipe-color-aware wall of short messages.
Box width adapts to the terminal: 79 chars on 80-col, up to 131 cols on wide.
ASCII mode substitutes plain box chars for CP437 box-drawing characters.

Pipe color reference (Synchronet/Mystic):
  |00 black  |01 blue    |02 green  |03 cyan
  |04 red    |05 magenta |06 yellow |07 white
  |08 dk-grey |09 br-blue |10 br-green |11 br-cyan
  |12 br-red |13 br-magenta |14 br-yellow |15 bright white
  |16-|23 background colours
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

_PIPE_FG = {
    '00': '30', '01': '34', '02': '32', '03': '36',
    '04': '31', '05': '35', '06': '33', '07': '37',
    '08': '90', '09': '94', '10': '92', '11': '96',
    '12': '91', '13': '95', '14': '93', '15': '97',
}
_PIPE_BG = {
    '16': '40', '17': '44', '18': '42', '19': '46',
    '20': '41', '21': '45', '22': '43', '23': '47',
}
_PIPE_RE = re.compile(r'\|(\d{2})')
_PIPE_STRIP_RE = re.compile(r'\|\d{2}')
_ANSI_ESC_RE = re.compile(r'\x1b\[[0-9;]*[mABCDEFGHJKSTfh]')

POSTS_PER_PAGE = 4   # 3 header + 4×4 posts + 3 footer = 22 lines max (fits 23)

_RST  = '\x1b[0m'
_BOLD = '\x1b[1m'
_DIM  = '\x1b[2m'
_CY   = '\x1b[96m'
_YE   = '\x1b[93m'
_GR   = '\x1b[92m'
_RD   = '\x1b[91m'
_WH   = '\x1b[97m'
_BLU  = '\x1b[94m'
_MAG  = '\x1b[95m'

# Preset color schemes: (border, title, username, hotkey)
WALL_SCHEMES = {
    'cyan':    (_CY,  _WH,  _GR,  _YE),
    'green':   (_GR,  _YE,  _WH,  _YE),
    'red':     (_RD,  _WH,  _YE,  _RD),
    'blue':    (_BLU, _WH,  _CY,  _YE),
    'amber':   (_YE,  _WH,  _WH,  _YE),
    'magenta': (_MAG, _WH,  _YE,  _MAG),
}
WALL_SCHEME_LABELS = {
    'cyan':    'Cyan (default)',
    'green':   'Green',
    'red':     'Red',
    'blue':    'Blue',
    'amber':   'Amber / Gold',
    'magenta': 'Magenta / Purple',
}


def _get_colors():
    """Return (border, title, user, key) for the configured color scheme."""
    import os
    scheme = 'cyan'
    try:
        env_path = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', '..', '.env'))
        with open(env_path) as _ef:
            for _ln in _ef:
                if _ln.startswith('WALL_COLOR_SCHEME='):
                    scheme = _ln.split('=', 1)[1].strip()
                    break
    except Exception:
        pass
    return WALL_SCHEMES.get(scheme, WALL_SCHEMES['cyan'])


def _wall_dims(session) -> tuple[int, bool]:
    """Return (inner_width, ascii_mode) for this session.

    inner_width: chars inside the border (box total = inner + 2, max 131).
    ascii_mode:  True when terminal can't render CP437 box-drawing chars.
    """
    from .ansi_ui import ui_width
    t = (getattr(session, 'term_type', '') or '').lower()
    ascii_mode = (t == 'ascii')
    # ui_width caps at 131; subtract 2 for the ╔ and ╗ border chars
    inner = ui_width(session) - 2
    return inner, ascii_mode


def _pipe_to_ansi(s: str) -> str:
    if not s or '|' not in s:
        return s
    def sub(m):
        c = m.group(1)
        sgr = _PIPE_FG.get(c) or _PIPE_BG.get(c)
        return f'\x1b[{sgr}m' if sgr else m.group(0)
    return _PIPE_RE.sub(sub, s)


def _strip_pipes(s: str) -> str:
    return _PIPE_STRIP_RE.sub('', s) if s else ''


def _visible_len(s: str) -> int:
    return len(_strip_pipes(s))


def _truncate_pipes(s: str, max_vis: int) -> str:
    if not s:
        return ''
    out = []
    vis = 0
    i = 0
    while i < len(s):
        if s[i] == '|' and i + 2 < len(s) and s[i+1:i+3].isdigit():
            out.append(s[i:i+3])
            i += 3
            continue
        if vis >= max_vis:
            break
        out.append(s[i])
        vis += 1
        i += 1
    return ''.join(out)


def _enc(s: str) -> bytes:
    return s.encode('cp437', errors='replace')


def _header(page: int, total: int, border: str, title_c: str,
            W: int, ascii_mode: bool) -> bytes:
    pages  = max(1, (total + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE)
    plural = 's' if total != 1 else ''
    if ascii_mode:
        hbar  = '-' * W
        tag   = f' [{total} post{plural} - Page {page}/{pages}] '
        title = ' *** GRAFFITI  WALL *** '
        pad   = W - len(title) - len(tag)
        inner = title + ' ' * max(0, pad) + tag
        top   = f'+{hbar}+\r\n'
        mid   = f'|{inner[:W]}|\r\n'
        bot   = f'+{hbar}+\r\n'
    else:
        hbar  = '═' * W
        tag   = f' [{total} post{plural} · Page {page}/{pages}] '
        title = ' ░▒▓  GRAFFITI  WALL  ▓▒░ '
        pad   = W - len(title) - len(tag)
        inner = title + ' ' * max(0, pad) + tag
        top   = f'{_BOLD}{border}╔{hbar}╗{_RST}\r\n'
        mid   = f'{_BOLD}{border}║{title_c}{inner[:W]}{border}║{_RST}\r\n'
        bot   = f'{_BOLD}{border}╚{hbar}╝{_RST}\r\n'
    return _enc(top + mid + bot)


def _footer(is_admin: bool, has_older: bool, has_newer: bool,
            border: str, key_c: str,
            W: int, ascii_mode: bool) -> bytes:
    if ascii_mode:
        hbar = '-' * W
        opts = []
        if has_newer: opts.append('[N]ewer')
        if has_older: opts.append('[O]lder')
        opts.append('[W]rite')
        if is_admin: opts.append('[D]el')
        opts.append('[Q]uit')
        line = '  ' + '  '.join(opts)
        pad  = ' ' * max(0, W - len(line))
        top  = f'+{hbar}+\r\n'
        nav  = f'|{line}{pad}|\r\n'
        bot  = f'+{hbar}+\r\n'
    else:
        hbar = '═' * W
        opts = []
        if has_newer: opts.append(f'{_BOLD}{key_c}[N]{_RST}ewer')
        if has_older: opts.append(f'{_BOLD}{key_c}[O]{_RST}lder')
        opts.append(f'{_BOLD}{key_c}[W]{_RST}rite')
        if is_admin: opts.append(f'{_BOLD}{_RD}[D]{_RST}el')
        opts.append(f'{_BOLD}{key_c}[Q]{_RST}uit')
        line = '  ' + '  '.join(opts)
        vis  = len(_ANSI_ESC_RE.sub('', line))
        pad  = ' ' * max(0, W - vis)
        top  = f'{_BOLD}{border}╔{hbar}╗{_RST}\r\n'
        nav  = f'{border}║{_RST}{line}{pad}{_BOLD}{border}║{_RST}\r\n'
        bot  = f'{_BOLD}{border}╚{hbar}╝{_RST}\r\n'
    return _enc(top + nav + bot)


def _render_post(post, is_admin: bool, user_c: str,
                 W: int, ascii_mode: bool) -> bytes:
    ts       = post.created_at.strftime('%m/%d/%y %H:%M') if post.created_at else ''
    uname    = (post.display_name or post.username or '?')[:16]
    node_str = f'Node {post.node}' if post.node else ''
    del_tag  = f'  {_RD}[#{post.id}]{_RST}' if is_admin else ''
    sep_char = '-' if ascii_mode else '─'
    sep_bar  = sep_char * (W - 2)
    header   = (
        f' {_BOLD}{user_c}{uname:<16}{_RST}  '
        f'{_DIM}{ts}  {node_str}{_RST}{del_tag}\r\n'
    )
    if ascii_mode:
        line1 = ' ' + _strip_pipes(post.line1 or '') + '\r\n'
        line2 = (' ' + _strip_pipes(post.line2) + '\r\n') if post.line2 else ''
    else:
        line1 = ' ' + _pipe_to_ansi(post.line1 or '') + _RST + '\r\n'
        line2 = (' ' + _pipe_to_ansi(post.line2) + _RST + '\r\n') if post.line2 else ''
    sep = f' {_DIM}{sep_bar}{_RST}\r\n'
    return _enc(header + line1 + line2 + sep)


async def show_wall(session, allow_post: bool = True) -> None:
    from ..features.bbs_ui import _app
    from ..models import WallPost

    is_admin = bool((session.user or {}).get('is_admin'))
    page = 1

    while True:
        border, title_c, user_c, key_c = _get_colors()
        W, ascii_mode = _wall_dims(session)

        app = _app()
        with app.app_context():
            total  = WallPost.query.filter_by(is_deleted=False).count()
            offset = (page - 1) * POSTS_PER_PAGE
            posts  = (WallPost.query
                      .filter_by(is_deleted=False)
                      .order_by(WallPost.created_at.desc())
                      .offset(offset)
                      .limit(POSTS_PER_PAGE)
                      .all())

        pages     = max(1, (total + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE)
        has_older = page < pages
        has_newer = page > 1

        session.writer.write(b'\x1b[2J\x1b[H')
        session.writer.write(_header(page, total, border, title_c, W, ascii_mode))
        if not posts:
            session.writer.write(
                _enc(f'\r\n  {_DIM}No posts yet \x14 be the first to write on the wall!{_RST}\r\n\r\n'))
        else:
            for post in posts:
                session.writer.write(_render_post(post, is_admin, user_c, W, ascii_mode))
        session.writer.write(_footer(is_admin, has_older, has_newer, border, key_c, W, ascii_mode))
        await session.writer.drain()

        prompt = f'{_WH}Wall>{_RST} '
        choice = (await session.read_line(prompt) or '').strip().lower()

        if choice in ('q', ''):
            break
        elif choice == 'n' and has_newer:
            page -= 1
        elif choice == 'o' and has_older:
            page += 1
        elif choice == 'w' and allow_post:
            await _post_to_wall(session, border, key_c, W, ascii_mode)
        elif choice == 'd' and is_admin:
            await _admin_delete(session)

    try:
        session.writer.write(b'\x1b[0m')
        await session.writer.drain()
    except Exception:
        pass


async def _post_to_wall(session, border: str, key_c: str,
                        W: int, ascii_mode: bool) -> None:
    from ..features.bbs_ui import _app
    from ..models import db, WallPost

    if ascii_mode:
        wbar  = '-' * W
        inner = '  Write on the Wall  (pipe colors: |12red |14yellow |15white)'
        pad   = ' ' * max(0, W - len(inner))
        session.writer.write(_enc(
            f'\r\n+{wbar}+\r\n'
            f'|{_WH}{inner}{pad}|\r\n'
            f'+{wbar}+\r\n'
            f'{_DIM}Max 2 lines x {W} chars. Leave blank to cancel.{_RST}\r\n\r\n'
        ))
    else:
        wbar  = '═' * W
        inner = '  Write on the Wall  (pipe colors: |12red |14yellow |15white)'
        pad   = ' ' * max(0, W - len(inner))
        session.writer.write(_enc(
            f'\r\n{_BOLD}{border}╔{wbar}╗{_RST}\r\n'
            f'{border}║{_WH}{inner}{pad}{_BOLD}{border}║{_RST}\r\n'
            f'{_BOLD}{border}╚{wbar}╝{_RST}\r\n'
            f'{_DIM}Max 2 lines x {W} chars. Leave blank to cancel.{_RST}\r\n\r\n'
        ))
    await session.writer.drain()

    raw1 = (await session.read_line(f'{key_c}Line 1:{_RST} ') or '').rstrip('\r\n')
    if not raw1.strip():
        session.writer.write(_enc(f'{_DIM}Cancelled.{_RST}\r\n'))
        await session.writer.drain()
        await asyncio.sleep(0.8)
        return

    raw1 = _truncate_pipes(raw1, W)

    raw2 = (await session.read_line(f'{key_c}Line 2 (Enter to skip):{_RST} ') or '').rstrip('\r\n')
    raw2 = _truncate_pipes(raw2, W) if raw2.strip() else None

    user = session.user or {}
    app  = _app()
    with app.app_context():
        post = WallPost(
            username=user.get('username', 'unknown'),
            display_name=user.get('display_name') or user.get('username', ''),
            line1=raw1,
            line2=raw2,
            node=getattr(getattr(session, '_node_entry', None), 'slot', 1),
        )
        db.session.add(post)
        db.session.commit()

    session.writer.write(_enc(f'\r\n{_GR}Posted!{_RST}\r\n'))
    await session.writer.drain()
    await asyncio.sleep(0.8)


async def _admin_delete(session) -> None:
    from ..features.bbs_ui import _app
    from ..models import db, WallPost

    raw = (await session.read_line(f'{_RD}Delete post #{_RST} ') or '').strip()
    if not raw.isdigit():
        return
    post_id = int(raw)
    app = _app()
    with app.app_context():
        post = WallPost.query.get(post_id)
        if post and not post.is_deleted:
            post.is_deleted = True
            db.session.commit()
            session.writer.write(_enc(f'{_RD}Post #{post_id} deleted.{_RST}\r\n'))
        else:
            session.writer.write(_enc(f'{_DIM}Post not found.{_RST}\r\n'))
        await session.writer.drain()
    await asyncio.sleep(0.8)
