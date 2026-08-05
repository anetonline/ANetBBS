"""Regression test for _drain_stale_session_input() (anetbbs/games/
door_runner.py) -- real bug found live bundling Minesweeper: every
play_*() door/remote-game bridge started forwarding session.reader
bytes to the freshly-launched target IMMEDIATELY, with nothing draining
whatever was already queued in that reader's buffer from the user's OWN
menu navigation (the Enter keypress that selected the game, a fast
double-tap, terminal-echo artifacts, etc). Those stale bytes got relayed
into the door before it had even finished starting up, and were
consumed as its very first "keypress" the instant it was ready to read.

Minesweeper's own real, intentional design (matching real Synchronet)
treats a bare Q as an UNCONFIRMED instant quit before the player's first
move -- so a single stray byte that happened to land on 'Q' silently
ended the session before the player ever saw it happen. Confirmed live:
intermittent (sometimes several real turns were possible, sometimes the
door exited within a fraction of a second) -- exactly the signature of
a race against however much was queued at launch time, not a
deterministic game-logic bug. Not Minesweeper-specific -- any door with
a consequential unconfirmed single-keystroke action at its own startup
is equally exposed, hence the fix living in the shared launcher.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.games.door_runner import _drain_stale_session_input


class _FakeSession:
    def __init__(self, reader):
        self.reader = reader


class DrainStaleSessionInputTests(unittest.TestCase):
    def test_drains_already_buffered_bytes(self):
        """The exact real scenario: a stray 'Q' left over from menu
        navigation is sitting in the buffer before the door starts
        reading -- must be discarded, not delivered as the door's first
        keypress."""
        async def _drive():
            reader = asyncio.StreamReader()
            reader.feed_data(b'Q')
            session = _FakeSession(reader)
            await _drain_stale_session_input(session, timeout=0.05)
            # Anything the drain missed would still be sitting in the
            # buffer -- a short follow-up read must come back empty.
            try:
                remaining = await asyncio.wait_for(reader.read(64), timeout=0.05)
            except asyncio.TimeoutError:
                remaining = b''
            return remaining

        remaining = asyncio.run(_drive())
        self.assertEqual(remaining, b'',
                         'stale byte must be fully drained, not left for the door to read')

    def test_drains_a_multi_byte_leftover_sequence(self):
        """A leftover arrow-key press (multi-byte CSI escape sequence)
        must be fully drained too, not just its first byte."""
        async def _drive():
            reader = asyncio.StreamReader()
            reader.feed_data(b'\x1b[A\x1b[B')
            session = _FakeSession(reader)
            await _drain_stale_session_input(session, timeout=0.05)
            try:
                remaining = await asyncio.wait_for(reader.read(64), timeout=0.05)
            except asyncio.TimeoutError:
                remaining = b''
            return remaining

        remaining = asyncio.run(_drive())
        self.assertEqual(remaining, b'')

    def test_does_not_block_when_nothing_is_buffered(self):
        """The common case -- a clean launch with no stale input at all
        -- must return promptly, not hang waiting for bytes that will
        never come."""
        async def _drive():
            reader = asyncio.StreamReader()
            session = _FakeSession(reader)
            start = asyncio.get_event_loop().time()
            await _drain_stale_session_input(session, timeout=0.05)
            return asyncio.get_event_loop().time() - start

        elapsed = asyncio.run(_drive())
        self.assertLess(elapsed, 0.5, 'must not block indefinitely when nothing is queued')

    def test_genuinely_new_input_after_the_drain_is_not_consumed(self):
        """Confirms the drain only discards what was ALREADY there at
        call time -- it must not eat real keystrokes the player types
        once the door is actually ready, which would just trade one
        input-loss bug for another."""
        async def _drive():
            reader = asyncio.StreamReader()
            session = _FakeSession(reader)
            await _drain_stale_session_input(session, timeout=0.05)
            reader.feed_data(b'D')  # a real, deliberate keypress after launch
            return await asyncio.wait_for(reader.read(1), timeout=0.5)

        result = asyncio.run(_drive())
        self.assertEqual(result, b'D')


if __name__ == '__main__':
    unittest.main()
