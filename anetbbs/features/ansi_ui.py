# anetbbs/features/ansi_ui.py
"""
Shared ANSI rendering helpers used by sub-menus and prompts in the
telnet/SSH/rlogin client. Keeps the look-and-feel consistent without each
sub-system having to duplicate the colour codes and box-drawing.
"""

# ANSI escape codes
RESET = '\x1b[0m'
BOLD  = '\x1b[1m'

FG = {
    # Bright colors are bold + base color (\x1b[1;3Xm), the classic
    # ANSI.SYS/BBS convention -- not the bare aixterm 90-97 range.
    # MagiTerm, NetRunner, and PuTTY (in ANSI-BBS mode) don't recognize
    # 90-97 and silently drop it (no color shown at all); SyncTerm
    # happens to support both, which is why this bug only showed up on
    # the other clients. The shipped .ans art files (e.g. screens/menus/
    # main.ans) already use bold+3X and render fine everywhere -- this
    # matches that same convention.
    'cyan':  '\x1b[1;36m', 'yel': '\x1b[1;33m', 'grn': '\x1b[1;32m',
    'wht':   '\x1b[1;37m', 'dim': '\x1b[37m',   'red': '\x1b[1;31m',
    'blu':   '\x1b[1;34m', 'mag': '\x1b[1;35m', 'gry': '\x1b[1;30m',
}
BG = {
    'blu': '\x1b[44m', 'cya': '\x1b[46m', 'red': '\x1b[41m',
    'blk': '\x1b[40m',
}


def banner(title, width=64):
    """Top banner with ▄▄ / centred title with cyan inverse / ▀▀ underline.

    Returns the multi-line string ready to be sent to a session."""
    top = '▄' * width
    bot = '▀' * width
    cell = f'╣ {title.upper()} ╠'.center(width)
    return (f"\r\n{FG['cyan']}{BG['blk']}{top}{RESET}\r\n"
            f"{BG['cya']}{FG['wht']}{BOLD}{cell}{RESET}\r\n"
            f"{FG['cyan']}{BG['blk']}{bot}{RESET}\r\n")


def menu_item(hotkey, label, width=64):
    """Single menu item: yellow [hotkey] + green label."""
    line = f"  [{hotkey}] {label}".ljust(width)
    return (f"{FG['grn']}" +
            line.replace(f'[{hotkey}]',
                         f'{FG["yel"]}{BOLD}[{hotkey}]{RESET}{FG["grn"]}', 1) +
            RESET)


def menu_items_two_col(items, width=64):
    """Render a list of (hotkey, label) tuples in 2 columns. Returns lines."""
    cols = 2
    col_w = (width // cols) - 2
    out = []
    padded = list(items)
    if len(padded) % cols:
        padded += [None] * (cols - len(padded) % cols)
    for i in range(0, len(padded), cols):
        pieces = []
        for j in range(cols):
            it = padded[i + j]
            if it is None:
                pieces.append(' ' * col_w)
                continue
            hk, lbl = it
            text = f"  [{hk}] {lbl}"
            if len(text) > col_w:
                text = text[:col_w - 1] + '>'
            text = text.ljust(col_w)
            text = text.replace(f'[{hk}]',
                                f'{FG["yel"]}{BOLD}[{hk}]{RESET}{FG["grn"]}', 1)
            pieces.append(f'{FG["grn"]}{text}{RESET}')
        out.append(''.join(pieces))
    return out


def footer(width=64):
    return f"{FG['gry']}{'─' * width}{RESET}"


def prompt(text='Choice: '):
    return f"\r\n{FG['cyan']}{BOLD}{text}{RESET}"


def ui_width(session) -> int:
    """Return usable display width for the session (terminal cols - 1).

    Clamped to [64, 131] so callers never need to guard against tiny or
    absurdly wide terminals.  Use this instead of hardcoding 64 so every
    screen automatically expands on 132-col (and other wide) terminals.
    """
    cols = getattr(session, 'window_size', (80, 24))[0]
    return max(64, min(131, cols - 1))


def load_menu_ansi(slot: str, mode: str = 'ansi'):
    """Return raw bytes for a menu art file, or None if nothing is found.

    Lookup order (mode-aware):
      wide : data/text/menus/{slot}132.ans → {slot}.ans
      ansi : data/text/menus/{slot}.ans
      ascii: data/text/menus/{slot}.asc   → {slot}.ans
    Each candidate is also tried under anetbbs/screens/menus/ as a bundled
    stock fallback before giving up.
    """
    from pathlib import Path
    data_dir   = Path(__file__).resolve().parent.parent.parent / 'data'
    menus_dir  = data_dir / 'text' / 'menus'
    stock_dir  = Path(__file__).resolve().parent.parent / 'screens' / 'menus'

    if mode == 'wide':
        candidates = [f'{slot}132.ans', f'{slot}.ans']
    elif mode == 'ascii':
        candidates = [f'{slot}.asc', f'{slot}.ans']
    else:
        candidates = [f'{slot}.ans']

    for fname in candidates:
        for directory in (menus_dir, stock_dir):
            f = directory / fname
            try:
                if f.is_file():
                    return f.read_bytes()
            except OSError:
                pass
    return None


async def write_menu_art(session, slot: str) -> bool:
    """Load menu art, apply display @-codes, and write it to the session.

    Returns True if art was found and written; callers fall back to their
    inline banner/menu_item code when False is returned.
    """
    ansi = load_menu_ansi(slot, session.term_mode)
    if ansi is None:
        return False
    try:
        from .display_codes import apply as _apply_codes
        from .bbs_ui import _app as _bbs_app
        import anetbbs as _anetbbs_pkg
        _cfg = _bbs_app().config
        body = _apply_codes(
            ansi.decode('latin-1'),
            user=session.user,
            bbs_name=_cfg.get('BBS_NAME', ''),
            sysop=_cfg.get('SYSOP_NAME', ''),
            node=(getattr(session, '_node_entry', None).slot
                  if getattr(session, '_node_entry', None) else 1),
            version=getattr(_anetbbs_pkg, '__version__', 'v1.0a'),
        )
        session.writer.write(b'\x1b[2J\x1b[H' + body.encode('latin-1'))
    except Exception:
        session.writer.write(b'\x1b[2J\x1b[H' + ansi)
    await session.writer.drain()
    return True
