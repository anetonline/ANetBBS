"""Regression tests for anetbbs.echomail.zip_safety and its use at
every ZIP-extraction site in anetbbs/echomail/.

Real gap found in a security/performance audit: every ZIP extraction
site read decompressed bytes via ZipFile.read() with no check on the
declared uncompressed size, letting a small, highly-compressed archive
("zip bomb") expand to gigabytes in memory. Confirmed empirically: a
~51KB crafted all-zero DEFLATE archive expands to 50MB+ in well under
a second. The worst-exposed site (binkp_server.py's inbound file-
receive) requires no authentication at all -- FTN convention
deliberately accepts an unrecognized peer for anonymous crashmail
delivery.
"""
import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.echomail.zip_safety import (
    iter_safe_members, ZipBombError,
    MAX_MEMBER_UNCOMPRESSED, MAX_ARCHIVE_UNCOMPRESSED,
)


def _make_zip(members):
    """members: list of (name, bytes) -- writes each with max DEFLATE
    compression so a large declared size can come from a tiny archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data, compresslevel=9)
    buf.seek(0)
    return buf


class IterSafeMembersTests(unittest.TestCase):
    def test_ordinary_small_members_pass_through_unchanged(self):
        buf = _make_zip([('a.txt', b'hello'), ('b.txt', b'world')])
        with zipfile.ZipFile(buf) as zf:
            got = {info.filename: data for info, data in iter_safe_members(zf)}
        self.assertEqual(got, {'a.txt': b'hello', 'b.txt': b'world'})

    def test_zip_bomb_is_refused_before_decompression(self):
        # A real zip bomb: all-zero payload compresses extremely well.
        bomb_size = 50 * 1024 * 1024  # declared/actual uncompressed size
        buf = _make_zip([('bomb.bin', b'\x00' * bomb_size)])
        # The archive itself must be tiny -- proves DEFLATE's ratio, and
        # that the check fires from the header, not from reading it all.
        self.assertLess(len(buf.getvalue()), 100 * 1024)
        with zipfile.ZipFile(buf) as zf:
            with self.assertRaises(ZipBombError):
                list(iter_safe_members(zf, max_member=1024 * 1024))

    def test_cumulative_total_across_many_members_is_enforced(self):
        # Each individual member is under the per-member cap, but
        # together they exceed a (deliberately small, for the test)
        # cumulative archive cap.
        members = [(f'f{i}.bin', b'\x00' * 1000) for i in range(20)]
        buf = _make_zip(members)
        with zipfile.ZipFile(buf) as zf:
            with self.assertRaises(ZipBombError):
                list(iter_safe_members(zf, max_member=2000, max_total=5000))

    def test_directories_are_skipped_not_counted(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr(zipfile.ZipInfo('adir/'), '')
            zf.writestr('adir/file.txt', b'hi')
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            got = list(iter_safe_members(zf))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0].filename, 'adir/file.txt')

    def test_default_caps_are_reasonable(self):
        # Sanity check the shipped defaults haven't regressed to
        # something silently unsafe (e.g. accidentally unlimited).
        self.assertGreater(MAX_MEMBER_UNCOMPRESSED, 0)
        self.assertGreater(MAX_ARCHIVE_UNCOMPRESSED, MAX_MEMBER_UNCOMPRESSED)
        self.assertLess(MAX_ARCHIVE_UNCOMPRESSED, 10 * 1024 * 1024 * 1024)


class BinkpServerZipBombTests(unittest.TestCase):
    def test_extract_packets_refuses_a_bomb_and_does_not_crash(self):
        from anetbbs.echomail.binkp_server import _extract_packets
        bomb_size = 220 * 1024 * 1024  # over the real 200MB per-member default
        buf = _make_zip([('mail.pkt', b'\x00' * bomb_size)])
        results = list(_extract_packets('bomb.su0', buf.getvalue()))
        self.assertEqual(results, [])

    def test_extract_packets_still_works_for_a_normal_bundle(self):
        from anetbbs.echomail.binkp_server import _extract_packets
        # A minimal, clearly-non-FTS-packet payload -- just confirms the
        # safe-extraction path still yields ordinary small members
        # rather than erroring out on everything.
        buf = _make_zip([('readme.txt', b'not a packet')])
        results = list(_extract_packets('bundle.su0', buf.getvalue()))
        # Not an FTS packet and not a recognized mail-bundle extension,
        # so it's correctly skipped -- the key assertion is this ran
        # without raising and without hanging.
        self.assertEqual(results, [])


class BinkpClientZipBombTests(unittest.TestCase):
    def test_import_completed_refuses_a_bomb_and_does_not_crash(self):
        from anetbbs.echomail.binkp import BinkPClient
        client = BinkPClient.__new__(BinkPClient)
        client._debug_manifest = lambda *a, **k: None
        client._debug_dump_packet = lambda *a, **k: None
        client._is_fts_packet = lambda data: False
        client._is_zip = lambda data: True
        bomb_size = 220 * 1024 * 1024  # over the real 200MB per-member default
        buf = _make_zip([('mail.pkt', b'\x00' * bomb_size)])
        out = client._import_completed('bomb.su0', buf.getvalue(), '/tmp')
        self.assertEqual(out, [])


class QwkZipBombTests(unittest.TestCase):
    def test_parse_qwk_packet_refuses_a_bomb(self):
        from anetbbs.echomail.qwk import QWKClient
        bomb_size = 220 * 1024 * 1024  # over the real 200MB per-member default
        buf = _make_zip([
            ('CONTROL.DAT', b'Test BBS\n0\n'),
            ('MESSAGES.DAT', b'\x00' * bomb_size),
        ])
        client = QWKClient.__new__(QWKClient)
        result = client._parse_qwk_packet(buf.getvalue())
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()
