# anetbbs/features/mud/parser.py
"""The command parser and verb handlers -- built entirely from
scratch (confirmed: no multi-word command parser of any kind exists
anywhere else in this codebase). A single dict keyed by every verb AND
its abbreviations (rather than a separate alias-resolution step) --
the real verb set is small enough that this stays simple.
"""
import time

from . import character as _char
from . import combat as _combat
from . import shop as _shop
from . import mp as _mp
from . import classes as _classes

DIRECTIONS = {
    'n': 'north', 'north': 'north',
    's': 'south', 'south': 'south',
    'e': 'east', 'east': 'east',
    'w': 'west', 'west': 'west',
    'u': 'up', 'up': 'up',
    'd': 'down', 'down': 'down',
    'ne': 'northeast', 'northeast': 'northeast',
    'nw': 'northwest', 'northwest': 'northwest',
    'se': 'southeast', 'southeast': 'southeast',
    'sw': 'southwest', 'southwest': 'southwest',
}

# Short per-session cooldown so a player can't instantly re-fight a mob
# they just fled from/defeated -- in-memory only (keyed by room id on
# the GameContext itself), not persisted; see combat.py's own docstring
# for why that's an acceptable, deliberate simplification.
MOB_RESPAWN_SECONDS = 15.0


class GameContext:
    def __init__(self, session, character):
        self.session = session
        self.character = character
        self.chat = _mp.ChatTail()
        self._mob_ready_at = {}  # room_id -> time.monotonic() it's fightable again
        self._joined_at = time.time()

    async def write(self, text):
        await self.session.write(text)

    def current_room(self):
        from ...models import MudRoom
        return MudRoom.query.get(self.character.current_room_id)

    def refresh_presence(self):
        room = self.current_room()
        if room is not None:
            _mp.publish_presence(self.character_username(), room.slug,
                                 self._joined_at)

    def character_username(self):
        return (self.session.user or {}).get('username', 'player')


async def dispatch(ctx: GameContext, line: str) -> bool:
    """Returns False to end the session's main loop (quit)."""
    line = (line or '').strip()
    if not line:
        return True
    parts = line.split(None, 1)
    verb = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ''

    if verb in DIRECTIONS:
        await _move(ctx, DIRECTIONS[verb])
    elif verb in ('l', 'look'):
        await _look(ctx)
    elif verb in ('i', 'inv', 'inventory'):
        await _inventory(ctx)
    elif verb in ('eq', 'equipment'):
        await _equipment(ctx)
    elif verb == 'get':
        await _get(ctx, rest)
    elif verb == 'drop':
        await _drop(ctx, rest)
    elif verb in ('wear', 'equip'):
        await _wear(ctx, rest)
    elif verb in ('remove', 'unequip'):
        await _unwear(ctx, rest)
    elif verb == 'use':
        await _use(ctx, rest)
    elif verb in ('a', 'attack', 'kill'):
        await _attack(ctx, rest)
    elif verb == 'shop':
        await _shop_verb(ctx)
    elif verb in ('heal', 'healer'):
        await _healer_verb(ctx)
    elif verb == 'say':
        await _chat(ctx, 'say', rest)
    elif verb == 'yell':
        await _chat(ctx, 'yell', rest)
    elif verb == 'tell':
        await _tell(ctx, rest)
    elif verb == 'emote':
        await _chat(ctx, 'emote', rest)
    elif verb == 'who':
        await _who(ctx)
    elif verb in ('score', 'stats'):
        await _score(ctx)
    elif verb in ('quest', 'quests'):
        await _quests_verb(ctx)
    elif verb == 'accept':
        await _accept_quest(ctx, rest)
    elif verb in ('turnin', 'turn-in'):
        await _turnin_quest(ctx, rest)
    elif verb == 'help':
        await _help(ctx)
    elif verb in ('q', 'quit'):
        await ctx.write("\r\nSafe travels, adventurer.\r\n")
        return False
    else:
        await ctx.write(f"\r\nI don't understand '{verb}'. Type HELP for a list of commands.\r\n")
    return True


async def _look(ctx):
    room = ctx.current_room()
    if room is None:
        await ctx.write("\r\nYou float in a formless void. (no current room set)\r\n")
        return
    desc_lines = '\r\n'.join(_char.word_wrap(room.description or ''))
    await ctx.write(f"\r\n\x1b[1;33m{room.name}\x1b[0m\r\n{desc_lines}\r\n")
    exits = sorted(e.direction for e in room.exits.all())
    if exits:
        await ctx.write(f"Exits: {', '.join(exits)}\r\n")
    else:
        await ctx.write("Exits: none\r\n")
    others = _mp.players_in_room(room.slug, exclude_username=ctx.character_username())
    if others:
        await ctx.write(f"Also here: {', '.join(others)}\r\n")
    if room.mob_template is not None:
        ready_at = ctx._mob_ready_at.get(room.id, 0)
        if time.monotonic() >= ready_at:
            await ctx.write(f"A {room.mob_template.name} lurks here.\r\n")
    if room.feature == 'shop':
        await ctx.write("There is a shop here. Type SHOP to browse.\r\n")
    elif room.feature == 'healer':
        await ctx.write("A healer offers their services here. Type HEAL.\r\n")
    await _announce_quest_giver(ctx, room)


async def _announce_quest_giver(ctx, room):
    from ...models import MudQuestTemplate
    templates = MudQuestTemplate.query.filter_by(giver_room_id=room.id).all()
    for template in templates:
        entry = _char.find_quest_entry(ctx.character, template.slug)
        if entry is None:
            await ctx.write(
                f"Someone here offers a task: \"{template.name}\" "
                f"(type 'accept {template.slug}').\r\n")
        elif entry.get('completed') and not entry.get('turned_in'):
            await ctx.write(
                f"You can turn in \"{template.name}\" here "
                f"(type 'turnin {template.slug}').\r\n")


async def _move(ctx, direction):
    room = ctx.current_room()
    if room is None:
        return
    exit_row = room.exits.filter_by(direction=direction).first()
    if exit_row is None:
        await ctx.write(f"\r\nYou can't go {direction} from here.\r\n")
        return
    from ...models import db
    ctx.character.current_room_id = exit_row.target_room_id
    _classes.regen_mana(ctx.character)
    db.session.commit()
    ctx.refresh_presence()
    await _look(ctx)


async def _inventory(ctx):
    from ...models import MudItemTemplate
    inv = _char.inventory(ctx.character)
    if not inv:
        await ctx.write("\r\nYou aren't carrying anything.\r\n")
        return
    await ctx.write(f"\r\nGold: {ctx.character.gold}\r\n")
    for entry in inv:
        item = MudItemTemplate.query.filter_by(slug=entry['item_slug']).first()
        name = item.name if item else entry['item_slug']
        tag = ' (equipped)' if entry.get('equipped') else ''
        await ctx.write(f"  {name} x{entry.get('qty', 1)}{tag}\r\n")


async def _equipment(ctx):
    items = _char.equipped_items(ctx.character)
    if not items:
        await ctx.write("\r\nYou have nothing equipped.\r\n")
        return
    await ctx.write("\r\nEquipped:\r\n")
    for item in items:
        await ctx.write(f"  {item.name}\r\n")


def _find_carried_item(character, name):
    from ...models import MudItemTemplate
    name = name.strip().lower()
    if not name:
        return None, None
    for entry in _char.inventory(character):
        item = MudItemTemplate.query.filter_by(slug=entry['item_slug']).first()
        if item and (item.slug.lower() == name or item.name.lower() == name):
            return entry, item
    return None, None


async def _get(ctx, rest):
    await ctx.write("\r\nThere's nothing like that to pick up here.\r\n")


async def _drop(ctx, rest):
    entry, item = _find_carried_item(ctx.character, rest)
    if item is None:
        await ctx.write("\r\nYou aren't carrying that.\r\n")
        return
    _char.remove_item(ctx.character, item.slug, entry.get('qty', 1))
    await ctx.write(f"\r\nYou drop the {item.name}.\r\n")


async def _wear(ctx, rest):
    entry, item = _find_carried_item(ctx.character, rest)
    if item is None:
        await ctx.write("\r\nYou aren't carrying that.\r\n")
        return
    if item.item_type not in ('weapon', 'armor'):
        await ctx.write(f"\r\nYou can't wear or wield the {item.name}.\r\n")
        return
    if not _char.can_equip(ctx.character, item):
        label = _classes.class_info(item.required_class)['label']
        await ctx.write(f"\r\nOnly a {label} can use the {item.name}.\r\n")
        return
    _char.set_equipped(ctx.character, item.slug, True)
    await ctx.write(f"\r\nYou equip the {item.name}.\r\n")


async def _unwear(ctx, rest):
    entry, item = _find_carried_item(ctx.character, rest)
    if item is None:
        await ctx.write("\r\nYou aren't carrying that.\r\n")
        return
    _char.set_equipped(ctx.character, item.slug, False)
    await ctx.write(f"\r\nYou remove the {item.name}.\r\n")


async def _use(ctx, rest):
    from ...models import db
    entry, item = _find_carried_item(ctx.character, rest)
    if item is None:
        await ctx.write("\r\nYou aren't carrying that.\r\n")
        return
    if item.item_type != 'consumable':
        await ctx.write(f"\r\nThe {item.name} isn't something you can use like that.\r\n")
        return
    if item.heal_amount:
        ctx.character.hp = min(ctx.character.hp_max,
                               ctx.character.hp + item.heal_amount)
        db.session.commit()
        await ctx.write(f"\r\nYou use the {item.name} and recover {item.heal_amount} HP.\r\n")
    else:
        await ctx.write("\r\nNothing happens.\r\n")
    _char.remove_item(ctx.character, item.slug, 1)


async def _attack(ctx, rest):
    room = ctx.current_room()
    if room is None or room.mob_template_id is None or room.mob_template is None:
        await ctx.write("\r\nThere's nothing here to fight.\r\n")
        return
    if time.monotonic() < ctx._mob_ready_at.get(room.id, 0):
        await ctx.write(f"\r\nThe {room.mob_template.name} hasn't returned yet.\r\n")
        return
    mob = _combat.MobInstance(room.mob_template)
    result = await _combat.run_combat(ctx.session, ctx.character, mob)
    if result in ('won', 'fled'):
        ctx._mob_ready_at[room.id] = time.monotonic() + MOB_RESPAWN_SECONDS
    elif result == 'died':
        # No permadeath/item-loss -- a forgiving choice for a BBS door
        # game. Send them back to the village square to recover.
        from ...models import db, MudRoom
        start = MudRoom.query.filter_by(slug='village-square').first()
        if start is not None:
            ctx.character.current_room_id = start.id
            db.session.commit()
        await ctx.write("\r\nYou wake up back in the village square.\r\n")
        await _look(ctx)


async def _shop_verb(ctx):
    room = ctx.current_room()
    if room is None or room.feature != 'shop':
        await ctx.write("\r\nThere's no shop here.\r\n")
        return
    await _shop.run_shop(ctx.session, ctx.character)


async def _healer_verb(ctx):
    room = ctx.current_room()
    if room is None or room.feature != 'healer':
        await ctx.write("\r\nThere's no healer here.\r\n")
        return
    await _shop.run_healer(ctx.session, ctx.character)


async def _chat(ctx, kind, text):
    text = text.strip()
    if not text:
        await ctx.write(f"\r\n{kind.capitalize()} what?\r\n")
        return
    room = ctx.current_room()
    ctx.chat.send(ctx.character_username(), kind, text,
                  room_slug=room.slug if room is not None else '')
    if kind == 'emote':
        await ctx.write(f"\r\n{ctx.character.name} {text}\r\n")
    else:
        await ctx.write(f"\r\nYou {kind}, \"{text}\"\r\n")


async def _tell(ctx, rest):
    parts = rest.split(None, 1)
    if len(parts) < 2:
        await ctx.write("\r\nTell whom what?\r\n")
        return
    target, text = parts[0], parts[1]
    ctx.chat.send(ctx.character_username(), 'tell', text, target=target)
    await ctx.write(f"\r\nYou tell {target}, \"{text}\"\r\n")


async def _who(ctx):
    players = _mp.list_players()
    if not players:
        await ctx.write("\r\nNo one else is currently in The Shattered Realm.\r\n")
        return
    await ctx.write("\r\nAdventurers in The Shattered Realm:\r\n")
    for uname in sorted(players):
        await ctx.write(f"  {uname}\r\n")


async def _score(ctx):
    c = ctx.character
    label = _classes.class_info(c.char_class)['label']
    mana_line = f"   Mana: {c.mana}/{c.mana_max}" if c.mana_max else ''
    await ctx.write(
        f"\r\n{c.name} the {label} -- Level {c.level}\r\n"
        f"HP: {c.hp}/{c.hp_max}   Attack: {_char.effective_attack(c)}   "
        f"Defense: {_char.effective_defense(c)}{mana_line}\r\n"
        f"XP: {c.xp}/{c.xp_next}   Gold: {c.gold}\r\n")


async def _quests_verb(ctx):
    from ...models import MudQuestTemplate
    entries = _char.quests(ctx.character)
    if not entries:
        await ctx.write("\r\nYou haven't accepted any quests yet.\r\n")
    else:
        await ctx.write("\r\nYour quests:\r\n")
        for entry in entries:
            template = MudQuestTemplate.query.filter_by(
                slug=entry['quest_slug']).first()
            if template is None:
                continue
            if entry.get('turned_in'):
                status = 'turned in'
            elif entry.get('completed'):
                status = 'complete -- turn in at the giver'
            else:
                status = f"{entry.get('progress', 0)}/{template.target_count}"
            await ctx.write(f"  {template.name} -- {status}\r\n")
    await _announce_quest_giver(ctx, ctx.current_room())


async def _accept_quest(ctx, rest):
    from ...models import MudQuestTemplate
    slug = rest.strip().lower()
    room = ctx.current_room()
    template = MudQuestTemplate.query.filter_by(slug=slug).first()
    if template is None or room is None or template.giver_room_id != room.id:
        await ctx.write("\r\nThere's no one here offering that quest.\r\n")
        return
    if not _char.accept_quest(ctx.character, slug):
        await ctx.write("\r\nYou've already accepted that quest.\r\n")
        return
    await ctx.write(f"\r\nQuest accepted: {template.name}\r\n")


async def _turnin_quest(ctx, rest):
    from ...models import MudQuestTemplate
    slug = rest.strip().lower()
    room = ctx.current_room()
    template = MudQuestTemplate.query.filter_by(slug=slug).first()
    if template is None or room is None or template.giver_room_id != room.id:
        await ctx.write("\r\nThere's no one here to turn that quest in to.\r\n")
        return
    result = _char.turn_in_quest(ctx.character, slug)
    if result is None:
        await ctx.write("\r\nThat quest isn't ready to turn in.\r\n")
        return
    reward_line = f"{result.reward_xp} XP and {result.reward_gold} gold"
    if result.reward_item_slug:
        reward_line += f", plus a {result.reward_item_slug}"
    await ctx.write(f"\r\nQuest complete: {result.name}! You receive {reward_line}.\r\n")


async def _help(ctx):
    await ctx.write(
        "\r\n\x1b[1;36mCommands:\x1b[0m\r\n"
        "  north/n, south/s, east/e, west/w, up/u, down/d,\r\n"
        "  northeast/ne, northwest/nw, southeast/se, southwest/sw\r\n"
        "  look (l)             -- look around\r\n"
        "  inventory (i, inv)   -- list what you're carrying\r\n"
        "  equipment (eq)       -- list what you have equipped\r\n"
        "  get/drop <item>      -- pick up or drop an item\r\n"
        "  wear/remove <item>   -- equip or unequip a weapon/armor\r\n"
        "  use <item>           -- consume a potion or similar\r\n"
        "  attack/kill          -- fight whatever's in this room\r\n"
        "  shop                 -- browse the shop (where available)\r\n"
        "  heal                 -- visit the healer (where available)\r\n"
        "  say/yell <text>      -- talk to the room / the whole realm\r\n"
        "  tell <player> <text> -- send a private message\r\n"
        "  emote <text>         -- perform an action\r\n"
        "  who                  -- see who else is online\r\n"
        "  score/stats          -- your character sheet\r\n"
        "  quests               -- list your quests / nearby quest givers\r\n"
        "  accept <slug>        -- accept a quest offered in this room\r\n"
        "  turnin <slug>        -- turn in a completed quest\r\n"
        "  quit (q)             -- leave The Shattered Realm\r\n"
        "\r\nIn combat, your class also has its own ability "
        "(see the fight prompt).\r\n")
