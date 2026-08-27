# anetbbs/features/social_card.py
"""
Render a retro-styled "highlight card" PNG for the auto-social-posting
queue (features/social_queue.py) -- a new #1 high score or a BBS
milestone, turned into something worth pasting into a Bluesky/Mastodon
post. Same vendored VGA font as features/ansi_png.py and the same
dark/phosphor-green visual language as the public /watch and postcard
pages, so everything this BBS shares off-site looks like one consistent
identity.

Not grid-based (unlike ansi_png.render_grid_png) -- this is plain
centered text on a fixed-size canvas, closer to a title card than a
terminal screen.
"""
import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

_FONT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'static', 'fonts', 'Ac437_IBM_VGA_9x16.ttf')

CARD_W, CARD_H = 1024, 512
BG = (5, 8, 6)
ACCENT = (57, 255, 136)
FG = (200, 255, 217)
FG_DIM = (95, 156, 124)

_font_cache = {}


def _font(size):
    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(_FONT_PATH, size)
    return _font_cache[size]


def _centered(draw, y, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text(((CARD_W - w) // 2, y), text, font=font, fill=fill)
    return bbox[3] - bbox[1]


def render_highlight_card(headline, detail_lines, bbs_name):
    """headline: short banner text (e.g. "NEW HIGH SCORE").
    detail_lines: list of strings shown below it, one per line.
    Returns PNG bytes, CARD_W x CARD_H."""
    img = Image.new('RGB', (CARD_W, CARD_H), BG)
    draw = ImageDraw.Draw(img)

    border = 6
    draw.rectangle([border, border, CARD_W - border, CARD_H - border],
                   outline=ACCENT, width=2)

    # Vertically center the headline + detail block as a whole, rather
    # than anchoring at a fixed offset -- one detail line and five look
    # equally intentional instead of one looking cramped-at-the-top.
    line_heights = [draw.textbbox((0, 0), headline.upper(), font=_font(48))[3]]
    line_heights += [draw.textbbox((0, 0), line, font=_font(32))[3] for line in detail_lines]
    gaps = 30 + 16 * len(detail_lines)
    total_h = sum(line_heights) + gaps
    y = max(70, (CARD_H - 90 - total_h) // 2)

    y += _centered(draw, y, headline.upper(), _font(48), ACCENT) + 30

    for line in detail_lines:
        y += _centered(draw, y, line, _font(32), FG) + 16

    footer = bbs_name
    fbbox = draw.textbbox((0, 0), footer, font=_font(20))
    fw = fbbox[2] - fbbox[0]
    draw.text(((CARD_W - fw) // 2, CARD_H - 60), footer,
              font=_font(20), fill=FG_DIM)

    buf = BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()
