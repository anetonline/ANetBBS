# anetbbs/installer/upgrade.py
"""
Interactive ANetBBS upgrade wizard.

Run as `anetbbs-upgrade /path/to/extracted/anetbbs-vXX`. It will:
  1. Verify the source looks like a valid ANetBBS extract
  2. Strongly suggest the sysop take their own backup, with a pause
  3. Snapshot data/, .env, and the SQLite DB to a timestamped backup dir
  4. Stop running services
  5. Rsync the new code in (preserving .env / data / venv / logs)
  6. pip install -e . to pick up new modules
  7. Run db.create_all + lightweight schema migrations via create_app()
  8. Restart services
  9. Probe /healthz to verify the web app is up
 10. Pause for sysop to confirm — offer rollback on failure
"""
import os
import sys
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .wizard import (banner, info, ok, warn, err, step,
                     ask, ask_bool,
                     C_RESET, C_CYAN, C_GREEN, C_YELLOW, C_BOLD)


def _run(cmd, **kw):
    """subprocess.run wrapper that prints the command first."""
    info('  $ ' + (cmd if isinstance(cmd, str) else ' '.join(cmd)))
    return subprocess.run(cmd, **kw)


def main():
    banner('ANetBBS — Upgrade Wizard')

    # 1. Source — accept either a directory OR a .tar.gz of a new release.
    src_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if not src_arg:
        src_arg = ask('Path to new version (.tar.gz or extracted dir)',
                      validator=lambda s: (Path(s).exists(),
                                           f"{s} does not exist."))
    src_path = Path(src_arg).resolve()

    if src_path.is_file() and (str(src_path).endswith('.tar.gz')
                                or str(src_path).endswith('.tgz')):
        info('Tarball detected — extracting to a temp dir...')
        import tempfile, tarfile
        tmp = Path(tempfile.mkdtemp(prefix='anetbbs-upgrade-'))
        try:
            with tarfile.open(src_path, 'r:gz') as tf:
                # PEP 706 safe-extraction filter (Python 3.12+) rejects
                # tar entries that would escape `tmp` via `../` paths,
                # absolute paths, or symlink tricks. hasattr guard for
                # older Python (the `filter` kwarg itself doesn't exist
                # pre-3.12), matching the compatibility pattern Python's
                # own tarfile docs recommend.
                if hasattr(tarfile, 'data_filter'):
                    tf.extractall(tmp, filter='data')
                else:
                    # Residual risk on Python <3.12 only (no filter kwarg
                    # available): this is a manually-invoked sysop CLI
                    # wizard pointed at a file path the sysop chose, not
                    # a network-triggered extraction.
                    tf.extractall(tmp)  # nosec B202
        except Exception as exc:
            err(f'Failed to extract: {exc}')
            sys.exit(1)
        # Find the extracted root — should be a single subdirectory
        # named anetbbs-rebuilt/ inside.
        children = [c for c in tmp.iterdir() if c.is_dir()]
        if len(children) == 1 and (children[0] / 'anetbbs').is_dir():
            src = children[0]
        elif (tmp / 'anetbbs').is_dir():
            src = tmp
        else:
            err(f'Tarball layout unexpected — no anetbbs/ subdir found '
                f'inside {tmp}.')
            sys.exit(1)
        ok(f'Extracted to {src}')
    elif src_path.is_dir() and (src_path / 'anetbbs').is_dir():
        src = src_path
    else:
        err(f'{src_path} is not an .tar.gz nor an extracted ANetBBS dir.')
        sys.exit(1)

    # 2. Install dir
    install_dir = ask('Install directory',
                      default='/opt/anetbbs',
                      validator=lambda s: (Path(s).is_dir(),
                                           'Not a directory.'))
    install_dir = Path(install_dir).resolve()
    if not (install_dir / 'venv' / 'bin' / 'python').exists():
        err(f'No venv found at {install_dir}/venv. '
            f'Run `anetbbs-install` first.')
        sys.exit(1)

    # Detect new version (if VERSION file present in source)
    new_ver = '?'
    vf = src / 'VERSION'
    if vf.exists():
        try:
            new_ver = vf.read_text().strip()
        except Exception:
            pass
    info(f'\nUpgrading {install_dir}  ←  {src} (version {new_ver})')

    # 3. Backup advisory + own-backup pause
    banner('Backup Advisory')
    print(f'{C_YELLOW}{C_BOLD}IMPORTANT:{C_RESET} The upgrade wizard will snapshot')
    print('  - data/  (uploads, avatars, etc.)')
    print('  - .env')
    print('  - the SQLite DB')
    print('to a timestamped backup directory beside your install.\n')
    print(f'{C_YELLOW}For maximum safety you should also take your own backup{C_RESET}')
    print(f'{C_YELLOW}now (cp -a, rsync, btrfs snapshot, whatever you trust).{C_RESET}\n')
    if not ask_bool(f'{C_BOLD}Have you taken a backup yourself?{C_RESET} '
                    'Type Y to continue', default=False):
        warn('Aborted by sysop — go take a backup, then re-run.')
        sys.exit(0)

    # 3b. Disk-space preflight. Real gap found in a full install/update
    # re-verify audit: update.sh already has this check (added after a
    # real incident where a sysop's disk hit zero mid-update and
    # corrupted the install beyond recovery) but this second, separate
    # upgrade tool never got the same fix. Same 500MB floor.
    MIN_FREE_MB = 500
    try:
        free_mb = shutil.disk_usage(install_dir).free // (1024 * 1024)
    except Exception:
        free_mb = None
    if free_mb is not None and free_mb < MIN_FREE_MB:
        err(f'Only {free_mb}MB free on {install_dir} — need at least '
            f'{MIN_FREE_MB}MB. Free up space first (old backups under '
            f'data/backups/, old logs/uploads), then re-run. Refusing to '
            f'proceed: running out of space mid-upgrade can corrupt the '
            f'install past recovery.')
        sys.exit(1)
    if free_mb is not None:
        ok(f'{free_mb}MB free on {install_dir}')

    # 4. Snapshot
    step('Snapshotting current install')
    stamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
    backup_root = install_dir.parent / f'{install_dir.name}.backup-{stamp}'
    backup_root.mkdir(parents=True, exist_ok=False)
    info(f'Backup dir: {backup_root}')
    for piece in ('data', '.env', 'logs'):
        srcpath = install_dir / piece
        if not srcpath.exists():
            continue
        dest = backup_root / piece
        try:
            if srcpath.is_dir():
                shutil.copytree(srcpath, dest, symlinks=True)
            else:
                shutil.copy2(srcpath, dest)
            ok(f'  saved {piece}')
        except Exception as exc:
            warn(f'  could not snapshot {piece}: {exc}')

    # 5. Stop services
    # Real gap found in a full install/update re-verify audit: this
    # listed the legacy pre-merge unit names (anetbbs-telnet/anetbbs-ssh/
    # anetbbs-rlogin), which update.sh itself actively removes as cruft
    # -- telnet/SSH/rlogin/FTP/PETSCII40/80/LMTP have all been one
    # combined `anetbbs.service` for a while now (Ubuntu's systemd
    # EnvironmentFile directive winning over per-unit Environment=
    # overrides meant the old split units couldn't reliably share .env
    # and fought each other for ports). Since `systemctl stop`/`start`
    # on a nonexistent unit just no-ops (or is skipped by the existence
    # check at step 9 below), this wizard silently never touched the
    # real running process at all -- it kept serving pre-upgrade code
    # for every terminal protocol and FTP indefinitely after a
    # supposedly-successful upgrade.
    step('Stopping services')
    for unit in ('anetbbs-web', 'anetbbs', 'anetbbs-mrc-bridge'):
        _run(['systemctl', 'stop', unit], capture_output=True)
    _run(['pkill', '-9', '-f', 'gunicorn'], capture_output=True)
    ok('Services stopped')

    # 6. Rsync the new code in
    # Real gap found in a full install/update re-verify audit: this
    # exclude list was missing /doors/, /gallery-config.json, and every
    # mrc/bridge/* carve-out update.sh's own rsync call has — those were
    # added there specifically after a real v195-era incident where a
    # --delete deploy wiped the doors/ tree (DSR, BotWars, RDQ3, the
    # 6500-GIF library) from a production install. That fix was never
    # mirrored to this second, separate upgrade tool. Also anchored the
    # patterns to the tree root (leading /) to match update.sh exactly,
    # since an unanchored 'data/' can match a nested directory of that
    # name anywhere in the source tree, not just the top-level one.
    step('Applying update')
    _run([
        'rsync', '-a',
        '--exclude=/.env', '--exclude=/data/', '--exclude=/venv/',
        '--exclude=*.db', '--exclude=/logs/',
        '--exclude=/doors/', '--exclude=/gallery-config.json',
        '--exclude=/mrc/bridge/config.json',
        '--exclude=/mrc/bridge/config/',
        '--exclude=/mrc/bridge/data/',
        '--exclude=/mrc/bridge/data-*/',
        '--exclude=/mrc/bridge/logs/',
        '--exclude=/mrc/bridge/*.db',
        '--exclude=/mrc/bridge/*.db-shm',
        '--exclude=/mrc/bridge/*.db-wal',
        f'{src}/', f'{install_dir}/',
    ], check=True)
    if (src / 'doors').is_dir():
        info('  doors/ left untouched (excluded to protect sysop-customized '
             'installs). To pick up bundled door updates from this release, '
             'use update.sh instead — it does an arch-aware per-file sync; '
             'this wizard intentionally does not.')
    # Copy pre-bundled game ZIPs — static assets, not user data.
    dos_src = src / 'data' / 'dos-games'
    if dos_src.is_dir():
        dos_dst = install_dir / 'data' / 'dos-games'
        dos_dst.mkdir(parents=True, exist_ok=True)
        _run(['rsync', '-a', f'{dos_src}/', f'{dos_dst}/'], check=False)
        ok('DOS game bundles updated')
    # Restore ownership in case rsync ran as a different user. If
    # SUDO_USER isn't set, fall back to whatever already owns the
    # install dir rather than guessing a specific username.
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user:
        _run(['chown', '-R', f'{sudo_user}:{sudo_user}', str(install_dir)],
             capture_output=True)
    else:
        owner_uid = install_dir.stat().st_uid
        owner_gid = install_dir.stat().st_gid
        _run(['chown', '-R', f'{owner_uid}:{owner_gid}', str(install_dir)],
             capture_output=True)
    ok('Files updated')

    # 7. pip install
    step('Updating Python deps')
    pip = install_dir / 'venv' / 'bin' / 'pip'
    _run([str(pip), 'install', '-e', str(install_dir), '--quiet'],
         check=True)
    ok('Deps refreshed')

    # 8. Schema migration.
    # Three things we have to set up so this Python subprocess actually
    # has what it needs:
    #   a) Load .env (so DATABASE_URL, SECRET_KEY etc. carry over)
    #   b) Auto-heal SECRET_KEY if it's missing or one of the known
    #      insecure defaults — v146+ refuses to boot in production with
    #      the dev key, and a stale install may not have a real one.
    #   c) Set ANETBBS_SCHEMA_MIGRATE_ONLY=1 as a belt-and-suspenders
    #      bypass so the guard doesn't trip even if the heal missed.
    step('Running DB schema migration')

    env_file = install_dir / '.env'
    env_lines = []
    env_map = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or '=' not in stripped:
                env_lines.append(line)
                continue
            k, v = stripped.split('=', 1)
            env_map[k.strip()] = v.strip()
            env_lines.append(line)

    BAD_KEYS = {'', 'changeme', 'your-secret-key-here',
                'dev-secret-key-change-in-production'}
    cur_key = env_map.get('SECRET_KEY', '')
    if cur_key in BAD_KEYS:
        import secrets as _secrets
        new_key = _secrets.token_urlsafe(48)
        info('  No real SECRET_KEY in .env — generating one')
        if 'SECRET_KEY' in env_map:
            env_lines = [
                (f'SECRET_KEY={new_key}'
                 if l.strip().startswith('SECRET_KEY=') else l)
                for l in env_lines]
        else:
            env_lines.append(f'SECRET_KEY={new_key}')
        env_file.write_text('\n'.join(env_lines) + '\n')
        os.chmod(env_file, 0o600)
        env_map['SECRET_KEY'] = new_key
        ok(f'  SECRET_KEY written to {env_file} (mode 0600)')

    # Build the child env: parent env + .env contents + safety flags
    child_env = os.environ.copy()
    child_env.update(env_map)
    child_env['FLASK_ENV'] = 'production'
    child_env['ANETBBS_SCHEMA_MIGRATE_ONLY'] = '1'

    pyexe = install_dir / 'venv' / 'bin' / 'python'
    r = subprocess.run(
        [str(pyexe), '-c',
         "from anetbbs.web_app import create_app; create_app('production')"],
        capture_output=True, text=True, env=child_env, cwd=str(install_dir))
    if r.returncode != 0:
        err('Schema migration failed:')
        print(r.stderr)
        if ask_bool('Roll back to the snapshot?', default=True):
            _rollback(install_dir, backup_root)
            sys.exit(2)
        else:
            sys.exit(2)
    ok('Schema up to date')

    # 9. Start services + ensure they're enabled so they auto-start
    # at boot. Skip enable for ones that don't exist (rlogin / mrc-bridge
    # are optional).
    step('Starting + enabling services')
    for unit in ('anetbbs-web', 'anetbbs', 'anetbbs-mrc-bridge',
                 'anetbbs-finger', 'anetbbs-binkp'):
        # Check unit exists before enable/start
        r = subprocess.run(['systemctl', 'cat', unit],
                           capture_output=True, text=True)
        if r.returncode != 0:
            continue
        _run(['systemctl', 'enable', unit], capture_output=True)
        _run(['systemctl', 'start', unit], capture_output=True)
    ok('Services started + enabled (will auto-start on reboot)')

    # 10. Health probe
    step('Health check')
    import time, urllib.request
    healthy = False
    web_port = _read_env_value(install_dir / '.env', 'WEB_PORT', '5000')
    for _ in range(10):
        try:
            with urllib.request.urlopen(  # nosec B310 -- fixed localhost health-check target
                    f'http://127.0.0.1:{web_port}/healthz', timeout=2) as r:
                if r.status == 200:
                    healthy = True
                    break
        except Exception:
            pass
        time.sleep(1)
    if healthy:
        ok('/healthz returned 200 — web is up')
    else:
        err('Health check failed — web app may not be running.')
        if ask_bool('Roll back?', default=True):
            _rollback(install_dir, backup_root)
            sys.exit(3)

    # 11. Refresh /usr/local/bin shortcuts
    step('Refreshing short command symlinks')
    try:
        from .symlinks import ensure_symlinks
        results = ensure_symlinks(install_dir)
        for name, status in results:
            if status.startswith('failed'):
                warn(f'  {name}: {status}')
            else:
                ok(f'  {name}: {status}')
    except Exception as exc:
        warn(f'Could not install symlinks: {exc}')

    # 12. Done
    banner('Upgrade Complete')
    print(f'  Install dir:   {C_CYAN}{install_dir}{C_RESET}')
    print(f'  Backup at:     {C_CYAN}{backup_root}{C_RESET}')
    print(f'  Version:       {C_CYAN}{new_ver}{C_RESET}')
    print()
    print(f'{C_GREEN}If anything looks off, roll back manually with:{C_RESET}')
    print('  sudo systemctl stop anetbbs-web anetbbs')
    print(f'  sudo cp -a {backup_root}/.env {install_dir}/.env')
    print(f'  sudo rsync -a --delete {backup_root}/data/ '
          f'{install_dir}/data/')
    print('  sudo systemctl start anetbbs-web anetbbs')
    print()


def _read_env_value(path, key, default=''):
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith(f'{key}='):
                return line.split('=', 1)[1]
    except Exception:
        pass
    return default


def _rollback(install_dir, backup_root):
    warn('Rolling back...')
    try:
        # Stop first
        for unit in ('anetbbs-web', 'anetbbs', 'anetbbs-mrc-bridge'):
            subprocess.run(['systemctl', 'stop', unit], capture_output=True)
        # Restore .env
        envb = backup_root / '.env'
        if envb.exists():
            shutil.copy2(envb, install_dir / '.env')
        # Restore data/
        datab = backup_root / 'data'
        if datab.exists():
            subprocess.run(['rsync', '-a', '--delete', f'{datab}/',
                            f'{install_dir}/data/'])
        # Restart
        for unit in ('anetbbs-web', 'anetbbs'):
            subprocess.run(['systemctl', 'start', unit], capture_output=True)
        ok('Rolled back to pre-upgrade state.')
    except Exception as exc:
        err(f'Rollback failed: {exc}')


if __name__ == '__main__':
    main()
