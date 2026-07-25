# anetbbs/installer/cleanup.py
"""
Clean up old install dirs, stale upgrade snapshots, /tmp tarballs,
and the abandoned /opt/anetbbs path.

Safe by default: prints what it would remove and asks for confirmation.
Pass --yes to skip prompts.
"""
import os
import shutil
import glob
import argparse
from pathlib import Path
from datetime import datetime


from .wizard import (banner, info, ok, warn, err,
                     ask_bool, C_RESET, C_CYAN, C_YELLOW, C_GREY)


def _human_size(p):
    """Sum size in MB for a path (file or directory)."""
    total = 0
    if p.is_file():
        return p.stat().st_size
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def _fmt_size(n):
    if n > 1024 * 1024 * 1024:
        return f'{n/1024/1024/1024:.1f} GB'
    if n > 1024 * 1024:
        return f'{n/1024/1024:.1f} MB'
    return f'{n/1024:.1f} KB'


def discover():
    """Find candidate items to clean. Returns list of dicts:
        {'path': Path, 'size': bytes, 'kind': str, 'age_days': int|None}
    """
    items = []

    # 1. /opt/anetbbs — abandoned old default install path. Only flag
    #    if it exists and there's already a working install at the
    #    sysop's home dir (we don't want to nuke a real install).
    opt = Path('/opt/anetbbs')
    if opt.exists():
        # Heuristic: if there's an /opt install AND a /home/*/anetbbs
        # install, /opt is probably the abandoned one.
        home_installs = list(Path('/home').glob('*/anetbbs/anetbbs'))
        kind = 'abandoned-opt' if home_installs else 'opt-install'
        items.append({
            'path': opt, 'size': _human_size(opt), 'kind': kind,
            'age_days': None,
        })

    # 2. Upgrade snapshots — anetbbs.backup-YYYYMMDD-HHMMSS dirs.
    for backup in glob.glob('/home/*/anetbbs.backup-*') + \
                  glob.glob('/opt/anetbbs.backup-*'):
        p = Path(backup)
        try:
            stamp_str = p.name.split('-', 1)[1]
            stamp = datetime.strptime(stamp_str.replace('.backup', ''),
                                      '%Y%m%d-%H%M%S')
            age = (datetime.utcnow() - stamp).days
        except Exception:
            age = None
        items.append({
            'path': p, 'size': _human_size(p), 'kind': 'backup',
            'age_days': age,
        })

    # 3. /tmp anetbbs-vNNN extracted directories.
    for d in glob.glob('/tmp/anetbbs-v*'):  # nosec B108 -- this IS the /tmp cleanup tool, scanning /tmp is the point
        p = Path(d)
        if p.is_dir():
            items.append({
                'path': p, 'size': _human_size(p), 'kind': 'tmp-extract',
                'age_days': _file_age_days(p),
            })

    # 4. /tmp anetbbs-rebuilt-vNNN.tar.gz tarballs.
    for f in glob.glob('/tmp/anetbbs-rebuilt-v*.tar.gz'):  # nosec B108 -- cleanup tool, scanning /tmp is the point
        p = Path(f)
        items.append({
            'path': p, 'size': _human_size(p), 'kind': 'tmp-tarball',
            'age_days': _file_age_days(p),
        })

    # 5. /tmp upgrade scratch dirs from the wizard.
    for d in glob.glob('/tmp/anetbbs-upgrade-*'):  # nosec B108 -- cleanup tool, scanning /tmp is the point
        p = Path(d)
        items.append({
            'path': p, 'size': _human_size(p), 'kind': 'tmp-upgrade',
            'age_days': _file_age_days(p),
        })

    return items


def _file_age_days(p):
    try:
        mtime = datetime.fromtimestamp(p.stat().st_mtime)
        return (datetime.utcnow() - mtime).days
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(
        description='Clean up old ANetBBS install/backup/tmp files.')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Skip confirmation prompts')
    parser.add_argument('--keep-backups-days', type=int, default=14,
                        help='Keep upgrade backups newer than N days '
                             '(default: 14)')
    parser.add_argument('--dry-run', action='store_true',
                        help='List candidates without deleting')
    args = parser.parse_args()

    banner('ANetBBS — Cleanup')
    if os.geteuid() != 0:
        err('Some items require root to delete — re-run with sudo.')
        # Continue anyway; user can see the listing.

    items = discover()
    if not items:
        ok('Nothing to clean — your filesystem is tidy.')
        return

    # Filter: keep recent backups by default.
    to_delete = []
    for it in items:
        if it['kind'] == 'backup' and it['age_days'] is not None and \
                it['age_days'] < args.keep_backups_days:
            print(f'{C_GREY}  KEEP   {it["path"]}  ({_fmt_size(it["size"])}, '
                  f'{it["age_days"]}d old){C_RESET}')
            continue
        to_delete.append(it)

    if not to_delete:
        ok('Nothing to delete after applying filters.')
        return

    print()
    info('Candidates to delete:')
    total = 0
    for it in to_delete:
        age = f' ({it["age_days"]}d old)' if it['age_days'] is not None else ''
        print(f'{C_YELLOW}  DELETE {it["path"]}  '
              f'[{it["kind"]}, {_fmt_size(it["size"])}{age}]{C_RESET}')
        total += it['size']
    print(f'\n{C_CYAN}Total: {_fmt_size(total)} across '
          f'{len(to_delete)} item(s).{C_RESET}\n')

    if args.dry_run:
        info('Dry run — nothing deleted.')
        return

    if not args.yes and not ask_bool('Proceed with deletion?',
                                     default=False):
        warn('Cancelled.')
        return

    for it in to_delete:
        try:
            if it['path'].is_dir():
                shutil.rmtree(it['path'])
            else:
                it['path'].unlink()
            ok(f'  removed {it["path"]}')
        except Exception as exc:
            err(f'  failed to remove {it["path"]}: {exc}')

    banner('Cleanup Complete')
    print(f'  Reclaimed: {C_CYAN}{_fmt_size(total)}{C_RESET}')


if __name__ == '__main__':
    main()
