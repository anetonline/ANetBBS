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
        from .ansi_ui import banner, menu_item, footer, prompt as _p
        while True:
            await self.session.write(banner('Game Center'))
            for hk, lbl in (('1', 'Door Games (LORD, TradeWars, etc.)'),
                            ('2', 'Number Guessing (built-in)'),
                            ('3', 'Return to Main Menu')):
                await self.session.write(menu_item(hk, lbl) + '\r\n')
            await self.session.write(footer() + '\r\n')
            choice = (await self.session.read_line(_p('Choice: ')) or '').strip()

            if choice == '3':
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
        from anetbbs.models import db, Game

        app = Flask(__name__)
        app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
        db.init_app(app)

        with app.app_context():
            games = (Game.query
                     .filter_by(is_active=True)
                     .filter(~Game.game_type.in_(['builtin_web', 'door_dos_browser']))
                     .order_by(Game.sort_order, Game.name)
                     .all())
            # Detach into plain dicts so we can use them outside the app context
            game_list = [{
                'id': g.id, 'name': g.name, 'description': g.description,
                'category': g.category, 'game_type': g.game_type,
            } for g in games]

        if not game_list:
            await self.session.write("\r\n\r\nNo door games are configured yet.\r\n"
                                     "An admin can add games via the web at /admin/games/.\r\n")
            await self.session.read_line("\r\nPress Enter to continue...")
            return

        CYAN = '\x1b[96m'; WHT = '\x1b[97m'; YEL = '\x1b[93m'
        GRN = '\x1b[92m'; BOLD = '\x1b[1m'; RESET = '\x1b[0m'; DIM = '\x1b[37m'
        while True:
            await self.session.write(f"\r\n{BOLD}{CYAN}╔══════════════════════════════════════════════════════╗{RESET}\r\n")
            await self.session.write(f"{BOLD}{CYAN}║                    {WHT}Door Games{CYAN}                        ║{RESET}\r\n")
            await self.session.write(f"{BOLD}{CYAN}╠══════════════════════════════════════════════════════╣{RESET}\r\n")
            # Inner width = 54 chars between the two ║ pillars.
            for i, g in enumerate(game_list, 1):
                line = (f"{BOLD}{CYAN}║  {YEL}{i:2d}{DIM}. {GRN}{g['name']:<25}{DIM} "
                        f"[{g['game_type']:<15}]     {CYAN}║{RESET}\r\n")
                await self.session.write(line)
            await self.session.write(f"{BOLD}{CYAN}║   {YEL}Q{DIM}. {GRN}Return{' ' * 42}{CYAN}║{RESET}\r\n")
            await self.session.write(f"{BOLD}{CYAN}╚══════════════════════════════════════════════════════╝{RESET}\r\n\r\n")

            choice = await self.session.read_line("Pick a game (number or Q): ")
            if not choice or choice.lower() == 'q':
                return
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(game_list):
                    await self._launch(game_list[idx])
                    continue
            except ValueError:
                pass
            await self.session.write("\r\nInvalid choice.\r\n")

    async def _launch(self, game_dict):
        """Launch a door game and bridge it to this session.
        DOS games use the TCP-nullmodem path (works with vanilla DOSBox).
        Other game types use the PTY path."""
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
