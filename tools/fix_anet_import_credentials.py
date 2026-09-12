#!/usr/bin/env python3
"""
One-shot repair for a real live bug (2026-09-01): the A-Net Game Server
bulk-import tool (Admin -> Door Games -> Add games from A-Net Game
Server) used to find its host/password/BBS-tag by hard-looking-up
Game.slug == 'a-net-game-server' -- the BUNDLED seed row's slug
specifically. A sysop who had already added their own A-Net Game
Server entry under a different slug before the bundled row ever
existed, and left the later bundled row inactive rather than deleting
it, got the bundled row's own random, never-actually-used credentials
silently copied onto every imported game instead of the real, active
configuration.

This script finds every imported game (slug like 'anet-%') whose
executable_path/command_line_args/rlogin_bbs_tag don't match your real,
active A-Net Game Server config, and rewrites them to match -- each
game's own xtrn=<code> is preserved, only the server address/password/
tag get corrected.

Usage:
    cd /opt/anetbbs   # (or wherever anetbbs-rebuilt is installed)
    python -m tools.fix_anet_import_credentials              # dry-run
    python -m tools.fix_anet_import_credentials --apply       # actually fix
"""
import argparse
import sys


def _describe_match(g, creds):
    """Format one 'active game pointed at A-Net Online' audit line.

    Real gap found in a security/performance audit: this used to print
    the live A-Net Online door password in the clear (`password={p!r}`)
    for every matched game, while the 'Correct config found' block just
    below already masks the very same category of secret
    (`"*" * len(password)`) -- an inconsistency within one script. This
    tool is meant to be run and re-run freely as a dry-run audit (the
    default, no --apply needed), and its stdout is exactly the kind of
    thing that ends up piped to a file, a terminal scrollback log, or a
    screen/tmux session log -- so the live door password shouldn't ever
    land there just from listing which games reference it. Mirrors the
    masking convention already used a few lines below instead of
    inventing a new one."""
    if creds is None:
        return (f'  - id={g.id}  name={g.name!r}  slug={g.slug!r}  '
                f'-- MISSING server address or password')
    h, p, t = creds
    masked = ('*' * len(p)) if p else ''
    return (f'  - id={g.id}  name={g.name!r}  slug={g.slug!r}  '
            f'host={h!r}  password={masked!r} ({len(p)} chars)  tag={t!r}')


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true',
                        help='Actually update the rows (default is dry-run).')
    args = parser.parse_args()

    from anetbbs.web_app import create_app
    from anetbbs.models import db, Game
    from anetbbs.features.anet_game_import import (
        base_server_credentials, AnetGameImportError)

    app = create_app()
    with app.app_context():
        # Show exactly which Game row this came from, by name AND
        # slug, before touching anything -- so this can be verified
        # against what's actually visible in Admin -> Door Games
        # rather than trusted blindly.
        from anetbbs.features.anet_game_import import (
            _ANET_HOST_MARKER, _extract_credentials)
        matched = (Game.query
                  .filter_by(game_type='door_rlogin', is_active=True)
                  .filter(Game.executable_path.ilike(f'%{_ANET_HOST_MARKER}%'))
                  .filter(~Game.slug.like('anet-%'))
                  .all())
        print(f'Active door_rlogin game(s) pointed at A-Net Online '
             f'(excluding already-imported anet-* rows): {len(matched)}')
        for g in matched:
            creds = _extract_credentials(g)
            print(_describe_match(g, creds))
        print()

        try:
            host_port, password, bbs_tag = base_server_credentials()
        except AnetGameImportError as exc:
            print(f'Could not determine your real A-Net Game Server config: {exc}')
            return 1

        print('Correct config found:')
        print(f'  host_port = {host_port!r}')
        print(f'  password  = {"*" * len(password)} ({len(password)} chars)')
        print(f'  bbs_tag   = {bbs_tag!r}')
        print()

        imported = Game.query.filter(Game.slug.like('anet-%')).order_by(Game.slug).all()
        if not imported:
            print('No imported (anet-*) games found -- nothing to do.')
            return 0

        to_fix = []
        for g in imported:
            cur_args = (g.command_line_args or '').strip()
            parts = cur_args.split(None, 2)
            xtrn_part = parts[2] if len(parts) > 2 else ''
            new_args = f'@USER@ {password} {xtrn_part}'.strip()
            needs_fix = (
                (g.executable_path or '').strip() != host_port
                or cur_args != new_args
                or (g.rlogin_bbs_tag or '').strip() != bbs_tag
            )
            if needs_fix:
                to_fix.append((g, new_args))

        print(f'{len(imported)} imported game(s) total, '
             f'{len(to_fix)} need correction.')
        if not to_fix:
            print('Nothing to fix.')
            return 0

        for g, new_args in to_fix[:20]:
            print(f'  - {g.slug:<30} tag {g.rlogin_bbs_tag!r} -> {bbs_tag!r}')
        if len(to_fix) > 20:
            print(f'  ... and {len(to_fix) - 20} more')

        if not args.apply:
            print('\nDry-run only. Re-run with --apply to actually fix these rows.')
            return 0

        for g, new_args in to_fix:
            g.executable_path = host_port
            g.command_line_args = new_args
            g.rlogin_bbs_tag = bbs_tag
        db.session.commit()
        print(f'\nFixed {len(to_fix)} game(s).')
        return 0


if __name__ == '__main__':
    sys.exit(main())
