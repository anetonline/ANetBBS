"""Parser for the vendored Mystic MRC theme .ini format (mrc/mystic_client/
vendor/scripts/mrctheme-*.ini) -- StackFault's own coordinate-based screen
layout spec, used to recreate the real Mystic MRC screen (border art +
exact element positions) in ANetBBS's own terminal renderer instead of
just recoloring ANetBBS's own layout. See PROVENANCE.md.

Format reference (from the vendored .ini files' own comments):
  ANSIFILE=<name>                          -- which text/mrc-*.ans to use
  TOPANSI=firstline,numlines,dispX,dispY,fg   -- static border art segment
  BOTANSI=firstline,numlines,dispX,dispY,fg   -- another static art segment
  <ELEMENT>=dispX,dispY,length,color1,color2  -- a dynamic content region
"""
import re
from pathlib import Path
from typing import Dict, Optional

_VENDOR_DIR = Path(__file__).resolve().parent / 'vendor'
_SCRIPTS_DIR = _VENDOR_DIR / 'scripts'
_TEXT_DIR = _VENDOR_DIR / 'text'

# Elements described as "Display X, Display Y, Length, Color1, Color2"
# (or NickFG/Background, BracketFG/BracketBG -- same 5-number shape).
_ELEMENT_KEYS = (
    'INPUT', 'DIRECT', 'CHATTL', 'CHATBL', 'NICKTL', 'NICKBL',
    'ROOM', 'TOPIC', 'SCROLL', 'LATENCY', 'CHATTERS', 'BUFFER',
    'HEARTBEAT', 'MENTIONS', 'CLOCK', 'BBSES', 'ROOMS', 'ACTIVITY',
)

# Art segments described as "First Line, Number of Lines, Display X,
# Display Y, InitialFGClr".
_ART_KEYS = ('TOPANSI', 'BOTANSI')

_MODE_RE = re.compile(r'^mrctheme-[^.]+\.(\d+)x(\d+)\.ini$')


def best_fit_mode(theme_name: str, cols: int, lines: int) -> str:
    """Pick the largest bundled size variant ('132x36', '160x44', etc)
    that fits within the caller's actual (cols, lines) without
    overflowing, or 'default' (the 80-col layout) if nothing fits or
    the theme has no wide variants at all -- 'least'/'2leet4u' only
    ever ship a 'default' .ini, matching load_theme_layout's own
    fallback. "Fits" means both dimensions are <= the caller's actual
    terminal size; "largest" is by area, so a 132x59 terminal doesn't
    settle for 132x36 when 132x59 itself is available."""
    best = None
    best_area = -1
    for path in _SCRIPTS_DIR.glob(f'mrctheme-{theme_name}.*.ini'):
        m = _MODE_RE.match(path.name)
        if not m:
            continue
        w, h = int(m.group(1)), int(m.group(2))
        if w > cols or h > lines:
            continue
        area = w * h
        if area > best_area:
            best_area = area
            best = f'{w}x{h}'
    return best or 'default'


class ThemeLayout:
    def __init__(self, ansifile: str, art: Dict[str, tuple], elements: Dict[str, tuple]):
        self.ansifile = ansifile
        self.art = art            # name -> (first_line, num_lines, x, y, fg)
        self.elements = elements  # name -> (x, y, length, color1, color2)

    def element(self, name: str) -> Optional[tuple]:
        return self.elements.get(name.upper())


def _parse_ini_text(text: str) -> dict:
    fields = {}
    for line in text.splitlines():
        line = line.split('//', 1)[0].strip()
        if not line or '=' not in line:
            continue
        key, _, value = line.partition('=')
        fields[key.strip().upper()] = value.strip()
    return fields


def load_theme_layout(theme_name: str, mode: str = 'default') -> Optional[ThemeLayout]:
    """theme_name: original/minimal/bitchx/2leet4u/least. mode: 'default'
    (80-col) unless the caller resolved a wider variant via
    best_fit_mode() -- see mrc_chat.py's _sync_mystic_layout(), which
    picks the largest 132x*/160x* variant that fits the caller's
    actual detected terminal size."""
    ini_path = _SCRIPTS_DIR / f'mrctheme-{theme_name}.{mode}.ini'
    if not ini_path.is_file():
        return None
    fields = _parse_ini_text(ini_path.read_text(encoding='utf-8', errors='replace'))

    ansifile = fields.get('ANSIFILE', '')
    if not ansifile:
        return None

    art = {}
    for key in _ART_KEYS:
        raw = fields.get(key)
        if not raw:
            continue
        parts = [p.strip() for p in raw.split(',')]
        if len(parts) != 5:
            continue
        try:
            art[key] = tuple(int(p) for p in parts)
        except ValueError:
            continue

    elements = {}
    for key in _ELEMENT_KEYS:
        raw = fields.get(key)
        if not raw:
            continue
        parts = [p.strip() for p in raw.split(',')]
        if len(parts) != 5:
            continue
        try:
            elements[key] = tuple(int(p) for p in parts)
        except ValueError:
            continue

    return ThemeLayout(ansifile, art, elements)


_ANSI_ART_LINE_SPLIT_RE = re.compile(r'\r\n|\n')


def load_art_lines(ansifile: str) -> Optional[list]:
    """Returns the .ans file's lines (CP437-decoded, no line endings),
    1-indexed access via lines[n-1], matching the .ini's 1-based
    First Line convention."""
    path = _TEXT_DIR / ansifile
    if not path.is_file():
        return None
    data = path.read_bytes().decode('cp437', errors='replace')
    return _ANSI_ART_LINE_SPLIT_RE.split(data)


def extract_art_segment(ansifile: str, first_line: int, num_lines: int) -> list:
    """Returns up to num_lines raw ANSI lines starting at first_line
    (1-indexed, inclusive), or [] if the file/range is unavailable."""
    lines = load_art_lines(ansifile)
    if lines is None:
        return []
    start = max(0, first_line - 1)
    end = start + max(0, num_lines)
    return lines[start:end]
