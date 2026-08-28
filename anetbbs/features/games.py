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
            # NodeSpy/anetbbs-monitor heartbeat -- real gap found live
            # (2026-08-28): _heartbeat_node only ever fired from
            # menu_engine.py's generic menu dispatch and _launch() below,
            # so a sysop watching "Doing" saw it frozen at "entered BBS"
            # for anyone browsing the Game Center itself rather than
            # actually playing a door -- GameManager's screens are their
            # own bespoke read_line loops, never routed through
            # menu_engine.py at all.
            if hasattr(self.session, '_heartbeat_node'):
                try:
                    self.session._heartbeat_node(page='games', action='menu: Game Center')
                except Exception:
                    pass
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
        from .db_scope import transient_app_context

        app = Flask(__name__)
        app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
        db.init_app(app)

        user_access = (self.session.user or {}).get('access_level') or 10

        with transient_app_context(app):
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

            # Build slug->name/as_submenu maps from DB categories. Order
            # no longer matters here -- category headers now follow the
            # games' own flat sort_order (see the comment further down),
            # not GameCategory.sort_order.
            cat_rows = GameCategory.query.all()
            cat_names = {c.slug: c.name for c in cat_rows}
            cat_as_submenu = {c.slug: bool(c.as_submenu) for c in cat_rows}

        if not game_list:
            await self.session.write("\r\n\r\nNo door games are configured yet.\r\n"
                                     "An admin can add games via the web at /admin/games/.\r\n")
            await self.session.read_line("\r\nPress Enter to continue...")
            return

        # Real gap Jerry hit live: sort_order used to only control a
        # game's position WITHIN its own category -- categories were
        # always grouped and rendered in their own GameCategory.sort_order,
        # regardless of the individual games' own sort_order values. A
        # sysop numbering every door 1-20 as one flat list (Jerry's own
        # words: "I have all the doors sort orders numbered 1-20")
        # reasonably expects that number to control absolute position in
        # the whole menu -- adding a new door with sort_order 21 landed
        # in the middle instead of at the end, because its CATEGORY
        # happened to sort earlier than categories containing higher-
        # numbered games. Fixed: game_list is already globally ordered by
        # Game.sort_order/name (the query above) -- walk it in that exact
        # order below and insert a category header/boundary wherever the
        # category actually changes, rather than pre-grouping by category
        # first. Category headers are now a side effect of the flat walk,
        # not the primary sort key.
        # grouped is still needed for as_submenu categories -- a section
        # needs ALL of that category's games (wherever they fall in the
        # flat order), not just the contiguous run at first sight.
        grouped = {}
        for g in game_list:
            grouped.setdefault(g['category'], []).append(g)

        from .ansi_ui import write_menu_art, ui_width
        # Bold+base (1;3X), not bare aixterm 90-97 -- MagiTerm/NetRunner/
        # PuTTY don't recognize 90-97 and silently drop the color.
        CYAN = '\x1b[1;36m'; WHT = '\x1b[1;37m'; YEL = '\x1b[1;33m'
        GRN = '\x1b[1;32m'; BOLD = '\x1b[1m'; RESET = '\x1b[0m'; DIM = '\x1b[37m'
        while True:
            _iw = ui_width(self.session)
            if hasattr(self.session, '_heartbeat_node'):
                try:
                    self.session._heartbeat_node(page='games:doors', action='menu: Door Games')
                except Exception:
                    pass
            # Two columns per category row -- one game per line ran off the
            # bottom of a real 24-row terminal once the door count passed
            # ~10 (Jerry: "once you get so many, you cant see them all...
            # I had to use the syncterm scroll back to see the top games").
            # Column width leaves room for a right-hand gutter between the
            # two columns; the [game_type] tag from the old single-column
            # layout is dropped here (implementation detail, not something
            # a player picking a game needs) to make room.
            _cols    = 2
            _col_w   = max(20, (_iw // _cols) - 2)
            # Numbered entries in on-screen order -- either a game dict
            # (launch it directly) or a ('submenu', slug, cat_name) tuple
            # (a category with as_submenu=True: drill into a second-level
            # menu of just that category's games instead of expanding
            # inline). Rebuilt every redraw since it's cheap and keeps
            # numbering in sync with what's actually on screen.
            numbered = []
            if not await write_menu_art(self.session, 'door_games'):
                hbar = '═' * _iw
                title = "Door Games".center(_iw)
                await self.session.write(f"\r\n{BOLD}{CYAN}{hbar}{RESET}\r\n")
                await self.session.write(f"{WHT}{title}{RESET}\r\n")
                await self.session.write(f"{BOLD}{CYAN}{hbar}{RESET}\r\n")
                num = 1
                prev_slug = None
                cells = []
                rendered_submenu_slugs = set()

                async def _flush_cells():
                    for i in range(0, len(cells), _cols):
                        row = '  '.join(cells[i:i + _cols])
                        await self.session.write(f"  {row}\r\n")
                    cells.clear()

                for g in game_list:
                    slug = g['category']
                    if cat_as_submenu.get(slug):
                        if slug in rendered_submenu_slugs:
                            continue  # already rendered as a section line
                        await _flush_cells()
                        cat_name = cat_names.get(slug, slug.capitalize())
                        cat_games = grouped[slug]
                        count = len(cat_games)
                        plural = 'door' if count == 1 else 'doors'
                        await self.session.write(
                            f"  {YEL}{num:2d}{DIM}. {GRN}{BOLD}{cat_name}{RESET}"
                            f"{DIM} → ({count} {plural}){RESET}\r\n")
                        numbered.append(('submenu', slug, cat_name))
                        num += 1
                        rendered_submenu_slugs.add(slug)
                        prev_slug = slug
                        continue
                    if slug != prev_slug:
                        await _flush_cells()
                        cat_name = cat_names.get(slug, slug.capitalize())
                        label = f"  ─── {cat_name} "
                        fill = '─' * max(0, _iw - len(label))
                        await self.session.write(f"{BOLD}{CYAN}{label}{fill}{RESET}\r\n")
                        prev_slug = slug
                    name_col = g['name'][:_col_w - 5]
                    cells.append(
                        f"{YEL}{num:2d}{DIM}. {GRN}{name_col:<{_col_w - 5}}{RESET}")
                    numbered.append(g)
                    num += 1
                await _flush_cells()
                await self.session.write(f"  {YEL}Q{DIM}. {GRN}Return{RESET}\r\n")
                await self.session.write(f"{BOLD}{CYAN}{hbar}{RESET}\r\n\r\n")
            else:
                # write_menu_art drew a custom screen -- numbered still
                # needs to be built (in the same order the loop above
                # would have numbered things) so input parsing matches
                # whatever the custom art actually shows.
                num = 1
                rendered_submenu_slugs = set()
                for g in game_list:
                    slug = g['category']
                    if cat_as_submenu.get(slug):
                        if slug in rendered_submenu_slugs:
                            continue
                        numbered.append(('submenu', slug, cat_names.get(slug, slug.capitalize())))
                        num += 1
                        rendered_submenu_slugs.add(slug)
                        continue
                    numbered.append(g)
                    num += 1

            choice = await self.session.read_line("Pick a game (number or Q): ")
            if not choice or choice.lower() == 'q':
                return
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(numbered):
                    entry = numbered[idx]
                    if isinstance(entry, tuple) and entry[0] == 'submenu':
                        _, slug, cat_name = entry
                        await self._show_category_submenu(slug, cat_name, grouped[slug])
                    else:
                        await self._launch(entry)
                    continue
            except ValueError:
                pass
            await self.session.write("\r\nInvalid choice.\r\n")

    async def _show_category_submenu(self, slug, cat_name, games):
        """Second-level door menu for a single as_submenu=True category --
        same flat numbered/2-column rendering as the top-level menu's
        inline category blocks, just scoped to one category and with
        'B'/'Q' returning to the parent door menu instead of the main
        menu. Deliberately one level deep only -- a submenu category's
        own games are always shown flat here, no further nesting.

        Custom art: checked under the slot name `door_games_<category-
        slug>` (e.g. a category with slug "synchronet-doors" looks for
        data/text/menus/door_games_synchronet-doors.ans), mirroring the
        top-level door menu's own `door_games` slot -- falls back to
        this generated layout if no matching file exists. See
        docs/04-ansi-screens.md's slot-names reference and
        docs/14-door-games.md's "Categories and submenu sections"."""
        from .ansi_ui import write_menu_art, ui_width
        CYAN = '\x1b[1;36m'; WHT = '\x1b[1;37m'; YEL = '\x1b[1;33m'
        GRN = '\x1b[1;32m'; BOLD = '\x1b[1m'; RESET = '\x1b[0m'; DIM = '\x1b[37m'
        art_slot = f'door_games_{slug}'
        while True:
            _iw = ui_width(self.session)
            if hasattr(self.session, '_heartbeat_node'):
                try:
                    self.session._heartbeat_node(page='games:doors', action=f'menu: {cat_name}')
                except Exception:
                    pass
            _cols = 2
            _col_w = max(20, (_iw // _cols) - 2)
            if not await write_menu_art(self.session, art_slot):
                hbar = '═' * _iw
                title = cat_name.center(_iw)
                await self.session.write(f"\r\n{BOLD}{CYAN}{hbar}{RESET}\r\n")
                await self.session.write(f"{WHT}{title}{RESET}\r\n")
                await self.session.write(f"{BOLD}{CYAN}{hbar}{RESET}\r\n")
                cells = []
                for i, g in enumerate(games, start=1):
                    name_col = g['name'][:_col_w - 5]
                    cells.append(f"{YEL}{i:2d}{DIM}. {GRN}{name_col:<{_col_w - 5}}{RESET}")
                for i in range(0, len(cells), _cols):
                    row = '  '.join(cells[i:i + _cols])
                    await self.session.write(f"  {row}\r\n")
                await self.session.write(f"  {YEL}B{DIM}. {GRN}Back{RESET}\r\n")
                await self.session.write(f"{BOLD}{CYAN}{hbar}{RESET}\r\n\r\n")

            choice = await self.session.read_line("Pick a game (number or B): ")
            if not choice or choice.lower() in ('b', 'q'):
                return
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(games):
                    await self._launch(games[idx])
                    continue
            except ValueError:
                pass
            await self.session.write("\r\nInvalid choice.\r\n")

    async def _launch(self, game_dict):
        """Launch a door game and bridge it to this session.
        DOS games use the TCP-nullmodem path (works with vanilla DOSBox).
        builtin_python games are called directly (no subprocess).
        Other game types use the PTY path."""

        # "Who's on" detail: menu_engine.py's dispatch only knows the user
        # picked the generic "games" action -- this is the one place that
        # knows WHICH game, so it's the natural spot to set the finer-
        # grained presence page Jerry asked for ("playing Lord" instead of
        # just "in games").
        presence = getattr(self.session, 'presence', None)
        if presence is not None:
            try:
                presence.set_page(f'games:{game_dict.get("name", "?")}')
            except Exception:
                pass
        if hasattr(self.session, '_heartbeat_node'):
            try:
                self.session._heartbeat_node(action=f'door: {game_dict.get("name", "?")}')
            except Exception:
                pass

        import time as _time
        _launch_started = _time.monotonic()
        if hasattr(self.session, '_log_activity'):
            try:
                self.session._log_activity('door_played', game_dict.get('name', '?'))
            except Exception:
                pass

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
            finally:
                if presence is not None:
                    try:
                        presence.set_page('games')
                    except Exception:
                        pass
                if hasattr(self.session, '_log_activity'):
                    try:
                        _elapsed = int(_time.monotonic() - _launch_started)
                        self.session._log_activity(
                            'door_exited',
                            f'{game_dict.get("name", "?")} ({_elapsed}s)')
                    except Exception:
                        pass
            return

        from flask import Flask
        from anetbbs.config import get_config
        from anetbbs.models import db, Game
        from ..games.door_runner import (play_door_game_telnet, play_rlogin_telnet,
                                         play_telnet_terminal)
        from .db_scope import transient_app_context

        app = Flask(__name__)
        app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
        db.init_app(app)

        with transient_app_context(app):
            g = Game.query.get(game_dict['id'])
            if g is None:
                await self.session.write("\r\nGame disappeared from the catalog.\r\n")
                return

        # door_rlogin / door_telnet are remote game-server types — no
        # local subprocess, just a TCP connection bridged to the user's
        # terminal. Everything else goes through the unified launch path.
        gtype = game_dict.get('game_type')
        if gtype == 'door_rlogin':
            launcher = play_rlogin_telnet
        elif gtype == 'door_telnet':
            launcher = play_telnet_terminal
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
        finally:
            if presence is not None:
                try:
                    presence.set_page('games')
                except Exception:
                    pass
            if hasattr(self.session, '_log_activity'):
                try:
                    _elapsed = int(_time.monotonic() - _launch_started)
                    self.session._log_activity(
                        'door_exited',
                        f'{game_dict.get("name", "?")} ({_elapsed}s)')
                except Exception:
                    pass

    async def play_number_guess(self):
        """Built-in number guessing — no door, pure session I/O."""
        import random
        if hasattr(self.session, '_heartbeat_node'):
            try:
                self.session._heartbeat_node(page='games:number-guess',
                                             action='playing: Number Guessing')
            except Exception:
                pass
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
