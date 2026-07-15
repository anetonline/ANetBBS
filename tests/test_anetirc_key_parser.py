"""Regression tests for anetbbs/features/anetirc2.py's _Keys parser.

Real bug report: a SyncTerm/SSH user found F2 (toggle the users panel)
did nothing at all. Root-caused against SyncTerm/CTerm's own official
documentation (src/conio/cterm.txt in the Synchronet source, "Sequences
sent by SyncTERM"), fetched directly rather than assumed: SyncTerm does
NOT use xterm's "ESC O P"-style SS3 codes for function keys -- it sends
CSI-tilde numeric codes (F1="\\033[11~" ... F12="\\033[24~", with
deliberate gaps at 16 and 22), and several other keys (End, PgUp, PgDn,
Insert, Back Tab) use SyncTerm-specific sequences that don't match
xterm's conventions either. The OLD parser was built entirely around
xterm/generic assumptions and silently dropped every one of these real
SyncTerm sequences -- not just F2, but F1 and F3-F12, PgUp, PgDn, End,
Insert, and Back Tab too.

Every sequence asserted below is copied verbatim from that
documentation table, not guessed.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class KeysParserTests(unittest.TestCase):
    def _parse_all(self, data: bytes):
        from anetbbs.features.anetirc2 import _Keys
        k = _Keys()
        k.feed(data)
        out = []
        while True:
            v = k.next()
            if v is None:
                break
            out.append(v)
        return out

    # ── SyncTerm function keys (the reported bug + its full blast radius) ──

    def test_syncterm_f1_through_f5(self):
        cases = [
            (b'\x1b[11~', 'F1'), (b'\x1b[12~', 'F2'), (b'\x1b[13~', 'F3'),
            (b'\x1b[14~', 'F4'), (b'\x1b[15~', 'F5'),
        ]
        for raw, expected in cases:
            self.assertEqual(self._parse_all(raw), [expected], raw)

    def test_syncterm_f6_through_f10_skips_16(self):
        cases = [
            (b'\x1b[17~', 'F6'), (b'\x1b[18~', 'F7'), (b'\x1b[19~', 'F8'),
            (b'\x1b[20~', 'F9'), (b'\x1b[21~', 'F10'),
        ]
        for raw, expected in cases:
            self.assertEqual(self._parse_all(raw), [expected], raw)
        # 16 is a deliberate gap in SyncTerm's own numbering -- must not
        # be misinterpreted as some other key, just unrecognized.
        self.assertEqual(self._parse_all(b'\x1b[16~'), [])

    def test_syncterm_f11_f12_skips_22(self):
        self.assertEqual(self._parse_all(b'\x1b[23~'), ['F11'])
        self.assertEqual(self._parse_all(b'\x1b[24~'), ['F12'])
        self.assertEqual(self._parse_all(b'\x1b[22~'), [])

    def test_syncterm_shift_alt_ctrl_function_keys(self):
        self.assertEqual(self._parse_all(b'\x1b[12;2~'), ['SHIFT_F2'])
        self.assertEqual(self._parse_all(b'\x1b[12;3~'), ['ALT_F2'])
        self.assertEqual(self._parse_all(b'\x1b[12;5~'), ['CTRL_F2'])

    # ── SyncTerm non-function-key sequences ─────────────────────────────

    def test_syncterm_page_up_down(self):
        self.assertEqual(self._parse_all(b'\x1b[V'), ['PGUP'])
        self.assertEqual(self._parse_all(b'\x1b[U'), ['PGDN'])

    def test_syncterm_end_key(self):
        self.assertEqual(self._parse_all(b'\x1b[K'), ['END'])

    def test_syncterm_insert_key(self):
        self.assertEqual(self._parse_all(b'\x1b[@'), ['INSERT'])

    def test_syncterm_back_tab(self):
        self.assertEqual(self._parse_all(b'\x1b[Z'), ['BACKTAB'])

    def test_syncterm_arrows_and_home_unaffected(self):
        # These already matched the pre-existing xterm-compatible
        # mapping and must keep working.
        self.assertEqual(self._parse_all(b'\x1b[A'), ['UP'])
        self.assertEqual(self._parse_all(b'\x1b[B'), ['DOWN'])
        self.assertEqual(self._parse_all(b'\x1b[C'), ['RIGHT'])
        self.assertEqual(self._parse_all(b'\x1b[D'), ['LEFT'])
        self.assertEqual(self._parse_all(b'\x1b[H'), ['HOME'])

    # ── Non-SyncTerm conventions, kept working for other clients ───────

    def test_xterm_ss3_function_keys_still_work(self):
        self.assertEqual(self._parse_all(b'\x1bOP'), ['F1'])
        self.assertEqual(self._parse_all(b'\x1bOQ'), ['F2'])
        self.assertEqual(self._parse_all(b'\x1bOR'), ['F3'])
        self.assertEqual(self._parse_all(b'\x1bOS'), ['F4'])

    def test_xterm_end_still_works(self):
        self.assertEqual(self._parse_all(b'\x1b[F'), ['END'])

    def test_vt220_tilde_navigation_still_works(self):
        self.assertEqual(self._parse_all(b'\x1b[2~'), ['INSERT'])
        self.assertEqual(self._parse_all(b'\x1b[3~'), ['DELETE'])
        self.assertEqual(self._parse_all(b'\x1b[5~'), ['PGUP'])
        self.assertEqual(self._parse_all(b'\x1b[6~'), ['PGDN'])

    def test_xterm_shift_arrow_still_works(self):
        self.assertEqual(self._parse_all(b'\x1b[1;2A'), ['SHIFT_A'])

    # ── Plain keys / regression safety ──────────────────────────────────

    def test_bare_esc_waits_for_more_data_at_this_layer(self):
        """A lone ESC byte is genuinely ambiguous (it might be the start
        of a longer sequence) -- _Keys.next() correctly returns None and
        leaves it buffered. The ANetIRC._read_key() caller is the layer
        that disambiguates this with a short timeout and resolves it to
        a real "ESC" keypress if nothing follows -- not tested here,
        that's a different unit."""
        self.assertEqual(self._parse_all(b'\x1b'), [])

    def test_esc_followed_by_unrecognized_byte_resolves_to_esc(self):
        # A second byte that isn't '[' or 'O' immediately disambiguates
        # -- this IS fully resolvable within next() alone.
        self.assertEqual(self._parse_all(b'\x1bx'), ['ESC', 'x'])

    def test_enter_backspace_tab(self):
        self.assertEqual(self._parse_all(b'\r'), ['ENTER'])
        self.assertEqual(self._parse_all(b'\n'), ['ENTER'])
        self.assertEqual(self._parse_all(b'\x7f'), ['BACKSPACE'])
        self.assertEqual(self._parse_all(b'\x08'), ['BACKSPACE'])
        self.assertEqual(self._parse_all(b'\t'), ['TAB'])

    def test_printable_passthrough(self):
        self.assertEqual(self._parse_all(b'hello'),
                         ['h', 'e', 'l', 'l', 'o'])

    def test_incomplete_sequence_waits_for_more_data(self):
        from anetbbs.features.anetirc2 import _Keys
        k = _Keys()
        k.feed(b'\x1b[1')
        self.assertIsNone(k.next(), 'must not resolve on a partial sequence')
        k.feed(b'2~')
        self.assertEqual(k.next(), 'F2')

    def test_unrecognized_sequence_does_not_hang_the_parser(self):
        """Regression guard for a related latent bug this fix also
        closes: before '@' was added as a valid terminator, an
        unrecognized-but-structurally-valid sequence ending in a
        non-alpha, non-'~', non-'@' byte could scan to end-of-buffer
        and report 'incomplete' forever, silently blocking all input
        queued behind it. A garbage tilde sequence must resolve (even
        if to None) rather than starve subsequent real keypresses."""
        from anetbbs.features.anetirc2 import _Keys
        k = _Keys()
        k.feed(b'\x1b[99~' + b'a')
        self.assertIsNone(k.next())  # unrecognized code 99, not a hang
        self.assertEqual(k.next(), 'a')  # next real key still gets through


if __name__ == '__main__':
    unittest.main()
