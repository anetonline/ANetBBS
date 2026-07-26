# anetbbs/features/anedit.py
"""
ANEdit v1 — full-featured terminal message editor for ANetBBS.

Beyond Synchronet SlyEdit/DCTEdit/IceEdit:
  - Undo/redo stack  (Ctrl+Z / Ctrl+R)
  - Block mark/cut/copy/paste  (F3, Ctrl+X/C/V)
  - Find + Replace  (Ctrl+F / Ctrl+H)
  - Word/char count live in status bar
  - Smart word-wrap  (wraps as you type)
  - Draft auto-save + recovery on next entry
  - Three color themes  (F9 to cycle)
  - Ctrl+Left/Right word navigation
  - Beautiful 79×23 double-border frame

Entry point:
    text = await launch_anedit(session, quote="", subject="", username="guest")
Returns the composed string, or None if the user aborted.
"""
import asyncio
import os
import re
import time
from typing import Optional

# ── Layout ─────────────────────────────────────────────────────────────────────
_W      = 79          # terminal width
_H      = 23          # terminal height
_TW     = _W - 2      # inner text width  (cols 2-78 = 77 chars)
_TEXT_T = 4           # first text row (1-indexed)
_TEXT_B = 20          # last text row
_TEXT_H = _TEXT_B - _TEXT_T + 1   # 17 visible lines
_MAX_LINES = 500

# ── ANSI helpers ───────────────────────────────────────────────────────────────
_E = "\x1b"
def _mv(r, c):   return f"{_E}[{r};{c}H"
def _cls():      return f"{_E}[2J{_E}[H"
def _alt_on():   return f"{_E}[?1049h"
def _alt_off():  return f"{_E}[?1049l"
def _hide():     return f"{_E}[?25l"
def _show():     return f"{_E}[?25h"
def _reset():    return f"{_E}[0m"
def _bold():     return f"{_E}[1m"
def _rev():      return f"{_E}[7m"
def _under():    return f"{_E}[4m"
def _fg(n):      return f"{_E}[38;5;{n}m"
def _bg(n):      return f"{_E}[48;5;{n}m"
def _clreol():   return f"{_E}[K"

_CSI_FULL = re.compile(r'\x1b\[[^@-~]*[@-~]|\x1b.')

def _ansi_trunc(line: str, maxlen: int) -> str:
    """Truncate line to maxlen *visible* characters, preserving ANSI sequences."""
    out = []; visible = 0; i = 0
    while i < len(line):
        m = _CSI_FULL.match(line, i)
        if m:
            out.append(m.group(0)); i = m.end()
        else:
            if visible < maxlen:
                out.append(line[i]); visible += 1
            i += 1
    return ''.join(out)

# ── Themes ─────────────────────────────────────────────────────────────────────
_THEMES = [
    {   # Cyan
        'name':      'Cyan',
        'border':    _fg(51),
        'title':     _bold() + _fg(255),
        'vtitle':    _bold() + _fg(87),
        'ruler_dim': _fg(24),
        'ruler_num': _fg(87),
        'stat_bg':   _bg(17)  + _fg(252),
        'stat_hi':   _bg(17)  + _bold() + _fg(51),
        'hint_bg':   _bg(234) + _fg(244),
        'hint_key':  _bg(234) + _bold() + _fg(51),
        'sel':       _bg(31)  + _fg(255),
        'quote':     _fg(179),
        'dirty':     _bold()  + _fg(196),
        'misspell':  _under() + _fg(196),
        'info':      _bold()  + _fg(226),
    },
    {   # Green
        'name':      'Green',
        'border':    _fg(46),
        'title':     _bold() + _fg(255),
        'vtitle':    _bold() + _fg(120),
        'ruler_dim': _fg(22),
        'ruler_num': _fg(120),
        'stat_bg':   _bg(22)  + _fg(252),
        'stat_hi':   _bg(22)  + _bold() + _fg(46),
        'hint_bg':   _bg(234) + _fg(244),
        'hint_key':  _bg(234) + _bold() + _fg(46),
        'sel':       _bg(34)  + _fg(255),
        'quote':     _fg(179),
        'dirty':     _bold()  + _fg(196),
        'misspell':  _under() + _fg(196),
        'info':      _bold()  + _fg(226),
    },
    {   # Amber
        'name':      'Amber',
        'border':    _fg(214),
        'title':     _bold() + _fg(255),
        'vtitle':    _bold() + _fg(223),
        'ruler_dim': _fg(130),
        'ruler_num': _fg(223),
        'stat_bg':   _bg(52)  + _fg(252),
        'stat_hi':   _bg(52)  + _bold() + _fg(214),
        'hint_bg':   _bg(234) + _fg(244),
        'hint_key':  _bg(234) + _bold() + _fg(214),
        'sel':       _bg(166) + _fg(255),
        'quote':     _fg(190),
        'dirty':     _bold()  + _fg(196),
        'misspell':  _under() + _fg(196),
        'info':      _bold()  + _fg(226),
    },
]


# ── Spell check ───────────────────────────────────────────────────────────────
# pyspellchecker bundles its own dictionary (no network, no external file) —
# see requirements.txt. Import lazily and degrade silently if it's ever
# missing, matching the markdown/bleach graceful-degradation pattern used
# elsewhere in this codebase (web_app.py).
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_spellchecker = None
_spellchecker_tried = False


def _get_spellchecker():
    global _spellchecker, _spellchecker_tried
    if not _spellchecker_tried:
        _spellchecker_tried = True
        try:
            from spellchecker import SpellChecker
            _spellchecker = SpellChecker()
        except ImportError:
            _spellchecker = None
    return _spellchecker


def _find_misspelled(line: str) -> list:
    """Return [(start, end, word), ...] for misspelled words in *line*.
    Skips quoted lines (caller's job) and all-caps tokens (BBS/ANSI jargon
    like SSH, QWK, ANSI reads as a false positive far more often than a
    real typo)."""
    sp = _get_spellchecker()
    if sp is None:
        return []
    candidates = []
    words = {}
    for m in _WORD_RE.finditer(line):
        w = m.group(0)
        if len(w) < 2 or (w.isupper() and len(w) > 1):
            continue
        words[w.lower()] = words.get(w.lower(), []) + [m]
    if not words:
        return []
    unknown = sp.unknown(words.keys())
    for w in unknown:
        for m in words[w]:
            candidates.append((m.start(), m.end(), m.group(0)))
    candidates.sort()
    return candidates


# ── Key parser ─────────────────────────────────────────────────────────────────
class _Keys:
    def __init__(self, encoding: str = 'cp437'):
        self._buf = b""
        self._enc = encoding

    def feed(self, data: bytes):
        self._buf += data

    def next(self) -> Optional[str]:
        if not self._buf:
            return None
        b = self._buf

        if b[0:1] == b'\x1b':
            if len(b) < 2:
                return None

            if b[1:2] == b'[':
                if len(b) < 3:
                    return None
                seq = b[2:].decode('latin-1', errors='replace')
                end = 0
                while end < len(seq) and not seq[end].isalpha() and seq[end] != '~':
                    end += 1
                if end == len(seq):
                    return None
                full = seq[:end + 1]
                self._buf = b[3 + end:]
                csi = {
                    'A':'UP','B':'DOWN','C':'RIGHT','D':'LEFT',
                    'H':'HOME','F':'END',
                    'P':'F1','Q':'F2','R':'F3','S':'F4',
                }
                if full in csi:
                    return csi[full]
                tbl = {
                    '2~':'INS',  '3~':'DEL',
                    '5~':'PGUP', '6~':'PGDN',
                    '15~':'F5',  '17~':'F6',  '18~':'F7',  '19~':'F8',
                    '20~':'F9',  '21~':'F10', '23~':'F11', '24~':'F12',
                    '1;5A':'CTRL_UP',   '1;5B':'CTRL_DOWN',
                    '1;5C':'CTRL_RIGHT','1;5D':'CTRL_LEFT',
                    '1;5H':'CTRL_HOME', '1;5F':'CTRL_END',
                    '1;2C':'SHIFT_RIGHT','1;2D':'SHIFT_LEFT',
                }
                return tbl.get(full)

            if b[1:2] == b'O':
                if len(b) < 3:
                    return None
                c = chr(b[2])
                self._buf = b[3:]
                return {'P':'F1','Q':'F2','R':'F3','S':'F4',
                        'H':'HOME','F':'END'}.get(c)

            # Alt+key
            if len(b) >= 2:
                c = b[1:2].decode('latin-1', errors='replace')
                self._buf = b[2:]
                amap = {'s':'ALT_S','x':'ALT_X','z':'ALT_Z'}
                return amap.get(c.lower(), f'ALT_{c.upper()}')

        # Single character — encoding-aware
        enc = self._enc
        if enc in ('utf-8', 'utf8'):
            # Determine UTF-8 sequence length from the leading byte
            fb = b[0]
            if fb < 0x80:
                n = 1
            elif fb < 0xC0:
                # Stray continuation byte — skip it
                self._buf = b[1:]
                return None
            elif fb < 0xE0:
                n = 2
            elif fb < 0xF0:
                n = 3
            else:
                n = 4
            if len(b) < n:
                return None  # Wait for the rest of the sequence
            try:
                c = b[:n].decode('utf-8')
            except UnicodeDecodeError:
                self._buf = b[1:]
                return None
            self._buf = b[n:]
        else:
            # CP437 or latin-1: each byte is one character
            try:
                c = b[0:1].decode(enc, errors='replace')
            except LookupError:
                c = b[0:1].decode('latin-1', errors='replace')
            self._buf = b[1:]
        ctrl = {
            '\r':'ENTER', '\n':'ENTER',
            '\x7f':'BACKSPACE', '\x08':'BACKSPACE',
            '\t':'TAB',
            '\x02':'CTRL_B',   # block mark
            '\x03':'CTRL_C',   # copy
            '\x04':'CTRL_D',   # delete word
            '\x06':'CTRL_F',   # find
            '\x07':'CTRL_G',   # delete char (WordStar compat)
            '\x08':'BACKSPACE',
            '\x0b':'CTRL_K',   # kill to EOL
            '\x0c':'CTRL_L',   # refresh
            '\x0e':'CTRL_N',   # new line below cursor
            '\x11':'CTRL_Q',   # quit
            '\x12':'CTRL_R',   # redo
            '\x13':'CTRL_S',   # save draft
            '\x14':'CTRL_T',   # delete word right (alt)
            '\x15':'CTRL_U',   # undo alt
            '\x16':'CTRL_V',   # paste
            '\x17':'CTRL_W',   # send/submit
            '\x18':'CTRL_X',   # cut
            '\x19':'CTRL_Y',   # delete line
            '\x1a':'CTRL_Z',   # undo
        }
        if c in ctrl:
            return ctrl[c]
        return c


# ── Undo stack ─────────────────────────────────────────────────────────────────
class _UndoStack:
    MAX = 60

    def __init__(self, lines: list):
        self._stack = [list(lines)]
        self._pos   = 0

    def push(self, lines: list):
        self._stack = self._stack[:self._pos + 1]
        self._stack.append(list(lines))
        if len(self._stack) > self.MAX:
            self._stack.pop(0)
        self._pos = len(self._stack) - 1

    def undo(self) -> Optional[list]:
        if self._pos > 0:
            self._pos -= 1
            return list(self._stack[self._pos])
        return None

    def redo(self) -> Optional[list]:
        if self._pos < len(self._stack) - 1:
            self._pos += 1
            return list(self._stack[self._pos])
        return None

    @property
    def can_undo(self): return self._pos > 0
    @property
    def can_redo(self): return self._pos < len(self._stack) - 1


# ── Screen renderer ────────────────────────────────────────────────────────────
class _Screen:
    def __init__(self, theme: dict):
        self.t = theme

    # ── Frame ──────────────────────────────────────────────────────────────────
    def draw_frame(self, subject: str, modified: bool) -> str:
        t  = self.t
        b  = t['border']
        W  = _W
        o  = [_reset(), _hide()]

        # Row 1: top border + title
        tag   = " ANEdit v1 "
        subj  = f" {subject[:30]} " if subject else ""
        mid   = f"{tag}{'■' if modified else '·'}{subj}"
        pad   = W - 2 - len(mid)
        lp    = "═" * (pad // 2)
        rp    = "═" * (pad - pad // 2)
        o.append(_mv(1, 1) + b + "╔" + lp
                 + t['title'] + mid + b
                 + rp + "╗" + _reset())

        # Row 2: ruler
        o.append(self.draw_ruler())

        # Row 3: separator
        o.append(_mv(3, 1) + b + "╠" + "═" * (W - 2) + "╣" + _reset())

        # Rows 4-20: side borders
        for r in range(_TEXT_T, _TEXT_B + 1):
            o.append(_mv(r, 1)   + b + "║" + _reset())
            o.append(_mv(r, W)   + b + "║" + _reset())

        # Row 21: separator
        o.append(_mv(21, 1) + b + "╠" + "═" * (W - 2) + "╣" + _reset())

        # Row 23: bottom border + hints
        hints = (
            " /?:Help  ^W:Send  ^S:Draft  ^F:Find  "
            "/m:Mark  ^Z:Undo  ^R:Redo  /t:Theme "
        )
        ph  = W - 2 - len(hints)
        rph = "═" * max(ph, 0)
        o.append(_mv(23, 1) + b + "╚" + hints[:W-2] + rph + "╝" + _reset())

        return "".join(o)

    def draw_ruler(self) -> str:
        t   = self.t
        b   = t['border']
        out = [_mv(2, 1) + b + "║" + _reset()]
        ruler = []
        for i in range(1, _TW + 1):
            if i % 10 == 0:
                s = str(i)
                ruler.append((i, s, 'num'))
            elif i % 5 == 0:
                ruler.append((i, '+', 'dim'))
            else:
                ruler.append((i, '-', 'dim'))
        # Overlay numbers: a number occupies its last digit's column
        display = [' '] * _TW
        styles  = ['dim'] * _TW
        for i in range(1, _TW + 1):
            if i % 10 == 0:
                s = str(i)
                for j, ch in enumerate(s):
                    pos = i - len(s) + j
                    if 0 <= pos < _TW:
                        display[pos] = ch
                        styles[pos]  = 'num'
            elif i % 5 == 0:
                if styles[i - 1] == 'dim':
                    display[i - 1] = '+'
            else:
                if display[i - 1] == ' ':
                    display[i - 1] = '-'

        result = []
        cur_style = None
        for ch, st in zip(display, styles):
            color = t['ruler_num'] if st == 'num' else t['ruler_dim']
            if color != cur_style:
                result.append(color)
                cur_style = color
        # Simpler: just build the string
        result2 = []
        for ch, st in zip(display, styles):
            color = t['ruler_num'] if st == 'num' else t['ruler_dim']
            result2.append(color + ch)
        out.append("".join(result2) + _reset())
        out.append(b + "║" + _reset())
        return "".join(out)

    # ── Text area ──────────────────────────────────────────────────────────────
    def draw_text(self, lines: list, scroll: int,
                  mark: Optional[tuple], cy: int, cx: int,
                  misspell: Optional[dict] = None) -> str:
        t  = self.t
        o  = []
        misspell = misspell or {}

        # Normalise selection bounds
        sel_s = sel_e = None
        if mark is not None:
            a = mark
            b2 = (cy, cx)
            sel_s = min(a, b2)
            sel_e = max(a, b2)

        for i in range(_TEXT_H):
            ly  = scroll + i
            row = _TEXT_T + i
            o.append(_mv(row, 2))

            if ly >= len(lines):
                o.append(_reset() + " " * _TW)
                continue

            line    = lines[ly]
            padded  = line[:_TW].ljust(_TW)
            is_q    = line.lstrip().startswith('>')
            spans   = misspell.get(ly)

            if sel_s is None and not spans:
                # No selection, no misspellings on this line — fast path
                col = t['quote'] if is_q else _reset()
                o.append(col + padded + _reset())
            else:
                # Render char-by-char for selection and/or misspell underline
                chunks = []
                for ci in range(_TW):
                    pos = (ly, ci)
                    in_sel = sel_s is not None and sel_s <= pos < sel_e
                    in_mis = spans is not None and any(s <= ci < e for s, e, _ in spans)
                    ch = padded[ci]
                    if in_sel and in_mis:
                        chunks.append(t['sel'] + _under() + ch)
                    elif in_sel:
                        chunks.append(t['sel'] + ch)
                    elif in_mis:
                        chunks.append(t['misspell'] + ch)
                    else:
                        chunks.append((t['quote'] if is_q else _reset()) + ch)
                o.append("".join(chunks) + _reset())

        return "".join(o)

    # ── Status bar ─────────────────────────────────────────────────────────────
    def draw_status(self, cy: int, cx: int, total: int,
                    words: int, chars: int,
                    overwrite: bool, modified: bool,
                    theme_name: str, flash: str) -> str:
        t     = self.t
        mode  = "OVR" if overwrite else "INS"
        dirty = "■" if modified else " "
        ln    = f"Ln:{cy+1}/{total}"
        col   = f"Col:{cx+1}"
        wc    = f"W:{words}"
        cc    = f"C:{chars}"
        th    = f"[{theme_name}]"

        if flash:
            msg = flash.center(_TW)[:_TW]
            return (_mv(22, 2) + t['stat_bg'] + t['info']
                    + msg + _reset())

        # Simpler, safe version:
        stat = f"{dirty} {ln}  {col}  {wc}  {cc}  [{mode}]  {th}"
        stat = stat[:_TW].ljust(_TW)
        return _mv(22, 2) + t['stat_bg'] + stat + _reset()

    # ── Cursor ─────────────────────────────────────────────────────────────────
    def move_cursor(self, cy: int, cx: int, scroll: int) -> str:
        row = _TEXT_T + (cy - scroll)
        col = cx + 2
        if _TEXT_T <= row <= _TEXT_B:
            return _mv(row, col) + _show()
        return _hide()

    # ── Overlay box (generic) ──────────────────────────────────────────────────
    def box(self, title: str, lines: list, r1: int, c1: int) -> str:
        t   = self.t
        b   = t['border']
        w   = max(len(title) + 4, max((len(l) for l in lines), default=0) + 4)
        w   = min(w, _W - c1 - 1)
        o   = [_reset(), _hide()]
        # top
        o.append(_mv(r1, c1) + b + "╔" + "═" * (w - 2)
                 + "╗" + _reset())
        # title row
        ttl = f" {title} ".center(w - 2, "═")
        o.append(_mv(r1 + 1, c1) + b + "║" + t['title'] + ttl + b + "║" + _reset())
        # content lines
        for i, ln in enumerate(lines):
            safe = ln[:w - 4]
            pad  = " " + safe.ljust(w - 4) + " "
            o.append(_mv(r1 + 2 + i, c1) + b + "║" + _reset()
                     + pad + b + "║" + _reset())
        # bottom
        o.append(_mv(r1 + 2 + len(lines), c1) + b + "╚" + "═" * (w - 2)
                 + "╝" + _reset())
        return "".join(o)


# ── Main editor ────────────────────────────────────────────────────────────────
class ANEdit:
    def __init__(self, session, lines: list, subject: str = "",
                 draft_path: str = "", theme_idx: int = 0,
                 tagline_picker=None):
        self.session    = session
        self.lines      = lines if lines else [""]
        self.subject    = subject
        self.draft_path = draft_path
        self._tidx      = theme_idx % len(_THEMES)
        self._scr       = _Screen(_THEMES[self._tidx])

        # Tagline pool pick, deferred to SEND time (not asked up front,
        # before the user has even started typing -- reported live:
        # "it should not ask you about a tag line until you send, when
        # you send it should bring up the tagline"). `tagline_picker`,
        # if given, is a zero-arg async callable that shows the actual
        # browsable picker and returns the chosen text (or None) -- the
        # caller (bbs_ui.py) owns it since it needs DB/Flask-app-context
        # access this UI module deliberately doesn't have; this module
        # just calls it opaquely, once, right before finalizing a send.
        self._tagline_picker = tagline_picker

        self.cy         = 0
        self.cx         = 0
        self.scroll     = 0
        self.overwrite  = False
        self.modified   = False

        self._mark:  Optional[tuple] = None   # (y, x) anchor, or None
        self._clip:  list            = []     # clipboard lines
        self._undo   = _UndoStack(self.lines)
        self._keys   = _Keys(getattr(session, 'encoding', 'cp437'))

        self._find_q    = ""
        self._repl_q    = ""
        self._find_case = False

        self._flash_msg  = ""
        self._flash_time = 0.0
        self._last_save  = time.time()
        self._typing_run = False   # True while user is in a continuous typing run

        # Dirty flags
        self._df = True   # frame
        self._dt = True   # text
        self._ds = True   # status
        self._dc = True   # cursor

        self.done    = False   # user submitted
        self.aborted = False   # user aborted

    # ── I/O ────────────────────────────────────────────────────────────────────
    async def _wr(self, text: str):
        try:
            await self.session.write(text)
        except Exception:
            pass

    async def _read_key(self) -> Optional[str]:
        k = self._keys.next()
        if k is not None:
            return k
        try:
            data = await asyncio.wait_for(self.session.read_raw(64), timeout=0.08)
            if data:
                self._keys.feed(data)
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass
        k = self._keys.next()
        if k is not None:
            return k
        # ESC disambiguation
        if self._keys._buf == b'\x1b':
            try:
                data = await asyncio.wait_for(self.session.read_raw(8), timeout=0.09)
                if data:
                    self._keys.feed(data)
            except asyncio.TimeoutError:
                self._keys._buf = b''
                return 'ESC'
            except Exception:
                self._keys._buf = b''
                return 'ESC'
        return self._keys.next()

    # ── Flash message ──────────────────────────────────────────────────────────
    def _flash(self, msg: str):
        self._flash_msg  = msg
        self._flash_time = time.time()
        self._ds = True

    # ── Entry / exit ───────────────────────────────────────────────────────────
    async def run(self) -> Optional[str]:
        await self._wr(_alt_on() + _cls() + _hide())
        # Check for recoverable draft
        draft_loaded = False
        if self.draft_path and os.path.exists(self.draft_path):
            if not any(self.lines) or self.lines == [""]:
                try:
                    with open(self.draft_path, encoding='utf-8', errors='replace') as f:
                        saved = [l.rstrip('\r\n') for l in f.readlines()]
                    if saved:
                        self.lines = saved
                        draft_loaded = True
                except Exception:
                    pass
        if draft_loaded:
            self._flash("Draft recovered - Ctrl+S to keep, Ctrl+Q to discard")

        self._df = self._dt = self._ds = self._dc = True
        try:
            while not self.done and not self.aborted:
                await self._redraw()
                key = await self._read_key()
                if key:
                    # End typing run on any non-printable key
                    if not (len(key) == 1 and (key.isprintable() or key == ' ')):
                        self._typing_run = False
                    await self._handle(key)
                # Flash timeout
                if self._flash_msg and time.time() - self._flash_time > 2.5:
                    self._flash_msg = ""
                    self._ds = True
                # Auto-save draft every 30 s if modified
                if (self.modified and self.draft_path
                        and time.time() - self._last_save > 30):
                    self._save_draft()
        finally:
            await self._wr(_alt_off() + _show() + _reset() + "\r\n")

        if self.done:
            if self.draft_path and os.path.exists(self.draft_path):
                try:
                    os.remove(self.draft_path)
                except Exception:
                    pass
            text = "\r\n".join(self.lines)
            if self._tagline_picker:
                tagline = await self._tagline_picker()
                if tagline:
                    # Classic "-- " signature-separator format so mail
                    # readers that recognize it can fold/hide it like a
                    # real signature. Kept as a plain inline format
                    # (rather than importing
                    # anetbbs.models.format_tagline_append) since this
                    # module has no DB/model dependency anywhere else
                    # and shouldn't gain one just for a one-line format.
                    text = text.rstrip('\r\n') + "\r\n\r\n-- \r\n" + tagline + "\r\n"
            return text
        return None

    # ── Redraw ─────────────────────────────────────────────────────────────────
    async def _redraw(self):
        out = []
        if self._df:
            out.append(self._scr.draw_frame(self.subject, self.modified))
            self._dt = self._ds = True
            self._df = False
        if self._dt:
            out.append(self._scr.draw_text(
                self.lines, self.scroll, self._mark, self.cy, self.cx,
                self._visible_misspellings()))
            self._dt = False
        if self._ds:
            fm = self._flash_msg if self._flash_msg else ""
            out.append(self._scr.draw_status(
                self.cy, self.cx, len(self.lines),
                self._word_count(), self._char_count(),
                self.overwrite, self.modified,
                _THEMES[self._tidx]['name'], fm))
            self._ds = False
        if self._dc or out:
            out.append(self._scr.move_cursor(self.cy, self.cx, self.scroll))
            self._dc = False
        if out:
            await self._wr("".join(out))

    # ── Stats ──────────────────────────────────────────────────────────────────
    def _word_count(self) -> int:
        text = " ".join(self.lines)
        return len(text.split()) if text.strip() else 0

    def _char_count(self) -> int:
        return sum(len(l) for l in self.lines)

    # ── Spell check ────────────────────────────────────────────────────────────
    def _visible_misspellings(self) -> dict:
        """Misspelled-word spans for the currently visible lines only —
        recomputed fresh each redraw (cheap: ~17 lines, sub-millisecond),
        so no cache/invalidation bookkeeping is needed. Quoted lines
        (someone else's text) are never flagged."""
        if _get_spellchecker() is None:
            return {}
        out = {}
        for ly in range(self.scroll, min(self.scroll + _TEXT_H, len(self.lines))):
            line = self.lines[ly]
            if line.lstrip().startswith('>'):
                continue
            spans = _find_misspelled(line)
            if spans:
                out[ly] = spans
        return out

    # ── Scroll helper ──────────────────────────────────────────────────────────
    def _ensure_visible(self):
        if self.cy < self.scroll:
            self.scroll = self.cy
            self._dt = True
        elif self.cy >= self.scroll + _TEXT_H:
            self.scroll = self.cy - _TEXT_H + 1
            self._dt = True
        self._dc = True

    # ── Navigation ─────────────────────────────────────────────────────────────
    def _clamp_cx(self):
        self.cx = min(self.cx, len(self.lines[self.cy]))

    def _up(self):
        if self.cy > 0:
            self.cy -= 1
            self._clamp_cx()
            self._ensure_visible()

    def _down(self):
        if self.cy < len(self.lines) - 1:
            self.cy += 1
            self._clamp_cx()
            self._ensure_visible()

    def _left(self):
        if self.cx > 0:
            self.cx -= 1
        elif self.cy > 0:
            self.cy -= 1
            self.cx = len(self.lines[self.cy])
            self._ensure_visible()
        self._dc = True

    def _right(self):
        line = self.lines[self.cy]
        if self.cx < len(line):
            self.cx += 1
        elif self.cy < len(self.lines) - 1:
            self.cy += 1
            self.cx = 0
            self._ensure_visible()
        self._dc = True

    def _home(self):
        # Toggle: first non-space, then col 0
        line    = self.lines[self.cy]
        first_c = len(line) - len(line.lstrip())
        self.cx = 0 if self.cx != first_c else 0
        self.cx = first_c if self.cx == 0 and first_c != 0 else 0
        self._dc = True

    def _end(self):
        self.cx = len(self.lines[self.cy])
        self._dc = True

    def _pgup(self):
        self.cy     = max(0, self.cy - _TEXT_H)
        self.scroll = max(0, self.scroll - _TEXT_H)
        self._clamp_cx()
        self._dt = self._dc = True

    def _pgdn(self):
        last        = len(self.lines) - 1
        self.cy     = min(last, self.cy + _TEXT_H)
        self.scroll = max(0, min(self.cy - _TEXT_H + 1,
                                  len(self.lines) - _TEXT_H))
        self._clamp_cx()
        self._dt = self._dc = True

    def _doc_start(self):
        self.cy = self.cx = self.scroll = 0
        self._dt = self._dc = True

    def _doc_end(self):
        self.cy = len(self.lines) - 1
        self.cx = len(self.lines[self.cy])
        self._ensure_visible()

    def _word_left(self):
        if self.cx > 0:
            line = self.lines[self.cy]
            i = self.cx - 1
            while i > 0 and line[i - 1].isspace():
                i -= 1
            while i > 0 and not line[i - 1].isspace():
                i -= 1
            self.cx = i
        elif self.cy > 0:
            self.cy -= 1
            self.cx = len(self.lines[self.cy])
            self._ensure_visible()
        self._dc = True

    def _word_right(self):
        line = self.lines[self.cy]
        i    = self.cx
        while i < len(line) and not line[i].isspace():
            i += 1
        while i < len(line) and line[i].isspace():
            i += 1
        if i <= len(line):
            self.cx = i
        elif self.cy < len(self.lines) - 1:
            self.cy += 1
            self.cx = 0
            self._ensure_visible()
        self._dc = True

    # ── Editing primitives ─────────────────────────────────────────────────────
    def _push_undo(self):
        self._undo.push(self.lines)

    def _mark_modified(self):
        self.modified = True
        self._ds = self._dc = True

    def _insert_char(self, ch: str):
        if not self._typing_run:
            self._push_undo()
            self._typing_run = True
        line = self.lines[self.cy]
        if self.overwrite and self.cx < len(line):
            self.lines[self.cy] = line[:self.cx] + ch + line[self.cx + 1:]
        else:
            self.lines[self.cy] = line[:self.cx] + ch + line[self.cx:]
        self.cx += 1
        self._mark_modified()
        self._dt = True
        # Word-wrap if needed
        if len(self.lines[self.cy]) > _TW:
            self._wrap_line(self.cy)

    def _wrap_line(self, ly: int):
        line = self.lines[ly]
        if len(line) <= _TW:
            return
        # Find best break point: last space within _TW
        wp = _TW
        for i in range(_TW - 1, max(_TW - 25, 0) - 1, -1):
            if i < len(line) and line[i] == ' ':
                wp = i
                break
        before   = line[:wp].rstrip()
        overflow = line[wp:].lstrip()
        self.lines[ly] = before

        if self.cx > wp:
            # Cursor was in the wrapped-off part
            self.cy += 1
            self.cx  = self.cx - wp
            if self.cx < 0:
                self.cx = 0

        if ly + 1 < len(self.lines):
            next_line = self.lines[ly + 1]
            # Don't merge with a quote line
            if next_line.lstrip().startswith('>') or next_line == "":
                self.lines.insert(ly + 1, overflow)
            else:
                sep = " " if overflow and next_line else ""
                self.lines[ly + 1] = overflow + sep + next_line
        else:
            self.lines.append(overflow)

        if len(self.lines) > _MAX_LINES:
            self.lines = self.lines[:_MAX_LINES]

        self._ensure_visible()
        self._dt = True

    def _insert_newline(self):
        self._push_undo()
        self._typing_run = False
        line = self.lines[self.cy]
        self.lines[self.cy] = line[:self.cx]
        self.lines.insert(self.cy + 1, line[self.cx:])
        self.cy += 1
        self.cx  = 0
        self._mark_modified()
        self._dt = True
        self._ensure_visible()

    def _backspace(self):
        if self.cx > 0:
            if not self._typing_run:
                self._push_undo()
            line = self.lines[self.cy]
            self.lines[self.cy] = line[:self.cx - 1] + line[self.cx:]
            self.cx -= 1
            self._typing_run = True
        elif self.cy > 0:
            self._push_undo()
            self._typing_run = False
            prev      = self.lines[self.cy - 1]
            curr      = self.lines[self.cy]
            self.cx   = len(prev)
            self.lines[self.cy - 1] = prev + curr
            self.lines.pop(self.cy)
            self.cy  -= 1
            self._ensure_visible()
        self._mark_modified()
        self._dt = True

    def _delete_char(self):
        self._push_undo()
        self._typing_run = False
        line = self.lines[self.cy]
        if self.cx < len(line):
            self.lines[self.cy] = line[:self.cx] + line[self.cx + 1:]
        elif self.cy < len(self.lines) - 1:
            self.lines[self.cy] = line + self.lines[self.cy + 1]
            self.lines.pop(self.cy + 1)
        self._mark_modified()
        self._dt = True

    def _delete_line(self):
        self._push_undo()
        self._typing_run = False
        if len(self.lines) > 1:
            self.lines.pop(self.cy)
            self.cy = min(self.cy, len(self.lines) - 1)
        else:
            self.lines[0] = ""
        self.cx = min(self.cx, len(self.lines[self.cy]))
        self._mark_modified()
        self._dt = True
        self._ensure_visible()
        self._flash("Line deleted")

    def _kill_to_eol(self):
        self._push_undo()
        self._typing_run = False
        line = self.lines[self.cy]
        if self.cx < len(line):
            self.lines[self.cy] = line[:self.cx]
        elif self.cy < len(self.lines) - 1:
            # Join with next line (delete the newline)
            self.lines[self.cy] = line + self.lines[self.cy + 1]
            self.lines.pop(self.cy + 1)
        self._mark_modified()
        self._dt = True

    def _delete_word(self):
        self._push_undo()
        self._typing_run = False
        line = self.lines[self.cy]
        i    = self.cx
        # Skip non-space chars
        while i < len(line) and not line[i].isspace():
            i += 1
        # Skip trailing spaces
        while i < len(line) and line[i].isspace():
            i += 1
        self.lines[self.cy] = line[:self.cx] + line[i:]
        self._mark_modified()
        self._dt = True

    def _insert_tab(self):
        spaces = 4 - (self.cx % 4)
        for _ in range(spaces):
            self._insert_char(' ')

    # ── Undo / Redo ────────────────────────────────────────────────────────────
    def _undo_action(self):
        result = self._undo.undo()
        if result is not None:
            self.lines = result
            self.cy    = min(self.cy, len(self.lines) - 1)
            self.cx    = min(self.cx, len(self.lines[self.cy]))
            self._dt = self._ds = True
            self._ensure_visible()
            self._flash("Undo")
        else:
            self._flash("Nothing to undo")

    def _redo_action(self):
        result = self._undo.redo()
        if result is not None:
            self.lines = result
            self.cy    = min(self.cy, len(self.lines) - 1)
            self.cx    = min(self.cx, len(self.lines[self.cy]))
            self._dt = self._ds = True
            self._ensure_visible()
            self._flash("Redo")
        else:
            self._flash("Nothing to redo")

    # ── Block mark / copy / cut / paste ────────────────────────────────────────
    def _toggle_mark(self):
        if self._mark is None:
            self._mark = (self.cy, self.cx)
            self._flash("Mark set - move cursor, then Ctrl+C/X")
        else:
            self._mark = None
            self._flash("Mark cleared")
        self._dt = True

    def _sel_bounds(self):
        if self._mark is None:
            return None
        a = self._mark
        b = (self.cy, self.cx)
        return min(a, b), max(a, b)

    def _copy_block(self):
        bounds = self._sel_bounds()
        if bounds is None:
            self._flash("No selection - /m to mark, then Ctrl+C/X")
            return
        (sy, sx), (ey, ex) = bounds
        self._clip = self._extract(sy, sx, ey, ex)
        self._mark = None
        self._dt   = True
        self._flash(f"Copied {len(self._clip)} line(s)")

    def _cut_block(self):
        bounds = self._sel_bounds()
        if bounds is None:
            self._flash("No selection - /m to mark, then Ctrl+C/X")
            return
        self._push_undo()
        (sy, sx), (ey, ex) = bounds
        self._clip = self._extract(sy, sx, ey, ex)
        self._delete_range(sy, sx, ey, ex)
        self._mark = None
        self._mark_modified()
        self._dt = True
        self._flash(f"Cut {len(self._clip)} line(s)")

    def _paste_block(self):
        if not self._clip:
            self._flash("Clipboard empty")
            return
        self._push_undo()
        clip = list(self._clip)
        line = self.lines[self.cy]
        before = line[:self.cx]
        after  = line[self.cx:]

        if len(clip) == 1:
            self.lines[self.cy] = before + clip[0] + after
            self.cx += len(clip[0])
        else:
            self.lines[self.cy] = before + clip[0]
            for i, cline in enumerate(clip[1:-1], 1):
                self.lines.insert(self.cy + i, cline)
            end_idx = self.cy + len(clip) - 1
            self.lines.insert(end_idx, clip[-1] + after)
            self.cy = end_idx
            self.cx = len(clip[-1])

        self._mark_modified()
        self._dt = True
        self._ensure_visible()
        self._flash("Pasted")

    def _extract(self, sy: int, sx: int, ey: int, ex: int) -> list:
        if sy == ey:
            return [self.lines[sy][sx:ex]]
        result = [self.lines[sy][sx:]]
        for y in range(sy + 1, ey):
            result.append(self.lines[y])
        result.append(self.lines[ey][:ex])
        return result

    def _delete_range(self, sy: int, sx: int, ey: int, ex: int):
        if sy == ey:
            line = self.lines[sy]
            self.lines[sy] = line[:sx] + line[ex:]
        else:
            first = self.lines[sy][:sx]
            last  = self.lines[ey][ex:]
            self.lines[sy] = first + last
            del self.lines[sy + 1:ey + 1]
        self.cy = sy
        self.cx = sx
        self._ensure_visible()

    # ── Draft ──────────────────────────────────────────────────────────────────
    def _save_draft(self):
        if not self.draft_path:
            return
        try:
            os.makedirs(os.path.dirname(self.draft_path), exist_ok=True)
            with open(self.draft_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(self.lines))
            self._last_save = time.time()
            self._flash("Draft saved")
        except Exception:
            self._flash("Draft save failed")

    # ── Theme ──────────────────────────────────────────────────────────────────
    def _cycle_theme(self):
        self._tidx = (self._tidx + 1) % len(_THEMES)
        self._scr  = _Screen(_THEMES[self._tidx])
        self._df = self._dt = self._ds = True
        self._flash(f"Theme: {_THEMES[self._tidx]['name']}")

    # ── Dialogs ────────────────────────────────────────────────────────────────
    async def _confirm_abort(self) -> bool:
        """Returns True if the user confirmed abort."""
        if not self.modified:
            self.aborted = True
            return True
        lines = [
            "",
            "  You have unsaved changes.",
            "  Abort anyway?",
            "",
            "  [Y] Yes - abandon message   [N] No - keep editing",
            "",
        ]
        box = self._scr.box("Abort?", lines, 8, 10)
        await self._wr(box)
        while True:
            key = await self._read_key()
            if key in ('y', 'Y'):
                self.aborted = True
                return True
            elif key in ('n', 'N', 'ESC'):
                self._df = self._dt = self._ds = True
                return False

    async def _show_help(self):
        """Full-screen help overlay.
        Strategy: draw the box with blank inner rows first, then write
        each content cell using absolute _mv() positioning.  ANSI escape
        codes in the cell strings never affect the box border positions."""
        t   = self._scr.t
        b   = t['border']
        hk  = t['hint_key']
        rst = _reset()

        # Box: full screen 1-23, cols 1-79
        BX   = 1; BW = _W; r1 = 1
        inn  = BW - 2     # 77 usable cols inside ║…║
        LC   = BX + 2     # left column screen col  (col 3)
        RC   = BX + 40    # right column screen col (col 41)

        def _blank(r):
            return _mv(r, BX) + b + "║" + rst + " " * inn + b + "║" + rst

        def _head(r, txt):
            pad = inn - 1 - len(txt)
            return (_mv(r, BX) + b + "║" + rst
                    + " " + hk + txt + rst + " " * max(pad, 0)
                    + b + "║" + rst)

        def _lc(r, k, v):
            return _mv(r, LC) + hk + f"{k:<17}" + rst + " " + t['hint_bg'] + v + rst

        def _rc(r, k, v):
            return _mv(r, RC) + hk + f"{k:<14}" + rst + " " + t['hint_bg'] + v + rst

        # ── Frame ──────────────────────────────────────────────────────────────
        ttl = " ANEdit v1 - Key Reference "
        lp  = "═" * ((inn - len(ttl)) // 2)
        rp  = "═" * (inn - len(ttl) - len(lp))
        o   = [rst, _hide(), _cls(),
               _mv(r1, BX) + b + "╔" + lp + t['title'] + ttl + b + rp + "╗" + rst]
        for r in range(r1 + 1, r1 + 22):
            o.append(_blank(r))
        o.append(_mv(r1 + 22, BX) + b + "╚" + "═" * inn + "╝" + rst)

        # ── Section 1: MOVEMENT / EDITING (rows 2-10) ─────────────────────────
        o.append(_head(r1 + 1, "MOVEMENT" + " " * 22 + "EDITING"))
        mvmt = [
            ("Arrow keys",     "Navigate",         "Enter",       "New line"),
            ("Home / End",     "Line start/end",   "Backspace",   "Delete left"),
            ("PgUp / PgDn",    "Scroll page",      "Del",         "Delete right"),
            ("Ctrl+Home/End",  "Doc start/end",    "Ctrl+Y",      "Delete line"),
            ("Ctrl+Left/Rt",   "Word jump",        "Ctrl+K",      "Kill to EOL"),
            ("Ins",            "INS/OVR toggle",   "Ctrl+T",      "Delete word"),
            ("Tab",            "Indent 4 spaces",  "Ctrl+N",      "New line below"),
        ]
        for i, (lk, lv, rk, rv) in enumerate(mvmt):
            o.append(_lc(r1 + 2 + i, lk, lv))
            o.append(_rc(r1 + 2 + i, rk, rv))

        # ── Section 2: CLIPBOARD / SLASH COMMANDS (rows 11-18) ────────────────
        o.append(_head(r1 + 10, "CLIPBOARD" + " " * 20 + "SLASH COMMANDS"))
        clip = [
            ("/m  Ctrl+B",   "Block mark on/off",  "/?  /help",    "This help"),
            ("Ctrl+C",       "Copy selection",      "/t  /theme",   "Cycle theme"),
            ("Ctrl+X",       "Cut selection",       "/find",        "Find"),
            ("Ctrl+V",       "Paste",               "/replace",     "Find & replace"),
            ("Ctrl+F",       "Find",                "/undo  /redo", "Undo / redo"),
            ("Ctrl+H",       "Find & replace",      "/cc",          "Color codes"),
            ("",             "",                    "/spell  /sp",  "Check spelling"),
        ]
        for i, (lk, lv, rk, rv) in enumerate(clip):
            o.append(_lc(r1 + 11 + i, lk, lv))
            o.append(_rc(r1 + 11 + i, rk, rv))

        # ── Section 3: SEND / ABORT (rows 18-21) ──────────────────────────────
        o.append(_head(r1 + 18, "SEND / ABORT"))
        send = [
            ("Ctrl+W  /send",  "Send/post message",   "Ctrl+Z  /undo", "Undo"),
            ("Ctrl+S  /save",  "Save draft",           "Ctrl+R  /redo", "Redo"),
            ("Esc  /q",        "Abort (with confirm)", "/send",         "Also sends"),
        ]
        for i, (lk, lv, rk, rv) in enumerate(send):
            o.append(_lc(r1 + 19 + i, lk, lv))
            o.append(_rc(r1 + 19 + i, rk, rv))

        # ── Tip / close hint (row 22) ──────────────────────────────────────────
        pak = "  Type / at line start for commands (e.g. /?  /t  /m  /send)  --  Press any key to close"
        pak = pak[:inn]
        o.append(_mv(r1 + 21, BX) + b + "║" + rst + t['info'] + pak.ljust(inn) + rst + b + "║" + rst)

        await self._wr("".join(o))

        while True:
            key = await self._read_key()
            if key:
                break

        self._df = self._dt = self._ds = True

    # ── Slash command entry ────────────────────────────────────────────────────
    # Triggered when '/' is typed at column 0.  Reads a short command word,
    # then dispatches or (on no match) inserts the typed text normally.
    _SLASH_HELP = (
        " Commands: /? or /help=Help  /t=Theme  /m=Mark  /cc=Colors"
        "  /find  /replace  /undo  /redo  /save  /send  /spell  /q=Abort"
    )
    _SLASH_MAP = {
        '?':       'help',   'help':    'help',
        't':       'theme',  'theme':   'theme',
        'm':       'mark',   'mark':    'mark',
        'cc':      'color',  'color':   'color',
        'find':    'find',   'f':       'find',
        'replace': 'replace','r':       'replace',
        'undo':    'undo',   'u':       'undo',   'z': 'undo',
        'redo':    'redo',
        'save':    'save',   's':       'save',
        'send':    'send',   'w':       'send',
        'spell':   'spell',  'sp':      'spell',
        'q':       'abort',  'quit':    'abort',  'abort': 'abort',
    }

    async def _slash_command(self):
        """Read /command from status bar; dispatch or fall back to text insert."""
        t    = self._scr.t
        cmd  = ""
        max_w = 20

        while True:
            disp = f"/{cmd}"
            bar  = (t['stat_bg'] + t['stat_hi'] + " /"
                    + _reset() + t['stat_bg']
                    + cmd.ljust(max_w)[:max_w] + "  (ESC=cancel) " + _reset())
            await self._wr(_mv(22, 2) + _hide() + bar
                           + _mv(22, len(disp) + 2) + _show())
            key = await self._read_key()
            if key is None:
                continue
            if key == 'ESC':
                self._ds = True
                return
            if key in ('ENTER', 'TAB', ' '):
                break
            if key == 'BACKSPACE':
                if cmd:
                    cmd = cmd[:-1]
                else:
                    self._ds = True
                    return
            elif len(key) == 1 and (key.isprintable() or key == '?'):
                cmd += key.lower()
                if len(cmd) > max_w:
                    break

        self._ds = True
        action = self._SLASH_MAP.get(cmd.strip())
        if action == 'help':
            await self._show_help()
        elif action == 'theme':
            self._cycle_theme()
        elif action == 'mark':
            self._toggle_mark()
        elif action == 'color':
            await self._color_picker()
        elif action == 'find':
            await self._find_dialog()
        elif action == 'replace':
            await self._replace_dialog()
        elif action == 'undo':
            self._undo_action()
        elif action == 'redo':
            self._redo_action()
        elif action == 'save':
            self._save_draft()
        elif action == 'send':
            self.done = True
        elif action == 'spell':
            await self._spell_check()
        elif action == 'abort':
            await self._confirm_abort()
        else:
            # Not a command — insert the typed characters as text
            self._flash(f"Unknown command '/{cmd}' - type /? for help")
            for ch in ('/' + cmd):
                self._insert_char(ch)

    async def _input_line(self, prompt: str, prefill: str = "") -> Optional[str]:
        """Single-line input in the hint bar row (row 22)."""
        t     = self._scr.t
        value = prefill
        cur   = len(value)
        max_w = _TW - len(prompt) - 2

        while True:
            disp = value[max(0, cur - max_w):cur + max_w]
            bar  = (t['stat_bg'] + " " + t['stat_hi'] + prompt + " "
                    + _reset() + t['stat_bg']
                    + disp.ljust(max_w)[:max_w] + " " + _reset())
            await self._wr(_mv(22, 2) + _hide() + bar
                           + _mv(22, len(prompt) + cur - max(0, cur - max_w) + 3)
                           + _show())
            key = await self._read_key()
            if key is None:
                continue
            if key == 'ESC':
                self._ds = True
                return None
            if key == 'ENTER':
                self._ds = True
                return value
            if key == 'BACKSPACE' and cur > 0:
                value = value[:cur - 1] + value[cur:]
                cur  -= 1
            elif key == 'DEL' and cur < len(value):
                value = value[:cur] + value[cur + 1:]
            elif key == 'LEFT' and cur > 0:
                cur -= 1
            elif key == 'RIGHT' and cur < len(value):
                cur += 1
            elif key == 'HOME':
                cur = 0
            elif key == 'END':
                cur = len(value)
            elif len(key) == 1 and (key.isprintable() or key == ' '):
                value = value[:cur] + key + value[cur:]
                cur  += 1

    async def _find_dialog(self):
        q = await self._input_line("Find:", self._find_q)
        if q is None:
            return
        self._find_q = q
        self._do_find(q, forward=True)

    async def _find_next(self, forward: bool = True):
        if not self._find_q:
            await self._find_dialog()
            return
        self._do_find(self._find_q, forward)

    def _do_find(self, q: str, forward: bool = True):
        if not q:
            return
        lines = self.lines
        fq    = q if self._find_case else q.lower()
        total = len(lines)

        # Search from cursor position
        start_y = self.cy
        start_x = self.cx + (1 if forward else 0)

        for delta in range(total + 1):
            ly = (start_y + delta) % total if forward else (start_y - delta) % total
            line = lines[ly]
            haystack = line if self._find_case else line.lower()

            start = (start_x if delta == 0 else 0) if forward else 0
            idx   = haystack.find(fq, start) if forward else haystack.rfind(fq)

            if idx != -1:
                self.cy = ly
                self.cx = idx
                self._ensure_visible()
                self._flash(f"Found at line {ly+1}")
                return

        self._flash(f"'{q}' not found")

    async def _replace_dialog(self):
        q = await self._input_line("Find:", self._find_q)
        if q is None:
            return
        self._find_q = q
        r = await self._input_line("Replace with:", self._repl_q)
        if r is None:
            return
        self._repl_q = r
        self._do_replace_all(q, r)

    def _do_replace_all(self, q: str, r: str):
        if not q:
            return
        count = 0
        self._push_undo()
        for i, line in enumerate(self.lines):
            new_line = (line.replace(q, r) if self._find_case
                        else re.sub(re.escape(q), r, line, flags=re.IGNORECASE))
            if new_line != line:
                self.lines[i] = new_line
                count += 1
        if count:
            self._mark_modified()
            self._dt = True
            self._flash(f"Replaced {count} occurrence(s)")
        else:
            self._flash(f"'{q}' not found")

    async def _color_picker(self):
        """Insert a Mystic |XX color code at cursor."""
        t  = self._scr.t
        names = ["Black","Blue","Green","Cyan","Red","Magenta",
                 "Brown","White","Gray","Lt.Blue","Lt.Green","Lt.Cyan",
                 "Lt.Red","Lt.Mag","Yellow","Br.White"]
        cmap  = [0,4,2,6,1,5,3,7,8,12,10,14,9,13,11,15]
        w     = 72
        r1    = 8

        def render():
            b  = t['border']
            o  = [_mv(r1, 4) + b + "╔" + "═"*(w-2) + "╗",
                  _mv(r1+1, 4) + b + "║" + t['title']
                  + " Color Code Picker - 0-9,A-F=FG  Shift=BG  Esc=cancel ".center(w-2)
                  + b + "║"]
            # FG row
            fg_row = ""
            for i, (name, cn) in enumerate(zip(names, cmap)):
                fg_row += _bg(cn) + _fg(15 if cn < 8 else 0) + f" {i:X}:{name[:7]:<7}" + _reset()
            o.append(_mv(r1+2, 4) + b + "║ " + _reset() + fg_row[:w-4] + b + " ║")
            o.append(_mv(r1+3, 4) + b + "╚" + "═"*(w-2) + "╝" + _reset())
            return "".join(o)

        await self._wr(render())
        while True:
            key = await self._read_key()
            if key is None:
                continue
            if key == 'ESC':
                break
            ch = key.upper() if len(key) == 1 else None
            if ch and ch in '0123456789ABCDEF':
                shift = key != key.lower() and key.isalpha()
                code  = f"|1{ch}" if shift else f"|0{ch}"
                for c in code:
                    self._insert_char(c)
                self._flash(f"Inserted {code}")
                break
        self._df = self._dt = self._ds = True

    def _find_next_misspelling(self):
        """Search forward from just after the cursor, wrapping around the
        whole document, for the next misspelled word. Returns
        (line_idx, start, end, word) or None."""
        if _get_spellchecker() is None:
            return None
        total = len(self.lines)
        for delta in range(total + 1):
            ly = (self.cy + delta) % total
            line = self.lines[ly]
            if line.lstrip().startswith('>'):
                continue
            spans = _find_misspelled(line)
            if not spans:
                continue
            if delta == 0:
                spans = [s for s in spans if s[0] > self.cx]
            if spans:
                start, end, word = spans[0]
                return ly, start, end, word
        return None

    async def _spell_check(self):
        """Jump to the next misspelled word and offer suggestions. Loops
        (not recurses) on skip/replace so a document full of unrecognised
        words can't grow an unbounded call stack."""
        if _get_spellchecker() is None:
            self._flash("Spell check unavailable")
            return
        sp = _get_spellchecker()
        t  = self._scr.t
        w  = 76
        r1 = 8

        while True:
            found = self._find_next_misspelling()
            if not found:
                self._flash("No misspelled words found")
                self._df = self._dt = self._ds = True
                return
            ly, start, end, word = found
            self.cy, self.cx = ly, start
            self._ensure_visible()
            self._dt = True
            await self._redraw()

            best  = sp.correction(word)
            cands = list(sp.candidates(word) or [])
            if best and best in cands:
                cands.remove(best)
            if best:
                cands.insert(0, best)
            cands = cands[:6]   # keep each entry readable within box width

            def render():
                b = t['border']
                o = [_mv(r1, 4) + b + "╔" + "═"*(w-2) + "╗",
                     _mv(r1+1, 4) + b + "║" + t['title']
                     + f' Misspelled: "{word}"  0-5=Replace  N=Skip  Esc=Cancel '.center(w-2)
                     + b + "║"]
                if cands:
                    row = "".join(f"{i}:{c[:10]:<10}" for i, c in enumerate(cands))
                else:
                    row = "(no suggestions)"
                o.append(_mv(r1+2, 4) + b + "║ " + _reset() + row[:w-4].ljust(w-4) + b + " ║")
                o.append(_mv(r1+3, 4) + b + "╚" + "═"*(w-2) + "╝" + _reset())
                return "".join(o)

            await self._wr(render())
            action = None
            while action is None:
                key = await self._read_key()
                if key is None:
                    continue
                if key == 'ESC':
                    action = 'cancel'
                elif key.upper() == 'N' or key in ('ENTER', ' '):
                    action = 'skip'
                elif len(key) == 1 and key.isdigit() and int(key) < len(cands):
                    action = int(key)

            if action == 'cancel':
                self._df = self._dt = self._ds = True
                return
            if action == 'skip':
                continue
            repl = cands[action]
            self._push_undo()
            line = self.lines[ly]
            self.lines[ly] = line[:start] + repl + line[end:]
            self.cx = start + len(repl)
            self._mark_modified()
            self._dt = True
            self._flash(f'Replaced "{word}" with "{repl}"')
            self._df = self._dt = self._ds = True
            return

    # ── Main key dispatcher ────────────────────────────────────────────────────
    async def _handle(self, key: str):
        # Navigation (never modify text)
        nav = {
            'UP':         self._up,
            'DOWN':       self._down,
            'LEFT':       self._left,
            'RIGHT':      self._right,
            'HOME':       self._home,
            'END':        self._end,
            'PGUP':       self._pgup,
            'PGDN':       self._pgdn,
            'CTRL_HOME':  self._doc_start,
            'CTRL_END':   self._doc_end,
            'CTRL_LEFT':  self._word_left,
            'CTRL_RIGHT': self._word_right,
        }
        if key in nav:
            nav[key]()
            if self._mark is not None:
                self._dt = True   # redraw selection highlight
            return

        # Submit / abort
        if key in ('CTRL_W', 'F10'):
            self.done = True
            return
        if key in ('ESC', 'CTRL_Q'):
            await self._confirm_abort()
            return

        # Draft / refresh
        if key in ('CTRL_S', 'ALT_S'):
            self._save_draft(); return
        if key == 'CTRL_L':
            self._df = self._dt = self._ds = True; return

        # Mode toggle
        if key == 'INS':
            self.overwrite = not self.overwrite; self._ds = True; return

        # Theme
        if key == 'F9':
            self._cycle_theme(); return

        # Help
        if key == 'F1':
            await self._show_help(); return

        # Block
        if key in ('F3', 'CTRL_B'):
            self._toggle_mark(); return
        if key == 'CTRL_C':
            self._copy_block(); return
        if key == 'CTRL_X':
            self._cut_block(); return
        if key == 'CTRL_V':
            await self._paste_block() if asyncio.iscoroutinefunction(self._paste_block) else self._paste_block(); return

        # Undo/Redo
        if key in ('CTRL_Z', 'CTRL_U'):
            self._undo_action(); return
        if key == 'CTRL_R':
            self._redo_action(); return

        # Find / Replace
        if key == 'CTRL_F':
            await self._find_dialog(); return
        if key == 'CTRL_H':
            await self._replace_dialog(); return
        if key == 'F2':   # find next
            self._do_find(self._find_q, forward=True); return

        # Color picker
        if key == 'F4':
            await self._color_picker(); return

        # Editing
        if key == 'ENTER':
            self._insert_newline(); return
        if key == 'BACKSPACE':
            self._backspace(); return
        if key in ('DEL', 'CTRL_G'):
            self._delete_char(); return
        if key == 'CTRL_Y':
            self._delete_line(); return
        if key == 'CTRL_K':
            self._kill_to_eol(); return
        if key in ('CTRL_D', 'CTRL_T'):
            self._delete_word(); return
        if key == 'TAB':
            self._insert_tab(); return
        if key == 'CTRL_N':
            # Insert blank line below cursor
            self._push_undo()
            self.lines.insert(self.cy + 1, "")
            self.cy += 1; self.cx = 0
            self._mark_modified(); self._dt = True
            self._ensure_visible(); return

        # Printable character — intercept '/' at col 0 for slash commands
        if len(key) == 1 and (key.isprintable() or key == ' '):
            if key == '/' and self.cx == 0:
                await self._slash_command()
            else:
                self._insert_char(key)


# ── Quote formatter ────────────────────────────────────────────────────────────
def _format_quote(raw: str, width: int = 74) -> list:
    """
    Wrap a quoted reply into '> ' prefixed lines.
    Strips existing quote markers to avoid deep >>>>> nesting.
    """
    lines_out = []
    for para in raw.split('\n'):
        para = para.rstrip('\r')
        # Strip existing quote prefixes
        clean = para
        while clean.lstrip().startswith('>'):
            clean = clean.lstrip()[1:].lstrip()
        if not clean.strip():
            lines_out.append('>')
            continue
        # Word-wrap to leave room for '> ' -- the check below compares
        # against len(line), which already includes that 2-char prefix,
        # so it self-accounts without a separate reserved-width constant.
        words = clean.split()
        line  = '> '
        for w in words:
            if len(line) + len(w) + 1 > width and line != '> ':
                lines_out.append(line.rstrip())
                line = '> ' + w + ' '
            else:
                line += w + ' '
        if line != '> ':
            lines_out.append(line.rstrip())
    return lines_out


# ── Entry point ────────────────────────────────────────────────────────────────
async def launch_anedit(session, quote: str = "", subject: str = "",
                         username: str = "guest",
                         tagline_picker=None) -> Optional[str]:
    """
    Launch the ANEdit full-screen terminal message editor.

    Args:
        session:  BBS session object with .write(str) and .read_raw(n).
        quote:    Optional quoted text to pre-fill (reply scenario).
        subject:  Message subject shown in the title bar.
        username: BBS username — used to locate the draft file.
        tagline_picker: Optional zero-arg async callable that shows a
            browsable tagline picker and returns the chosen text (or
            None). Called once, at send time (not before editing starts)
            -- the caller (bbs_ui.py) owns it since it needs DB access
            this module deliberately doesn't.

    Returns:
        The composed message as a string, or None if the user aborted.
    """
    # Build initial line list
    if quote:
        q_lines = _format_quote(quote)
        lines   = q_lines + ["", ""]
        # Start cursor at the blank line below the quote
        start_y = len(q_lines) + 1
    else:
        lines   = [""]
        start_y = 0

    # Draft path
    try:
        here  = os.path.dirname(os.path.abspath(__file__))
        root  = os.path.abspath(os.path.join(here, '..', '..'))
        ddir  = os.path.join(root, 'data', 'anedit', 'drafts')
        dpath = os.path.join(ddir, f"{username}.txt")
    except Exception:
        dpath = ""

    editor = ANEdit(session, lines, subject=subject,
                    draft_path=dpath, theme_idx=0,
                    tagline_picker=tagline_picker)
    editor.cy = min(start_y, len(editor.lines) - 1)
    editor.cx = 0

    return await editor.run()


# ── Read-only viewer ───────────────────────────────────────────────────────────

# Viewer-specific layout constants (independent of the ANEdit frame).
_VTEXT_T = 2   # first content row (rows 2-23 = 22 lines)
_VTEXT_B = 23
_VTEXT_H = _VTEXT_B - _VTEXT_T + 1  # 22 visible lines


class _ViewerScreen(_Screen):
    """Borderless viewer frame — full-width content, no box-drawing."""

    def __init__(self, theme: dict, width: int = 80):
        super().__init__(theme)
        self._vw = width

    def draw_frame(self, subject: str, modified: bool) -> str:
        t = self.t
        o = [_reset(), _hide()]
        tag  = "ANView"
        subj = (subject or '')[:50]
        bar  = f" {tag}  {subj} ".ljust(self._vw)[:self._vw]
        o.append(_mv(1, 1) + t['stat_bg'] + t['vtitle'] + bar + _reset())
        for r in range(_VTEXT_T, _VTEXT_B + 1):
            o.append(_mv(r, 1) + _clreol())
        return "".join(o)

    def draw_status(self, cy: int, cx: int, total: int,
                    words: int, chars: int,
                    overwrite: bool, modified: bool,
                    theme_name: str, flash: str) -> str:
        t = self.t
        if flash:
            return _mv(24, 1) + t['stat_bg'] + t['info'] + flash.ljust(self._vw)[:self._vw] + _reset()
        last_vis = min(cy + _VTEXT_H, total)
        end_tag  = '  END' if last_vis >= total else ''
        stat = f" Ln:{last_vis}/{total}{end_tag}  Up/Dn  R=Reply  N=New  Q=Back "
        return _mv(24, 1) + t['stat_bg'] + t['hint_key'] + stat.ljust(self._vw)[:self._vw] + _reset()

    def draw_text(self, lines: list, scroll: int,
                  mark, cy: int, cx: int, misspell=None) -> str:
        # `misspell` exists only so this matches the base _Screen.draw_text()
        # signature that ANEdit._redraw() (inherited by ANView, which is
        # read-only and never spell-checks) always calls with 6 args --
        # this class just ignores it since there's nothing to underline
        # in a read-only viewer.
        o = []
        for i in range(_VTEXT_H):
            ly  = scroll + i
            row = _VTEXT_T + i
            o.append(_mv(row, 1))
            if ly >= len(lines):
                o.append(_reset() + _clreol())
            else:
                o.append(_ansi_trunc(lines[ly], self._vw) + _reset() + _clreol())
        return "".join(o)

    def move_cursor(self, cy: int, cx: int, scroll: int) -> str:
        return _hide()


class ANView(ANEdit):
    """Read-only borderless message viewer.  Returns 'reply', 'new', or 'back'."""

    def __init__(self, session, lines: list, subject: str = ""):
        super().__init__(session, lines, subject=subject, draft_path="")
        from .ansi_ui import ui_width
        self._scr        = _ViewerScreen(_THEMES[self._tidx], width=ui_width(session))
        self.view_result = 'back'

    def _ensure_visible(self):
        if self.cy < self.scroll:
            self.scroll = self.cy
            self._dt = True
        elif self.cy >= self.scroll + _VTEXT_H:
            self.scroll = self.cy - _VTEXT_H + 1
            self._dt = True
        self._dc = True

    def _scroll_view(self, delta: int):
        max_scroll = max(0, len(self.lines) - _VTEXT_H)
        self.scroll = max(0, min(max_scroll, self.scroll + delta))
        self.cy     = self.scroll
        self._dt    = self._ds = True

    def _pgup(self):
        self.scroll = max(0, self.scroll - _VTEXT_H)
        self.cy     = self.scroll
        self._dt    = self._ds = True

    def _pgdn(self):
        max_scroll  = max(0, len(self.lines) - _VTEXT_H)
        self.scroll = min(max_scroll, self.scroll + _VTEXT_H)
        self.cy     = self.scroll
        self._dt    = self._ds = True

    def _doc_start(self):
        self.cy = self.cx = self.scroll = 0
        self._dt = self._dc = True

    def _doc_end(self):
        last        = max(0, len(self.lines) - 1)
        self.cy     = last
        self.scroll = max(0, last - _VTEXT_H + 1)
        self._dt    = self._ds = True

    async def run(self) -> str:
        await self._wr(_alt_on() + _cls() + _hide())
        self._df = self._dt = self._ds = True
        self._dc = False
        try:
            while not self.done and not self.aborted:
                await self._redraw()
                key = await self._read_key()
                if key:
                    await self._handle(key)
                if self._flash_msg and time.time() - self._flash_time > 2.5:
                    self._flash_msg = ""
                    self._ds = True
        finally:
            await self._wr(_alt_off() + _show() + _reset() + "\r\n")
        return self.view_result

    async def _handle(self, key: str):
        # UP/DOWN scroll 1 line; PgUp/PgDn scroll a full page
        if key == 'UP':
            self._scroll_view(-1); return
        if key in ('DOWN', 'RIGHT'):
            self._scroll_view(1); return
        if key == 'PGUP':
            self._pgup(); self.cy = self.scroll; self._ds = True; return
        if key == 'PGDN':
            self._pgdn(); self.cy = self.scroll; self._ds = True; return
        if key in ('HOME', 'CTRL_HOME'):
            self._doc_start(); return
        if key in ('END', 'CTRL_END'):
            self._doc_end(); self.cy = self.scroll; self._ds = True; return
        if key == 'CTRL_L':
            self._df = self._dt = self._ds = True; return
        k = key.upper() if len(key) == 1 else key
        if k == ' ':                       # Space scrolls a page
            self._pgdn(); self.cy = self.scroll; self._ds = True; return
        if k in ('Q', 'ESC') or key == 'ENTER':
            self.view_result = 'back';  self.done = True
        elif k == 'R':
            self.view_result = 'reply'; self.done = True
        elif k == 'N':
            self.view_result = 'new';   self.done = True


def render_message_body_lines(body: str) -> list:
    """Decode a stored message body (CP437-as-latin1 mojibake, the real
    wire convention for FidoNet/terminal-composed text) into a list of
    80-col ANSI-coloured terminal lines, same pipeline launch_aneview()
    uses for a single message. Extracted so other terminal-composed
    message types (local board threads, which go through the same
    ANEdit-based compose path as echomail/private messages) can render
    correctly through ANView too, instead of duplicating this logic --
    the CP437/pipe-code/flat-art-vs-cursor-pos handling here is subtle
    enough that a second, drifted copy would be a real correctness risk.
    """
    from .ansi_html import to_ansi_lines, _HAS_CURSOR_POS, _HAS_BLOCK_ART

    # CP437 decode — body is stored as latin-1 mojibake from DB for
    # wire-composed messages (BinkP/QWK). Real bug found live (same
    # class as web/render_msg.py's now-fixed _decode_charset(), fixed
    # here too): a message composed through the web UI stores genuine
    # already-decoded Unicode text instead -- box-drawing characters a
    # user pasted directly (codepoints above 0xFF, which a real latin-1
    # decode of raw bytes could never produce) were getting silently
    # turned into '?' by the old unconditional
    # encode('latin-1', errors='replace') below. Decode per character:
    # only byte-representable characters (0-0xFF) go through the CP437
    # round-trip; anything already outside that range passes through
    # untouched, so a message with a genuine mix of raw CP437 bytes and
    # real Unicode characters still renders correctly.
    out = []
    for c in (body or ''):
        b = ord(c)
        if b > 0xFF or 0x01 <= b <= 0x1F:
            out.append(c)
            continue
        out.append(bytes([b]).decode('cp437', errors='replace'))
    body_unicode = ''.join(out)

    # Pipe codes (|NN Synchronet/Mystic format) → ANSI SGR.
    if '|' in body_unicode:
        _PIPE = {
            '00':'30','01':'34','02':'32','03':'36',
            '04':'31','05':'35','06':'33','07':'37',
            # Bright (08-15) are bold + base color -- bare aixterm
            # 90-97 isn't recognized by MagiTerm/NetRunner/PuTTY.
            '08':'1;30','09':'1;34','10':'1;32','11':'1;36',
            '12':'1;31','13':'1;35','14':'1;33','15':'1;37',
            '16':'40','17':'44','18':'42','19':'46',
            '20':'41','21':'45','22':'43','23':'47',
        }
        body_unicode = re.sub(
            r'\|(\d{2})',
            lambda m: (f'\x1b[{_PIPE[m.group(1)]}m'
                       if m.group(1) in _PIPE else m.group(0)),
            body_unicode)

    # Strip record-boundary \n ONLY for pure flat block art (no cursor-pos).
    # Flat art has no absolute positioning — artifact \n from QWK \xe3 scatter
    # blocks across rows, so stripping fixes the staircase (each art row is
    # exactly 80 cols; the VT renderer wraps at 80, so the subsequent \n would
    # double-advance to produce a blank row between every art line).
    # Cursor-pos art (including mixed flat+cursor-pos) must KEEP \n: stripping
    # collapses flat header sections (like full-screen logos) by overflowing
    # past col 80 where the VT renderer clips.  Cursor-pos sequences set
    # absolute row/col so artifact \n between them have no visual effect.
    # IMPORTANT: only strip when the body is DENSE flat art (avg line > 70 chars).
    # Text messages with colored ANSI signatures have block chars too but short
    # lines — stripping their \n would collapse the entire message onto far fewer
    # rows, cutting off visible content.
    has_cpos  = bool(_HAS_CURSOR_POS.search(body_unicode))
    has_block = bool('\x1b' in body_unicode and _HAS_BLOCK_ART.search(body_unicode))
    if has_block and not has_cpos:
        _nl = body_unicode.count('\n')
        _avg_line = len(body_unicode) / max(_nl, 1)
        body_for_vt = body_unicode.replace('\n', '') if _avg_line > 70 else body_unicode
    else:
        body_for_vt = body_unicode

    # Render through VT renderer → list of 80-col ANSI-coloured terminal lines.
    return to_ansi_lines(body_for_vt)


async def launch_aneview(session, body: str, subject: str = "",
                         from_name: str = "", to_name: str = "",
                         date_str: str = "") -> str:
    """Display a message body in a scrollable read-only ANView frame.

    Runs the body through CP437 decode, pipe-code conversion, and the VT
    renderer so ANSI art (cursor-pos and flat block) displays correctly.
    Returns 'reply', 'new', or 'back'.
    """
    display_lines = render_message_body_lines(body)

    # Build a short message header above the body.
    header = []
    if from_name:
        header.append(f'\x1b[36mFrom:\x1b[0m {from_name}')
    if to_name:
        header.append(f'\x1b[36mTo:  \x1b[0m {to_name}')
    if date_str:
        header.append(f'\x1b[36mDate:\x1b[0m {date_str}')
    if header:
        from .ansi_ui import ui_width as _ui_width
        header.append('\x1b[36m' + '─' * _ui_width(session) + '\x1b[0m')
        header.append('')

    viewer = ANView(session, header + display_lines,
                    subject=subject or "(no subject)")
    return await viewer.run()
