# anetbbs/features/mud/__init__.py
"""The Shattered Realm -- a native multiplayer text MUD door game.

Entry point `launch_mud(session, username)` matches the exact
`builtin_python` game-type calling convention `GameManager._launch()`
(anetbbs/features/games.py) already uses for every other in-process
door (e.g. ANetCRAFT) -- wired into a `Game` row via
`web_game_module='anetbbs.features.mud:launch_mud'`.

Deliberately NOT wired into a direct BbsMenuItem 'door' action
(menu_engine.py's own separate dispatch, which spawns a subprocess via
door_runner.py/builtin_runner.py instead) -- that path's `_StdioSession`
shim has no access to `session.mud_protocol_negotiate_start()`/
`send_gmcp()` at all (confirmed: it only holds a bare
`asyncio.StreamReader`, no reference to the real BBSSession/writer).
A sysop who wires it in that way anyway still gets a fully playable
MUD, just without MSSP/MCCP2/GMCP -- see docs/40-mud.md.
"""
import asyncio
from .parser import GameContext, dispatch, _look
from . import mp as _mp
from . import classes as _classes
from . import character as _char

STARTING_ROOM_SLUG = 'village-square'
MSSP_UPTIME_START = __import__('time').time()


async def launch_mud(session, username):
    from ...models import db, MudCharacter, MudRoom

    user_id = (session.user or {}).get('id')
    if user_id is None:
        await session.write("\r\nYou need to be logged in to play.\r\n")
        return

    character = MudCharacter.query.filter_by(user_id=user_id).first()
    if character is None:
        character = await _create_character(session, user_id)
        if character is None:
            return  # player backed out of character creation

    if character.current_room_id is None:
        start = MudRoom.query.filter_by(slug=STARTING_ROOM_SLUG).first()
        if start is not None:
            character.current_room_id = start.id
            db.session.commit()

    try:
        await session.mud_protocol_negotiate_start(mssp_vars={
            'NAME': 'The Shattered Realm',
            'PLAYERS': str(_mp.live_player_count()),
            'UPTIME': str(int(MSSP_UPTIME_START)),
        })
    except AttributeError:
        # Defensive only -- every real caller of this entry point is a
        # full BBSSession per this module's own docstring, but a test
        # harness or a future caller shaped differently shouldn't be
        # able to crash the whole game over an optional protocol nicety.
        pass

    ctx = GameContext(session, character)
    ctx.refresh_presence()
    try:
        character.last_played_at = __import__('datetime').datetime.utcnow()
        db.session.commit()
        await session.write(
            f"\r\n\x1b[1;35mWelcome back to The Shattered Realm, "
            f"{character.name}.\x1b[0m\r\n")
        await _look(ctx)
        chat_task = asyncio.create_task(_chat_pump(ctx))
        while True:
            line = await session.read_line("\r\n> ")
            if line is None:
                break
            keep_going = await dispatch(ctx, line)
            if not keep_going:
                break
            ctx.refresh_presence()
    finally:
        try:
            chat_task.cancel()
        except NameError:
            pass
        _mp.remove_presence(username)
        try:
            await session.mud_protocol_negotiate_stop()
        except AttributeError:
            pass


async def _create_character(session, user_id):
    from ...models import MudRoom

    await session.write(
        "\r\n\x1b[1;35mWelcome to The Shattered Realm!\x1b[0m\r\n"
        "You have no character on this account yet.\r\n")
    name = None
    for _attempt in range(3):
        candidate = (await session.read_line("What is your character's name? ") or '').strip()
        if not candidate:
            await session.write("\r\nYou must enter a name.\r\n")
            continue
        if len(candidate) > 50:
            await session.write("\r\nThat name is too long (50 characters max).\r\n")
            continue
        name = candidate
        break
    if name is None:
        await session.write("\r\nToo many failed attempts -- try again later.\r\n")
        return None

    await session.write("\r\nChoose a class:\r\n")
    for slug in _classes.CLASS_SLUGS:
        info = _classes.class_info(slug)
        lines = _char.word_wrap(f"  {info['label']} -- {info['description']}",
                                indent='      ')
        await session.write('\r\n'.join(lines) + '\r\n')
    char_class = None
    for _attempt in range(3):
        choice = (await session.read_line(
            f"\r\nClass ({'/'.join(_classes.CLASS_SLUGS)}): ") or '').strip().lower()
        if choice in _classes.CLASS_SLUGS:
            char_class = choice
            break
        await session.write(f"\r\nPick one of: {', '.join(_classes.CLASS_SLUGS)}\r\n")
    if char_class is None:
        char_class = 'warrior'
        await session.write("\r\nDefaulting to Warrior.\r\n")

    start = MudRoom.query.filter_by(slug=STARTING_ROOM_SLUG).first()
    character = _char.create_character(
        user_id, name, start.id if start else None, char_class=char_class)
    label = _classes.class_info(char_class)['label']
    await session.write(f"\r\nWelcome, {name} the {label}. Your adventure begins...\r\n")
    return character


async def _chat_pump(ctx, interval=1.0):
    """Deliver other players' say/emote (same room), yell, and tells to
    this player while they're at the prompt (fix F-1)."""
    while True:
        await asyncio.sleep(interval)
        try:
            entries = ctx.chat.poll()
            if not entries:
                continue
            room = ctx.current_room()
            my_room = room.slug if room is not None else ''
            me = ctx.character_username()
            for entry in entries:
                text = _mp.deliverable(entry, me, my_room)
                if text:
                    await ctx.write(f"\r\n\x1b[1;36m{text}\x1b[0m\r\n> ")
        except asyncio.CancelledError:
            raise
        except Exception:
            continue
