"""Upgrade path for anetbbs-mail.service hardening (audit F-24:
RestrictAddressFamilies, plus an explicit CapabilityBoundingSet).

Runs the REAL update.sh retrofit code (from `_retrofit_hardening() {`
through `# (end of systemd hardening retrofit)`) in bash against a
throwaway fake /etc/systemd/system, with unit fixtures shaped like what
earlier releases actually generated, sysop customizations, and the
fresh-install unit from install.sh. Same technique as
test_update_sh_hardening_retrofit.py.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_BASH = shutil.which('bash') or '/bin/bash'
MANAGED = '20-anetbbs-hardening-v2.conf'
NEW = {'CapabilityBoundingSet': 'CAP_NET_BIND_SERVICE',
       'RestrictAddressFamilies': 'AF_INET AF_INET6 AF_UNIX'}

# What update.sh/install.sh generated for anetbbs-mail.service before this
# release (has ProtectHome=, so _retrofit_hardening never touched it).
PREVIOUS_RELEASE_UNIT = """[Unit]
Description=ANetBBS Internet Mail (SMTP receiver, RFC 5321)
After=network.target

[Service]
Type=simple
User=anetbbs
Group=anetbbs
WorkingDirectory=/opt/anetbbs
EnvironmentFile=/opt/anetbbs/.env
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=read-only
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
RestrictRealtime=yes
LockPersonality=yes
SystemCallArchitectures=native
ProtectSystem=strict
ReadWritePaths=/opt/anetbbs/data /opt/anetbbs/logs
ProtectClock=yes
ProtectHostname=yes
MemoryDenyWriteExecute=yes
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM
ExecStart=/opt/anetbbs/venv/bin/python -m anetbbs.mail.smtp_server
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""

# A hand-made unit from before any sandboxing existed.
PRE_HARDENING_UNIT = """[Unit]
Description=ANetBBS Internet Mail

[Service]
User=anetbbs
ExecStart=/opt/anetbbs/venv/bin/python -m anetbbs.mail.smtp_server
"""


def _block():
    text = (REPO_ROOT / 'update.sh').read_text()
    m = re.search(r'^_retrofit_hardening\(\) \{.*?^# \(end of systemd hardening retrofit\)$',
                  text, re.DOTALL | re.MULTILINE)
    assert m, 'retrofit region not found in update.sh'
    block = m.group(0)
    assert block.count('/etc/systemd/system') == 2, 'update.sh changed shape'
    return block


@pytest.fixture
def root(tmp_path):
    d = tmp_path / 'etc-systemd-system'
    d.mkdir()
    return d


def _run(root):
    script = ('set -euo pipefail\nok() { echo "OK: $*"; }\n'
              "INSTALL_DIR=/opt/anetbbs\nRWPATHS='/opt/anetbbs/data /opt/anetbbs/logs'\n"
              'PROTECTHOME_MODE=read-only\n'
              + _block().replace('/etc/systemd/system', str(root)) + '\n')
    r = subprocess.run([_BASH, '-c', script], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _assignments(root, key):
    """Every assignment of `key` systemd would read, in its load order
    (main unit, then drop-ins sorted by file name)."""
    unit = root / 'anetbbs-mail.service'
    files = [unit] + sorted((root / 'anetbbs-mail.service.d').glob('*.conf'))
    out = []
    for f in files:
        if f.exists():
            for line in f.read_text().splitlines():
                if re.match(rf'\s*{key}\s*=', line):
                    out.append((f.name, line.split('=', 1)[1].strip()))
    return out


def _write(root, body, name='anetbbs-mail.service'):
    (root / name).write_text(body)


def test_previous_release_unit_gets_new_directives_without_touching_main_unit(root):
    _write(root, PREVIOUS_RELEASE_UNIT)
    out = _run(root)
    assert (root / 'anetbbs-mail.service').read_text() == PREVIOUS_RELEASE_UNIT
    assert not (root / 'anetbbs-mail.service.d' / '10-security-hardening.conf').exists()
    for key, value in NEW.items():
        assert _assignments(root, key) == [(MANAGED, value)]
    assert 'added 2 hardening directive(s)' in out


def test_pre_hardening_unit_gets_full_baseline_plus_new_directives(root):
    _write(root, PRE_HARDENING_UNIT)
    _run(root)
    d = root / 'anetbbs-mail.service.d'
    assert 'ProtectSystem=strict' in (d / '10-security-hardening.conf').read_text()
    for key, value in NEW.items():
        assert _assignments(root, key) == [(MANAGED, value)]
    assert (root / 'anetbbs-mail.service').read_text() == PRE_HARDENING_UNIT


def test_sysop_customizations_are_preserved(root):
    custom_unit = PREVIOUS_RELEASE_UNIT.replace(
        'ExecStart=/opt/anetbbs/venv/bin/python -m anetbbs.mail.smtp_server',
        'Environment=SMTP_LISTEN_PORT=2525\n'
        'ExecStart=/srv/bbs/venv/bin/python -m anetbbs.mail.smtp_server')
    _write(root, custom_unit)
    d = root / 'anetbbs-mail.service.d'
    d.mkdir()
    override = ('[Service]\n# local: our resolver needs netlink\n'
                'RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK\n')
    (d / 'override.conf').write_text(override)
    _run(root)
    assert (root / 'anetbbs-mail.service').read_text() == custom_unit
    assert (d / 'override.conf').read_text() == override
    assert _assignments(root, 'RestrictAddressFamilies') == [
        ('override.conf', 'AF_INET AF_INET6 AF_UNIX AF_NETLINK')]
    assert _assignments(root, 'CapabilityBoundingSet') == [(MANAGED, 'CAP_NET_BIND_SERVICE')]


def test_sysop_empty_reset_is_respected(root):
    _write(root, PREVIOUS_RELEASE_UNIT)
    d = root / 'anetbbs-mail.service.d'
    d.mkdir()
    (d / 'local.conf').write_text('[Service]\nRestrictAddressFamilies=\nCapabilityBoundingSet=\n')
    _run(root)
    assert not (d / MANAGED).exists()


def test_rerun_is_idempotent_and_yields_to_later_sysop_setting(root):
    _write(root, PREVIOUS_RELEASE_UNIT)
    _run(root)
    d = root / 'anetbbs-mail.service.d'
    first = (d / MANAGED).read_text()
    _run(root)
    assert (d / MANAGED).read_text() == first
    # sysop later pins their own bounding set: update.sh stops managing it
    (d / 'override.conf').write_text('[Service]\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE CAP_SYS_CHROOT\n')
    _run(root)
    assert _assignments(root, 'CapabilityBoundingSet') == [
        ('override.conf', 'CAP_NET_BIND_SERVICE CAP_SYS_CHROOT')]
    assert _assignments(root, 'RestrictAddressFamilies') == [(MANAGED, NEW['RestrictAddressFamilies'])]
    # ...and once every key is the sysop's, the managed file disappears
    (d / 'override.conf').write_text('[Service]\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE\n'
                                     'RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX\n')
    _run(root)
    assert not (d / MANAGED).exists()


def test_commented_out_directive_does_not_count_as_set(root):
    _write(root, PREVIOUS_RELEASE_UNIT.replace(
        'RestrictRealtime=yes', 'RestrictRealtime=yes\n# RestrictAddressFamilies=AF_INET'))
    _run(root)
    assert _assignments(root, 'RestrictAddressFamilies') == [(MANAGED, NEW['RestrictAddressFamilies'])]


def test_missing_unit_creates_nothing(root):
    _run(root)
    assert list(root.iterdir()) == []


def _fresh_unit(script):
    text = (REPO_ROOT / script).read_text()
    m = re.search(r'cat > /etc/systemd/system/anetbbs-mail\.service << SVCEOF\n(.*?)\nSVCEOF',
                  text, re.S)
    assert m, script
    return (m.group(1).replace('$SERVICE_USER', 'anetbbs').replace('$INSTALL_DIR', '/opt/anetbbs')
            .replace('$ENV_FILE', '/opt/anetbbs/.env').replace('$VENV_DIR', '/opt/anetbbs/venv')
            .replace('$PROTECTHOME_MODE', 'read-only') + '\n')


@pytest.mark.parametrize('script', ['install.sh', 'update.sh'])
def test_fresh_install_unit_already_hardened_and_needs_no_dropin(root, script):
    unit = _fresh_unit(script)
    assert '$' not in unit
    for key, value in NEW.items():
        assert f'\n{key}={value}\n' in unit
    _write(root, unit)
    _run(root)
    assert not (root / 'anetbbs-mail.service.d').exists()
    assert (root / 'anetbbs-mail.service').read_text() == unit


def test_reference_template_matches_generated_unit_security_directives():
    def directives(text):
        return {ln.split('=', 1)[0]: ln.split('=', 1)[1] for ln in text.splitlines()
                if '=' in ln and not ln.lstrip().startswith('#')}
    template = directives((REPO_ROOT / 'deploy' / 'anetbbs-mail.service').read_text())
    for script in ('install.sh', 'update.sh'):
        generated = directives(_fresh_unit(script))
        for key, value in NEW.items():
            assert template[key] == generated[key] == value
