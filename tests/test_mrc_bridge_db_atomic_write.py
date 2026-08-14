"""Regression test for a real gap found in a security/performance
audit: mrc/bridge/db.py's BridgeDB._save_json() used to write directly
to the real sessions.json/profiles.json file, not atomically -- a
crash or kill mid-write leaves a truncated/invalid JSON file behind,
which _load_json() silently treats as {} on the next start, losing
every user's session/profile state at once. Fixed via temp-file +
os.replace(), which is atomic on both POSIX and Windows.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.db import BridgeDB


class BridgeDbAtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = BridgeDB(data_dir=self._tmp.name)

    def test_normal_save_and_load_round_trip(self):
        self.db.save_profile('alice', {'nick': 'alice', 'color': 'red'})
        fresh = BridgeDB(data_dir=self._tmp.name)
        profile = fresh.get_profile('alice')
        self.assertEqual(profile['nick'], 'alice')
        self.assertEqual(profile['color'], 'red')

    def test_no_leftover_tmp_file_after_a_successful_write(self):
        self.db.save_session('sess1', {'nick': 'bob'})
        tmp_path = self.db.sessions_file.with_suffix(
            self.db.sessions_file.suffix + '.tmp')
        self.assertFalse(tmp_path.exists(),
                         'the temp file must be renamed away, not left behind')
        self.assertTrue(self.db.sessions_file.exists())

    def test_crash_mid_write_does_not_corrupt_the_existing_file(self):
        # Establish a known-good file first.
        self.db.save_profile('carol', {'nick': 'carol'})
        good_content = self.db.profiles_file.read_text()

        # Simulate a crash partway through json.dump() on the NEXT
        # write -- the real file must be completely unaffected (still
        # the previous good content), not truncated/partial, since the
        # write only ever touches a sibling .tmp file until the final
        # atomic os.replace().
        with patch('mrc.bridge.db.json.dump', side_effect=OSError('disk full (simulated)')):
            with self.assertRaises(OSError):
                self.db.save_profile('dave', {'nick': 'dave'})

        self.assertEqual(self.db.profiles_file.read_text(), good_content,
                         'a failed write must never corrupt/truncate the real file')
        # Reloading from disk must still see the last good state, not
        # an empty {} (the silent-data-loss symptom this fix prevents).
        fresh = BridgeDB(data_dir=self._tmp.name)
        self.assertIsNotNone(fresh.get_profile('carol'))
        self.assertIsNone(fresh.get_profile('dave'),
                          'the failed write must not have partially landed')

    def test_stale_tmp_file_from_a_real_past_crash_does_not_block_future_writes(self):
        # A leftover .tmp from a genuinely crashed prior process --
        # the next successful save must still overwrite/replace it
        # cleanly.
        tmp_path = self.db.sessions_file.with_suffix(
            self.db.sessions_file.suffix + '.tmp')
        tmp_path.write_text('{"garbage": "from a past crash"')  # invalid JSON, on purpose

        self.db.save_session('sess2', {'nick': 'eve'})

        self.assertEqual(json.loads(self.db.sessions_file.read_text())['sess2']['nick'], 'eve')


if __name__ == '__main__':
    unittest.main()
