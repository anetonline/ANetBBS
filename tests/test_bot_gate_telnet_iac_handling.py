"""Regression test for a follow-up audit finding (v1.1.50-present
sweep, 2026-10-28): BBSSession._bot_gate() (the pre-login anti-bot
challenge requiring ESC or '*' twice) filtered telnet IAC negotiation
noise itself by just skipping bytes >= 0xF0 -- the leading telnet
command byte only. That missed a WILL/WONT/DO/DONT sequence's trailing
OPTION byte and any IAC SB subnegotiation's PAYLOAD bytes (e.g. a NAWS
width/height of 27 or 42 -- both entirely plausible real terminal
dimensions), which could land on 0x1b (ESC) or 0x2a ('*') and
spuriously count toward passing the gate without a real keypress --
the same bug shape already fixed this week in read_key()/
read_key_arrow() (see test_read_key_telnet_handling.py).

Fixed by routing _bot_gate() through read_raw() -- the same IAC-aware
primitive read_key()/read_line()/read_password() all use -- instead of
a second, narrower hand-rolled IAC filter.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_read_line_telnet_iac_handling import _make_session

_IAC = 0xFF
_SB = 0xFA
_SE = 0xF0
_NAWS = 0x1F
_WILL = 0xFB
_DO = 0xFD


def _naws(cols, rows):
    return bytes([_IAC, _SB, _NAWS,
                  (cols >> 8) & 0xFF, cols & 0xFF,
                  (rows >> 8) & 0xFF, rows & 0xFF,
                  _IAC, _SE])


def _run(coro):
    return asyncio.run(coro)


class BotGateTelnetIacHandlingTests(unittest.TestCase):
    def test_naws_payload_byte_matching_esc_does_not_count_as_a_keypress(self):
        # width 27 (0x001B) -- its low byte IS 0x1b, the exact byte
        # _bot_gate() is watching for. A naive filter that only skips
        # the IAC/SB/option command bytes lets this payload byte
        # through as if it were a real ESC keypress.
        chunks = ([bytes([b]) for b in _naws(27, 24)]
                 + [b'\x1b', b'*'])
        session, _writer = _make_session(chunks)
        result = _run(session._bot_gate())
        self.assertTrue(result, 'two real ESC/* presses after the NAWS '
                               'noise must still pass the gate')
        self.assertEqual(session.window_size, (27, 24))

    def test_naws_alone_never_passes_the_gate_on_its_own(self):
        # Same width-27 NAWS sent twice: if its 0x1b payload byte were
        # wrongly counted twice, this alone would incorrectly pass.
        chunks = [bytes([b]) for b in _naws(27, 24)] * 2
        session, _writer = _make_session(chunks)
        result = _run(session._bot_gate())
        self.assertFalse(result, 'NAWS negotiation noise alone must '
                                'never satisfy the two-keypress challenge')

    def test_will_do_option_byte_matching_star_does_not_count(self):
        # IAC DO <option=0x2a> -- a 3-byte sequence whose OPTION byte
        # (not just the command byte) equals '*'. The old filter only
        # skipped bytes >= 0xF0, missing this trailing byte entirely.
        chunks = [bytes([_IAC]), bytes([_DO]), bytes([0x2a]),
                 bytes([_IAC]), bytes([_WILL]), bytes([0x2a]),
                 b'\x1b', b'*']
        session, _writer = _make_session(chunks)
        result = _run(session._bot_gate())
        self.assertTrue(result, 'two real presses after the '
                               'negotiation noise must still pass')

    def test_two_real_presses_still_pass_normally(self):
        session, _writer = _make_session([b'\x1b', b'*'])
        result = _run(session._bot_gate())
        self.assertTrue(result)

    def test_single_press_times_out_without_a_second(self):
        import os
        os.environ['BOT_GATE_TIMEOUT'] = '1'
        try:
            session, _writer = _make_session([b'\x1b'])
            result = _run(session._bot_gate())
            self.assertFalse(result)
        finally:
            os.environ.pop('BOT_GATE_TIMEOUT', None)

    def test_disconnect_mid_challenge_fails_the_gate_not_passes(self):
        session, _writer = _make_session([])  # empty queue -> EOF
        result = _run(session._bot_gate())
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
