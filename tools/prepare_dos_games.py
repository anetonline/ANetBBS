#!/usr/bin/env python3
"""
prepare_dos_games.py — package DOS game files into dosbox_pure bundles.

Creates a ZIP containing the game files plus a dosbox.conf (no [autoexec]).
dosbox_pure auto-starts the single EXE it finds and shuts down the core when
the game exits (controlled by EJS_defaultOptions dosbox_pure_menu_time='0').

Usage examples:
    # Doom shareware
    python tools/prepare_dos_games.py \
        --source-dir "/media/jerry/EXT HDD/Doom" \
        --exe DOOM.EXE \
        --output doom \
        --name "DOOM (Shareware)" \
        --exclude SETUP.EXE DWANGO.EXE IPXSETUP.EXE SERSETUP.EXE DM.EXE

    # Duke Nukem 3D shareware
    python tools/prepare_dos_games.py \
        --source-dir "/media/jerry/EXT HDD/DUKE3D" \
        --exe DUKE3D.EXE \
        --output duke3d \
        --name "Duke Nukem 3D (Shareware)" \
        --exclude SETUP.EXE SETMAIN.EXE COMMIT.EXE DN3DHELP.EXE

Output: data/dos-games/<output>.zip
Admin:  game_type=door_dos_browser
        web_game_url=/games/dos-data/<output>.zip
"""
import argparse
import os
import sys
import zipfile
from pathlib import Path

# dosbox_pure in EmulatorJS does NOT auto-mount the game ZIP — [autoexec]
# must do the mount explicitly. On exit, launch.bat shows a "GAME OVER"
# message and loops back (exit is blocked by dosbox_pure's top-shell guard;
# the loop lets users press any key to replay or close the tab to quit).
_DOSBOX_CONF_TEMPLATE = """\
[cpu]
core=auto
cycles=max

[sblaster]
sbtype=sb16
sbbase=220
irq=7
dma=1
hdma=5

[midi]
mpu401=intelligent
{gus_section}
[autoexec]
mount c .
c:
SET BLASTER=A220 I7 D1 H5 T6
{ultrasnd_line}launch.bat
"""

_LAUNCH_BAT_TEMPLATE = """\
@echo off
:restart
{exe}
echo.
echo  ==========================================
echo       G A M E   O V E R
echo  ==========================================
echo    Press any key to play again
echo    -- or --
echo    Close this tab to exit
echo  ==========================================
echo.
pause > nul
goto restart
"""

# Appended when --gus is passed. DOSBox emulates GUS at IRQ 7 so Duke3D's
# "UltraSound IRQ must be 7 or less" check passes.
_GUS_SECTION = """\
[gus]
gus=true
gusrate=22050
gusbase=240
gusirq=7
gusdma=3
"""

_ULTRASND_LINE = "SET ULTRASND=240,3,3,7,7\n"


def _find_install_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(
        description='Package DOS game files into a dosbox_pure ZIP bundle.'
    )
    ap.add_argument('--source-dir', required=True,
                    help='Directory containing the game files')
    ap.add_argument('--exe', required=True,
                    help='Primary DOS executable (e.g. DOOM.EXE) — used for validation only')
    ap.add_argument('--output', required=True,
                    help='Output filename without extension (e.g. doom -> doom.zip)')
    ap.add_argument('--name', default='',
                    help='Human-readable game name (printed to console only)')
    ap.add_argument('--out-dir', default='',
                    help='Output directory (default: <install_root>/data/dos-games/)')
    ap.add_argument('--include', nargs='*',
                    help='Glob patterns to include (default: all files in source-dir)')
    ap.add_argument('--exclude', nargs='*', metavar='FILENAME',
                    help='Filenames to exclude, case-insensitive (e.g. SETUP.EXE DWANGO.EXE)')
    ap.add_argument('--gus', action='store_true',
                    help='Enable GUS emulation (IRQ 7) for games that need UltraSound (e.g. Duke3D)')
    ap.add_argument('--dry-run', action='store_true',
                    help='List files that would be packaged without writing anything')
    args = ap.parse_args()

    source_dir = Path(args.source_dir).expanduser().resolve()
    if not source_dir.is_dir():
        print(f'ERROR: source-dir not found: {source_dir}', file=sys.stderr)
        sys.exit(1)

    if args.out_dir:
        out_dir = Path(args.out_dir).expanduser().resolve()
    else:
        install_root = _find_install_root()
        out_dir = install_root / 'data' / 'dos-games'

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f'{args.output}.zip'

    # Collect files
    if args.include:
        files = []
        for pat in args.include:
            files.extend(source_dir.glob(pat))
        files = sorted(set(files))
    else:
        files = sorted(p for p in source_dir.rglob('*') if p.is_file())

    # Apply exclusions (case-insensitive filename match)
    if args.exclude:
        exclude_upper = {x.upper() for x in args.exclude}
        files = [f for f in files if f.name.upper() not in exclude_upper]

    if not files:
        print('ERROR: No files found after applying filters.', file=sys.stderr)
        sys.exit(1)

    exe_upper = args.exe.upper()
    exe_found = any(f.name.upper() == exe_upper for f in files)
    if not exe_found:
        print(f'WARNING: executable {args.exe!r} not found among collected files.',
              file=sys.stderr)

    exes = [f for f in files if f.suffix.upper() == '.EXE' and f.parent == source_dir]
    if len(exes) > 1:
        print(f'WARNING: {len(exes)} EXEs in bundle root — dosbox_pure will show a '
              f'selection menu instead of auto-starting. Use --exclude to remove extras:',
              file=sys.stderr)
        for e in exes:
            print(f'  {e.name}', file=sys.stderr)

    game_label = args.name or args.output

    conf_content = _DOSBOX_CONF_TEMPLATE.format(
        exe=exe_upper,
        gus_section=_GUS_SECTION if args.gus else '',
        ultrasnd_line=_ULTRASND_LINE if args.gus else '',
    )
    launch_bat = _LAUNCH_BAT_TEMPLATE.format(exe=exe_upper)

    print(f'Packaging: {game_label}')
    print(f'  Source : {source_dir}')
    print(f'  Output : {zip_path}')
    print(f'  Launch : {exe_upper}')
    print(f'  Files  : {len(files)} + dosbox.conf + launch.bat')

    if args.dry_run:
        for f in files:
            rel = f.relative_to(source_dir)
            print(f'  [dry]  {rel}')
        print('  [dry]  dosbox.conf (generated)')
        print('  [dry]  launch.bat (generated)')
        print('Dry run — no files written.')
        return

    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('dosbox.conf', conf_content)
        zf.writestr('launch.bat', launch_bat)
        for f in files:
            rel = f.relative_to(source_dir)
            zf.write(f, str(rel))
            print(f'  added  {rel}')

    size_mb = zip_path.stat().st_size / 1_048_576
    print(f'\nDone: {zip_path} ({size_mb:.1f} MB)')
    print(f'\nAdmin panel settings:')
    print(f'  Game Type   : In-Browser DOS Game (js-dos)')
    print(f'  Web Game URL: /games/dos-data/{args.output}.zip')


if __name__ == '__main__':
    main()
