"""Admin -> Backups "Restore DB" must restore the database and bring the
BBS back.

Real gap found in a runtime rehearsal: the restore helper ran inside
anetbbs-web (the Restore button's own process tree) and began with
`systemctl stop anetbbs-web`. Stopping a unit kills everything in its
cgroup, so the helper died before copying anything and nothing started
the web unit again: the database was not restored and the web interface
stayed down. The other services also kept the database open across the
swap.

deploy/run_restore.sh now hands the swap to its own transient unit,
stops every running ANetBBS service, moves the live database and its
WAL/SHM aside, checks the backup and the result with
PRAGMA integrity_check, and starts the same services again.

These tests run a copy of the real helper with only its root-owned
config path substituted; runuser, systemctl and systemd-run are shims
that record what they were asked to do.
"""
import os
import re
import sqlite3
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'deploy' / 'run_restore.sh'


def _make_db(path, rows, wal=False):
    c = sqlite3.connect(path)
    if wal:
        c.execute('pragma journal_mode=wal')
    c.execute('create table users(id integer primary key, name text)')
    c.executemany('insert into users(name) values (?)', [(r,) for r in rows])
    c.commit()
    c.close()


class RestoreDbDetached(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='restore-db-test-'))
        t = self.tmp
        self.inst = t / 'inst'
        (self.inst / 'data' / 'backups').mkdir(parents=True)
        self.bk = self.inst / 'data' / 'backups' / 'anetbbs-backup-20261125000000'
        self.bk.mkdir()
        self.calls = t / 'calls.log'
        conf = t / 'anetbbs.install'
        conf.write_text(f'INSTALL_DIR={self.inst}\nSERVICE_USER=svcuser\n')
        os.chmod(conf, 0o644)
        # Copy of the real helper: only the config path and its root-owner
        # check are substituted (the test can't create root-owned files).
        src = HELPER.read_text()
        self.assertIn('CONF=/etc/anetbbs.install', src)
        src = src.replace('CONF=/etc/anetbbs.install', f'CONF={conf}')
        src = src.replace('[ "$(stat -c %u "$CONF")" = 0 ] || fail', 'true || fail')
        self.helper = t / 'run_restore.sh'
        self.helper.write_text(src)
        os.chmod(self.helper, 0o755)
        bin_ = t / 'bin'
        bin_.mkdir()
        shims = {
            # runuser -u USER -- CMD...: run CMD as the test user.
            'runuser': 'echo "runuser $*" >> "$CALLS"; shift 3; exec "$@"',
            'systemctl': textwrap.dedent('''\
                echo "systemctl $*" >> "$CALLS"
                if [ "$1" = list-units ]; then
                    printf '%s\\n' 'anetbbs.service loaded active running x' \\
                        'anetbbs-web.service loaded active running x' \\
                        'anetbbs-mrc-bridge.service loaded active running x' \\
                        'anetbbs-upgrade.service loaded active running x'
                fi
                exit 0'''),
            # systemd-run ... CMD...: record, then run CMD in "the new unit"
            # (synchronously here) with the --setenv it was given.
            'systemd-run': textwrap.dedent('''\
                echo "systemd-run $*" >> "$CALLS"
                envs=()
                while [ $# -gt 0 ]; do
                    case "$1" in
                        --setenv=*) envs+=("${1#--setenv=}"); shift ;;
                        --*) shift ;;
                        *) break ;;
                    esac
                done
                env "${envs[@]}" "$@" >> "$CALLS.detached" 2>&1
                echo "detached-rc=$?" >> "$CALLS"
                exit 0'''),
        }
        for name, body in shims.items():
            p = bin_ / name
            p.write_text('#!/bin/bash\n' + body + '\n')
            os.chmod(p, 0o755)
        self.env = dict(os.environ, PATH=f'{bin_}:{os.environ["PATH"]}', CALLS=str(self.calls))

    def run_helper(self, *args):
        return subprocess.run(['bash', str(self.helper), *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def calls_list(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_restore_detaches_stops_all_swaps_and_restarts(self):
        live = self.inst / 'data' / 'anetbbs.db'
        _make_db(live, ['alice', 'bob', 'sentinel-live-only'], wal=True)
        (self.inst / 'data' / 'anetbbs.db-wal').write_bytes(b'stale wal that must not be replayed')
        _make_db(self.bk / 'anetbbs.db.bak', ['alice', 'bob'])
        r = self.run_helper('db', str(self.bk))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('started in anetbbs-restore-db.service', r.stdout)
        calls = self.calls_list()
        detached = (self.tmp / 'calls.log.detached').read_text()
        self.assertIn('detached-rc=0', calls, detached)
        # Detach happens before anything is stopped, and only once.
        i_run = next(i for i, c in enumerate(calls) if c.startswith('systemd-run '))
        i_stop = next(i for i, c in enumerate(calls) if c.startswith('systemctl stop'))
        self.assertLess(i_run, i_stop)
        self.assertIn('--unit=anetbbs-restore-db', calls[i_run])
        self.assertIn('--setenv=ANETBBS_RESTORE_DETACHED=1', calls[i_run])
        self.assertEqual(sum(c.startswith('systemd-run ') for c in calls), 1)
        # Every running service except the upgrade unit stops, then starts again.
        stops = [c for c in calls if c.startswith('systemctl stop')]
        starts = [c for c in calls if c.startswith('systemctl start')]
        want = 'anetbbs.service anetbbs-web.service anetbbs-mrc-bridge.service'
        self.assertEqual(stops, [f'systemctl stop {want}'])
        self.assertEqual(starts, [f'systemctl start {want}'])
        self.assertLess(calls.index(stops[0]), calls.index(starts[0]))
        # Restored content, integrity, no stale WAL beside it, previous DB kept.
        c = sqlite3.connect(live)
        self.assertEqual(c.execute('pragma integrity_check').fetchone()[0], 'ok')
        self.assertEqual([r[0] for r in c.execute('select name from users order by id')], ['alice', 'bob'])
        c.close()
        data = sorted(p.name for p in (self.inst / 'data').iterdir())
        pre = [n for n in data if n.startswith('anetbbs.db.pre-restore-')]
        self.assertTrue(any(n.endswith('-wal') for n in pre), data)
        self.assertTrue(any(re.fullmatch(r'anetbbs\.db\.pre-restore-\d{14}', n) for n in pre), data)
        self.assertEqual(stat.S_IMODE(os.stat(live).st_mode), 0o600)
        # Every file operation went through the service user.
        self.assertTrue(any(c.startswith('runuser -u svcuser -- cp -f --remove-destination') for c in calls))
        self.assertTrue(any(c.startswith('runuser -u svcuser -- mv -f') for c in calls))

    def test_corrupt_backup_refused_before_anything_stops(self):
        live = self.inst / 'data' / 'anetbbs.db'
        _make_db(live, ['alice'])
        before = live.read_bytes()
        (self.bk / 'anetbbs.db.bak').write_bytes(b'SQLite format 3\x00' + b'\x00' * 200)
        r = self.run_helper('db', str(self.bk))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('integrity_check', r.stderr)
        calls = self.calls_list()
        self.assertFalse([c for c in calls if c.startswith(('systemctl stop', 'systemd-run'))], calls)
        self.assertEqual(live.read_bytes(), before)

    def test_symlinked_backup_db_refused(self):
        _make_db(self.inst / 'data' / 'anetbbs.db', ['alice'])
        os.symlink('/etc/passwd', self.bk / 'anetbbs.db.bak')
        r = self.run_helper('db', str(self.bk))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('is a symlink', r.stderr)
        self.assertFalse([c for c in self.calls_list() if c.startswith(('systemctl stop', 'systemd-run'))])


class RestoreDbFlashSaysStarted(unittest.TestCase):
    def test_message_does_not_claim_the_restore_already_finished(self):
        src = (ROOT / 'anetbbs' / 'web' / 'backups_admin.py').read_text()
        self.assertIn("'DB restore from ' + name + ' started", src)
        self.assertNotIn("'DB restored from '", src)


if __name__ == '__main__':
    unittest.main()
