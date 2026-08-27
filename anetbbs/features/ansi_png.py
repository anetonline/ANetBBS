# anetbbs/features/ansi_png.py
"""
Render a {width, height, cells:[{c,fg,bg}]} grid -- the same shape the web
ANSI editor (web/ansi_editor.py) produces and saves -- to a PNG image.

Exists for sharing art off-platform (social media posts, direct
downloads) where a live HTML/terminal render isn't usable -- a link posted
to Bluesky/Mastodon needs an actual image to preview.

Uses the vendored IBM VGA 9x16 bitmap font
(static/fonts/Ac437_IBM_VGA_9x16.ttf, converted once via a faithful WOFF1
table-extraction -- NOT a font-library rebuild, which risked silently
picking a different cmap subtable -- from the same .woff already used for
on-site CSS rendering; see static/fonts/LICENSE.txt) at its native pixel
size, then nearest-neighbor upscales the whole image so a pixel font
stays crisp instead of blurring through anti-aliased scaling.
"""
import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

_CELL_W, _CELL_H = 9, 16   # native metrics of the vendored VGA font
_FONT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'static', 'fonts', 'Ac437_IBM_VGA_9x16.ttf')

# Same 16-color, mIRC-ordered palette as the web editor's own JS palette
# (web/ansi_editor.py's grid fg 0-15 / bg 0-7 indices) -- kept in sync by
# hand since one copy lives in JS (browser canvas) and this one in Python
# (server-side PNG export); a color added to one needs the other updated.
PALETTE = [
    '#ffffff', '#000000', '#00007f', '#009300',
    '#ff0000', '#7f0000', '#9c009c', '#fc7f00',
    '#ffff00', '#00fc00', '#009393', '#00ffff',
    '#0000fc', '#ff00ff', '#7f7f7f', '#d2d2d2',
]

_font_cache = {}


def _font():
    if 'f' not in _font_cache:
        _font_cache['f'] = ImageFont.truetype(_FONT_PATH, _CELL_H)
    return _font_cache['f']


def render_grid_png(grid, scale=2):
    """grid: {width, height, cells:[{c,fg,bg}, ...]} (row-major, y=0 top).
    Returns PNG bytes."""
    width = int(grid.get('width', 80))
    height = int(grid.get('height', 25))
    cells = grid.get('cells', [])
    font = _font()

    img = Image.new('RGB', (width * _CELL_W, height * _CELL_H), PALETTE[1])
    draw = ImageDraw.Draw(img)

    for y in range(height):
        for x in range(width):
            idx = y * width + x
            cell = cells[idx] if idx < len(cells) else None
            c = ((cell.get('c') if cell else None) or ' ')[:1]
            fg = (int(cell.get('fg', 15)) & 0x0F) if cell else 15
            bg = (int(cell.get('bg', 1)) & 0x07) if cell else 1
            px, py = x * _CELL_W, y * _CELL_H
            draw.rectangle([px, py, px + _CELL_W - 1, py + _CELL_H - 1],
                           fill=PALETTE[bg])
            if c != ' ':
                draw.text((px, py), c, font=font, fill=PALETTE[fg])

    if scale and scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)

    buf = BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()
