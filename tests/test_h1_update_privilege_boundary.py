"""Root never reads, writes, chmods or chowns through the service user's
tree (audit H-1, review findings F1-F4, F16).

1. A static check over update.sh and install.sh: every file operation on a
   path under INSTALL_DIR (or .env, the backup dir, the MRC config, the
   gallery script) runs through as_service. The few root-side operations
   that remain are listed explicitly with the reason they are safe.
2. Functional tests of the helpers update.sh relies on: root-only rollback
   snapshots, the installed-release record, trust checks on the code root
   runs, and the release trust files.
3. The REAL blocks extracted from the scripts, run against planted
   pathnames: dangling symlinks at the MRC config and gallery script
   paths, and a .env port value carrying a command.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HELPERS = REPO / 'deploy' / 'privileged_helpers.sh'

SERVICE_PATH = re.compile(
    r'\$\{?(INSTALL_DIR|ENV_FILE|BACKUP_DIR|DB_FILE|MRC_BRIDGE_CONFIG|MRC_CONFIG_FILE|'
    r'CODE_BACKUP_DIR|GALLERY_SCRIPT_DST|VENV_DIR|FTP_ROOT_DIR_GUESS|SSH_KEY)\b')
FILE_OP = re.compile(
    r'(^|[\s;&|(])(cp|mv|rm|rsync|chmod|chown|chgrp|mkdir|ln|touch|install|sed\s+-i|tee|'
    r'cat|grep|sqlite3|python3|find|truncate|dd)\s')
REDIRECT = re.compile(r'>>?\s*"?\$\{?(INSTALL_DIR|ENV_FILE|BACKUP_DIR|DB_FILE|MRC_BRIDGE_CONFIG|'
                      r'MRC_CONFIG_FILE|GALLERY_SCRIPT_DST)')


def _logical_lines(text):
    """Yield (lineno, logical line) outside comments and heredoc bodies."""
    lines = text.splitlines()
    i = 0
    heredoc_end = None
    while i < len(lines):
        raw = lines[i]
        if heredoc_end is not None:
            if raw.strip() == heredoc_end:
                heredoc_end = None
            i += 1
            continue
        start = i
        buf = raw
        while buf.rstrip().endswith('\\') and i + 1 < len(lines):
            i += 1
            buf = buf.rstrip()[:-1] + ' ' + lines[i]
        m = re.search(r"<<-?\s*'?\"?([A-Z][A-Z0-9_]*)'?\"?", buf)
        if m:
            heredoc_end = m.group(1)
        s = buf.strip()
        if s and not s.startswith('#'):
            yield start + 1, s
        i += 1


# Root-side operations on the service tree that stay, and why.
ALLOWED = {
    'update.sh': [
        # Ownership normalisation before the service user writes the tree:
        # GNU chown -R -P never follows symlinks (fchownat NOFOLLOW).
        'chown -R -h -P "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"',
        # Version recorded in the backup manifest is read as the service user.
        'OLD_VERSION=$(as_service cat',
        # Only sets a variable used in a comment-only no-op.
        'FTP_ROOT_DIR_GUESS="$INSTALL_DIR/data/ftp_root"',
        # find -quit / stat checks on root-owned staging and the source tree.
        'anetbbs_tree_not_service_writable "$SOURCE_DIR"',
        # The install path used as a search PATTERN in root-owned files
        # (/proc/<pid>/cmdline, /etc/sudoers.d), never opened as a file.
        'grep -qF "$INSTALL_DIR"; then',
        'grep -q "$INSTALL_DIR/deploy/run_',
    ],
    'install.sh': [],
}


class StaticBoundaryCheck(unittest.TestCase):
    def _violations(self, name):
        text = (REPO / name).read_text()
        bad = []
        for ln, line in _logical_lines(text):
            if not SERVICE_PATH.search(line):
                continue
            if not (FILE_OP.search(line) or REDIRECT.search(line)):
                continue
            if 'as_service' in line:
                continue
            if re.match(r'(info|warn|ok|fail|skip|bad|echo|step)\s', line):
                continue
            if any(a in line for a in ALLOWED[name]):
                continue
            bad.append(f'{name}:{ln}: {line[:160]}')
        return bad

    def test_update_sh_has_no_root_file_operation_on_service_paths(self):
        self.assertEqual(self._violations('update.sh'), [])

    def test_install_sh_service_tree_writes_after_handover(self):
        # install.sh is gated to a tree the service account never controlled
        # (see InstallGate below), so only the post-handover operations that
        # create service-user files are required to go through as_service.
        text = (REPO / 'install.sh').read_text()
        self.assertIn('as_service mkdir -p -- "$INSTALL_DIR/mrc/bridge"', text)
        self.assertIn("as_service env MRC_BACKEND=", text)
        self.assertIn("O_EXCL", text)
        self.assertIn("as_service bash -c 'set -o noclobber; cat > \"$1\"", text)
        self.assertNotIn('chmod 0755 "$INSTALL_DIR/deploy/$w"', text)

    def test_obsolete_wrapper_chmod_loop_removed(self):           # F16
        for name in ('update.sh', 'install.sh'):
            self.assertNotIn('chmod 0755 "$INSTALL_DIR/deploy/$w"', (REPO / name).read_text(), name)

    def test_rollback_never_restores_units_from_the_service_backup(self):   # F3
        text = (REPO / 'update.sh').read_text()
        self.assertNotIn('.service.bak', text)
        self.assertNotIn('anetbbs-nginx.bak', text)
        self.assertIn('anetbbs_snapshot_restore "$ROLLBACK_SNAP" "${ROLLBACK_FILES[@]}"', text)
        self.assertNotIn('chown -R "$SERVICE_USER":"$SERVICE_USER" "$BACKUP_DIR"', text)

    def test_env_values_reach_root_contexts_only_as_validated_ports(self):  # new finding
        text = (REPO / 'update.sh').read_text()
        code = '\n'.join(l for _, l in _logical_lines(text))
        self.assertNotRegex(code, r'grep[^\n]*\$INSTALL_DIR/\.env')
        self.assertNotRegex(code, r'grep[^\n]*"\$ENV_FILE"')
        for key in ('WEB_PORT', 'MRC_BRIDGE_PORT'):
            for m in re.finditer(re.escape(f'"${{EXISTING_ENV[{key}]:-'), code):
                before = code[max(0, m.start() - 4):m.start()]
                self.assertIn(before.strip()[-2:], ('-n', '-z'),
                              f'{key} from .env used other than as an emptiness test')


def _bash(script, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(['bash', '-c', f'set -eu -o pipefail\nwarn(){{ echo "W: $*" >&2; }}\n'
                           f'. "{HELPERS}"\n{script}'], capture_output=True, text=True,
                          timeout=60, env=e)


class SnapshotHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='snap-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.etc = self.tmp / 'etc'
        (self.etc / 'unit.service.d').mkdir(parents=True)
        (self.etc / 'unit.service').write_text('[Service]\nUser=anetbbs\n')
        (self.etc / 'unit.service.d' / 'x.conf').write_text('[Service]\nNice=1\n')
        self.files = [str(self.etc / 'unit.service'), str(self.etc / 'unit.service.d'),
                      str(self.etc / 'absent.service')]

    def _create(self):
        r = _bash(f'anetbbs_snapshot_create "{self.tmp}/rb" ' + ' '.join(f'"{f}"' for f in self.files))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_snapshot_is_root_only_complete_and_restorable(self):
        snap = self._create()
        self.assertEqual(os.stat(self.tmp / 'rb').st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(snap).st_mode & 0o777, 0o700)
        self.assertTrue(Path(snap, 'COMPLETE').exists())
        # The update changes all three.
        (self.etc / 'unit.service').write_text('[Service]\nUser=root\n')
        (self.etc / 'unit.service.d' / 'x.conf').unlink()
        (self.etc / 'absent.service').write_text('new\n')
        r = _bash(f'anetbbs_snapshot_restore "{snap}" ' + ' '.join(f'"{f}"' for f in self.files))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.etc / 'unit.service').read_text(), '[Service]\nUser=anetbbs\n')
        self.assertTrue((self.etc / 'unit.service.d' / 'x.conf').exists())
        self.assertFalse((self.etc / 'absent.service').exists())

    def test_tampered_or_incomplete_snapshot_is_not_used(self):
        for damage in ('edit', 'add', 'incomplete', 'mode'):
            snap = Path(self._create())
            stored = next(snap.joinpath('files').rglob('unit.service'))
            if damage == 'edit':
                stored.write_text('[Service]\nUser=root\nExecStart=/bin/sh -c evil\n')
            elif damage == 'add':
                (stored.parent / 'extra.service').write_text('x')
            elif damage == 'incomplete':
                (snap / 'COMPLETE').unlink()
            else:
                snap.chmod(0o755)
            (self.etc / 'unit.service').write_text('current\n')
            r = _bash(f'anetbbs_snapshot_restore "{snap}" "{self.etc}/unit.service"')
            self.assertNotEqual(r.returncode, 0, damage)
            self.assertEqual((self.etc / 'unit.service').read_text(), 'current\n', damage)

    def test_prune_keeps_newest(self):
        snaps = [self._create() for _ in range(4)]
        r = _bash(f'anetbbs_snapshot_prune "{self.tmp}/rb" 2')
        self.assertEqual(r.returncode, 0, r.stderr)
        left = sorted(str(p) for p in (self.tmp / 'rb').iterdir())
        self.assertEqual(left, sorted(snaps)[-2:])


class ReleaseStateAndTrust(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rs-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_state_roundtrip_and_untrusted_state_ignored(self):
        st = self.tmp / 'var' / 'release-state'
        r = _bash(f'anetbbs_release_state_write v1.1.74 "{"a" * 64}" "{st}" && anetbbs_release_state_read "{st}"')
        self.assertEqual(r.stdout.strip(), 'v1.1.74', r.stderr)
        self.assertEqual(st.stat().st_mode & 0o777, 0o644)
        st.chmod(0o664)
        r = _bash(f'anetbbs_release_state_read "{st}" || echo UNTRUSTED')
        self.assertIn('UNTRUSTED', r.stdout)
        r = _bash(f'anetbbs_release_state_write "v1.1.74;rm" "" "{st}" || echo REFUSED')
        self.assertIn('REFUSED', r.stdout)

    def test_version_compare(self):
        for a, b, want in (('v1.1.74', 'v1.1.9', '1'), ('v1.1.74', 'v1.1.74', '0'),
                           ('v1.1.74', 'v1.2.0', '-1'), ('v2.0.0', 'v1.99.99', '1'),
                           ('v1.1.08', 'v1.1.8', '0')):
            self.assertEqual(_bash(f'anetbbs_version_cmp {a} {b}').stdout.strip(), want, (a, b))

    def test_service_writable_source_detected(self):
        src = self.tmp / 'src'
        src.mkdir()
        (src / 'update.sh').write_text('#!/bin/bash\n')
        me = subprocess.run(['id', '-un'], capture_output=True, text=True).stdout.strip()
        r = _bash(f'anetbbs_tree_not_service_writable "{src}" {me} && echo TRUSTED || echo UNTRUSTED')
        self.assertIn('UNTRUSTED', r.stdout)          # owned by the "service user"
        r = _bash(f'anetbbs_tree_not_service_writable "{src}" nobody && echo TRUSTED || echo UNTRUSTED')
        self.assertIn('TRUSTED', r.stdout)
        (src / 'update.sh').chmod(0o666)              # world-writable file inside
        r = _bash(f'anetbbs_tree_not_service_writable "{src}" nobody && echo TRUSTED || echo UNTRUSTED')
        self.assertIn('UNTRUSTED', r.stdout)

    def test_trust_files_install_and_empty_template_removes(self):
        repo = self.tmp / 'r'
        (repo / 'deploy').mkdir(parents=True)
        (repo / 'deploy' / 'release-signers').write_text('anetbbs-release ssh-ed25519 AAAA x\n')
        (repo / 'deploy' / 'release-revoked-keys').write_text('# none\n')
        (repo / 'deploy' / 'release-denylist').write_text('a' * 64 + ' bad\n')
        dst = self.tmp / 'etc'
        dst.mkdir()
        (dst / 'release-revoked-keys').write_text('stale\n')
        r = _bash(f'anetbbs_install_release_trust "{repo}" "{dst}"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((dst / 'release-signers').exists())
        self.assertTrue((dst / 'release-denylist').exists())
        self.assertFalse((dst / 'release-revoked-keys').exists(), 'empty template must remove the old copy')
        for f in ('release-signers', 'release-denylist'):
            self.assertEqual((dst / f).stat().st_mode & 0o777, 0o644)
        (repo / 'deploy' / 'release-signers').write_text('# withdrawn\n')
        _bash(f'anetbbs_install_release_trust "{repo}" "{dst}"')
        self.assertFalse((dst / 'release-signers').exists())

    def test_shipped_trust_files(self):
        deploy = REPO / 'deploy'
        signers = [l for l in (deploy / 'release-signers').read_text().splitlines()
                   if l.strip() and not l.startswith('#')]
        revoked = [l for l in (deploy / 'release-revoked-keys').read_text().splitlines()
                   if l.strip() and not l.startswith('#')]
        deny = [l.split()[0] for l in (deploy / 'release-denylist').read_text().splitlines()
                if l.strip() and not l.startswith('#')]
        self.assertEqual(len(signers), 1)
        self.assertEqual(len(revoked), 1)
        # The trusted key is not the revoked one.
        self.assertNotIn(revoked[0].split()[1], signers[0])
        # The rejected, signed candidate archive is denied.
        self.assertIn('a9a85305d24b8192998675a01a027c74e2ef13c8c2f2ece91009c3a1388d6aae', deny)
        for d in deny:
            self.assertRegex(d, r'^[0-9a-f]{64}$')


class PlantedPathnames(unittest.TestCase):
    """The real generation blocks, against symlinks planted by the service
    user. as_service is the current user here; what matters is that nothing
    is written through the link and that the block never runs as root."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='plant-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.inst = self.tmp / 'inst'
        (self.inst / 'mrc' / 'bridge').mkdir(parents=True)
        (self.inst / 'data').mkdir()
        self.target = self.tmp / 'root-owned-target'      # e.g. /etc/cron.d/x

    def _update_block(self, start, end):
        text = (REPO / 'update.sh').read_text()
        i = text.index(start)
        j = text.index(end, i)
        return text[i:j]

    def _env_port(self):
        text = (REPO / 'update.sh').read_text()
        i = text.index('_env_port() {')
        return text[i:text.index('\n}\n', i) + 3]

    def _run(self, block, extra=''):
        script = (f'as_service() {{ "$@"; }}\ninfo(){{ :; }}\nok(){{ :; }}\n'
                  f'INSTALL_DIR="{self.inst}"\nSERVICE_USER=anetbbs\n'
                  f'declare -A EXISTING_ENV=([BBS_NAME]=x)\n{self._env_port()}\n{extra}\n{block}')
        return _bash(script)

    def test_mrc_config_dangling_symlink_is_not_followed(self):          # F1
        cfg = self.inst / 'mrc' / 'bridge' / 'config.json'
        cfg.symlink_to(self.target)
        block = self._update_block('# Ensure MRC bridge config.json exists',
                                   '# Ensure data/mrc directory exists')
        r = self._run(block, f'MRC_BRIDGE_CONFIG="{cfg}"\nNEW_VERSION=v1\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.target.exists(), 'root-side target was created through the link')
        self.assertTrue(cfg.is_symlink())

    def test_mrc_config_created_exclusively_when_absent(self):
        cfg = self.inst / 'mrc' / 'bridge' / 'config.json'
        block = self._update_block('# Ensure MRC bridge config.json exists',
                                   '# Ensure data/mrc directory exists')
        r = self._run(block, f'MRC_BRIDGE_CONFIG="{cfg}"\nNEW_VERSION=v1\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(cfg.is_file() and not cfg.is_symlink())
        self.assertEqual(cfg.stat().st_mode & 0o777, 0o640)

    def test_gallery_script_symlink_is_not_followed(self):              # F2
        stage = self.tmp / 'stage' / 'deploy'
        stage.mkdir(parents=True)
        (stage / 'anet-gallery.sh').write_text('#!/bin/sh\necho __INSTALL_DIR__\n')
        dst = self.inst / 'anet-gallery.sh'
        dst.symlink_to(self.target)
        block = self._update_block('GALLERY_SCRIPT_SRC="$SRC_FOR_SERVICE/deploy/anet-gallery.sh"',
                                   'anetbbs_write_install_conf "$INSTALL_DIR"')
        r = self._run(block, f'SRC_FOR_SERVICE="{self.tmp / "stage"}"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.target.exists())
        # And with nothing planted, it is created normally.
        dst.unlink()
        r = self._run(block, f'SRC_FOR_SERVICE="{self.tmp / "stage"}"\n')
        self.assertEqual(dst.read_text(), f'#!/bin/sh\necho {self.inst}\n')
        self.assertEqual(dst.stat().st_mode & 0o777, 0o755)

    def test_env_port_rejects_anything_but_a_port(self):
        script = (f'declare -A EXISTING_ENV=([A]="1|;s|.*|id|e;#" [B]=" 8081 " [C]="70000" [D]="")\n'
                  f'{self._env_port()}\n'
                  'echo "$(_env_port A 8080) $(_env_port B 1) $(_env_port C 2) $(_env_port D 3)"')
        r = _bash(script)
        self.assertEqual(r.stdout.strip(), '8080 8081 2 3', r.stderr)


class InstallGate(unittest.TestCase):
    """install.sh refuses to run as root over a tree the service account
    already controls, unless the operator overrides it explicitly."""

    def _gate(self):
        text = (REPO / 'install.sh').read_text()
        i = text.index('# ─── Trust boundary (audit H-1)')
        j = text.index('step "Step 4/9: Creating system user and directories"', i)
        return text[i:j]

    def _run(self, inst, src, env=None):
        script = (f'fail(){{ echo "FAIL: $*"; }}\nINSTALL_DIR="{inst}"\nSOURCE_DIR="{src}"\n'
                  f'SERVICE_USER=nobody-such-user-x\n{self._gate()}\necho PASSED')
        return _bash(script, env)

    def test_existing_non_root_tree_refused_without_override(self):
        tmp = Path(tempfile.mkdtemp(prefix='gate-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        inst = tmp / 'opt-anetbbs'
        inst.mkdir()
        (inst / '.env').write_text('x=1\n')
        r = self._run(inst, tmp / 'src')
        self.assertNotIn('PASSED', r.stdout)
        self.assertIn('Refusing to install as root', r.stdout)
        r = self._run(inst, tmp / 'src', {'ANETBBS_REINSTALL_TRUSTS_SERVICE_ACCOUNT': 'yes'})
        self.assertIn('PASSED', r.stdout)

    def test_fresh_location_passes(self):
        tmp = Path(tempfile.mkdtemp(prefix='gate-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        r = self._run(tmp / 'does-not-exist', tmp)
        self.assertIn('PASSED', r.stdout, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
