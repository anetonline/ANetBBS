"""Regression test for a real live incident: `anetbbs-cfg` (added in
v1.0.20) was installed into venv/bin/ by `pip install -e .` on update,
but had no /usr/local/bin/ shortcut -- anetbbs/installer/symlinks.py's
WRAPPERS tuple is a hardcoded list of console-script names, and
anetbbs-cfg's entry point was never added to it. `update.sh` also never
called ensure_symlinks() at all (only the install/upgrade wizards did),
so even fixing WRAPPERS wouldn't have self-healed an existing install --
both gaps are fixed together (WRAPPERS here, update.sh separately).

This test covers the WRAPPERS list and ensure_symlinks()'s own
create/update/idempotent behavior directly (no root or real
/usr/local/bin needed -- uses a temp dir as the target).
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.installer.symlinks import WRAPPERS, ensure_symlinks


class WrappersListTests(unittest.TestCase):
    def test_anetbbs_cfg_is_registered(self):
        self.assertIn("anetbbs-cfg", WRAPPERS)

    def test_no_duplicate_entries(self):
        self.assertEqual(len(WRAPPERS), len(set(WRAPPERS)))


class EnsureSymlinksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.install_dir = self.tmp / "install"
        self.venv_bin = self.install_dir / "venv" / "bin"
        self.venv_bin.mkdir(parents=True)
        self.target_dir = self.tmp / "usr_local_bin"
        self.target_dir.mkdir()
        for name in WRAPPERS:
            script = self.venv_bin / name
            script.write_text("#!/bin/sh\necho stub\n")
            script.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_a_symlink_per_wrapper(self):
        results = ensure_symlinks(self.install_dir, target_dir=str(self.target_dir))
        statuses = dict(results)
        self.assertEqual(set(statuses), set(WRAPPERS))
        for name in WRAPPERS:
            self.assertEqual(statuses[name], "created")
            link = self.target_dir / name
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(str(link)), str(self.venv_bin / name))

    def test_rerun_is_idempotent(self):
        ensure_symlinks(self.install_dir, target_dir=str(self.target_dir))
        results = ensure_symlinks(self.install_dir, target_dir=str(self.target_dir))
        statuses = dict(results)
        for name in WRAPPERS:
            self.assertEqual(statuses[name], "exists")

    def test_missing_venv_script_reported_as_failed(self):
        (self.venv_bin / "anetbbs-cfg").unlink()
        results = ensure_symlinks(self.install_dir, target_dir=str(self.target_dir))
        statuses = dict(results)
        self.assertEqual(statuses["anetbbs-cfg"], "failed:not in venv")
        self.assertFalse((self.target_dir / "anetbbs-cfg").exists())

    def test_repoints_stale_symlink_to_new_venv(self):
        # Simulate an old install pointing at a different venv path.
        stale_target = self.tmp / "old_venv_bin" / "anetbbs-cfg"
        stale_target.parent.mkdir(parents=True)
        stale_target.write_text("old")
        (self.target_dir / "anetbbs-cfg").symlink_to(stale_target)

        results = ensure_symlinks(self.install_dir, target_dir=str(self.target_dir))
        statuses = dict(results)
        self.assertEqual(statuses["anetbbs-cfg"], "updated")
        self.assertEqual(
            os.readlink(str(self.target_dir / "anetbbs-cfg")),
            str(self.venv_bin / "anetbbs-cfg"),
        )


if __name__ == "__main__":
    unittest.main()
