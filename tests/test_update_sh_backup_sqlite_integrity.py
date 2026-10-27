"""Regression test for a real review finding (2026-10-23):
update.sh's backup_sqlite() used to fall back to a raw cp(1) of the
live database if sqlite3 wasn't installed OR if `.backup` itself
failed. A raw copy of a LIVE, possibly mid-write WAL-mode SQLite
database can silently omit committed transactions still sitting in
the -wal file -- producing a backup that opens fine but is quietly
missing data, while the function still reported success.

Fixed (2026-10-23): no cp fallback at all, `.backup` must succeed,
and the resulting backup file must then pass its own `PRAGMA
integrity_check` (exactly "ok") before the function reports success.

Second real bug, this one a live report from a sysop (AloxeCorton,
2026-10-27): removing the cp(1) fallback made the sqlite3(1) CLI
package a hard, undocumented dependency for every future update --
install.sh/update.sh never verify or install it. A sysop without that
package installed got stuck permanently unable to update at all,
since the only remaining options were an unsafe cp(1) (correctly
removed) or refusing entirely. Fixed by falling back to Python's
stdlib sqlite3 module, which wraps the exact same SQLite Online
Backup API the CLI's own .backup command uses -- not a raw cp(1), and
still gated by the same integrity_check requirement. python3 is
already a hard, pre-existing ANetBBS dependency, so this adds none.

This test extracts the REAL backup_sqlite() function body out of
update.sh and runs it in actual bash -- not a reimplementation --
with a real on-disk SQLite database (via Python's sqlite3 module) and,
for the failure-mode tests, a fake `sqlite3` shim placed first on
PATH, so it can't drift from what's actually shipped.
"""
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_HAS_REAL_SQLITE3_CLI = shutil.which('sqlite3') is not None
_BASH = shutil.which('bash') or '/bin/bash'


def _extract_backup_sqlite(content):
    m = re.search(
        r'^backup_sqlite\(\) \{.*?\n\}\n', content, re.DOTALL | re.MULTILINE)
    assert m, 'could not find backup_sqlite() in update.sh'
    return m.group(0)


class _Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        content = (REPO_ROOT / 'update.sh').read_text()
        cls.fn_code = _extract_backup_sqlite(content)
        # Confirm the exact real-bug literal is gone -- not present
        # anywhere inside the extracted function body.
        assert 'falling back to cp' not in cls.fn_code
        assert re.search(r'\bif cp "\$src" "\$dst"', cls.fn_code) is None

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.dir = Path(self.tmpdir.name)

    def _make_real_db(self, name='src.db', rows=1):
        path = self.dir / name
        conn = sqlite3.connect(str(path))
        conn.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)')
        for i in range(rows):
            conn.execute('INSERT INTO t (v) VALUES (?)', (f'row{i}',))
        conn.commit()
        conn.close()
        return path

    def _run(self, src, dst, label='test DB', extra_path=None):
        env = os.environ.copy()
        if extra_path is not None:
            env['PATH'] = f"{extra_path}:{env.get('PATH', '')}"
        script = (
            'ok() { echo "OK: $*"; }\n'
            'warn() { echo "WARN: $*" >&2; }\n'
            f'{self.fn_code}\n'
            f'backup_sqlite {src!r} {dst!r} {label!r}\n'
            'echo "RC=$?"\n'
        )
        return subprocess.run(['bash', '-c', script],
                              capture_output=True, text=True, env=env)

    def _fake_sqlite3_on_path(self, behavior):
        """Writes a fake `sqlite3` executable (not the real backup_sqlite
        extraction, just a PATH shim used to force specific failure
        modes that are hard to trigger with a real sqlite3 binary) and
        returns its containing directory."""
        bindir = self.dir / 'fakebin'
        bindir.mkdir()
        fake = bindir / 'sqlite3'
        fake.write_text(behavior)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        return str(bindir)

    def _python3_only_path(self):
        """A PATH directory containing only a real python3 (via symlink)
        and nothing else -- simulates a system where the sqlite3(1) CLI
        package was never installed but python3 (a hard, pre-existing
        ANetBBS dependency) is present. This is the exact shape of the
        real AloxeCorton bug report: sqlite3(1) missing, update blocked
        indefinitely."""
        real_python3 = shutil.which('python3')
        assert real_python3, ('python3 not found on this machine -- '
                              'required to run this test file at all')
        bindir = self.dir / 'python3onlybin'
        bindir.mkdir()
        os.symlink(real_python3, bindir / 'python3')
        return str(bindir)


@unittest.skipUnless(_HAS_REAL_SQLITE3_CLI,
                     'sqlite3 CLI binary not installed on this machine -- '
                     'these two tests exercise the REAL binary deliberately '
                     '(everything else in this file uses a PATH shim so it '
                     'never depends on one being present)')
class SuccessfulBackupTests(_Harness):
    def test_real_sqlite_backup_succeeds_and_verifies(self):
        src = self._make_real_db(rows=3)
        dst = self.dir / 'dst.db'
        result = self._run(str(src), str(dst))
        self.assertIn('RC=0', result.stdout)
        self.assertIn('integrity_check ok', result.stdout, result.stdout)
        self.assertTrue(dst.exists())
        conn = sqlite3.connect(str(dst))
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM t').fetchone()[0], 3)
        conn.close()

    def test_nonexistent_source_is_not_a_failure(self):
        src = self.dir / 'does_not_exist.db'
        dst = self.dir / 'dst.db'
        result = self._run(str(src), str(dst))
        self.assertIn('RC=0', result.stdout)
        self.assertFalse(dst.exists())


class MissingSqlite3Tests(_Harness):
    def test_missing_sqlite3_binary_fails_with_no_cp_fallback(self):
        src = self._make_real_db()
        dst = self.dir / 'dst.db'
        # Empty PATH-prepend dir with nothing in it, and strip the
        # real sqlite3 out of PATH entirely for this one subprocess.
        env = os.environ.copy()
        env['PATH'] = '/nonexistent-empty-dir'
        script = (
            'ok() { echo "OK: $*"; }\n'
            'warn() { echo "WARN: $*" >&2; }\n'
            f'{self.fn_code}\n'
            f'backup_sqlite {str(src)!r} {str(dst)!r} "test DB"\n'
            'echo "RC=$?"\n'
        )
        result = subprocess.run([_BASH, '-c', script],
                                capture_output=True, text=True, env=env)
        self.assertIn('RC=1', result.stdout)
        self.assertIn('not installed', result.stderr)
        self.assertFalse(dst.exists(),
                         'no cp fallback -- the destination must never be '
                         'created when sqlite3 itself is unavailable')


class MissingSqlite3CliButPython3AvailableTests(_Harness):
    """The real AloxeCorton bug: sqlite3(1) CLI absent, python3 present
    -- update must now fall back to python3's stdlib sqlite3 module
    instead of refusing outright."""

    def test_falls_back_to_python_sqlite3_module_and_succeeds(self):
        src = self._make_real_db(rows=5)
        dst = self.dir / 'dst.db'
        env = os.environ.copy()
        # PATH contains ONLY a real python3 -- no sqlite3(1) anywhere.
        env['PATH'] = self._python3_only_path()
        script = (
            'ok() { echo "OK: $*"; }\n'
            'warn() { echo "WARN: $*" >&2; }\n'
            f'{self.fn_code}\n'
            f'backup_sqlite {str(src)!r} {str(dst)!r} "production DB"\n'
            'echo "RC=$?"\n'
        )
        result = subprocess.run([_BASH, '-c', script],
                                capture_output=True, text=True, env=env)
        self.assertIn('RC=0', result.stdout, result.stderr)
        self.assertIn('integrity_check ok', result.stdout, result.stdout)
        self.assertTrue(dst.exists())
        conn = sqlite3.connect(str(dst))
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM t').fetchone()[0], 5)
        conn.close()

    def test_a_non_database_source_still_fails_via_python_fallback(self):
        """The python fallback must not silently 'succeed' backing up
        garbage -- it's gated by the same integrity_check as the CLI
        path."""
        src = self.dir / 'not_a_real_db.db'
        src.write_text('this is not a sqlite database')
        dst = self.dir / 'dst.db'
        env = os.environ.copy()
        env['PATH'] = self._python3_only_path()
        script = (
            'ok() { echo "OK: $*"; }\n'
            'warn() { echo "WARN: $*" >&2; }\n'
            f'{self.fn_code}\n'
            f'backup_sqlite {str(src)!r} {str(dst)!r} "production DB"\n'
            'echo "RC=$?"\n'
        )
        result = subprocess.run([_BASH, '-c', script],
                                capture_output=True, text=True, env=env)
        self.assertIn('RC=1', result.stdout)

    def test_prefers_real_sqlite3_cli_over_python_fallback_when_both_present(self):
        """When sqlite3(1) IS installed, it must still be used directly
        -- the python3 fallback is a last resort, not a replacement."""
        if not _HAS_REAL_SQLITE3_CLI:
            self.skipTest('sqlite3 CLI binary not installed on this machine')
        src = self._make_real_db(rows=2)
        dst = self.dir / 'dst.db'
        result = self._run(str(src), str(dst))
        self.assertIn('RC=0', result.stdout)
        self.assertIn('sqlite3 .backup', result.stdout)
        self.assertNotIn('python3 sqlite3 backup API', result.stdout)


class FailedBackupCommandTests(_Harness):
    def test_backup_command_failure_propagates_as_failure(self):
        src = self._make_real_db()
        dst = self.dir / 'dst.db'
        fakebin = self._fake_sqlite3_on_path(
            '#!/bin/bash\nexit 1\n')  # .backup itself always fails
        result = self._run(str(src), str(dst), extra_path=fakebin)
        self.assertIn('RC=1', result.stdout)
        self.assertIn('.backup', result.stderr)
        self.assertFalse(dst.exists(),
                         'no cp fallback -- a failed .backup must not '
                         'silently produce a raw-copy destination file')


class CorruptedBackupDetectionTests(_Harness):
    def test_backup_that_fails_integrity_check_is_treated_as_a_failure(self):
        src = self._make_real_db()
        dst = self.dir / 'dst.db'
        # Fake sqlite3: the .backup invocation "succeeds" (just touches
        # the destination with garbage, simulating a corrupted/torn
        # backup), but the integrity_check invocation reports real
        # corruption instead of "ok".
        fakebin = self._fake_sqlite3_on_path(
            '#!/bin/bash\n'
            'if [[ "$2" == .backup* ]]; then\n'
            '    dst=$(echo "$2" | sed "s/.backup .//; s/.$//")\n'
            '    echo "not a real sqlite file" > "$dst"\n'
            '    exit 0\n'
            'fi\n'
            'if [[ "$2" == *integrity_check* ]]; then\n'
            '    echo "database disk image is malformed"\n'
            '    exit 0\n'
            'fi\n')
        result = self._run(str(src), str(dst), extra_path=fakebin)
        self.assertIn('RC=1', result.stdout)
        self.assertIn('integrity_check', result.stderr)
        self.assertIn('database disk image is malformed', result.stderr)

    def test_integrity_check_returning_empty_is_also_a_failure(self):
        """Defends the exact-match check itself -- an empty/missing
        integrity_check result must not be mistaken for "ok" by a
        looser comparison (e.g. a substring or truthiness check)."""
        src = self._make_real_db()
        dst = self.dir / 'dst.db'
        fakebin = self._fake_sqlite3_on_path(
            '#!/bin/bash\n'
            'if [[ "$2" == .backup* ]]; then exit 0; fi\n'
            'exit 0\n')  # integrity_check prints nothing at all
        result = self._run(str(src), str(dst), extra_path=fakebin)
        self.assertIn('RC=1', result.stdout)


class FailurePropagationTests(_Harness):
    """Confirms the real caller in update.sh (unchanged by this fix)
    still correctly treats a backup_sqlite failure as fatal."""

    def test_caller_aborts_before_touching_anything_live_on_failure(self):
        content = (REPO_ROOT / 'update.sh').read_text()
        self.assertIn(
            'if ! backup_sqlite "$INSTALL_DIR/data/anetbbs.db" '
            '"$BACKUP_DIR/anetbbs.db.bak" "production DB"; then',
            content)
        self.assertIn(
            'fail "Could not back up data/anetbbs.db — aborting before '
            'touching anything live."',
            content)

    def test_end_to_end_caller_pattern_aborts_on_missing_sqlite3(self):
        src = self._make_real_db()
        dst = self.dir / 'dst.db'
        env = os.environ.copy()
        env['PATH'] = '/nonexistent-empty-dir'
        script = (
            'ok() { echo "OK: $*"; }\n'
            'warn() { echo "WARN: $*" >&2; }\n'
            'fail() { echo "FAIL: $*" >&2; }\n'
            f'{self.fn_code}\n'
            f'if ! backup_sqlite {str(src)!r} {str(dst)!r} "production DB"; then\n'
            '    fail "Could not back up data/anetbbs.db — aborting before touching anything live."\n'
            '    exit 1\n'
            'fi\n'
            'echo "SHOULD NOT REACH HERE"\n'
        )
        result = subprocess.run([_BASH, '-c', script],
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('SHOULD NOT REACH HERE', result.stdout)
        self.assertIn('aborting before touching anything live', result.stderr)


if __name__ == '__main__':
    unittest.main()
