"""
Data-driven BBS menu engine.

Reads BbsMenu / BbsMenuItem from the DB, renders the menu's ANSI screen +
hotkey items, and dispatches the user's choice to the appropriate action.

Falls back to a hard-coded default menu if no menus are configured (so a
fresh install still works without a sysop having to set up menus).
"""
import asyncio
import logging
from .bbs_ui import BBSMenuUI, _app

logger = logging.getLogger(__name__)


def _get_flags(session):
    """Return UserAccessFlags for the session user, or None (unrestricted)."""
    try:
        from anetbbs.models import UserAccessFlags
        uid = (session.user or {}).get('id')
        if not uid:
            return None
        with _app().app_context():
            return UserAccessFlags.query.filter_by(user_id=uid).first()
    except Exception:
        return None


async def _suspended(session, feature):
    """Write a 'feature suspended' notice and wait for a keypress."""
    await session.write(
        f'\r\n\x1b[1;31mYour {feature} access has been suspended.\x1b[0m\r\n')
    await session.read_key('\r\n\x1b[33m[Press any key]\x1b[0m')


# Mapping from action_type -> async coroutine taking (BBSMenuUI, action_args)
async def _act_goto(ui, args):
    # Returns the next menu name to load (the engine handles the loop)
    return ('goto', args)

async def _act_logoff(ui, args):
    """Quit / log off — confirm first so a stray Q doesn't drop a session."""
    sess = ui.session
    from .ansi_ui import FG, RESET, BOLD
    answer = await sess.read_key(
        f"\r\n{FG['yel']}{BOLD}Log off - are you sure? (Y/N): {RESET}")
    if (answer or '').upper() == 'Y':
        return ('quit', None)
    await sess.write(f"{FG['cyan']}Cancelled.{RESET}\r\n")
    return None

async def _act_door(ui, args):
    """Launch a specific Game by id."""
    f = _get_flags(ui.session)
    if f and f.no_games:
        return await _suspended(ui.session, 'games')
    from anetbbs.models import Game
    from anetbbs.games.door_runner import play_door_game_telnet
    try:
        game_id = int(args or 0)
    except ValueError:
        await ui.session.write("\r\nMenu config error: door action_args must be a Game id.\r\n")
        return None
    with _app().app_context():
        g = Game.query.get(game_id)
        if not g:
            await ui.session.write("\r\nGame not found.\r\n")
            return None
    await play_door_game_telnet(g, ui.session.user, ui.session)
    return None

# Clear-screen prefix used by every submenu action so menus don't stack.
_CLR = '\x1b[2J\x1b[H'

async def _act_boards(ui, args):  await ui.session.write(_CLR); await ui.list_boards();      return None
async def _act_pm(ui, args):      await ui.session.write(_CLR); await ui.list_pm_inbox();    return None
async def _act_pm_send(ui, args): await ui.session.write(_CLR); await ui.send_pm();          return None
async def _act_imsg(ui, args):    await ui.session.write(_CLR); await ui.list_imsg_inbox();  return None
async def _act_imsg_send(ui, args): await ui.session.write(_CLR); await ui.send_imsg();      return None
async def _act_bulletins(ui, args): await ui.session.write(_CLR); await ui.list_bulletins(); return None

async def _act_echo(ui, args):
    f = _get_flags(ui.session)
    if f and f.no_echomail:
        return await _suspended(ui.session, 'echomail')
    await ui.session.write(_CLR); await ui.list_echo_areas(); return None

async def _act_echo_post(ui, args):
    f = _get_flags(ui.session)
    if f and f.no_echomail:
        return await _suspended(ui.session, 'echomail')
    await ui.session.write(_CLR); await ui.compose_echomail(); return None

async def _act_files(ui, args):
    f = _get_flags(ui.session)
    if f and f.no_files:
        return await _suspended(ui.session, 'file downloads')
    await ui.session.write(_CLR); await ui.list_files(); return None

async def _act_who(ui, args):     await ui.session.write(_CLR); await ui.show_online();      return None
async def _act_profile(ui, args): await ui.session.write(_CLR); await ui.show_profile();     return None
async def _act_edit_prof(ui, args): await ui.session.write(_CLR); await ui.edit_profile();   return None
async def _act_passwd(ui, args):  await ui.session.write(_CLR); await ui.change_password();  return None
async def _act_sysop(ui, args):   await ui.session.write(_CLR); await ui.sysop_menu();       return None
async def _act_chat(ui, args):    await ui.session.write(_CLR); await ui.session.chat.show_menu(); return None
async def _act_rss(ui, args):     await ui.session.write(_CLR); await ui.show_rss();           return None

async def _act_ebooks(ui, args):
    """Ebook reader -- gated by the ebooks Game row's terminal_enabled
    flag (sysop-configurable independently of the web version)."""
    from anetbbs.models import Game
    with _app().app_context():
        g = Game.query.filter_by(slug='ebooks').first()
        enabled = bool(g and g.is_active and g.terminal_enabled)
    if not enabled:
        await ui.session.write(
            '\r\n\x1b[1;31mThe terminal Ebook Reader is currently disabled.\x1b[0m\r\n')
        await ui.session.read_key('\r\n\x1b[33m[Press any key]\x1b[0m')
        return None
    await ui.session.write(_CLR); await ui.show_ebooks(); return None


async def _act_multinode(ui, args):
    """Multinode chat: see who's connected and broadcast lines to them.
    Type /q to quit, /w <slot> <msg> to whisper, /list to see nodes."""
    from .multinode import run_chat_session
    await run_chat_session(ui.session)
    return None
async def _act_games(ui, args):
    f = _get_flags(ui.session)
    if f and f.no_games:
        return await _suspended(ui.session, 'games')
    await ui.session.write(_CLR); await ui.session.games.show_menu(); return None

async def _act_dialout(ui, args):
    """Open the dial-out / BBS travel menu — telnet/SSH OUT to other BBSes."""
    from .dialout import DialoutMenu
    await DialoutMenu(ui.session).show_menu()
    return None


async def _act_ansi(ui, args):
    """Show a sysop-defined ANSI screen by slot name, then return to caller."""
    if not args:
        await ui.session.write("\r\nMenu config error: ansi action_args needed.\r\n")
        return None
    try:
        await ui.session._show_ansi_screen(args.strip(), force_pause=True)
    except Exception:
        logger.exception('ansi action failed for slot %r', args)
    return None


async def _act_wall(ui, args):
    """Launch the graffiti wall."""
    try:
        from .wall import show_wall
        await show_wall(ui.session)
    except Exception:
        logger.exception('wall action failed')
    return None


async def _act_lastcallers(ui, args):
    """Show the paginated Last Callers list."""
    try:
        from .lastcallers import show_last_callers
        await show_last_callers(ui.session, args)
    except Exception:
        logger.exception('lastcallers action failed')
    return None


async def _act_exec(ui, args):
    """Run an external program with the user's terminal attached.

    `args` is either a plain command line ("/usr/local/bin/weather --zip 12345")
    or a JSON object with keys:
      - cmd:      shell command to run (required)
      - name:     friendly name for the dropfile + logging (optional)
      - dropfile: 'door.sys' / 'doorsys' / 'door32.sys' / null (optional)
      - cwd:      working directory (optional)

    The session is bridged to the child process's stdin/stdout — output
    is forwarded to the user, keystrokes are forwarded to the child, until
    the child exits. Sysop must already trust whatever they put here;
    this is a sysop-only configuration field.
    """
    import json
    import asyncio
    import os as _os

    if not args:
        await ui.session.write("\r\nMenu config error: exec action_args required.\r\n")
        return None
    cmd = None
    cfg = {}
    try:
        cfg = json.loads(args)
        if isinstance(cfg, dict):
            cmd = cfg.get('cmd')
        else:
            cfg = {}
    except (ValueError, TypeError):
        cmd = args
        cfg = {'cmd': cmd}
    if not cmd:
        await ui.session.write("\r\nMenu config error: no 'cmd' in exec action.\r\n")
        return None

    name = cfg.get('name') or 'External program'
    dropfile = (cfg.get('dropfile') or '').lower() or None
    cwd = cfg.get('cwd') or None

    # Optional dropfile generation (BBS door convention).
    user = ui.session.user or {}
    drop_dir = None
    if dropfile:
        try:
            from ..games.dropfile import write_dropfile
            # NEVER import anetbbs.web_app here — it triggers
            # eventlet.monkey_patch() which corrupts threading in the
            # telnet/SSH/rlogin processes.
            from ..features.bbs_ui import _app
            with _app().app_context():
                drop_dir, _ = write_dropfile(
                    dropfile, user.get('id'), user.get('username', '?'),
                    sysop_name=_os.environ.get('SYSOP_NAME', 'Sysop'),
                    bbs_name=_os.environ.get('BBS_NAME', 'ANetBBS'),
                    node=1, baud=38400)
        except Exception:
            logger.exception('dropfile write failed; running without one')

    # Substitute simple variables in the command.
    subs = {
        '{user}':     user.get('username', ''),
        '{userid}':   str(user.get('id', '')),
        '{dropdir}':  drop_dir or '',
    }
    rendered = cmd
    for k, v in subs.items():
        rendered = rendered.replace(k, v)

    await ui.session.write(f"\r\n[ Launching {name}... ]\r\n")
    try:
        proc = await asyncio.create_subprocess_shell(
            rendered,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
    except Exception as exc:
        await ui.session.write(f"\r\nFailed to launch: {exc}\r\n")
        return None

    async def pump_out():
        """Forward child stdout to the BBS terminal."""
        try:
            while True:
                chunk = await proc.stdout.read(1024)
                if not chunk:
                    break
                try:
                    await ui.session.write(chunk.decode('cp437',
                                                        errors='replace'))
                except Exception:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('exec stdout pump failed')

    async def pump_in():
        """Forward terminal keystrokes to child stdin (raw bytes)."""
        try:
            while proc.returncode is None:
                buf = await ui.session.read_raw(64)
                if not buf:
                    break
                try:
                    proc.stdin.write(buf)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('exec stdin pump failed')

    has_read_raw = hasattr(ui.session, 'read_raw')
    out_task = asyncio.create_task(pump_out())
    in_task = asyncio.create_task(pump_in()) if has_read_raw else None
    try:
        await proc.wait()
    finally:
        try:
            if proc.returncode is None:
                proc.terminate()
        except Exception:
            pass
        out_task.cancel()
        if in_task:
            in_task.cancel()
        for t in (out_task, in_task):
            if t:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

    await ui.session.write(f"\r\n[ {name} exited (rc={proc.returncode}) ]\r\n")
    return None


async def _act_page(ui, args):
    """Page the sysop — record + emit a real-time toast on web admin tabs."""
    await ui.session.write(
        '\r\nPage the sysop:\r\n'
        '  Type a one-line message (or just Enter to cancel).\r\n')
    msg = await ui.session.read_line('Message: ')
    if not msg or not msg.strip():
        await ui.session.write('\r\nCancelled.\r\n')
        return None
    try:
        from .sysop_paging import page_sysop
        wname = type(ui.session.writer).__name__.lower()
        proto = 'ssh' if 'ssh' in wname else ('rlogin' if 'rlogin' in wname else 'telnet')
        page_id = page_sysop(ui.session.user['id'], msg.strip(), service=proto)
    except Exception:
        page_id = 0
    if page_id:
        await ui.session.write(
            f'\r\nSysop has been paged. Reference #{page_id}.\r\n')
    else:
        await ui.session.write(
            '\r\nCould not record your page (logged for review).\r\n')
    return None


_ACTIONS = {
    'goto': _act_goto,
    'logoff': _act_logoff,
    'door': _act_door,
    'boards': _act_boards,
    'pm': _act_pm,
    'pm_send': _act_pm_send,
    'imsg': _act_imsg,
    'imsg_send': _act_imsg_send,
    'bulletins': _act_bulletins,
    'echo': _act_echo,
    'echo_post': _act_echo_post,
    'files': _act_files,
    'who': _act_who,
    'profile': _act_profile,
    'edit_prof': _act_edit_prof,
    'passwd': _act_passwd,
    'sysop': _act_sysop,
    'chat': _act_chat,
    'rss': _act_rss,
    'ebooks': _act_ebooks,
    'games': _act_games,
    'page': _act_page,
    'dialout': _act_dialout,
    'ansi': _act_ansi,
    'wall': _act_wall,
    'lastcallers': _act_lastcallers,
    'exec': _act_exec,
    'multinode': _act_multinode,
}


def _user_access_level(user):
    """Return an access integer for the user.
    Sysops are forced to >=100. Otherwise honor user.access_level.
    """
    if user.get('is_admin'):
        return max(100, user.get('access_level') or 100)
    return user.get('access_level') or 10


def _apply_menu_translations(menu_name, title, item_list, lang):
    """Look up MenuTranslation overrides for one menu render and return
    (translated_title, translated_item_list). No-op (returns the inputs
    unchanged) for 'en' or a falsy lang, so the common case does zero
    extra queries.

    Keys are derived from menu_name + hotkey (f'menu.{menu_name}.title',
    f'menu.{menu_name}.item.{hotkey}') rather than a dedicated column --
    BbsMenu.name is DB-unique so there's no cross-menu collision. Falls
    back to the source text when no translation row exists for a given
    key, matching MenuTranslation's own docstring. One batched query for
    the whole menu, not one per item.

    Must be called inside an app context (queries MenuTranslation).
    Pulled out of run_menu() as its own function so it's testable without
    driving the full async menu-render loop.
    """
    if not lang or lang == 'en':
        return title, item_list

    from anetbbs.models import MenuTranslation

    title_key = f'menu.{menu_name}.title'
    item_keys = [f'menu.{menu_name}.item.{hk}' for hk, _l, _a, _ar in item_list]
    rows = (MenuTranslation.query
           .filter_by(lang=lang)
           .filter(MenuTranslation.key.in_([title_key] + item_keys))
           .all())
    translations = {r.key: r.text for r in rows}

    new_title = translations.get(title_key, title)
    new_item_list = [
        (hk, translations.get(f'menu.{menu_name}.item.{hk}', label), action_type, action_args)
        for hk, label, action_type, action_args in item_list
    ]
    return new_title, new_item_list


async def run_menu(session, start='main'):
    """Run the data-driven menu loop starting at *start* menu name.
    Falls back to BBSMenuUI.show_main() if no menus exist in the DB."""
    ui = BBSMenuUI(session)

    # Bootstrap: if no menus defined, use the hard-coded UI as fallback
    from anetbbs.models import BbsMenu, BbsMenuItem
    with _app().app_context():
        n = BbsMenu.query.count()
    if n == 0:
        logger.info('No BbsMenu rows — falling back to hard-coded BBSMenuUI')
        await ui.show_main()
        return

    access = _user_access_level(session.user)
    current = start
    while True:
        # Drain any sysop replies into the user's terminal before drawing
        # the next menu. Keeps the conversation flowing without blocking
        # them from continuing whatever they were doing.
        try:
            from .sysop_paging import pop_messages
            pending = pop_messages(session.user.get('id'))
            if pending:
                await session.write('\r\n\x1b[1;31m=== Sysop Reply ===\x1b[0m\r\n')
                for m in pending:
                    await session.write(
                        f'\x1b[31m[{m["sender"]}]\x1b[0m {m["text"]}\r\n')
                await session.write('\r\n')
        except Exception:
            pass

        with _app().app_context():
            menu = BbsMenu.query.filter_by(name=current).first()
            if not menu:
                await session.write(f"\r\nMenu '{current}' not found.\r\n")
                return
            if menu.min_access > access:
                await session.write("\r\nAccess denied.\r\n")
                return
            items = (BbsMenuItem.query
                     .filter_by(menu_id=menu.id, is_visible=True)
                     .filter(BbsMenuItem.min_access <= access)
                     .order_by(BbsMenuItem.sort_order, BbsMenuItem.id)
                     .all())
            # Detach into plain tuples for use outside the app context
            title = menu.title
            prompt = menu.prompt or 'Choice: '
            item_list = [(it.hotkey.upper(), it.label, it.action_type, it.action_args)
                         for it in items]

            # Menu translation overrides (MenuTranslation) -- previously
            # defined in the schema but never read anywhere.
            lang = (session.user.get('language') or 'en') if isinstance(session.user, dict) else 'en'
            title, item_list = _apply_menu_translations(menu.name, title, item_list, lang)
            # Mode-aware screen content lookup.
            # wide : data/text/menus/{name}132.ans → {name}.ans → DB ANSI field
            # ansi : data/text/menus/{name}.ans → DB ANSI field
            # ascii: data/text/menus/{name}.asc  → (no DB — it has ANSI content)
            import os as _os
            from flask import current_app as _ca
            _mode = getattr(session, 'term_mode', 'ansi')
            _menus_dir = _os.path.join(_ca.config.get('DATA_DIR', ''), 'text', 'menus')
            is_plain_text = False
            screen = ''
            if _mode == 'ascii':
                _asc = _os.path.join(_menus_dir, f'{menu.name}.asc')
                if _os.path.exists(_asc):
                    try:
                        with open(_asc, 'rb') as _fh:
                            screen = _fh.read().decode('latin-1')
                            is_plain_text = True
                    except Exception:
                        pass
            else:
                _candidates = (
                    [f'{menu.name}132.ans', f'{menu.name}.ans']
                    if _mode == 'wide' else [f'{menu.name}.ans']
                )
                for _fname in _candidates:
                    _fpath = _os.path.join(_menus_dir, _fname)
                    if _os.path.exists(_fpath):
                        try:
                            with open(_fpath, 'rb') as _fh:
                                screen = _fh.read().decode('latin-1')
                        except Exception:
                            pass
                        break
                if not screen:
                    screen = menu.ansi_screen or ''

        # ANSI palette
        RESET = '\x1b[0m'
        BOLD  = '\x1b[1m'
        # Foregrounds (bright = bold + base color, classic ANSI.SYS
        # convention -- bare 90-97 aixterm codes aren't recognized by
        # MagiTerm/NetRunner/PuTTY, see ansi_ui.FG for the full story)
        FG = {
            'cyan':  '\x1b[1;36m', 'yel': '\x1b[1;33m', 'grn': '\x1b[1;32m',
            'wht':   '\x1b[1;37m', 'dim': '\x1b[37m',   'red': '\x1b[1;31m',
            'blu':   '\x1b[1;34m', 'mag': '\x1b[1;35m', 'gry': '\x1b[1;30m',
        }
        BG = {
            'blu': '\x1b[44m', 'cya': '\x1b[46m', 'red': '\x1b[41m',
            'blk': '\x1b[40m',
        }

        # Clear screen.  ASCII terminals get newlines; ANSI/wide get ESC[2J.
        if _mode == 'ascii':
            await session.write('\r\n' * 4)
        else:
            await session.write('\x1b[2J\x1b[H')

        if screen.strip():
            # Custom screen passthrough (ANSI art or plain .asc file).
            # Substitute Synchronet @CODE@ / Mystic |XX placeholders first.
            try:
                from .display_codes import apply as _apply_codes
                from .bbs_ui import _app as _bbs_app
                import anetbbs as _anetbbs_pkg
                _cfg = _bbs_app().config
                rendered = _apply_codes(
                    screen,
                    user=session.user,
                    bbs_name=_cfg.get('BBS_NAME', ''),
                    sysop=_cfg.get('SYSOP_NAME', ''),
                    node=(getattr(session, '_node_entry', None).slot
                          if getattr(session, '_node_entry', None) else 1),
                    version=getattr(_anetbbs_pkg, '__version__', 'v1.0a'),
                )
            except Exception:
                rendered = screen
            if is_plain_text:
                await session.write(rendered)
            else:
                # Write as raw bytes to preserve CP437 high-byte characters.
                # session.write() re-encodes strings as CP437, which corrupts
                # content decoded from latin-1: e.g. U+00DC (from byte 0xDC ▄)
                # re-encodes to CP437 byte 0x9A (Ü) — wrong glyph on terminal.
                try:
                    session.writer.write(rendered.encode('latin-1'))
                    await session.writer.drain()
                except (UnicodeEncodeError, AttributeError):
                    await session.write(rendered)
            await session.write("\r\n")
        elif _mode == 'ascii':
            # ASCII auto-render: plain text, no escape codes, ASCII box chars.
            inner_w = 64
            bar = '+' + '-' * (inner_w - 2) + '+'
            cell = f'| {title.upper()}'.ljust(inner_w - 1) + '|'
            await session.write(f"{bar}\r\n{cell}\r\n{bar}\r\n")

            # Single-column item list for ASCII readability
            for hk, lbl, _, _ in item_list:
                line = f"  [{hk}] {lbl}"
                await session.write(line + '\r\n')

            await session.write('-' * inner_w + '\r\n')
        else:
            # ANSI/wide auto-render.
            inner_w = 128 if _mode == 'wide' else 64
            top_bar = '▄' * inner_w
            bot_bar = '▀' * inner_w
            await session.write(f"{FG['cyan']}{BG['blk']}{top_bar}{RESET}\r\n")
            cell = f"╣ {title.upper()} ╠".center(inner_w)
            await session.write(
                f"{BG['cya']}{FG['wht']}{BOLD}{cell}{RESET}\r\n")
            await session.write(f"{FG['cyan']}{BG['blk']}{bot_bar}{RESET}\r\n")

            # Two-column item list — fits more options on one screen
            cols = 2
            col_w = (inner_w // cols) - 2
            items_padded = list(item_list)
            if len(items_padded) % cols:
                items_padded += [None] * (cols - len(items_padded) % cols)
            for i in range(0, len(items_padded), cols):
                pieces = []
                for j in range(cols):
                    it = items_padded[i + j]
                    if it is None:
                        pieces.append(' ' * col_w)
                        continue
                    hk, lbl, _, _ = it
                    text = f"  [{hk}] {lbl}"
                    if len(text) > col_w:
                        text = text[:col_w - 1] + '>'
                    text = text.ljust(col_w)
                    text = text.replace(
                        f"[{hk}]",
                        f"{FG['yel']}{BOLD}[{hk}]{RESET}{FG['grn']}", 1)
                    pieces.append(f"{FG['grn']}{text}{RESET}")
                await session.write(''.join(pieces) + '\r\n')

            await session.write(f"{FG['gry']}{'─' * inner_w}{RESET}\r\n")

        # NodeSpy heartbeat — log current menu so sysop's web panel can see.
        try:
            if hasattr(session, '_heartbeat_node'):
                session._heartbeat_node(page=current,
                                        action=f'menu: {title}')
        except Exception:
            pass

        # Single-key hotkey input — no Enter required. Bare Enter
        # falls through to a redraw.
        choice = await session.read_key(f"\r\n{prompt}")
        if not choice:
            continue

        # Match a hotkey
        match = next((it for it in item_list if it[0] == choice), None)
        if match is None:
            await session.write(f"\r\nUnknown choice '{choice}'.\r\n")
            continue
        _, _, action_type, action_args = match
        action = _ACTIONS.get(action_type)
        if action is None:
            await session.write(f"\r\nMenu config error: unknown action_type '{action_type}'.\r\n")
            continue

        try:
            try:
                if hasattr(session, '_heartbeat_node'):
                    session._heartbeat_node(
                        action=f'{action_type}({action_args or ""})')
            except Exception:
                pass
            result = await action(ui, action_args)
        except Exception as exc:
            # Carrier dropped inside an action — propagate so the session
            # unwinds cleanly. Anything else: log + continue.
            from anetbbs.core.session import CarrierLost
            if isinstance(exc, CarrierLost):
                raise
            logger.exception('Menu action %s(%r) failed', action_type, action_args)
            try:
                await session.write("\r\nMenu action failed (see server log).\r\n")
            except CarrierLost:
                raise
            except Exception:
                pass
            continue

        if isinstance(result, tuple):
            kind = result[0]
            if kind == 'quit':
                return
            if kind == 'goto':
                current = result[1] or current
                continue


# ---------------------------------------------------------------------------
# Default menu seeding (called by web app startup if no menus exist)
# ---------------------------------------------------------------------------

DEFAULT_MENUS = [
    {
        'name': 'main', 'title': 'ANetBBS - Main Menu', 'is_default': True,
        'items': [
            {'hotkey': 'M', 'label': 'Message Boards', 'action_type': 'boards', 'sort_order': 10},
            {'hotkey': 'B', 'label': 'Bulletins', 'action_type': 'bulletins', 'sort_order': 20},
            {'hotkey': 'P', 'label': 'PM Inbox', 'action_type': 'pm', 'sort_order': 30},
            {'hotkey': 'N', 'label': 'New PM', 'action_type': 'pm_send', 'sort_order': 35},
            {'hotkey': 'I', 'label': 'InterBBS IM Inbox', 'action_type': 'imsg', 'sort_order': 36},
            {'hotkey': 'J', 'label': 'Send InterBBS IM', 'action_type': 'imsg_send', 'sort_order': 37},
            {'hotkey': 'E', 'label': 'Echomail', 'action_type': 'echo', 'sort_order': 40},
            {'hotkey': 'C', 'label': 'Compose Echomail', 'action_type': 'echo_post', 'sort_order': 45},
            {'hotkey': 'F', 'label': 'File Library', 'action_type': 'files', 'sort_order': 50},
            {'hotkey': 'G', 'label': 'Game Center', 'action_type': 'games', 'sort_order': 60},
            {'hotkey': 'H', 'label': 'Chat', 'action_type': 'chat', 'sort_order': 70},
            {'hotkey': 'R', 'label': 'RSS News Reader', 'action_type': 'rss', 'sort_order': 75},
            {'hotkey': 'K', 'label': 'Ebook Reader', 'action_type': 'ebooks', 'sort_order': 76},
            {'hotkey': 'U', 'label': "Who's Online", 'action_type': 'who', 'sort_order': 80},
            {'hotkey': 'A', 'label': 'Page Sysop', 'action_type': 'page', 'sort_order': 85},
            {'hotkey': 'D', 'label': 'Dial Out (visit other BBSes)', 'action_type': 'dialout', 'sort_order': 86},
            {'hotkey': 'Y', 'label': 'Your Profile', 'action_type': 'profile', 'sort_order': 90},
            {'hotkey': 'X', 'label': 'Edit Profile', 'action_type': 'edit_prof', 'sort_order': 95},
            {'hotkey': 'W', 'label': 'Change Password', 'action_type': 'passwd', 'sort_order': 100},
            {'hotkey': 'S', 'label': 'Sysop Tools', 'action_type': 'sysop', 'sort_order': 200, 'min_access': 100},
            {'hotkey': 'Q', 'label': 'Logoff', 'action_type': 'logoff', 'sort_order': 999},
        ],
    },
]


def seed_default_menus():
    """Create default menu rows + backfill missing default items on existing
    installs. Idempotent — safe to call on every startup."""
    from anetbbs.models import db, BbsMenu, BbsMenuItem
    with _app().app_context():
        # Existing install: only top up newly-added default items
        # (e.g. when a new release ships an extra hotkey).
        if BbsMenu.query.count() > 0:
            backfilled = 0
            for mdef in DEFAULT_MENUS:
                m = BbsMenu.query.filter_by(name=mdef['name']).first()
                if not m:
                    continue
                existing_items = BbsMenuItem.query.filter_by(menu_id=m.id).all()
                # Check by (action_type, action_args) — not by hotkey — so sysops
                # who rebound or removed default hotkeys don't get duplicates added
                # back on every startup. Only truly new action types (new features
                # added in a new release) get backfilled.
                existing_actions = {
                    (it.action_type, it.action_args or '')
                    for it in existing_items
                }
                existing_labels  = {it.label for it in existing_items}
                existing_hotkeys = {(it.hotkey or '').upper() for it in existing_items}
                for idef in mdef['items']:
                    action_key = (idef['action_type'], idef.get('action_args') or '')
                    if action_key in existing_actions:
                        continue  # functionality already present; hotkey may differ — leave it alone
                    if idef['label'] in existing_labels:
                        continue  # same label exists under a legacy action_type — leave it alone
                    hotkey = idef['hotkey']
                    if hotkey.upper() in existing_hotkeys:
                        continue  # new feature but hotkey conflicts — skip rather than create duplicate
                    db.session.add(BbsMenuItem(
                        menu_id=m.id,
                        hotkey=hotkey,
                        label=idef['label'],
                        action_type=idef['action_type'],
                        action_args=idef.get('action_args'),
                        min_access=idef.get('min_access', 0),
                        sort_order=idef.get('sort_order', 0),
                        is_visible=True,
                    ))
                    backfilled += 1
            if backfilled:
                db.session.commit()
            return backfilled
        added = 0
        for mdef in DEFAULT_MENUS:
            m = BbsMenu(
                name=mdef['name'],
                title=mdef['title'],
                is_default=mdef.get('is_default', False),
                ansi_screen=mdef.get('ansi_screen', ''),
                prompt=mdef.get('prompt', 'Choice: '),
                min_access=mdef.get('min_access', 0),
            )
            db.session.add(m)
            db.session.flush()
            for idef in mdef['items']:
                db.session.add(BbsMenuItem(
                    menu_id=m.id,
                    hotkey=idef['hotkey'],
                    label=idef['label'],
                    action_type=idef['action_type'],
                    action_args=idef.get('action_args'),
                    min_access=idef.get('min_access', 0),
                    sort_order=idef.get('sort_order', 0),
                    is_visible=True,
                ))
                added += 1
        db.session.commit()
        return added


async def _act_oneliners(ui, args):
    """Show last 30 one-liners + last 10 callers like Mystic does."""
    sess = ui.session
    try:
        from ..models import OneLiner
        from .bbs_ui import _app
        from .lastcallers import recent_callers_query
        with _app().app_context():
            ol = (OneLiner.query.filter_by(is_hidden=False)
                  .order_by(OneLiner.created_at.desc()).limit(30).all())
            ol = [(o.user.username if o.user else '?',
                   o.text, o.created_at) for o in ol]
            callers = recent_callers_query(10).all()
            callers = [(c.username or '?', c.service or '?',
                        c.started_at) for c in callers]
    except Exception:
        ol, callers = [], []
    await sess.write("\r\n\x1b[1;36m=== Last 10 Callers ===\x1b[0m\r\n")
    for u, p, w in callers:
        await sess.write(
            f"  \x1b[1;33m{u:<20}\x1b[0m \x1b[1;30m{p:<8}\x1b[0m "
            f"{w.strftime('%m-%d %H:%M')}\r\n")
    await sess.write("\r\n\x1b[1;36m=== Recent One-Liners ===\x1b[0m\r\n")
    if not ol:
        await sess.write("  (none yet)\r\n")
    for u, txt, w in ol:
        await sess.write(
            f"  \x1b[33m<{u}>\x1b[0m {txt}\r\n")
    line = await sess.read_line(
        "\r\nLeave a one-liner (Enter to skip): ")
    if line and line.strip():
        try:
            from ..models import OneLiner, db as _db
            from .bbs_ui import _app as _a
            with _a().app_context():
                _db.session.add(OneLiner(
                    user_id=sess.user.get('id'),
                    text=line.strip()[:120]))
                _db.session.commit()
            await sess.write("\r\nThanks!\r\n")
        except Exception:
            pass
    return None

# Late-bound action registrations — for actions defined below the dict.
_ACTIONS['oneliners'] = _act_oneliners

