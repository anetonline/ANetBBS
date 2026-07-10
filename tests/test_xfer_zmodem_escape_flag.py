"""Regression test for a ZMODEM upload handshake failure in
anetbbs/features/xfer.py.

Confirmed live: every upload attempt via SyncTERM over SSH failed
identically -- the client (sz-equivalent sender) logged
"UNEXPECTED ZRPOS received instead of ZRINIT" after repeated ZRQINIT
retries, then the transfer was cancelled. 100% reproducible, not
intermittent, which rules out a timing race and points at a protocol/flag
mismatch instead.

This codebase already has a proven fix for the mirror-image bug on the
send side: `sz --escape` causes sz to send ZSINIT to negotiate extended
escaping, and SyncTERM (among other terminal emulators) replies with
ZRINIT instead of the expected ZACK, breaking the handshake -- so
--escape was deliberately omitted from ZMODEM's send_flags. The receive
side (`rz --escape`, which sets the ESCCTL bit in our ZRINIT to request
the sender escape control characters) turned out to trigger the same
class of SyncTERM handshake failure in the opposite direction. Removing
--escape from ZMODEM's recv_flags mirrors the already-proven send-side
fix.

XMODEM has no escape flag at all (not a ZMODEM-family protocol, no
ZSINIT/ZRINIT negotiation), and YMODEM's --escape is left alone here --
no live report of a YMODEM failure, and changing it wouldn't be
justified by evidence, only by pattern-matching.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.features.xfer import _PROTOCOLS


class ZmodemEscapeFlagTests(unittest.TestCase):
    def test_zmodem_recv_flags_omits_escape(self):
        self.assertNotIn('--escape', _PROTOCOLS['zmodem']['recv_flags'],
                         "rz --escape breaks the ZRQINIT/ZRINIT handshake "
                         "with SyncTERM (confirmed live) -- same failure "
                         "class as the already-fixed sz --escape bug on "
                         "the send side.")

    def test_zmodem_send_flags_still_omits_escape(self):
        """Baseline: confirm the original send-side fix this mirrors is
        still in place and hasn't regressed."""
        self.assertNotIn('--escape', _PROTOCOLS['zmodem']['send_flags'])

    def test_zmodem_still_uses_binary_flag(self):
        """The fix must only remove --escape, not --binary (which
        prevents newline translation of binary file data and is
        unrelated to this handshake bug)."""
        self.assertIn('--binary', _PROTOCOLS['zmodem']['send_flags'])


if __name__ == '__main__':
    unittest.main()
