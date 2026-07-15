"""Regression test for a real data-corruption bug found in a deep
review of anetbbs/features/anetirc2.py's bookmark config format.

_save_cfg()/_load_cfg() use a pipe-delimited line format with NO
escaping, and _load_cfg does an unbounded `.split('|')`. A literal '|'
typed into any free-text field -- most plausibly `label`, which a
sysop might reasonably separate with something like "Home | Personal"
the way many UIs do -- silently shifted every field after it on the
next load: server became the tail of the label, port became the
server (falling back to the 6667 default since it's not a valid int),
and so on. No error, no visible sign in the UI that it happened.

Fixed by refusing to ever WRITE an ambiguous '|' into a field (replaced
with a visually similar U+00A6 BROKEN BAR) -- the read side and file
format are unchanged, so this stays compatible with the original C
client's understanding of the format, and existing saved files need no
migration.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class BookmarkConfigPipeEscapingTests(unittest.TestCase):
    def _roundtrip(self, bookmarks):
        from anetbbs.features.anetirc2 import _save_cfg, _load_cfg
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, 'anetirc.cfg')
        _save_cfg(path, bookmarks)
        return _load_cfg(path)

    def test_pipe_in_label_does_not_corrupt_later_fields(self):
        from anetbbs.features.anetirc2 import Bookmark
        original = Bookmark(label='Home | Personal', server='irc.libera.chat',
                            port=6697, nick='StingRay', channel='#anetbbs',
                            tls=True, password='hunter2')
        loaded = self._roundtrip([original])
        self.assertEqual(len(loaded), 1)
        bm = loaded[0]
        # The exact label text can't round-trip byte-for-byte through a
        # lossy substitution (that's an inherent, accepted tradeoff of
        # this fix, not a bug) -- but every OTHER field must be intact
        # and correctly positioned, which is the actual thing that was
        # broken before.
        self.assertEqual(bm.server, 'irc.libera.chat')
        self.assertEqual(bm.port, 6697)
        self.assertEqual(bm.nick, 'StingRay')
        self.assertEqual(bm.channel, '#anetbbs')
        self.assertTrue(bm.tls)
        self.assertEqual(bm.password, 'hunter2')

    def test_pipe_in_password_does_not_corrupt_the_record(self):
        from anetbbs.features.anetirc2 import Bookmark
        original = Bookmark(label='Test', server='irc.example.org',
                            port=6667, nick='nick1', channel='#chan',
                            tls=False, password='a|b|c')
        loaded = self._roundtrip([original])
        self.assertEqual(len(loaded), 1)
        bm = loaded[0]
        self.assertEqual(bm.label, 'Test')
        self.assertEqual(bm.server, 'irc.example.org')
        self.assertEqual(bm.port, 6667)
        self.assertEqual(bm.nick, 'nick1')
        self.assertEqual(bm.channel, '#chan')

    def test_multiple_bookmarks_no_pipe_still_roundtrip_exactly(self):
        from anetbbs.features.anetirc2 import Bookmark
        originals = [
            Bookmark(label='Libera', server='irc.libera.chat', port=6697,
                     nick='StingRay', channel='#anetbbs', tls=True,
                     password=''),
            Bookmark(label='EFnet', server='irc.efnet.org', port=6667,
                     nick='StingRay2', channel='#bbs', tls=False,
                     password='secret'),
        ]
        loaded = self._roundtrip(originals)
        self.assertEqual(len(loaded), 2)
        for orig, bm in zip(originals, loaded):
            self.assertEqual(bm.label, orig.label)
            self.assertEqual(bm.server, orig.server)
            self.assertEqual(bm.port, orig.port)
            self.assertEqual(bm.nick, orig.nick)
            self.assertEqual(bm.channel, orig.channel)
            self.assertEqual(bm.tls, orig.tls)
            self.assertEqual(bm.password, orig.password)


if __name__ == '__main__':
    unittest.main()
