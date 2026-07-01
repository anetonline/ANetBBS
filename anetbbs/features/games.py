# anetbbs/features/games.py
"""
GameManager — telnet/SSH/rlogin door-game launcher and built-in mini-games.

Door games are pulled from the same Game table the web UI uses, so adding a
game in /admin/games/ makes it instantly available to telnet users too.
"""
import os
import logging

logger = logging.getLogger(__name__)


class GameManager:
    def __init__(self, session):
        self.session = session

    async def show_menu(self):
        from .ansi_ui import banner, menu_item, footer, prompt as _p, write_menu_art, ui_width
        while True:
            _w = ui_width(self.session)
            if not await write_menu_art(self.session, 'game_center'):
                await self.session.write(banner('Game Center', _w))
                for hk, lbl in (('1', 'Door Games (LORD, TradeWars, etc.)'),
                                ('2', 'Number Guessing (built-in)'),
                                ('3', 'Return to Main Menu')):
                    await self.session.write(menu_item(hk, lbl, _w) + '\r\n')
                await self.session.write(footer(_w) + '\r\n')
            choice = (await self.session.read_line(_p('Choice: ')) or '').strip().upper()

            if choice in ('3', 'Q') or not choice:
                break
            elif choice == '1':
                await self.show_door_menu()
            elif choice == '2':
                await self.play_number_guess()

    async def show_door_menu(self):
        """List active door games from the DB and let the user launch one."""
        # Need a Flask app context to query the SQLAlchemy Game table
        from flask import Flask
        from anetbbs.config import get_config
        from anetbbs.models import db, Game, GameCategory

        app = Flask(__name__)
        app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
        db.init_app(app)

        user_access = (self.session.user or {}).get('access_level') or 10

        with app.app_context():
            games = (Game.query
                     .filter_by(is_active=True)
                     .filter(~Game.game_type.in_(['builtin_web', 'door_dos_browser']))
                     .order_by(Game.sort_order, Game.name)
                     .all())
            # Filter by access level; detach into plain dicts
            game_list = [{
                'id': g.id, 'name': g.name, 'description': g.description,
                'category': g.category or 'other', 'game_type': g.game_type,
                'min_access_level': g.min_access_level or 0,
                'web_game_module': g.web_game_module or '',
            } for g in games if (g.min_access_level or 0) <= user_access]

            # Build slug->name map from DB categories
            cat_rows = GameCategory.query.order_by(GameCategory.sort_order, GameCategory.name).all()
            cat_names = {c.slug: c.name for c in cat_rows}
            cat_order = [c.slug for c in cat_rows]

        if not game_list:
            await self.session.write("\r\n\r\nNo door games are configured yet.\r\n"
                                     "An admin can add games via the web at /admin/games/.\r\n")
            await self.session.read_line("\r\nPress Enter to continue...")
            return

        # Group games by category, preserving DB sort order
        grouped = {}
        for g in game_list:
            grouped.setdefault(g['category'], []).append(g)
        # Ordered category slugs: DB-ordered first, then any unknown slugs
        ordered_slugs = [s for s in cat_order if s in grouped]
        ordered_slugs += [s for s in grouped if s not in ordered_slugs]

        # Build a flat numbered list (for input parsing) alongside grouped display
        flat_list = [g for slug in ordered_slugs for g in grouped[slug]]

        from .ansi_ui import write_menu_art, ui_width
        CYAN = '\x1b[96m'; WHT = '\x1b[97m'; YEL = '\x1b[93m'
        GRN = '\x1b[92m'; BOLD = '\x1b[1m'; RESET = '\x1b[0m'; DIM = '\x1b[37m'
        while True:
            _iw = ui_width(self.session)
            _name_w  = max(25, _iw - 28)       # game name column
            _type_w  = 15
            # Row: (2){num:2}(2).(1) (1){name:_name_w}(1)[{type:_type_w}](1){pad}
            # = 9 + _name_w + _type_w + _pad → _pad = _iw - 7 - _name_w - _type_w
            _pad     = max(0, _iw - 7 - _name_w - _type_w)
            if not await write_menu_art(self.session, 'door_games'):
                hbar = '═' * _iw
                title = "Door Games".center(_iw)
                await self.session.write(f"\r\n{BOLD}{CYAN}{hbar}{RESET}\r\n")
                await self.session.write(f"{WHT}{title}{RESET}\r\n")
                await self.session.write(f"{BOLD}{CYAN}{hbar}{RESET}\r\n")
                num = 1
                for slug in ordered_slugs:
                    cat_name = cat_names.get(slug, slug.capitalize())
                    label = f"  ─── {cat_name} "
                    fill = '─' * max(0, _iw - len(label))
                    await self.session.write(f"{BOLD}{CYAN}{label}{fill}{RESET}\r\n")
                    for g in grouped[slug]:
                        name_col = g['name'][:_name_w]
                        line = (f"  {YEL}{num:2d}{DIM}. {GRN}{name_col:<{_name_w}}{DIM} "
                                f"[{g['game_type']:<{_type_w}}]{' ' * _pad}{RESET}\r\n")
                        await self.session.write(line)
                        num += 1
                await self.session.write(f"  {YEL}Q{DIM}. {GRN}Return{RESET}\r\n")
                await self.session.write(f"{BOLD}{CYAN}{hbar}{RESET}\r\n\r\n")

            choice = await self.session.read_line("Pick a game (number or Q): ")
            if not choice or choice.lower() == 'q':
                return
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(flat_list):
                    await self._launch(flat_list[idx])
                    continue
            except ValueError:
                pass
            await self.session.write("\r\nInvalid choice.\r\n")

    async def _launch(self, game_dict):
        """Launch a door game and bridge it to this session.
        DOS games use the TCP-nullmodem path (works with vanilla DOSBox).
        builtin_python games are called directly (no subprocess).
        Other game types use the PTY path."""

        # builtin_python — pure Python game, no subprocess or door runner needed.
        if game_dict.get('game_type') == 'builtin_python':
            module_path = game_dict.get('web_game_module', '')
            if not module_path:
                await self.session.write("\r\nGame has no module configured.\r\n")
                return
            try:
                import importlib
                mod_name, func_name = (module_path.rsplit(':', 1)
                                       if ':' in module_path
                                       else (module_path, 'launch'))
                mod = importlib.import_module(mod_name)
                fn  = getattr(mod, func_name)
                username = (self.session.user or {}).get('username', 'player')
                await fn(self.session, username)
            except Exception as exc:
                logger.exception('builtin_python game error: %s', exc)
                await self.session.write(f"\r\nError launching game: {exc}\r\n")
                await self.session.read_line("\r\nPress Enter...")
            return

        from flask import Flask
        from anetbbs.config import get_config
        from anetbbs.models import db, Game
        from ..games.door_runner import play_door_game_telnet, play_rlogin_telnet

        app = Flask(__name__)
        app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
        db.init_app(app)

        with app.app_context():
            g = Game.query.get(game_dict['id'])
            if g is None:
                await self.session.write("\r\nGame disappeared from the catalog.\r\n")
                return

        # door_rlogin is a remote game-server type — no local subprocess,
        # just a TCP rlogin handshake bridged to the user's terminal.
        # Everything else goes through the unified launch path.
        if game_dict.get('game_type') == 'door_rlogin':
            launcher = play_rlogin_telnet
        else:
            launcher = play_door_game_telnet

        try:
            await launcher(
                game=g,
                user=self.session.user,
                session=self.session,
            )
        except Exception as exc:
            logger.exception('Door game error: %s', exc)
            await self.session.write(f"\r\nError launching game: {exc}\r\n")
            await self.session.read_line("\r\nPress Enter...")

    async def play_number_guess(self):
        """Built-in number guessing — no door, pure session I/O."""
        import random
        number = random.randint(1, 100)
        tries = 0

        await self.session.write("\r\nI'm thinking of a number between 1 and 100\r\n")

        while True:
            guess = await self.session.read_line("Your guess: ")
            try:
                guess = int(guess)
                tries += 1

                if guess < number:
                    await self.session.write("Too low!\r\n")
                elif guess > number:
                    await self.session.write("Too high!\r\n")
                else:
                    await self.session.write(f"\r\nCongratulations! You got it in {tries} tries!\r\n")
                    break
            except ValueError:
                await self.session.write("Please enter a valid number\r\n")

        await self.session.read_line("\r\nPress Enter to continue...")
