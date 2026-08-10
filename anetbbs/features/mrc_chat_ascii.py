# anetbbs/features/mrc_chat_ascii.py
"""
ASCII (plain, non-ANSI) MRC chat -- a thin subclass of MRCChat that
forces the plain-scroll rendering mode already built into the base
class, instead of its default ANSI split-screen mode. Sibling of
mrc_chat_petscii.py's PetsciiMRCChat -- same reasoning, same shape,
but simpler: no PETSCII case-inversion, no PETSCII color-byte
translation, no PETSCII DEL key.

term_mode == 'ascii' sessions were never gated away from the regular
chat.py::ChatManager MRC option the way PETSCII sessions were (PETSCII
has its own entirely separate menu system, see petscii_ui.py/
session.py's post-login dispatch; ascii sessions run through the exact
same menu_engine.py main menu as full ANSI, including chat.py). That
meant an ascii-mode session opening MRC hit the full cursor-addressed
MRCChat as-is -- and core/session.py's write() strips every ANSI
escape sequence outright for term_mode == 'ascii'
(_ANSI_ESC_RE.sub('', text), see write()), so the split-screen mode's
DECSTBM scroll-region setup, CPR terminal-size probe, and
cursor-addressed status/input/ticker draws were all silently dropped,
leaving no layout at all -- a real, pre-existing bug, not a
hypothetical one, structurally identical to what PETSCII had before
mrc_chat_petscii.py existed.

This class overrides the same four methods PetsciiMRCChat does, for
the same two categories of reason (see that module's docstring for the
full account of the AFK/masking/splicing/word-wrap bugs found live on
the Pi against the PETSCII client -- the same bugs would apply here
verbatim, since they all stem from MRCChat's plain-scroll fallback
path itself, not from anything PETSCII-specific):

  - _enter_split_screen() / _exit_split_screen(): force plain-scroll
    mode, no CPR probe / DECSTBM / cursor-addressed draws.
  - _read_chat_line(): read raw bytes directly off self.session.reader
    (never through session.read_line(), which always risks the AFK
    screensaver interrupting -- see read_raw()'s own docstring: "IRC/
    MRC are web features and don't go through this path"), with
    per-character password masking reusing _should_mask().
  - _emit(): word-wrap via MRCChat's own _word_wrap() helper instead
    of relying on the terminal's own hardware auto-wrap, and share
    self._input_lock with _read_chat_line() so an incoming message
    can't splice into a line still being typed.

Unlike PETSCII, there's no charset case-inversion and no special DEL
byte -- ascii backspace is the standard 0x7f/0x08, echoed as '\\b \\b',
matching core/session.py's own read_line()/read_raw() convention for
every non-PETSCII term_mode already.
"""
from .mrc_chat import MRCChat, _word_wrap


class AsciiMRCChat(MRCChat):

    async def _emit(self, text, extra_indent=''):
        self._scrollback.append(text)
        width = max(10, self._chat_width)
        lines = _word_wrap(text, width, indent=extra_indent)
        out = '\r\n'.join(lines) + '\r\n'
        async with self._input_lock:
            await self.session.write(out)

    async def _enter_split_screen(self):
        # No CPR probe -- a plain ascii terminal isn't guaranteed to
        # answer an ANSI cursor-position-report escape at all, and
        # write() would strip the probe sequence before it even went
        # out. window_size is already populated by NAWS negotiation (or
        # its (80, 24) default) the same way every other term_mode
        # relies on it elsewhere in this codebase.
        self._split_screen = False
        ws = getattr(self.session, 'window_size', None) or (80, 24)
        try:
            cols = int(ws[0])
        except (TypeError, ValueError):
            cols = 80
        width = max(40, min(240, cols))
        self._chat_width = width
        self._term_columns = width
        await self.session.write('\r\n' * 4)

    async def _exit_split_screen(self):
        await self.session.write('\r\n' * 2)

    async def _read_chat_line(self):
        """Character-by-character input, matching the ANSI base
        class's own approach of reading straight off self.session.reader
        (see this module's docstring for why -- AFK avoidance) instead
        of delegating to session.read_line().

        Reimplements per-keystroke password masking
        (MRCChat._should_mask()) at the single-character level, since
        _draw_input_line() (where the base class normally does this)
        no-ops in plain-scroll mode.
        """
        reader = self.session.reader
        buf = []
        while True:
            ch = await reader.read(1)
            if not ch:
                return ''

            if ch in (b'\r', b'\n'):
                async with self._input_lock:
                    await self.session.write('\r\n')
                return ''.join(buf)

            if ch in (b'\x7f', b'\x08'):
                if buf:
                    buf.pop()
                    async with self._input_lock:
                        await self.session.write('\b \b')
                continue

            if ch < b' ':
                continue

            if not (32 <= ch[0] <= 126):
                continue

            c = ch.decode('ascii', errors='replace')
            buf.append(c)
            echo = '*' if self._should_mask(''.join(buf)) else c
            async with self._input_lock:
                await self.session.write(echo)
