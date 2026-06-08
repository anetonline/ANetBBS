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
    'cyan':  '\x1b[96m', 'yel': '\x1b[93m', 'grn': '\x1b[92m',
    'wht':   '\x1b[97m', 'dim': '\x1b[37m', 'red': '\x1b[91m',
    'blu':   '\x1b[94m', 'mag': '\x1b[95m', 'gry': '\x1b[90m',
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


def load_menu_ansi(slot: str):
    """Return raw bytes from data/text/menus/<slot>.ans, or None if absent.

    Call at the top of each menu loop. If bytes are returned, write them
    directly via session.writer.write()/drain() instead of banner() so the
    original CP437 block characters reach the terminal unmodified.
    """
    from pathlib import Path
    # DATA_DIR is never written to .env — derive it from __file__ the same
    # way config.py does: BASE_DIR = Path(__file__).parent.parent (install root).
    # ansi_ui.py lives at <install>/anetbbs/features/ansi_ui.py so go up 3 levels.
    data_dir = Path(__file__).resolve().parent.parent.parent / 'data'
    path = data_dir / 'text' / 'menus' / f'{slot}.ans'
    try:
        if path.is_file():
            return path.read_bytes()
    except OSError:
        pass
    return None
