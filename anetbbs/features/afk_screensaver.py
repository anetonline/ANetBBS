"""Generated matrix-rain screensaver renderer, used by
Session._run_afk_sequence() (anetbbs/core/session.py) once a caller's
terminal has sat idle past AFK_WARNING_SECONDS. Kept separate from
session.py (a session/transport-concerns file) since this is pure
animation/state logic with no I/O of its own -- the caller writes
whatever frame_lines() returns to the terminal itself.

Jerry's reference for this feature was a real Mystic Pascal script
(rcsafk.mps) whose own screensaver stage shells out to real cmatrix on
Linux -- this is a from-scratch Python equivalent of that same
falling-character look, not a port of any of that script's code.
"""
import random

from .ansi_ui import FG, RESET

_CHARS = '01234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ$%#@&*+=<>?/\\|'


class MatrixRain:
    """Pure frame-state machine for a falling-character rain effect.

    step() advances the simulation by one frame (state mutation only,
    no output). frame_lines() renders the CURRENT state as a list of
    cursor-positioned ANSI strings a caller can write directly to the
    terminal -- deliberately NOT a full scrolling text buffer, so this
    works inside any fixed screen region without needing its own
    scroll handling.
    """

    def __init__(self, width, height, rng=None):
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self._rng = rng or random.Random()
        # One falling drop per column: None (idle) or {'row': int, 'len': int}.
        self._drops = [None] * self.width

    def step(self):
        for col in range(self.width):
            drop = self._drops[col]
            if drop is None:
                if self._rng.random() < 0.05:
                    self._drops[col] = {
                        'row': 0,
                        'len': self._rng.randint(4, max(4, min(16, self.height))),
                    }
                continue
            drop['row'] += 1
            if drop['row'] - drop['len'] > self.height:
                self._drops[col] = None

    def frame_lines(self, x_offset=1, y_offset=1):
        """One '\\x1b[{row};{col}H<color><char>' string per visible
        character this frame -- head bright white, near-trail bold
        green, far-trail dim green, matching the classic look.
        x_offset/y_offset (1-indexed terminal coords) let a caller
        place this within an arbitrary screen region."""
        out = []
        for col in range(self.width):
            drop = self._drops[col]
            if drop is None:
                continue
            head = drop['row']
            for i in range(drop['len']):
                row = head - i
                if row < 0 or row >= self.height:
                    continue
                ch = self._rng.choice(_CHARS)
                if i == 0:
                    color = FG['wht']
                elif i < 3:
                    color = FG['grn']
                else:
                    color = '\x1b[32m'  # dim (non-bold) green trail
                out.append(f'\x1b[{row + y_offset};{col + x_offset}H{color}{ch}{RESET}')
        return out
