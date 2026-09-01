#!/usr/bin/env python3
"""
One-shot repair (2026-09-01): align every one of Jerry's pre-existing,
hand-added A-Net Game Server door_rlogin entries onto the ONE confirmed-
correct password. His 14 active door_rlogin games pointed at
game.a-net-online.lol had drifted to two different password values
(13 sharing one, the "A-Net Game Server" master entry on a different,
stale one) -- Jerry confirmed directly which value is actually correct.
This does NOT touch the ~291 games created by the bulk-import tool
(slug like 'anet-%') -- those are handled separately by
tools/fix_anet_import_credentials.py, which needs this alignment done
FIRST so base_server_credentials() has an unambiguous value to read.

Usage:
    cd /opt/anetbbs   # (or wherever anetbbs-rebuilt is installed)
    python -m tools.align_anet_password 'Zkzl49@ceRP1'              # dry-run
    python -m tools.align_anet_password 'Zkzl49@ceRP1' --apply       # actually fix
"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('password', help='The one confirmed-correct password.')
    parser.add_argument('--apply', action='store_true',
                        help='Actually update the rows (default is dry-run).')
    args = parser.parse_args()

    from anetbbs.web_app import create_app
    from anetbbs.models import db, Game
    from anetbbs.features.anet_game_import import _ANET_HOST_MARKER

    app = create_app()
    with app.app_context():
        candidates = (Game.query
                     .filter_by(game_type='door_rlogin', is_active=True)
                     .filter(Game.executable_path.ilike(f'%{_ANET_HOST_MARKER}%'))
                     .filter(~Game.slug.like('anet-%'))
                     .order_by(Game.id)
                     .all())

        if not candidates:
            print('No active door_rlogin games pointed at A-Net Online found.')
            return 0

        to_fix = []
        for g in candidates:
            cur_args = (g.command_line_args or '').strip()
            parts = cur_args.split(None, 2)
            template = parts[0] if parts else '@USER@'
            xtrn_part = parts[2] if len(parts) > 2 else ''
            new_args = f'{template} {args.password} {xtrn_part}'.strip()
            if cur_args != new_args:
                to_fix.append((g, new_args))

        print(f'{len(candidates)} active A-Net Online door_rlogin game(s) total, '
             f'{len(to_fix)} need their password aligned.')
        for g, new_args in to_fix:
            print(f'  - id={g.id:<5} name={g.name!r:<28} slug={g.slug!r}')

        if not to_fix:
            print('Nothing to fix -- all entries already agree.')
            return 0

        if not args.apply:
            print('\nDry-run only. Re-run with --apply to actually fix these rows.')
            return 0

        for g, new_args in to_fix:
            g.command_line_args = new_args
        db.session.commit()
        print(f'\nAligned {len(to_fix)} game(s) to the confirmed password.')
        return 0


if __name__ == '__main__':
    sys.exit(main())
