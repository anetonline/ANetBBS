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
import re
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, Response, jsonify)
from flask_login import login_required, current_user

from ..models import db, AnsiArt
from .access_control import require_admin_or_403


ansi_bp = Blueprint('ansi_editor', __name__, url_prefix='/admin/ansi')


_SLUG_RE = re.compile(r'[^a-z0-9_-]+')


def _slugify(text):
    s = _SLUG_RE.sub('-', (text or 'art').lower()).strip('-')
    return s or 'art'


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
    art.width = int(grid.get('width') or art.width)
    art.height = int(grid.get('height') or art.height)
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
    """SAUCE 128-byte trailer (acid.org spec) — Pablo/Moebius read these."""
    import struct
    title  = (art.name or '')[:35].ljust(35)
    author = ((art.created_by.username if art.created_by else '') or '')[:20].ljust(20)
    group  = 'ANetBBS'.ljust(20)
    date   = (art.updated_at or art.created_at or datetime.utcnow()).strftime('%Y%m%d')
    body_bytes = (art.ansi_text or '').encode('cp437', errors='replace')
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
        (b' ' * 22)
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
