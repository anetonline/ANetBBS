# anetbbs/features/petscii_theme.py
"""
Small color-composition helpers for petscii_ui.py's plain-text menu
screens -- the PETSCII counterpart to the ANSI side's ansi_ui.py (FG/
BG dicts, banner()/menu_item()/footer()). Built directly on
petscii_codec's existing 16 COLOR_* constants and REVERSE_ON/REVERSE_OFF
-- no new byte values, nothing beyond what ansi_to_petscii() already
uses internally for the same reason.

PETSCII has no single "reset to default" byte the way ANSI SGR has
code 0 -- resetting means re-emitting whatever the body-text color is
(COLOR_WHITE here), same as ansi_to_petscii() itself already does.
"""
from .petscii_codec import (
    REVERSE_ON, REVERSE_OFF,
    COLOR_BLACK, COLOR_WHITE, COLOR_RED, COLOR_CYAN, COLOR_PURPLE,
    COLOR_GREEN, COLOR_BLUE, COLOR_YELLOW, COLOR_ORANGE, COLOR_BROWN,
    COLOR_LIGHT_RED, COLOR_DARK_GREY, COLOR_GREY, COLOR_LIGHT_GREEN,
    COLOR_LIGHT_BLUE, COLOR_LIGHT_GREY,
)

# Name -> byte, for admin-facing color pickers and PetsciiMenu.theme_color
# storage (a plain name string, e.g. 'LIGHT_BLUE', not a raw byte --
# keeps the stored value human-readable in the DB/admin UI).
COLOR_NAMES = {
    'BLACK': COLOR_BLACK, 'WHITE': COLOR_WHITE, 'RED': COLOR_RED,
    'CYAN': COLOR_CYAN, 'PURPLE': COLOR_PURPLE, 'GREEN': COLOR_GREEN,
    'BLUE': COLOR_BLUE, 'YELLOW': COLOR_YELLOW, 'ORANGE': COLOR_ORANGE,
    'BROWN': COLOR_BROWN, 'LIGHT_RED': COLOR_LIGHT_RED,
    'DARK_GREY': COLOR_DARK_GREY, 'GREY': COLOR_GREY,
    'LIGHT_GREEN': COLOR_LIGHT_GREEN, 'LIGHT_BLUE': COLOR_LIGHT_BLUE,
    'LIGHT_GREY': COLOR_LIGHT_GREY,
}

DEFAULT_BODY_COLOR = COLOR_WHITE
DEFAULT_HEADER_COLOR = COLOR_LIGHT_BLUE
DEFAULT_HOTKEY_COLOR = COLOR_YELLOW


def resolve_color(name, default=DEFAULT_HEADER_COLOR):
    """Look up a stored color name (e.g. a PetsciiMenu.theme_color
    value) -- unknown/None falls back to `default` rather than
    raising, so a bad/legacy stored value degrades to the plain
    default instead of breaking the menu."""
    if not name:
        return default
    return COLOR_NAMES.get(name.strip().upper(), default)


def header_bar(title, width, color=DEFAULT_HEADER_COLOR):
    """A colored reverse-video title bar. Color persists through
    reverse-video on real C64 hardware (reverse swaps fg/bg of
    whatever color is currently active), so this is a real,
    commonly-used effect, not a no-op."""
    inner = max(1, width - 2)
    return f'{color}{REVERSE_ON} {title[:inner].ljust(inner)}{REVERSE_OFF}'


def menu_line(hotkey, label, hotkey_color=DEFAULT_HOTKEY_COLOR,
             label_color=DEFAULT_BODY_COLOR):
    """One '  H. Label' menu row with a colored hotkey letter, resetting
    to label_color (typically the plain body-text color) afterward."""
    return f'  {hotkey_color}{hotkey}{label_color}. {label}'
