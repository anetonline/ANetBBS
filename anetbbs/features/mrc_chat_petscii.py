# anetbbs/features/mrc_chat_petscii.py
"""
PETSCII (C64/128) MRC chat -- a thin subclass of MRCChat that forces
the plain-scroll rendering mode already built into the base class,
instead of its default ANSI split-screen mode.

MRCChat._emit() already has a `if not self._split_screen:` fallback
that just does `await self.session.write(text + '\r\n')` -- plain SGR
color text through the normal write() path, which
petscii_codec.ansi_to_petscii() already translates into real C64 color
bytes for term_mode == 'petscii' sessions (see core/session.py's
write()). A dozen other methods (_draw_status_line, _draw_ticker_line,
_draw_input_line, the palette/geometry resync paths) already
self-guard on `if not self._split_screen: return` too. None of that
plain-scroll mode is reachable for a real PETSCII session today,
though: self.session.reader is a real asyncio.StreamReader for every
protocol including PETSCII, so _enter_split_screen() always runs its
ANSI-only path first -- CPR terminal-size probing, DECSTBM scroll
regions, raw cursor-addressed draws -- none of which a real C64
understands, all silently dropped or garbled.

This class overrides exactly three methods, and nothing else -- the
bridge websocket connect, JSON protocol, join_room, ping/pong
keepalive, _handle_event()'s state parsing, _split_for_wire(),
_pipe_to_ansi(), slash commands (including /set color, which covers
the outgoing-color-cycling this class drops -- see _read_chat_line
below), and show_menu()'s bridge-URL resolution are all inherited
unchanged.

_read_chat_line() originally just delegated to session.read_line(),
which turned out to be wrong two ways, both found live on the Pi:
  1. No password masking -- read_line() has none; the base class's
     masking (_should_mask()/_draw_input_line()) is gated behind
     `if not self._split_screen: return` (this class's whole point),
     so typing "/identify <pass>" echoed the real password in the
     clear.
  2. The AFK warning/screensaver fired mid-chat -- read_line() always
     calls read_raw(1, allow_afk=True) internally. MRC is deliberately
     supposed to never go through the AFK/idle-timeout path at all
     (see core/session.py's read_raw() docstring: "IRC/MRC are web
     features and don't go through this path"); the ANSI base class
     achieves that by reading straight off self.session.reader
     instead of through read_line()/read_key(). This class now does
     the same.

_emit() is ALSO overridden (a fourth method, despite the "exactly
three" claim above having been true before this fix) for TWO more bugs
found live on the Pi:

  3. An incoming message arriving while the user was mid-keystroke got
     spliced into the middle of the line being typed, corrupting the
     display (though the message actually SENT was still correct --
     this was a display-only race). The ANSI split-screen mode never
     has this problem because incoming messages and the input line are
     drawn to separate cursor-addressed screen regions
     (_redraw_chat_area() vs _draw_input_line()), and the former
     already takes self._input_lock before writing (see that method's
     own code) to stay serialized against the latter's own
     per-keystroke writes. Plain-scroll mode has no such spatial
     separation -- both sides write directly to the same linear output
     stream -- so they need to share that same lock instead.
  4. Long messages wrapped badly, especially at 40 columns -- MRCChat's
     own plain-mode _emit() fallback (`if not self._split_screen: await
     self.session.write(text + '\r\n')`) just writes the raw string and
     lets the TERMINAL'S OWN hardware auto-wrap break it wherever the
     physical column happens to land, with no word-boundary awareness
     and no indent -- fine for the rare edge case that fallback was
     originally written for (a reader with no .read() method), but a
     real problem now that PETSCII actually uses this path for every
     message, especially on a real 40-column screen where wraps happen
     constantly. The ANSI split-screen _emit() already has proper
     word-wrap for exactly this (the module-level _word_wrap() helper,
     reused here) -- the plain-mode fallback just never needed it
     before.
"""
from .mrc_chat import MRCChat, _word_wrap


class PetsciiMRCChat(MRCChat):

    async def _emit(self, text, extra_indent=''):
        self._scrollback.append(text)
        width = max(10, self._chat_width)
        lines = _word_wrap(text, width, indent=extra_indent)
        out = '\r\n'.join(lines) + '\r\n'
        async with self._input_lock:
            await self.session.write(out)

    async def _enter_split_screen(self):
        # _chat_width drives _emit()'s word-wrap regardless of
        # split-screen state, so it must be set even in plain mode --
        # mirrors petscii_ui.py::_width()'s own fallback. _term_columns
        # (used by a couple of width calculations that fall back to it
        # when the sidebar is disabled, which it always is here) is
        # normally only set by the ANSI-only code in the base class's
        # version of this method -- set it here too so it doesn't sit
        # at its __init__ default (80) on a 40-column session.
        self._split_screen = False
        width = getattr(self.session, 'petscii_width', 40)
        self._chat_width = width
        self._term_columns = width
        await self.session.clear_screen()

    async def _exit_split_screen(self):
        await self.session.clear_screen()

    async def _read_chat_line(self):
        """Character-by-character input, PETSCII-safe (no ANSI escape-
        sequence parsing -- a real C64 keyboard never sends those, so
        Ctrl+Up/Down history recall and left/right arrow color-cycling
        aren't reachable here; /set color covers the same outcome via
        a typed command) and reading raw off the transport directly
        (matching the base class's own approach -- see this module's
        docstring for why that matters for AFK).

        Reimplements per-keystroke password masking
        (MRCChat._should_mask()) at the single-character level, since
        _draw_input_line() (where the base class normally does this)
        no-ops in plain-scroll mode.
        """
        from .petscii_codec import decode_char, CURSOR_LEFT

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

            if ch == b'\x14':  # real C64 DEL/INST key (not ASCII 0x7f/0x08)
                if buf:
                    buf.pop()
                    async with self._input_lock:
                        await self.session.write(f'{CURSOR_LEFT} {CURSOR_LEFT}')
                continue

            if ch < b' ':
                continue

            c = decode_char(ch[0])
            buf.append(c)
            echo = '*' if self._should_mask(''.join(buf)) else c
            async with self._input_lock:
                await self.session.write(echo)
