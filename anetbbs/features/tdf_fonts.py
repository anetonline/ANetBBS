# anetbbs/features/tdf_fonts.py
"""
TheDraw (.TDF) banner fonts -- toward ANetDRAW's "3,716 TheDraw fonts
with a live preview and TheDraw's 19 outline styles" feature.

Format and the full 19-style outline table are ported directly from
ANetDRAW's own real, working C implementation (~/anet/ANetDRAW-Door/
ANetDRAW_opendoors/src/tdf.c, include/tdf.h) -- that header's own
comment says it's "checked against tdfiglet and Synchronet's
tdfonts_lib.js" (the latter also vendored in this repo, at
anetbbs/games/sbbs_stubs/tdfonts_lib.js, for Synchronet-door-compat
purposes -- a second independent cross-check), so this is a faithful
port of an already-verified implementation, not a from-scratch guess
at an undocumented binary format:

    file:  0x13 "TheDraw FONTS file" 0x1A, then one or more fonts
    font:  55 AA 00 FF, name length, name[12], 4 unused, type
           (0 outline, 1 block, 2 color), letter spacing, data size
           (u16 LE), 94 u16 LE glyph offsets for '!'..'~' (0xFFFF = none),
           then the glyph data
    glyph: width, height, then cells row by row -- 0x0D ends a row,
           0x00 ends the glyph; color fonts store (char, attr) pairs,
           block and outline fonts just the char. Cells a row doesn't
           reach are transparent.

Fonts come from files nobody vouches for, so every offset, size, and
row/column is bounds-checked, same discipline as the C source.
"""
import os

TDF_MAGIC = b'\x13TheDraw FONTS file\x1a'
_MAGIC_LEN = 20
_HEADER_LEN = 25
_OFFSETS_LEN = 188  # 94 * 2
_MAX_GLYPH_W = 30
_MAX_GLYPH_H = 12
_MAX_TEXT_W = 1000

OUTLINE, BLOCK, COLOR = 0, 1, 2

# TheDraw's 19 outline styles: the CP437 code each letter 'A'..'Q' in an
# outline font's raw glyph data becomes. Style index 9 (the 10th style)
# is the one Roy/SAC's TDF spec itself prints, and TheDraw's own
# default. Ported byte-for-byte from tdf.c's OUTLINE_STYLES table.
OUTLINE_STYLES = [
    [0xC4, 0xC4, 0xB3, 0xB3, 0xDA, 0xBF, 0xDA, 0xBF, 0xC0, 0xD9, 0xC0, 0xD9, 0xB4, 0xC3, 0x20, 0x20, 0x20],
    [0xCD, 0xC4, 0xB3, 0xB3, 0xD5, 0xB8, 0xDA, 0xBF, 0xD4, 0xBE, 0xC0, 0xD9, 0xB5, 0xC3, 0x20, 0x20, 0x20],
    [0xC4, 0xCD, 0xB3, 0xB3, 0xDA, 0xBF, 0xD5, 0xB8, 0xC0, 0xD9, 0xD4, 0xBE, 0xB4, 0xC6, 0x20, 0x20, 0x20],
    [0xCD, 0xCD, 0xB3, 0xB3, 0xD5, 0xB8, 0xD5, 0xB8, 0xD4, 0xBE, 0xD4, 0xBE, 0xB5, 0xC6, 0x20, 0x20, 0x20],
    [0xC4, 0xC4, 0xBA, 0xB3, 0xD6, 0xBF, 0xDA, 0xB7, 0xC0, 0xBD, 0xD3, 0xD9, 0xB6, 0xC3, 0x20, 0x20, 0x20],
    [0xCD, 0xC4, 0xBA, 0xB3, 0xC9, 0xB8, 0xDA, 0xB7, 0xD4, 0xBC, 0xD3, 0xD9, 0xB9, 0xC3, 0x20, 0x20, 0x20],
    [0xC4, 0xCD, 0xBA, 0xB3, 0xD6, 0xBF, 0xD5, 0xBB, 0xC0, 0xBD, 0xC8, 0xBE, 0xB6, 0xC6, 0x20, 0x20, 0x20],
    [0xCD, 0xCD, 0xBA, 0xB3, 0xC9, 0xB8, 0xD5, 0xBB, 0xD4, 0xBC, 0xC8, 0xBE, 0xB9, 0xC6, 0x20, 0x20, 0x20],
    [0xC4, 0xC4, 0xB3, 0xBA, 0xDA, 0xB7, 0xD6, 0xBF, 0xD3, 0xD9, 0xC0, 0xBD, 0xB4, 0xC7, 0x20, 0x20, 0x20],
    [0xCD, 0xC4, 0xB3, 0xBA, 0xD5, 0xBB, 0xD6, 0xBF, 0xC8, 0xBE, 0xC0, 0xBD, 0xB5, 0xC7, 0x20, 0x20, 0x20],
    [0xC4, 0xCD, 0xB3, 0xBA, 0xDA, 0xB7, 0xC9, 0xB8, 0xD3, 0xD9, 0xD4, 0xBC, 0xB4, 0xCC, 0x20, 0x20, 0x20],
    [0xCD, 0xCD, 0xB3, 0xBA, 0xD5, 0xBB, 0xC9, 0xB8, 0xC8, 0xBE, 0xD4, 0xBC, 0xB5, 0xCC, 0x20, 0x20, 0x20],
    [0xC4, 0xC4, 0xBA, 0xBA, 0xD6, 0xB7, 0xD6, 0xB7, 0xD3, 0xBD, 0xD3, 0xBD, 0xB6, 0xC7, 0x20, 0x20, 0x20],
    [0xCD, 0xC4, 0xBA, 0xBA, 0xC9, 0xBB, 0xD6, 0xB7, 0xC8, 0xBC, 0xD3, 0xBD, 0xB9, 0xC7, 0x20, 0x20, 0x20],
    [0xC4, 0xCD, 0xBA, 0xBA, 0xD6, 0xB7, 0xC9, 0xBB, 0xD3, 0xBD, 0xC8, 0xBC, 0xB6, 0xCC, 0x20, 0x20, 0x20],
    [0xCD, 0xCD, 0xBA, 0xBA, 0xC9, 0xBB, 0xC9, 0xBB, 0xC8, 0xBC, 0xC8, 0xBC, 0xB9, 0xCC, 0x20, 0x20, 0x20],
    [0xDC, 0xDC, 0xDB, 0xDB, 0xDC, 0xDC, 0xDC, 0xDC, 0xDB, 0xDB, 0xDB, 0xDB, 0xDB, 0xDB, 0x20, 0x20, 0x20],
    [0xDF, 0xDF, 0xDB, 0xDB, 0xDB, 0xDB, 0xDB, 0xDB, 0xDF, 0xDF, 0xDF, 0xDF, 0xDB, 0xDB, 0x20, 0x20, 0x20],
    [0xDF, 0xDC, 0xDE, 0xDD, 0xDE, 0xDD, 0xDC, 0xDC, 0xDF, 0xDF, 0xDE, 0xDD, 0xDB, 0xDB, 0x20, 0x20, 0x20],
]
OUTLINE_STYLE_COUNT = len(OUTLINE_STYLES)  # 19
OUTLINE_STYLE_DEFAULT = 9  # style 10 (1-indexed): Roy/SAC's published table


def scan_fonts(directory):
    """List every font in every .tdf file directly inside `directory`
    (not recursive, matching ad_tdf_scan's own opendir() scan). Returns
    [{name, file, offset, type}, ...] sorted by name -- `file` and
    `offset` are exactly what load_font() needs to load that one font
    back out without re-scanning."""
    out = []
    try:
        entries = os.listdir(directory)
    except OSError:
        return out
    for fname in entries:
        if not fname.lower().endswith('.tdf'):
            continue
        path = os.path.join(directory, fname)
        if not os.path.isfile(path):
            continue
        out.extend(_scan_one_file(path))
    out.sort(key=lambda e: e['name'].lower())
    return out


def _scan_one_file(path):
    out = []
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return out
    if data[:_MAGIC_LEN] != TDF_MAGIC:
        return out
    pos = _MAGIC_LEN
    length = len(data)
    while pos + _HEADER_LEN + _OFFSETS_LEN <= length:
        header = data[pos:pos + _HEADER_LEN]
        if header[0:4] != b'\x55\xAA\x00\xFF':
            break
        size = header[23] | (header[24] << 8)
        if pos + _HEADER_LEN + _OFFSETS_LEN + size > length:
            break
        name_len = min(header[4], 12)
        raw_name = header[5:5 + name_len]
        name = ''.join(chr(b) if 0x20 <= b < 0x7f else '?' for b in raw_name).rstrip(' ')
        if not name:
            name = '(unnamed)'
        out.append({'name': name, 'file': path, 'offset': pos, 'type': header[21]})
        pos += _HEADER_LEN + _OFFSETS_LEN + size
    return out


def load_font(file_path, offset):
    """Load one font's full data (offsets table + glyph bytes) given
    the (file, offset) an entry from scan_fonts() provides. Returns
    None if the file changed/shrank since scanning (caller's job to
    re-scan and retry, not crash)."""
    try:
        with open(file_path, 'rb') as f:
            f.seek(offset)
            header = f.read(_HEADER_LEN + _OFFSETS_LEN)
            if len(header) != _HEADER_LEN + _OFFSETS_LEN:
                return None
            font_type = header[21]
            spacing = min(header[22], 40)
            size = header[23] | (header[24] << 8)
            offs = []
            for k in range(94):
                o = header[25 + k * 2] | (header[26 + k * 2] << 8)
                offs.append(o)
            glyph_data = f.read(size)
            if len(glyph_data) != size:
                return None
    except OSError:
        return None
    return {'type': font_type, 'spacing': spacing, 'offs': offs, 'data': glyph_data,
            'outline_style': OUTLINE_STYLE_DEFAULT}


def _glyph_index(font, ch):
    """Index into font['offs'] for character `ch` (a 1-char str),
    trying uppercase when a font has no lowercase (most don't). None if
    the character isn't in this font or its glyph is corrupt/missing."""
    code = ord(ch) if ch else 0
    if 33 <= code <= 126:
        i = code - 33
        off = font['offs'][i]
        data = font['data']
        if off != 0xFFFF and off + 2 <= len(data):
            w, h = data[off], data[off + 1]
            if 1 <= w <= _MAX_GLYPH_W and 1 <= h <= _MAX_GLYPH_H:
                return i
    if ch.islower():
        return _glyph_index(font, ch.upper())
    return None


def _glyph_size(font, gi):
    off = font['offs'][gi]
    data = font['data']
    return data[off], data[off + 1]


def _outline_char(code, style):
    """code: raw glyph byte for an outline-type font. Returns the real
    CP437 code point to draw, or None for a transparent cell (the
    '@'/'&' filler/descender markers)."""
    if style < 0 or style >= OUTLINE_STYLE_COUNT:
        style = OUTLINE_STYLE_DEFAULT
    ch = chr(code)
    if ch == 'O':
        return 0x20  # hard space: inside the letter, not see-through
    if ch in ('@', '&'):
        return None  # filler / descender mark: see-through
    if 'A' <= ch <= 'Q':
        return OUTLINE_STYLES[style][code - ord('A')]
    return code


def _dos_cga_fg_bg_from_attr(attr):
    """A TDF color-font glyph's packed DOS/CGA attribute byte
    ((bg<<4)|fg) -> this editor's own (fg, bg) grid indices. Reuses
    web/ansi_editor.py's real, already-correct ANSI<->grid tables
    (lazy import to avoid a circular import -- ansi_editor.py doesn't
    import this module, but keeping the dependency one-directional and
    explicit is worth the two extra lines) rather than a third
    independent color-order guess."""
    from ..web.ansi_editor import _FG_FROM_ANSI, _BG_FROM_ANSI
    dos_to_ansi_base = {0: 0, 4: 1, 2: 2, 6: 3, 1: 4, 5: 5, 3: 6, 7: 7}
    fg_nibble = attr & 0x0F
    bg_nibble = (attr >> 4) & 0x0F
    fg_bright = fg_nibble >= 8
    fg_ansi = 30 + dos_to_ansi_base[fg_nibble % 8] + (60 if fg_bright else 0)
    bg_ansi = 40 + dos_to_ansi_base[bg_nibble % 8]  # this editor's bg has no bright variant
    return _FG_FROM_ANSI.get(fg_ansi, 7), _BG_FROM_ANSI.get(bg_ansi, 0)


def _draw_glyph(font, gi, fg, bg, out_cells, out_w, out_h, x0):
    """Blits one glyph into out_cells (a flat out_w*out_h list, None =
    transparent) at column x0. Mutates out_cells in place."""
    data = font['data']
    off = font['offs'][gi]
    w, h = _glyph_size(font, gi)
    p = off + 2
    end = len(data)
    row = col = 0
    while p < end and data[p] != 0:
        ch = data[p]
        p += 1
        cell_fg, cell_bg = fg, bg
        if ch == 0x0D:
            row += 1
            col = 0
            continue
        if font['type'] == COLOR:
            if p >= end:
                break
            attr = data[p]
            p += 1
            cell_fg, cell_bg = _dos_cga_fg_bg_from_attr(attr)
        draw_code = 0x20 if ch < 0x20 else ch
        if font['type'] == OUTLINE:
            mapped = _outline_char(ch, font.get('outline_style', OUTLINE_STYLE_DEFAULT))
            draw_code = mapped  # may be None (transparent)
        if draw_code is not None and row < h and col < w and row < out_h and x0 + col < out_w:
            out_cells[row * out_w + x0 + col] = {
                'c': bytes([draw_code]).decode('cp437', errors='replace'),
                'fg': cell_fg, 'bg': cell_bg,
            }
        col += 1


def render_text(font, text, fg=7, bg=0):
    """Renders `text` with `font` (as loaded by load_font()), using
    (fg, bg) for BLOCK/OUTLINE fonts (COLOR fonts carry their own,
    per-glyph, from the file). Returns {width, height, cells} where
    `cells` is a flat width*height list -- entries are either a real
    {c, fg, bg} dict or None (transparent: no glyph reaches that cell,
    matching TheDraw's own "cells a row doesn't reach are transparent"
    behavior). Returns None if nothing in `text` exists in this font,
    or the font has no usable data.
    """
    if not font.get('data'):
        return None
    space_w = 2
    gi = _glyph_index(font, 'A')
    if gi is None:
        gi = _glyph_index(font, 'a')
    if gi is not None:
        w, _h = _glyph_size(font, gi)
        space_w = max(2, w // 2)

    total = 0
    height = 0
    n = 0
    for ch in text:
        if ch == ' ':
            w, h = space_w, 0
        else:
            gi = _glyph_index(font, ch)
            if gi is None:
                continue
            w, h = _glyph_size(font, gi)
        if n:
            total += font['spacing']
        n += 1
        total += w
        if h > height:
            height = h
        if total > _MAX_TEXT_W:
            break
    if total <= 0 or height <= 0:
        return None
    total = min(total, _MAX_TEXT_W)

    cells = [None] * (total * height)
    x = 0
    n = 0
    for ch in text:
        if x >= total:
            break
        if ch == ' ':
            w = space_w
        else:
            gi = _glyph_index(font, ch)
            if gi is None:
                continue
            w, _h = _glyph_size(font, gi)
        if n:
            x += font['spacing']
        n += 1
        if ch != ' ':
            _draw_glyph(font, gi, fg, bg, cells, total, height, x)
        x += w
    return {'width': total, 'height': height, 'cells': cells}
