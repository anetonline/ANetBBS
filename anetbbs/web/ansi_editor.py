# anetbbs/web/ansi_editor.py
"""
Web-based ANSI art editor + library.

Storage: each AnsiArt row keeps both
    - grid_json: full per-cell state (char, fg, bg, bright) for reloading
    - ansi_text: pre-rendered escape-coded text suitable for serving to telnet

Editor: front-end is a CP437 grid (default 80x25) with a character palette,
colour pickers, and tools (pencil, eraser, line, fill, text). Save POSTs the
grid back; the server re-renders ansi_text from grid_json so the two stay
in sync.
"""
import json
import os
import re
import struct
from datetime import datetime

from flask import (Blueprint, current_app, render_template, request, redirect,
                   url_for, flash, Response, jsonify)
from flask_login import login_required, current_user

from ..models import db, AnsiArt
from .access_control import require_admin_or_403


ansi_bp = Blueprint('ansi_editor', __name__, url_prefix='/admin/ansi')


_SLUG_RE = re.compile(r'[^a-z0-9_-]+')


def _slugify(text):
    s = _SLUG_RE.sub('-', (text or 'art').lower()).strip('-')
    return s or 'art'


_FILE_NAME_RE = re.compile(r'[^A-Za-z0-9_.-]+')


def _browse_roots():
    """Parse ANSI_EDITOR_BROWSE_DIRS ("label:path;label:path...") into
    [(label, abs_path), ...], creating each directory if it doesn't
    exist yet (matches DOWNLOADS_DIR/FILE_BULLETINS_DIR's own
    "auto-detect a drop-in directory" convention -- a sysop shouldn't
    have to manually mkdir before this works)."""
    raw = current_app.config.get('ANSI_EDITOR_BROWSE_DIRS', '') or ''
    roots = []
    for entry in raw.split(';'):
        entry = entry.strip()
        if not entry or ':' not in entry:
            continue
        label, path = entry.split(':', 1)
        label, path = label.strip(), path.strip()
        if not label or not path:
            continue
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            continue
        roots.append((label, os.path.realpath(path)))
    return roots


def _safe_join(root_abs, filename):
    """Resolve `filename` (sysop/caller-typed) against root_abs and
    confirm the result is still INSIDE root_abs -- the one real
    security concern here (per this project's own audit history):
    without this, "../../../../etc/passwd" or an absolute path in
    `filename` would let a save/open reach anywhere the process can
    write/read, not just the configured art directories. Returns None
    (caller's job to reject) rather than raising, so a bad filename is
    just an ordinary "not found"/"invalid name" response, not a 500.
    """
    if not filename or '\x00' in filename:
        return None
    # os.path.join with an absolute-looking `filename` would otherwise
    # just discard root_abs entirely (a classic path-join footgun) --
    # strip any leading slash/drive so it's always treated as relative.
    filename = filename.lstrip('/\\')
    candidate = os.path.realpath(os.path.join(root_abs, filename))
    if candidate != root_abs and not candidate.startswith(root_abs + os.sep):
        return None
    return candidate


# Map mIRC-style 16-color palette to ANSI fg/bg codes for serialization.
_FG_CODES = [30, 34, 32, 36, 31, 35, 33, 37,    # 0-7 normal
             90, 94, 92, 96, 91, 95, 93, 97]    # 8-15 bright
_BG_CODES = [40, 44, 42, 46, 41, 45, 43, 47]    # bg only goes 0-7


# --- ANSI -> grid parser ---------------------------------------------------
# Map ANSI fg codes back to our 0-15 palette index.
_FG_FROM_ANSI = {
    30: 1,  31: 4,  32: 3,  33: 6,  34: 2,  35: 5,  36: 11, 37: 7,
    90: 8,  91: 12, 92: 9,  93: 14, 94: 12, 95: 13, 96: 11, 97: 0,
}
# Wait — our palette layout is mIRC-ish: 0=white, 1=black, 2=blue, 3=green,
# 4=red, 5=brown, 6=magenta, 7=orange, 8=yellow, 9=lt green, 10=cyan,
# 11=lt cyan, 12=lt blue, 13=pink, 14=grey, 15=lt grey.
# Re-map ANSI codes accordingly.
_FG_FROM_ANSI = {
    # normal
    30: 1,  # black
    31: 4,  # red
    32: 3,  # green
    33: 5,  # brown/dark yellow
    34: 2,  # blue
    35: 6,  # magenta
    36: 10, # cyan
    37: 15, # light grey
    # bright
    90: 14, # dark grey
    91: 12, # bright red
    92: 9,  # light green
    93: 8,  # yellow
    94: 12, # light blue
    95: 13, # pink
    96: 11, # light cyan
    97: 0,  # white
}
_BG_FROM_ANSI = {
    40: 1, 41: 4, 42: 3, 43: 5, 44: 2, 45: 6, 46: 10, 47: 15,
    100: 14, 101: 12, 102: 9, 103: 8, 104: 12, 105: 13, 106: 11, 107: 0,
}


def parse_ansi_to_grid(text, width=80, height=25):
    """Render an ANSI escape-coded string back into a grid dict.

    Best-effort: handles the 4-bit color SGR codes (30-37, 40-47, 90-97,
    100-107), bold/reset, cursor position (CSI H), clear (CSI 2J), and
    plain printable text. Anything we don't understand is silently dropped
    so a partial parse still produces a usable grid."""
    cells = [{'c': ' ', 'fg': 15, 'bg': 1} for _ in range(width * height)]
    cur_fg = 7
    cur_bg = 0
    bright = False
    row = 0
    col = 0
    i = 0
    n = len(text)

    def put(ch):
        nonlocal row, col
        if row >= height:
            return
        if col >= width:
            row += 1
            col = 0
            if row >= height:
                return
        idx = row * width + col
        cells[idx] = {'c': ch, 'fg': cur_fg, 'bg': cur_bg}
        col += 1

    while i < n:
        c = text[i]
        if c == '\x1b' and i + 1 < n and text[i + 1] == '[':
            # CSI sequence — read until terminator (a letter)
            j = i + 2
            while j < n and not text[j].isalpha():
                j += 1
            if j >= n:
                break
            args_str = text[i + 2:j]
            terminator = text[j]
            params = []
            for piece in args_str.split(';'):
                if piece == '':
                    params.append(0)
                else:
                    try:
                        params.append(int(piece))
                    except ValueError:
                        params.append(0)
            if terminator == 'm':
                # SGR — color/attribute
                if not params:
                    params = [0]
                for p in params:
                    if p == 0:
                        cur_fg = 7; cur_bg = 0; bright = False
                    elif p == 1:
                        bright = True
                    elif p in _FG_FROM_ANSI:
                        cur_fg = _FG_FROM_ANSI[p]
                        # If "bright" is on AND base is a 30-37, bump to bright variant.
                        if bright and 30 <= p <= 37:
                            bright_p = p + 60
                            if bright_p in _FG_FROM_ANSI:
                                cur_fg = _FG_FROM_ANSI[bright_p]
                    elif p in _BG_FROM_ANSI:
                        cur_bg = _BG_FROM_ANSI[p] & 0x07
            elif terminator == 'H' or terminator == 'f':
                # Cursor position: CSI [r ; c H — 1-indexed
                r = (params[0] if len(params) > 0 else 1) or 1
                cc = (params[1] if len(params) > 1 else 1) or 1
                row = max(0, min(height - 1, r - 1))
                col = max(0, min(width - 1, cc - 1))
            elif terminator == 'A':  # cursor up
                row = max(0, row - (params[0] or 1))
            elif terminator == 'B':  # cursor down
                row = min(height - 1, row + (params[0] or 1))
            elif terminator == 'C':  # forward
                col = min(width - 1, col + (params[0] or 1))
            elif terminator == 'D':  # back
                col = max(0, col - (params[0] or 1))
            elif terminator == 'J':  # erase in display
                if params and params[0] == 2:
                    cells = [{'c': ' ', 'fg': cur_fg, 'bg': cur_bg}
                             for _ in range(width * height)]
                    row = 0; col = 0
            elif terminator == 'K':
                # erase in line — fill rest of row with spaces
                start = row * width + col
                end = (row + 1) * width
                for k in range(start, end):
                    cells[k] = {'c': ' ', 'fg': cur_fg, 'bg': cur_bg}
            # other CSI codes silently ignored
            i = j + 1
            continue
        if c == '\r':
            col = 0
            i += 1
            continue
        if c == '\n':
            row += 1
            col = 0
            i += 1
            continue
        if ord(c) < 32:
            i += 1
            continue
        put(c)
        i += 1

    return {'width': width, 'height': height, 'cells': cells}


def render_ansi_text(grid):
    """Convert a {width, height, cells:[{c,fg,bg}]} dict into ANSI text.

    `cells` is row-major (y=0 top). Each cell: c=char (1 char), fg=0..15,
    bg=0..7. Empty cells are rendered as spaces with fg=7,bg=0."""
    width = int(grid.get('width', 80))
    height = int(grid.get('height', 25))
    cells = grid.get('cells', [])
    out = ['\x1b[2J\x1b[H']  # clear + home so the screen lays out cleanly
    last_fg = None
    last_bg = None
    for y in range(height):
        for x in range(width):
            idx = y * width + x
            cell = cells[idx] if idx < len(cells) else None
            if cell:
                c = cell.get('c') or ' '
                fg = int(cell.get('fg', 7)) & 0x0F
                bg = int(cell.get('bg', 0)) & 0x07
            else:
                c = ' '; fg = 7; bg = 0
            if fg != last_fg or bg != last_bg:
                out.append(f'\x1b[0;{_FG_CODES[fg]};{_BG_CODES[bg]}m')
                last_fg = fg
                last_bg = bg
            # Strip control bytes from the char to avoid breaking the stream
            if not c or ord(c) < 32:
                c = ' '
            out.append(c[:1])
        out.append('\x1b[0m\r\n')
        last_fg = None
        last_bg = None
    return ''.join(out)


# --- Multi-BBS export formats (toward ANetDRAW's "Formats: ... PCBoard
# @X, Renegade/Mystic pipe codes and Synchronet Ctrl-A" feature) --------
#
# Each derives its color codes from the SAME _FG_CODES/_BG_CODES tables
# render_ansi_text() above already uses for real, tested ANSI export --
# one source of truth for "what ANSI code does grid index N actually
# mean", rather than three more hand-typed, independently-driftable
# copies of the palette.

def _dos_cga_index(ansi_code):
    """Standard ANSI SGR fg (30-37 normal, 90-97 bright) or bg (40-47)
    code -> the classic DOS/CGA attribute-nibble value (0-15) that
    PCBoard's @X codes and the BIN/XBin file formats both use. Derived
    from the real ANSI base-color order (black/red/green/yellow/blue/
    magenta/cyan/white) mapped onto the real DOS/CGA order (black/blue/
    green/cyan/red/magenta/brown/lightgray, then the same order again
    +8 for the bright/high-intensity half) -- these two 8-color orders
    are genuinely different, not just relabeled, so this table is
    real, not decorative.
    """
    ansi_base_to_dos = {0: 0, 1: 4, 2: 2, 3: 6, 4: 1, 5: 5, 6: 3, 7: 7}
    if ansi_code >= 90:
        return ansi_base_to_dos[ansi_code - 90] + 8
    if ansi_code >= 40:
        return ansi_base_to_dos[ansi_code - 40]
    return ansi_base_to_dos[ansi_code - 30]


def _iter_cells(grid):
    """Yields (c, fg, bg) for every cell in row-major order, same
    bounds-safe defaulting render_ansi_text() uses -- shared by every
    format renderer below so a short/missing cells list can't crash
    any of them."""
    width = int(grid.get('width', 80))
    height = int(grid.get('height', 25))
    cells = grid.get('cells', [])
    for y in range(height):
        for x in range(width):
            idx = y * width + x
            cell = cells[idx] if idx < len(cells) else None
            if cell:
                c = cell.get('c') or ' '
                fg = int(cell.get('fg', 7)) & 0x0F
                bg = int(cell.get('bg', 0)) & 0x07
            else:
                c = ' '; fg = 7; bg = 0
            if not c or ord(c) < 32:
                c = ' '
            yield (x, y, c[:1], fg, bg)
    return


def render_pcboard(grid):
    """PCBoard @X codes: literal "@X" + 2 hex digits (background,
    foreground), each a DOS/CGA attribute nibble 0-F."""
    out = []
    last_fg = last_bg = None
    for x, y, c, fg, bg in _iter_cells(grid):
        if x == 0 and y > 0:
            out.append('\r\n')
            last_fg = last_bg = None
        dos_fg = _dos_cga_index(_FG_CODES[fg])
        dos_bg = _dos_cga_index(_BG_CODES[bg])
        if dos_fg != last_fg or dos_bg != last_bg:
            out.append(f'@X{dos_bg:X}{dos_fg:X}')
            last_fg, last_bg = dos_fg, dos_bg
        out.append(c)
    return ''.join(out)


def render_pipe_codes(grid):
    """Renegade/Mystic/SBBSecho-style |NN pipe color codes -- reuses
    web/render_msg.py's own _PIPE_FG/_PIPE_BG (inverted here), the same
    table that page already trusts to translate these in the OTHER
    direction for message-body display, rather than a second
    independently-maintained copy of the same 16+8 codes."""
    from .render_msg import _PIPE_FG, _PIPE_BG
    fg_from_ansi = {int(v): k for k, v in _PIPE_FG.items()}
    bg_from_ansi = {int(v): k for k, v in _PIPE_BG.items()}
    out = []
    last_fg = last_bg = None
    for x, y, c, fg, bg in _iter_cells(grid):
        if x == 0 and y > 0:
            out.append('\r\n')
            last_fg = last_bg = None
        ansi_fg, ansi_bg = _FG_CODES[fg], _BG_CODES[bg]
        if ansi_fg != last_fg:
            out.append('|' + fg_from_ansi[ansi_fg])
            last_fg = ansi_fg
        if ansi_bg != last_bg:
            out.append('|' + bg_from_ansi[ansi_bg])
            last_bg = ansi_bg
        out.append(c)
    return ''.join(out)


# Synchronet's real \x01-prefixed Ctrl-A codes -- same table
# games/synchronet_compat.py's own _CTRLA_MAP already establishes for
# the OTHER direction (translating a Synchronet door's own Ctrl-A
# output into real ANSI for display); inverted here rather than
# re-invented, so both directions agree on what each letter means.
_SYNC_CTRLA_FG = {30: 'K', 31: 'R', 32: 'G', 33: 'Y',
                  34: 'B', 35: 'M', 36: 'C', 37: 'W'}
_SYNC_CTRLA_BG = {40: '0', 41: '4', 42: '2', 43: '6',
                  44: '1', 45: '5', 46: '3', 47: '7'}


def render_synchronet_ctrl_a(grid):
    """Synchronet-native \\x01-prefixed color codes -- \\x01N resets,
    \\x01H is the bold/high-intensity toggle (Synchronet has no
    separate bright-color letters; bright is always base-letter + a
    preceding \\x01H), \\x01<letter> sets foreground, \\x01<digit> sets
    background."""
    out = []
    last_fg = last_bg = None
    for x, y, c, fg, bg in _iter_cells(grid):
        if x == 0 and y > 0:
            out.append('\r\n')
            last_fg = last_bg = None
        ansi_fg, ansi_bg = _FG_CODES[fg], _BG_CODES[bg]
        if ansi_fg != last_fg or ansi_bg != last_bg:
            bright = ansi_fg >= 90
            base_fg = ansi_fg - 60 if bright else ansi_fg
            out.append('\x01N')
            if bright:
                out.append('\x01H')
            out.append('\x01' + _SYNC_CTRLA_FG[base_fg])
            out.append('\x01' + _SYNC_CTRLA_BG[ansi_bg])
            last_fg, last_bg = ansi_fg, ansi_bg
        out.append(c)
    return ''.join(out)


def render_bin(grid):
    """Raw .BIN format: no header, no line breaks -- just
    (char, attribute) byte pairs in row-major order, attribute =
    (bg_nibble << 4) | fg_nibble. Traditionally always 80 columns; the
    grid's own width is used instead (matches XBin's approach of
    storing real width rather than assuming 80)."""
    out = bytearray()
    for x, y, c, fg, bg in _iter_cells(grid):
        attr = (_dos_cga_index(_BG_CODES[bg]) << 4) | _dos_cga_index(_FG_CODES[fg])
        out += c.encode('cp437', errors='replace')[:1] or b' '
        out.append(attr)
    return bytes(out)


def parse_bin_to_grid(data, width=80):
    """Reverse of render_bin() -- (char, attribute) byte pairs, no
    header. `width` must be supplied by the caller (a .BIN file has no
    way to declare its own width; 80 is the near-universal convention,
    matching acidview/TheDraw's own default)."""
    n_cells = len(data) // 2
    height = max(1, -(-n_cells // width))  # ceil
    cells = []
    for i in range(n_cells):
        ch_byte, attr = data[2 * i], data[2 * i + 1]
        cells.append({
            'c': bytes([ch_byte]).decode('cp437', errors='replace'),
            'fg': attr & 0x0F,
            'bg': (attr >> 4) & 0x0F,
        })
    while len(cells) < width * height:
        cells.append({'c': ' ', 'fg': 7, 'bg': 0})
    return {'width': width, 'height': height, 'cells': cells}


_XBIN_ID = b'XBIN\x1a'


def render_xbin(grid):
    """XBin format (uncompressed, no custom palette/font sections --
    this editor's palette IS the XBin default VGA palette already, so
    there's nothing to declare). Header per the real XBin spec:
    'XBIN' + 0x1A, width (u16 LE), height (u16 LE), font height (u8,
    16 = standard VGA), palette/font flags (u8, 0 = neither present),
    then raw (char, attribute) pairs -- same payload render_bin()
    produces."""
    width = int(grid.get('width', 80))
    height = int(grid.get('height', 25))
    header = _XBIN_ID + struct.pack('<HHBB', width, height, 16, 0)
    return header + render_bin(grid)


def parse_xbin_to_grid(data):
    """Reverse of render_xbin() -- reads the real header (so width/
    height/compression come from the file, not a guess), skips any
    palette (48 bytes) / font (font_height * 256 bytes... actually
    font_height * num_chars, always 256 chars) section the flags
    declare, and RLE-decodes the character/attribute data when the
    compression flag is set (real XBin files in the wild commonly are
    compressed, unlike the uncompressed files render_xbin() writes --
    a reader that only handled the uncompressed case would fail on
    most real-world XBin art).
    """
    if data[:5] != _XBIN_ID:
        raise ValueError('not an XBin file (bad magic)')
    width, height, _font_height, flags = struct.unpack('<HHBB', data[5:11])
    pos = 11
    has_palette = bool(flags & 0x01)
    has_font = bool(flags & 0x02)
    is_compressed = bool(flags & 0x04)
    font_height = data[9]
    if has_palette:
        pos += 48
    if has_font:
        pos += font_height * 256

    cells = []
    if is_compressed:
        n_needed = width * height
        while len(cells) < n_needed and pos < len(data):
            ctrl = data[pos]; pos += 1
            run_type = (ctrl & 0xC0) >> 6
            run_len = (ctrl & 0x3F) + 1
            if run_type == 0:  # no compression: run_len literal pairs follow
                for _ in range(run_len):
                    if pos + 1 >= len(data):
                        break
                    cells.append({'c': bytes([data[pos]]).decode('cp437', errors='replace'),
                                  'fg': data[pos + 1] & 0x0F, 'bg': (data[pos + 1] >> 4) & 0x0F})
                    pos += 2
            else:  # char run / attr run / both run: one pair, repeated
                if pos + 1 >= len(data):
                    break
                ch_byte, attr = data[pos], data[pos + 1]
                pos += 2
                for _ in range(run_len):
                    cells.append({'c': bytes([ch_byte]).decode('cp437', errors='replace'),
                                  'fg': attr & 0x0F, 'bg': (attr >> 4) & 0x0F})
    else:
        raw = data[pos:pos + width * height * 2]
        grid_from_bin = parse_bin_to_grid(raw, width=width)
        cells = grid_from_bin['cells']

    n_needed = width * height
    while len(cells) < n_needed:
        cells.append({'c': ' ', 'fg': 7, 'bg': 0})
    return {'width': width, 'height': height, 'cells': cells[:n_needed]}


@ansi_bp.route('/')
@login_required
def index():
    require_admin_or_403()
    arts = AnsiArt.query.order_by(AnsiArt.updated_at.desc()).all()
    return render_template('ansi_editor/index.html', arts=arts)


@ansi_bp.route('/new', methods=['GET', 'POST'])
@login_required
def create():
    require_admin_or_403()
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip() or 'Untitled'
        width = max(20, min(132, request.form.get('width', type=int) or 80))
        height = max(5, min(50, request.form.get('height', type=int) or 25))
        slug = _slugify(name) + '-' + datetime.utcnow().strftime('%H%M%S')
        # Empty grid
        grid = {'width': width, 'height': height,
                'cells': [{'c': ' ', 'fg': 15, 'bg': 1}
                          for _ in range(width * height)]}
        art = AnsiArt(
            name=name, slug=slug, width=width, height=height,
            grid_json=json.dumps(grid),
            ansi_text=render_ansi_text(grid),
            created_by_id=current_user.id,
        )
        db.session.add(art)
        db.session.commit()
        return redirect(url_for('ansi_editor.edit', art_id=art.id))
    return render_template('ansi_editor/new.html')


@ansi_bp.route('/import', methods=['GET', 'POST'])
@login_required
def import_ans():
    """Upload an existing .ans file and load it into a new AnsiArt row."""
    require_admin_or_403()
    if request.method == 'POST':
        upload = request.files.get('ans_file')
        name = (request.form.get('name') or '').strip()
        width = max(20, min(132, request.form.get('width', type=int) or 80))
        height = max(5, min(50, request.form.get('height', type=int) or 25))
        if not upload or not upload.filename:
            flash('No file uploaded.', 'danger')
            return redirect(url_for('ansi_editor.import_ans'))
        try:
            raw = upload.read()
        except Exception as exc:
            flash(f'Read failed: {exc}', 'danger')
            return redirect(url_for('ansi_editor.import_ans'))
        # Strip SAUCE trailer if present so we don't get garbage chars
        # at the end. Also extract any metadata to flash for the sysop.
        try:
            from ..features.sauce import parse as _parse_sauce, strip as _strip_sauce
            sauce = _parse_sauce(raw)
            if sauce:
                raw = _strip_sauce(raw)
                bits = []
                if sauce.get('title'):  bits.append(f'title="{sauce["title"]}"')
                if sauce.get('author'): bits.append(f'by {sauce["author"]}')
                if sauce.get('group'):  bits.append(f'/{sauce["group"]}')
                if bits:
                    flash('SAUCE detected: ' + ' '.join(bits), 'info')
        except Exception:
            pass
        # ANSI files are usually CP437 — try that first, fall back to latin-1.
        for enc in ('cp437', 'latin-1', 'utf-8'):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                text = None
        if text is None:
            text = raw.decode('utf-8', errors='replace')

        grid = parse_ansi_to_grid(text, width=width, height=height)
        slug = (_slugify(name or upload.filename) + '-' +
                datetime.utcnow().strftime('%H%M%S'))
        art = AnsiArt(
            name=name or upload.filename,
            slug=slug, width=width, height=height,
            grid_json=json.dumps(grid),
            ansi_text=render_ansi_text(grid),
            description=f'Imported from {upload.filename}',
            created_by_id=current_user.id,
        )
        db.session.add(art)
        db.session.commit()
        flash(f'Imported {upload.filename} as "{art.name}".', 'success')
        return redirect(url_for('ansi_editor.edit', art_id=art.id))
    return render_template('ansi_editor/import.html')


@ansi_bp.route('/<int:art_id>/edit')
@login_required
def edit(art_id):
    require_admin_or_403()
    from ..models import BbsMenu
    art = AnsiArt.query.get_or_404(art_id)
    grid = json.loads(art.grid_json or '{}')
    if not grid:
        grid = {'width': art.width or 80, 'height': art.height or 25,
                'cells': [{'c': ' ', 'fg': 15, 'bg': 1}
                          for _ in range((art.width or 80) * (art.height or 25))]}
    menus = BbsMenu.query.order_by(BbsMenu.name).all()
    return render_template('ansi_editor/edit.html',
                           art=art, grid=grid, grid_json=json.dumps(grid),
                           menus=menus)


@ansi_bp.route('/<int:art_id>/save', methods=['POST'])
@login_required
def save(art_id):
    require_admin_or_403()
    art = AnsiArt.query.get_or_404(art_id)
    payload = request.get_json(silent=True) or {}
    grid = payload.get('grid')
    if not grid or 'cells' not in grid:
        return jsonify({'ok': False, 'error': 'missing grid'}), 400
    art.name = payload.get('name') or art.name
    art.description = payload.get('description', art.description)
    # Real Low finding from a security/performance audit (2026-09-02):
    # unlike create()/import_ans() above, this never clamped width/
    # height to the same [20,132]/[5,50] bounds. Must clamp the GRID
    # DICT's own width/height too, not just the stored art.width/
    # art.height columns -- render_ansi_text() below reads width/height
    # straight out of `grid` itself, so clamping only the columns still
    # left its nested row/col loop free to attempt a huge iteration
    # (confirmed: an initial fix that clamped only the columns still
    # hung on a 999999x999999 grid, caught by this fix's own test).
    # Admin-only, so low severity, but the same clamp costs nothing to
    # apply consistently.
    art.width = max(20, min(132, int(grid.get('width') or art.width)))
    art.height = max(5, min(50, int(grid.get('height') or art.height)))
    grid['width'] = art.width
    grid['height'] = art.height
    art.grid_json = json.dumps(grid)
    art.ansi_text = render_ansi_text(grid)
    art.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'updated_at': art.updated_at.isoformat()})


@ansi_bp.route('/<int:art_id>/delete', methods=['POST'])
@login_required
def delete(art_id):
    require_admin_or_403()
    art = AnsiArt.query.get_or_404(art_id)
    name = art.name
    db.session.delete(art)
    db.session.commit()
    flash(f'Deleted "{name}".', 'success')
    return redirect(url_for('ansi_editor.index'))


@ansi_bp.route('/<int:art_id>/preview')
@login_required
def preview(art_id):
    """Quick visual preview of the saved ANSI as a black <pre>.

    Renders the grid with HTML spans (cells coloured from grid_json) so
    the sysop can see what the terminal will render without actually
    opening a telnet client."""
    require_admin_or_403()
    art = AnsiArt.query.get_or_404(art_id)
    grid = json.loads(art.grid_json or '{}')
    return render_template('ansi_editor/preview.html', art=art, grid=grid)


def _build_sauce(art):
    """SAUCE 128-byte trailer (acid.org spec) — Pablo/Moebius read these.

    TInfoS (FontName, the last 22 bytes) is "IBM VGA" NUL-padded, not
    left blank -- real gap found comparing against ANetDRAW's own SAUCE
    writer (Jerry's reference implementation): a viewer reading this
    field picks the codepage/font to render CP437 glyphs with, and a
    blank field is a real, if minor, spec gap for a value this editor
    always actually knows (everything here is rendered/decoded as
    CP437). TFlags (the byte just before it) stays 0 -- unlike
    ANetDRAW's writer, which sets the iCE-color bit, this editor's own
    palette only ever produces classic 8-color (non-iCE) backgrounds
    (_BG_CODES above has 8 entries, not 16), so claiming iCE color
    would be actively wrong metadata, not just incomplete.
    """
    title  = (art.name or '')[:35].ljust(35)
    author = ((art.created_by.username if art.created_by else '') or '')[:20].ljust(20)
    group  = 'ANetBBS'.ljust(20)
    date   = (art.updated_at or art.created_at or datetime.utcnow()).strftime('%Y%m%d')
    body_bytes = (art.ansi_text or '').encode('cp437', errors='replace')
    font_name = 'IBM VGA'.encode('cp437')[:22].ljust(22, b'\x00')
    record = (
        b'SAUCE00' +
        title.encode('cp437', errors='replace') +
        author.encode('cp437', errors='replace') +
        group.encode('cp437', errors='replace') +
        date.encode('cp437', errors='replace') +
        struct.pack('<I', len(body_bytes)) +
        struct.pack('<BB', 1, 1) +                # Character / ANSI
        struct.pack('<HH', art.width or 80, art.height or 25) +
        struct.pack('<HH', 0, 0) +
        b'\x00' + b'\x00' +
        font_name
    )
    return record[:128].ljust(128, b'\x00')


@ansi_bp.route('/<int:art_id>/duplicate', methods=['POST'])
@login_required
def duplicate(art_id):
    """Save-as-copy."""
    require_admin_or_403()
    src = AnsiArt.query.get_or_404(art_id)
    new_slug = _slugify(src.name + '-copy') + '-' + datetime.utcnow().strftime('%H%M%S')
    new_art = AnsiArt(
        name=f'{src.name} (copy)', slug=new_slug,
        description=src.description, width=src.width, height=src.height,
        grid_json=src.grid_json, ansi_text=src.ansi_text,
        created_by_id=current_user.id,
    )
    db.session.add(new_art)
    db.session.commit()
    flash(f'Duplicated to "{new_art.name}".', 'success')
    return redirect(url_for('ansi_editor.edit', art_id=new_art.id))


@ansi_bp.route('/<int:art_id>/raw.ans')
@login_required
def raw_ansi(art_id):
    """Download the rendered ANSI text + SAUCE trailer (compatible with
    Pablo Draw / Moebius / SyncTERM)."""
    require_admin_or_403()
    art = AnsiArt.query.get_or_404(art_id)
    body = (art.ansi_text or '').encode('cp437', errors='replace')
    sauce = _build_sauce(art)
    payload = body + b'\x1a' + sauce       # 0x1A = SUB / EOF marker
    return Response(payload,
                    mimetype='application/octet-stream',
                    headers={'Content-Disposition':
                             f'attachment; filename="{art.slug}.ans"'})


@ansi_bp.route('/<int:art_id>/raw.png')
@login_required
def raw_png(art_id):
    """Download this art rendered as a real PNG image -- for sharing
    off-platform where a live terminal/HTML render isn't usable.
    Reuses features/ansi_png.py's render_grid_png(), the same renderer
    postcards.py already uses for its own PNG export -- toward
    ANetDRAW's "Formats: ... PNG" feature, not a second implementation.
    """
    require_admin_or_403()
    from ..features.ansi_png import render_grid_png
    art = AnsiArt.query.get_or_404(art_id)
    grid = json.loads(art.grid_json or '{}')
    png_bytes = render_grid_png(grid, scale=2)
    return Response(png_bytes, mimetype='image/png',
                    headers={'Content-Disposition':
                             f'attachment; filename="{art.slug}.png"'})


def _download(payload, filename):
    return Response(payload, mimetype='application/octet-stream',
                    headers={'Content-Disposition':
                             f'attachment; filename="{filename}"'})


@ansi_bp.route('/<int:art_id>/raw.bin')
@login_required
def raw_bin(art_id):
    """Download as a raw .BIN file (no header, char+attribute pairs)."""
    require_admin_or_403()
    art = AnsiArt.query.get_or_404(art_id)
    grid = json.loads(art.grid_json or '{}')
    return _download(render_bin(grid), f'{art.slug}.bin')


@ansi_bp.route('/<int:art_id>/raw.xb')
@login_required
def raw_xbin(art_id):
    """Download as an uncompressed XBin file."""
    require_admin_or_403()
    art = AnsiArt.query.get_or_404(art_id)
    grid = json.loads(art.grid_json or '{}')
    return _download(render_xbin(grid), f'{art.slug}.xb')


@ansi_bp.route('/<int:art_id>/raw.pcb')
@login_required
def raw_pcboard(art_id):
    """Download with PCBoard @X color codes."""
    require_admin_or_403()
    art = AnsiArt.query.get_or_404(art_id)
    grid = json.loads(art.grid_json or '{}')
    return _download(render_pcboard(grid).encode('cp437', errors='replace'),
                     f'{art.slug}.pcb')


@ansi_bp.route('/<int:art_id>/raw.ren')
@login_required
def raw_pipe(art_id):
    """Download with Renegade/Mystic-style |NN pipe color codes."""
    require_admin_or_403()
    art = AnsiArt.query.get_or_404(art_id)
    grid = json.loads(art.grid_json or '{}')
    return _download(render_pipe_codes(grid).encode('cp437', errors='replace'),
                     f'{art.slug}.ren')


@ansi_bp.route('/<int:art_id>/raw.syn')
@login_required
def raw_synchronet(art_id):
    """Download with Synchronet-native \\x01-prefixed Ctrl-A color codes."""
    require_admin_or_403()
    art = AnsiArt.query.get_or_404(art_id)
    grid = json.loads(art.grid_json or '{}')
    return _download(render_synchronet_ctrl_a(grid).encode('cp437', errors='replace'),
                     f'{art.slug}.syn')


@ansi_bp.route('/import-binfile', methods=['GET', 'POST'])
@login_required
def import_binfile():
    """Upload a real .BIN or .XB(IN) file into a new AnsiArt row --
    the binary-format counterpart to import_ans() above (which only
    handles text-based ANSI/ASCII). Format is picked from the uploaded
    filename's extension, not content-sniffed -- both formats can
    start with arbitrary bytes with no reliable magic number for BIN
    specifically."""
    require_admin_or_403()
    if request.method == 'POST':
        upload = request.files.get('bin_file')
        name = (request.form.get('name') or '').strip()
        width = max(20, min(132, request.form.get('width', type=int) or 80))
        if not upload or not upload.filename:
            flash('No file uploaded.', 'danger')
            return redirect(url_for('ansi_editor.import_binfile'))
        try:
            raw = upload.read()
        except Exception as exc:
            flash(f'Read failed: {exc}', 'danger')
            return redirect(url_for('ansi_editor.import_binfile'))

        lower = upload.filename.lower()
        try:
            if lower.endswith('.xb') or lower.endswith('.xbin') or raw[:5] == _XBIN_ID:
                grid = parse_xbin_to_grid(raw)
            else:
                grid = parse_bin_to_grid(raw, width=width)
        except (ValueError, IndexError, struct.error) as exc:
            # struct.error (a truncated/short header) isn't a ValueError
            # or IndexError -- a genuinely truncated or hand-crafted
            # malicious upload must not 500 the route, same as any
            # other untrusted-input parse here.
            flash(f'Could not parse {upload.filename}: {exc}', 'danger')
            return redirect(url_for('ansi_editor.import_binfile'))

        slug = _slugify(name or upload.filename) + '-' + datetime.utcnow().strftime('%H%M%S')
        art = AnsiArt(
            name=name or upload.filename,
            slug=slug, width=grid['width'], height=grid['height'],
            grid_json=json.dumps(grid),
            ansi_text=render_ansi_text(grid),
            description=f'Imported from {upload.filename}',
            created_by_id=current_user.id,
        )
        db.session.add(art)
        db.session.commit()
        flash(f'Imported {upload.filename} as "{art.name}".', 'success')
        return redirect(url_for('ansi_editor.edit', art_id=art.id))
    return render_template('ansi_editor/import_binfile.html')


@ansi_bp.route('/apply-to-menu', methods=['POST'])
@login_required
def apply_to_menu():
    """Set this AnsiArt as the ansi_screen of a BbsMenu."""
    require_admin_or_403()
    from ..models import BbsMenu
    art = AnsiArt.query.get_or_404(request.form.get('art_id', type=int))
    menu = BbsMenu.query.get_or_404(request.form.get('menu_id', type=int))
    menu.ansi_screen = art.ansi_text
    db.session.commit()
    flash(f'Applied "{art.name}" to menu "{menu.name}".', 'success')
    return redirect(url_for('ansi_editor.edit', art_id=art.id))


@ansi_bp.route('/apply-to-screen', methods=['POST'])
@login_required
def apply_to_screen():
    """Set this AnsiArt as a BbsAnsiScreen slot (welcome, goodbye, newuser)."""
    require_admin_or_403()
    from ..models import BbsAnsiScreen
    art = AnsiArt.query.get_or_404(request.form.get('art_id', type=int))
    slot = (request.form.get('slot') or '').strip()
    if not slot:
        flash('No screen slot specified.', 'danger')
        return redirect(url_for('ansi_editor.edit', art_id=art.id))
    screen = BbsAnsiScreen.query.filter_by(slot=slot).first()
    if not screen:
        screen = BbsAnsiScreen(slot=slot)
        db.session.add(screen)
    screen.body = art.ansi_text
    screen.is_active = True
    db.session.commit()
    flash(f'Applied "{art.name}" to the "{slot}" screen slot.', 'success')
    return redirect(url_for('ansi_editor.edit', art_id=art.id))


@ansi_bp.route('/fonts')
@login_required
def list_fonts():
    """JSON list of every TheDraw font found under TDF_FONTS_DIR, for
    the editor's font picker -- {name, type, index}. `index` is the
    entry's position in the scan (stable within one request/process;
    the picker sends it straight back to /fonts/render rather than a
    filename+offset pair, keeping the client-facing API simple). A
    fresh scan on every call, matching this feature's other file-
    browser routes (browse() above) -- a sysop adding fonts to the
    directory doesn't need a restart to see them."""
    require_admin_or_403()
    from ..features import tdf_fonts
    fonts_dir = current_app.config.get('TDF_FONTS_DIR', '')
    entries = tdf_fonts.scan_fonts(fonts_dir) if fonts_dir else []
    return jsonify([{'index': i, 'name': e['name'], 'type': e['type']}
                    for i, e in enumerate(entries)])


@ansi_bp.route('/fonts/install', methods=['POST'])
@login_required
def install_fonts():
    """Downloads the TheDraw font pack zip from TDF_FONTS_PACK_URL and
    extracts every .TDF file it finds into TDF_FONTS_DIR -- the "THEDRAW
    FONTS" panel's "Download & Install Fonts" button target, for the
    common case where a sysop hasn't set up a font pack by hand at all.

    Flattens on extraction (writes every .TDF by its own basename
    directly into TDF_FONTS_DIR, discarding whatever folder structure
    the zip itself used) rather than preserving the zip's own layout --
    tdf_fonts.scan_fonts() only ever scans directly inside TDF_FONTS_DIR,
    not recursively (matching ANetDRAW's own real font-scan behavior),
    so a zip shaped like ANetDRAW's own tdf-fonts.zip (a flat top level
    plus a SETS/ subfolder of mega-pack files) needs flattening before
    those fonts are actually visible to the picker. Confirmed no
    filename collisions exist between the two in the real pack this
    was built against -- a collision here would just mean one file
    quietly overwrites the other, same as re-running this twice
    harmlessly overwrites already-installed files with themselves.

    Streams the download to a temp file (never buffers the whole zip
    in memory) and caps it at a generous sanity limit -- this is meant
    for a multi-MB font pack, not an open-ended fetch."""
    require_admin_or_403()
    pack_url = current_app.config.get('TDF_FONTS_PACK_URL', '')
    if not pack_url:
        return jsonify({'error': 'TDF_FONTS_PACK_URL is not configured — '
                                  'set it in your environment/config first'}), 400
    fonts_dir = current_app.config.get('TDF_FONTS_DIR', '')
    if not fonts_dir:
        return jsonify({'error': 'TDF_FONTS_DIR is not configured'}), 400

    import zipfile

    from ._addon_download import DownloadError, download_zip_to_tempfile

    try:
        tmp_path = download_zip_to_tempfile(pack_url, user_agent='ANetBBS/ansi_editor')
    except DownloadError as exc:
        return jsonify({'error': str(exc)}), 502

    os.makedirs(fonts_dir, exist_ok=True)
    root = os.path.realpath(fonts_dir)
    try:
        installed = 0
        with zipfile.ZipFile(tmp_path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                name = os.path.basename(info.filename)
                if not name.lower().endswith('.tdf'):
                    continue
                # Zip-slip guard: confirm the flattened destination
                # really lands inside fonts_dir before writing (a
                # basename can't contain a path separator, but a
                # crafted filename like "..%2f..%2fetc" on some
                # platforms could still resolve oddly -- belt and
                # suspenders, same discipline as this project's other
                # upload-derived file writes).
                dest = os.path.realpath(os.path.join(fonts_dir, name))
                if dest != root and not dest.startswith(root + os.sep):
                    continue
                with z.open(info) as src, open(dest, 'wb') as out:
                    out.write(src.read())
                installed += 1
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        return jsonify({'error': f'install failed: {exc}'}), 500
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if installed == 0:
        return jsonify({'error': 'downloaded file contained no .TDF fonts'}), 500
    return jsonify({'installed': installed})


@ansi_bp.route('/fonts/render')
@login_required
def render_font_preview():
    """Renders ?index=N's font against ?text=... (fg/bg from ?fg=&bg=)
    and returns {width, height, cells} JSON -- cells matches the same
    {c,fg,bg}-or-null shape tdf_fonts.render_text() produces, ready for
    the editor's JS to paste directly into the grid the same way
    Copy/Paste already does. Re-scans + reloads the font fresh each
    call rather than caching -- these are quick (a handful of KB) and
    correctness (always the current file on disk) matters more than
    shaving a few ms off a preview keystroke."""
    require_admin_or_403()
    from ..features import tdf_fonts
    fonts_dir = current_app.config.get('TDF_FONTS_DIR', '')
    index = request.args.get('index', type=int)
    text = request.args.get('text', '')
    fg = max(0, min(15, request.args.get('fg', type=int) or 7))
    bg = max(0, min(7, request.args.get('bg', type=int) or 0))
    if index is None or not fonts_dir or not text:
        return jsonify({'error': 'missing index/text'}), 400
    entries = tdf_fonts.scan_fonts(fonts_dir)
    if index < 0 or index >= len(entries):
        return jsonify({'error': 'font not found'}), 404
    e = entries[index]
    font = tdf_fonts.load_font(e['file'], e['offset'])
    if font is None:
        return jsonify({'error': 'could not load font'}), 500
    result = tdf_fonts.render_text(font, text[:200], fg=fg, bg=bg)
    if result is None:
        return jsonify({'error': 'no matching characters in this font'}), 200
    return jsonify(result)


@ansi_bp.route('/browse')
@login_required
def browse():
    """List real .ans files on disk under each configured root
    (ANSI_EDITOR_BROWSE_DIRS) -- the "Open from disk" / "Save to disk"
    picker. Additive to the DB-backed AnsiArt library above, not a
    replacement for it."""
    require_admin_or_403()
    roots = []
    for label, root_abs in _browse_roots():
        try:
            files = sorted(
                f for f in os.listdir(root_abs)
                if f.lower().endswith('.ans') and
                os.path.isfile(os.path.join(root_abs, f)))
        except OSError:
            files = []
        roots.append({'label': label, 'files': files})
    art_id = request.args.get('art_id', type=int)
    return render_template('ansi_editor/browse.html', roots=roots, art_id=art_id)


@ansi_bp.route('/<int:art_id>/save-to-disk', methods=['POST'])
@login_required
def save_to_disk(art_id):
    """Write this art's current rendered .ans out to a real file under
    one of the configured browse roots -- alongside the existing
    DB save (this doesn't replace it, both stay in sync only if the
    sysop re-saves-to-disk after further DB edits)."""
    require_admin_or_403()
    art = AnsiArt.query.get_or_404(art_id)
    root_label = request.form.get('root') or ''
    filename = (request.form.get('filename') or '').strip()
    roots = dict(_browse_roots())
    root_abs = roots.get(root_label)
    if root_abs is None:
        flash('Unknown destination directory.', 'danger')
        return redirect(url_for('ansi_editor.edit', art_id=art.id))
    if not filename:
        filename = _slugify(art.name)
    filename = _FILE_NAME_RE.sub('_', filename)
    if not filename.lower().endswith('.ans'):
        filename += '.ans'
    dest = _safe_join(root_abs, filename)
    if dest is None:
        flash('Invalid filename.', 'danger')
        return redirect(url_for('ansi_editor.edit', art_id=art.id))
    try:
        with open(dest, 'w', encoding='cp437', errors='replace') as f:
            f.write(art.ansi_text or '')
    except OSError as exc:
        flash(f'Could not write file: {exc}', 'danger')
        return redirect(url_for('ansi_editor.edit', art_id=art.id))
    flash(f'Saved to {root_label}/{filename}.', 'success')
    return redirect(url_for('ansi_editor.edit', art_id=art.id))


@ansi_bp.route('/open-from-disk', methods=['POST'])
@login_required
def open_from_disk():
    """Load a real .ans file from one of the configured browse roots
    into a new AnsiArt row -- same decode/SAUCE-strip handling as
    import_ans() above (a file uploaded through the browser and a file
    already sitting in a configured directory are the same kind of
    input, just sourced differently), reusing parse_ansi_to_grid()
    rather than a second copy of that logic."""
    require_admin_or_403()
    root_label = request.form.get('root') or ''
    filename = (request.form.get('filename') or '').strip()
    roots = dict(_browse_roots())
    root_abs = roots.get(root_label)
    if root_abs is None:
        flash('Unknown source directory.', 'danger')
        return redirect(url_for('ansi_editor.browse'))
    src = _safe_join(root_abs, filename)
    if src is None or not os.path.isfile(src):
        flash('File not found.', 'danger')
        return redirect(url_for('ansi_editor.browse'))
    try:
        with open(src, 'rb') as f:
            raw = f.read()
    except OSError as exc:
        flash(f'Could not read file: {exc}', 'danger')
        return redirect(url_for('ansi_editor.browse'))

    try:
        from ..features.sauce import parse as _parse_sauce, strip as _strip_sauce
        sauce = _parse_sauce(raw)
        if sauce:
            raw = _strip_sauce(raw)
    except Exception:
        pass
    for enc in ('cp437', 'latin-1', 'utf-8'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            text = None
    if text is None:
        text = raw.decode('utf-8', errors='replace')

    grid = parse_ansi_to_grid(text, width=80, height=25)
    base_name = os.path.splitext(os.path.basename(filename))[0]
    slug = _slugify(base_name) + '-' + datetime.utcnow().strftime('%H%M%S')
    art = AnsiArt(
        name=base_name, slug=slug, width=grid['width'], height=grid['height'],
        grid_json=json.dumps(grid),
        ansi_text=render_ansi_text(grid),
        description=f'Opened from {root_label}/{filename}',
        created_by_id=current_user.id,
    )
    db.session.add(art)
    db.session.commit()
    flash(f'Opened {root_label}/{filename} as "{art.name}".', 'success')
    return redirect(url_for('ansi_editor.edit', art_id=art.id))
