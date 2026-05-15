# anetbbs/installer/symlinks.py
"""
Install /usr/local/bin/ shortcuts for the anetbbs-* console scripts so
the sysop doesn't have to type the venv path. Idempotent.

Used by both the install and upgrade wizards.
"""
import os
import sys
from pathlib import Path


WRAPPERS = ('anetbbs', 'anetbbs-web', 'anetbbs-install',
            'anetbbs-upgrade', 'anetbbs-symlinks', 'anetbbs-cleanup')


def main():
    """Console entry point: `anetbbs-symlinks [install_dir]`."""
    install_dir = sys.argv[1] if len(sys.argv) > 1 else '/home/stingray/anetbbs'
    print(f'Installing /usr/local/bin/ symlinks pointing at {install_dir}/venv/bin/...')
    for name, status in ensure_symlinks(install_dir):
        marker = '✗' if status.startswith('failed') else '✓'
        print(f'  {marker} {name}: {status}')


def ensure_symlinks(install_dir, target_dir='/usr/local/bin'):
    """For each console script in the venv's bin, drop a symlink in
    `target_dir`. Returns a list of (name, status) tuples.

    status is one of: 'created', 'updated', 'exists', 'failed:<msg>'.
    """
    install_dir = Path(install_dir)
    venv_bin = install_dir / 'venv' / 'bin'
    target = Path(target_dir)
    out = []
    if not target.is_dir():
        return [(n, f'failed:{target} not a dir') for n in WRAPPERS]

    for name in WRAPPERS:
        src = venv_bin / name
        if not src.exists():
            out.append((name, 'failed:not in venv'))
            continue
        dst = target / name
        try:
            if dst.is_symlink() or dst.exists():
                # Re-point if it's a stale symlink to a different venv;
                # leave alone if it already points where we want.
                if dst.is_symlink() and os.readlink(str(dst)) == str(src):
                    out.append((name, 'exists'))
                    continue
                dst.unlink()
                dst.symlink_to(src)
                out.append((name, 'updated'))
            else:
                dst.symlink_to(src)
                out.append((name, 'created'))
        except PermissionError:
            out.append((name, 'failed:need sudo'))
        except Exception as exc:
            out.append((name, f'failed:{exc}'))
    return out
