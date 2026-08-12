"""Entry point for the anetbbs-cfg terminal admin tool.

Usage:
    python -m anetbbs.cfg
    anetbbs-cfg               (once installed via setup.py console_scripts)

Uses anetbbs.cfg.db_bootstrap.create_minimal_app() rather than
anetbbs.web_app.create_app() -- this tool only needs db/models bound to
an app context for plain queries, not the full web server (blueprints,
eventlet, background services). See db_bootstrap.py's docstring for why
that matters: it's the difference between anetbbs-cfg launching in a
couple seconds vs ~10s on a Raspberry Pi 3. Both paths point at the same
database as the running BBS/web processes, so there's no separate
config to keep in sync either way.
"""
import curses
import sys

from anetbbs import __version__
from anetbbs.cfg import ui
from anetbbs.cfg.db_bootstrap import create_minimal_app
from anetbbs.cfg.sections import (
    boards, echomail, files, users, system, games, hub, file_bulletins,
    gallery, menu, petscii_menu, events, wall, login_modules, lastcallers,
    backups,
)


SECTIONS = [
    ("boards", "Boards & Message Areas", boards.run),
    ("echomail", "Echomail Networks & Areas", echomail.run),
    ("hub", "Echomail Hub (AreaFix/Poll Log/QWK Requests)", hub.run),
    ("files", "File Areas", files.run),
    ("file_bulletins", "File Bulletins", file_bulletins.run),
    ("users", "Users & Security", users.run),
    ("games", "Games", games.run),
    ("gallery", "Image Galleries", gallery.run),
    ("menu", "BBS Menus", menu.run),
    ("petscii_menu", "PETSCII Menus", petscii_menu.run),
    ("events", "Scheduled Events", events.run),
    ("wall", "Graffiti Wall", wall.run),
    ("login_modules", "Login Modules", login_modules.run),
    ("lastcallers", "Last Callers", lastcallers.run),
    ("backups", "Backups", backups.run),
    ("system", "System / Network Settings", system.run),
]


def _main_menu(stdscr):
    ui.safe_curs_set(0)
    try:
        ui.init_colors()
    except curses.error:
        pass
    items = [(key, label) for key, label, _fn in SECTIONS] + [("quit", "Quit")]
    by_key = {key: fn for key, _label, fn in SECTIONS}
    while True:
        choice = ui.run_menu(stdscr, f"Main Menu (v{__version__})", items,
                              footer="[Up/Down] Move  [Enter] Select  [Q/Esc] Quit")
        if choice is None or choice == "quit":
            return
        by_key[choice](stdscr)


def main():
    app = create_minimal_app()
    with app.app_context():
        try:
            curses.wrapper(_main_menu)
        except KeyboardInterrupt:
            pass
    print("Goodbye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
