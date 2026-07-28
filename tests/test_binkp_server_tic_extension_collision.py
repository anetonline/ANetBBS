"""Regression test for a critical live-caught bug: EVERY inbound .tic
manifest received by the BinkP inbound listener (binkp_server.py, used
whenever a peer dials INTO this BBS -- exactly how a downstream node or
upstream hub delivers TIC-distributed files) vanished with zero trace,
never reaching the TIC scanner at all.

Root cause: the extension regex meant to catch Mystic's point-targeted
mail-bundle naming convention (.tc1, .td2, .th3, ...) --
`t[cdih][0-9a-f]` -- ALSO coincidentally matches the universal FTN
'.tic' extension itself: 't' + 'i' (a member of [cdih], for "immediate"
mail) + 'c' (a valid hex digit). Every .tic file was misrouted into the
mail-packet extractor, attempted to parse as a real FTS-0001 packet
(which plain-text TIC content never is), and discarded -- never
imported as mail, never written to inbound_dir for TIC processing,
never even logged as a failure.

Confirmed live: a downstream Synchronet system's own BinkP sender log
showed a real .tic manifest (`ti_00000.tic`) transmitted successfully
immediately after its paired binary, but nothing about it ever appeared
anywhere in this BBS's own receive-side logs -- exactly the signature
of this bug (binkp.py, the OUTBOUND-dial side, has no such extension
check at all and was never affected -- this was specific to
binkp_server.py's independently-duplicated classification logic).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TicExtensionCollisionTests(unittest.TestCase):
    def test_tic_extension_is_never_treated_as_a_mail_bundle(self):
        from anetbbs.echomail.binkp_server import _looks_like_mail_bundle_ext
        self.assertFalse(_looks_like_mail_bundle_ext('ti_00000.tic'))
        self.assertFalse(_looks_like_mail_bundle_ext('ADVENT25.TIC'))
        self.assertFalse(_looks_like_mail_bundle_ext('whatever.Tic'))

    def test_genuine_mystic_point_bundle_extensions_still_match(self):
        """Baseline / guard against a too-broad fix: the real Mystic
        point-targeted bundle convention this regex exists for must
        keep working."""
        from anetbbs.echomail.binkp_server import _looks_like_mail_bundle_ext
        self.assertTrue(_looks_like_mail_bundle_ext('mail.tc1'))
        self.assertTrue(_looks_like_mail_bundle_ext('mail.td2'))
        self.assertTrue(_looks_like_mail_bundle_ext('mail.th5'))
        self.assertTrue(_looks_like_mail_bundle_ext('mail.ti4'))
        self.assertTrue(_looks_like_mail_bundle_ext('mail.pkt'))
        self.assertTrue(_looks_like_mail_bundle_ext('mail.cut'))
        self.assertTrue(_looks_like_mail_bundle_ext('mail.we3'))

    def test_mod_extension_is_never_treated_as_a_mail_bundle(self):
        """Real live-caught collision: a 48MB TIC-distributed zip
        (mist0226.zip, ANN.FILES.ANSIART) had a MELODIA-*.MOD tracker
        module member inside. The day-of-week branch's point-number
        character class used to accept any alphanumeric char, so '.mod'
        (mo + d) matched "Monday point-bundle" -- misrouting one member
        as bogus mail (imported as 0 messages) and, because ANY match
        makes the whole extract non-empty, silently dropping the ENTIRE
        zip instead of ever writing it to inbound_dir for TIC filing.
        Confirmed live via journalctl: "Imported 0 messages from
        MELODIA-JAMES_BROWN_IS_DEAD.MOD into network 5", and the zip
        was genuinely absent from inbound/ and inbound/processed/
        afterward."""
        from anetbbs.echomail.binkp_server import _looks_like_mail_bundle_ext
        self.assertFalse(_looks_like_mail_bundle_ext('MELODIA-JAMES_BROWN_IS_DEAD.MOD'))
        self.assertFalse(_looks_like_mail_bundle_ext('anything.mod'))
        self.assertFalse(_looks_like_mail_bundle_ext('ANYTHING.MOD'))

    def test_other_extensions_that_would_have_collided_under_the_old_class(self):
        """The old [0-9a-z] point-character class was broad enough to
        false-positive on plenty of real, common extensions beyond just
        .mod -- confirm the digit-only fix protects these too, not just
        the one that happened to bite live."""
        from anetbbs.echomail.binkp_server import _looks_like_mail_bundle_ext
        for ext in ('save.sav', 'image.sun', 'archive.wee',
                    'mobile.mob', 'file.tux'):
            self.assertFalse(_looks_like_mail_bundle_ext(ext),
                             f'{ext!r} should not match the mail-bundle pattern')

    def test_extract_packets_writes_the_whole_zip_when_mod_member_present(self):
        """End-to-end: a zip containing an innocent .mod file alongside
        other art assets must extract as EMPTY (not mail), so the
        caller falls through to writing the raw zip to inbound_dir --
        exactly the real live scenario."""
        import io
        import zipfile
        from anetbbs.echomail.binkp_server import _extract_packets

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('SOME-ART.ANS', b'not mail')
            zf.writestr('SOME-TUNE.MOD', b'not mail either, just a tracker module')
        zip_bytes = buf.getvalue()

        results = list(_extract_packets('mist0226.zip', zip_bytes))
        self.assertEqual(results, [],
                         'a zip with only art/tracker files must never be '
                         'treated as containing mail packets')

    def test_extract_packets_skips_a_tic_file_instead_of_yielding_it(self):
        from anetbbs.echomail.binkp_server import _extract_packets
        tic_content = (
            b'File advent25.zip\r\nArea ANN.FILES.ANSIART\r\n'
            b'Origin 1:100/1\r\nFrom 1:100/1\r\nTo 1200:1/1\r\n'
        )
        results = list(_extract_packets('ti_00000.tic', tic_content))
        self.assertEqual(results, [],
                         'a .tic manifest must never be yielded as a mail '
                         'packet, regardless of its content')

    def test_extract_packets_still_finds_a_real_pkt_file(self):
        """Baseline: a real FTS-0001 packet (correct magic bytes) must
        still be recognized regardless of extension."""
        from anetbbs.echomail.binkp_server import _extract_packets
        # Minimal fake header: 60 bytes, byte 18-19 = 0x02 0x00.
        fake_pkt = bytearray(60)
        fake_pkt[18] = 0x02
        fake_pkt[19] = 0x00
        results = list(_extract_packets('mail.pkt', bytes(fake_pkt)))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], 'mail.pkt')

    def test_extract_packets_still_finds_pkt_inside_a_zip_bundle(self):
        import io
        import zipfile
        from anetbbs.echomail.binkp_server import _extract_packets

        fake_pkt = bytearray(60)
        fake_pkt[18] = 0x02
        fake_pkt[19] = 0x01
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('inner.pkt', bytes(fake_pkt))
            zf.writestr('README.TXT', b'not mail')
        zip_bytes = buf.getvalue()

        results = list(_extract_packets('bundle.su0', zip_bytes))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], 'inner.pkt')

    def test_extract_packets_ignores_a_tic_file_bundled_inside_a_zip(self):
        """Same collision, but for the (unusual but possible) case of a
        .tic ending up as a zip member name rather than the top-level
        transferred filename."""
        import io
        import zipfile
        from anetbbs.echomail.binkp_server import _extract_packets

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('manifest.tic', b'File x.zip\r\nArea TEST\r\n')
        zip_bytes = buf.getvalue()

        results = list(_extract_packets('bundle.zip', zip_bytes))
        self.assertEqual(results, [])


if __name__ == '__main__':
    unittest.main()
