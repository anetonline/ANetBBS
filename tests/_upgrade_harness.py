"""Shared harness: run the REAL deploy/run_upgrade.sh as a normal user.

Only its root-only constants are swapped for paths under a temp directory
(each substitution must match exactly once, so the tests stay tied to the
real file). ``systemd-run`` is replaced by a PATH shim that runs the
re-exec'd wrapper in the foreground with the same --setenv variables and
appends its output to the --property=StandardOutput=append: file, so the
whole pre-detach phase (lock, fresh log, run id) is exercised too.
"""
import functools
import hashlib
import http.server
import os
import subprocess
import tarfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / 'deploy' / 'run_upgrade.sh'

_SYSTEMD_RUN_SHIM = r'''#!/usr/bin/env bash
# Test shim for systemd-run: foreground, honours --setenv and append: output.
out=""
envs=()
while [ $# -gt 0 ]; do
    case "$1" in
        --setenv=*) envs+=("${1#--setenv=}") ;;
        --property=StandardOutput=append:*) out="${1#--property=StandardOutput=append:}" ;;
        --*) ;;
        *) break ;;
    esac
    shift
done
echo "$*" >> "$(dirname "$0")/systemd-run.calls"
if [ -n "$out" ]; then
    env "${envs[@]}" "$@" >> "$out" 2>&1
else
    env "${envs[@]}" "$@"
fi
'''


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(directory):
    handler = functools.partial(_Quiet, directory=directory)
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()


class Wrapper:
    """A sandboxed copy of the real wrapper plus its trusted files."""

    def __init__(self, tmp, *, require_signed='yes', set_by='default',
                 registry_host='bbs.a-net.fyi', installed='v1.1.74'):
        self.tmp = Path(tmp)
        self.inst = self.tmp / 'inst'
        (self.inst / 'data').mkdir(parents=True, exist_ok=True)
        self.etc = self.tmp / 'etc'
        self.trust = self.etc / 'anetbbs'
        self.trust.mkdir(parents=True, exist_ok=True)
        self.conf = self.etc / 'anetbbs.install'
        self.state = self.tmp / 'var' / 'release-state'
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir = self.tmp / 'log'
        self.lock = self.tmp / 'run' / 'upgrade.lock'
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        self.bin = self.tmp / 'bin'
        self.bin.mkdir(exist_ok=True)
        shim = self.bin / 'systemd-run'
        shim.write_text(_SYSTEMD_RUN_SHIM)
        shim.chmod(0o755)
        self.write_conf(require_signed=require_signed, set_by=set_by,
                        registry_host=registry_host)
        if installed:
            self.write_state(installed)
        self.script = self._script()

    # ── trusted files ────────────────────────────────────────────────────
    def write_conf(self, *, require_signed='yes', set_by='default',
                   registry_host='bbs.a-net.fyi', extra=''):
        self.conf.write_text(
            f'INSTALL_DIR={self.inst}\nSERVICE_USER=anetbbs\n'
            f'REGISTRY_HOST={registry_host}\nREQUIRE_SIGNED_UPGRADES={require_signed}\n'
            f'REQUIRE_SIGNED_SET_BY={set_by}\n{extra}')
        self.conf.chmod(0o644)

    def write_state(self, version):
        self.state.write_text(f'INSTALLED_VERSION={version}\nINSTALLED_SHA256=\n')
        self.state.chmod(0o644)

    def trust_file(self, name, text):
        p = self.trust / name
        p.write_text(text)
        p.chmod(0o644)
        return p

    def _script(self):
        src = WRAPPER.read_text()
        subs = (
            ('CONF=/etc/anetbbs.install', f'CONF={self.conf}'),
            ('TRUST_DIR=/etc/anetbbs', f'TRUST_DIR={self.trust}'),
            ('STATE=/var/lib/anetbbs/release-state', f'STATE={self.state}'),
            ('LOCK=/run/anetbbs-upgrade.lock', f'LOCK={self.lock}'),
            ('LOG_DIR=/var/log/anetbbs', f'LOG_DIR={self.log_dir}'),
            ('ROOT_UID=0', f'ROOT_UID={os.getuid()}'),
            ('install -d -o root -g root -m 0755 "$LOG_DIR"', 'install -d -m 0755 "$LOG_DIR"'),
            ('TMP=$(mktemp -d /tmp/anetbbs-upgrade-XXXXXX)',
             f'TMP=$(mktemp -d {self.tmp}/work-XXXXXX)'),
        )
        for old, new in subs:
            n = src.count(old)
            if n != 1:
                raise AssertionError(f'{old!r} occurs {n} times in run_upgrade.sh')
            src = src.replace(old, new)
        p = self.tmp / 'run_upgrade.sh'
        p.write_text(src)
        p.chmod(0o755)
        return p

    # ── running ──────────────────────────────────────────────────────────
    def run(self, *args, detached=True, timeout=120, env=None):
        e = dict(os.environ)
        e['PATH'] = f'{self.bin}:/usr/bin:/bin'
        for k in list(e):
            if k.startswith('ANETBBS_'):
                del e[k]
        if detached:
            e['ANETBBS_UPGRADE_DETACHED'] = '1'
        if env:
            e.update(env)
        return subprocess.run([str(self.script), *args], capture_output=True,
                              text=True, timeout=timeout, env=e)

    @property
    def log_text(self):
        p = self.log_dir / 'upgrade.log'
        return p.read_text() if p.exists() else ''


def make_release(build_dir, out_dir, version, *, inner_version=None, top=None,
                 state_path=None, marker=None, update_rc=0, extra_files=None):
    """Build ANetBBS-<version>.tar.gz whose update.sh records the state file
    (like the real one does on success) and drops a marker."""
    top = top or f'ANetBBS-{version}'
    root = Path(build_dir) / top
    root.mkdir(parents=True)
    (root / 'VERSION').write_text((inner_version or version) + '\n')
    lines = ['#!/bin/bash', 'set -e']
    if marker:
        lines.append(f'printf "%s|%s|%s\\n" "$*" "$ANETBBS_EXPECTED_VERSION" '
                     f'"$ANETBBS_RELEASE_SHA256" >> {marker}')
    if state_path and update_rc == 0:
        lines.append(f'printf "INSTALLED_VERSION=%s\\n" "$(cat VERSION)" > {state_path}')
        lines.append(f'chmod 0644 {state_path}')
    lines.append(f'exit {update_rc}')
    (root / 'update.sh').write_text('\n'.join(lines) + '\n')
    for name, text in (extra_files or {}).items():
        (root / name).write_text(text)
    out = Path(out_dir) / f'ANetBBS-{version}.tar.gz'
    with tarfile.open(out, 'w:gz') as t:
        t.add(str(root), arcname=top)
    return out


def make_key(directory, name='k'):
    key = Path(directory) / name
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key),
                    '-C', 'anetbbs-release'], check=True)
    return key


def sign(key, path):
    subprocess.run(['ssh-keygen', '-Y', 'sign', '-f', str(key), '-n', 'anetbbs-release',
                    str(path)], check=True, capture_output=True)
    return Path(str(path) + '.sig')


def signer_line(key):
    return 'anetbbs-release ' + Path(str(key) + '.pub').read_text()
