# The Shattered Realm (built-in MUD)

A native multiplayer text MUD, built directly into ANetBBS — no
external door, no dropfile, no separate process to manage. One
persistent character per account, a classic fantasy dungeon-crawl
setting (Village → Forest → Dungeon → Ruined Citadel), live chat with
everyone else exploring, a shop, a healer, real leveling, a choice of
four character classes, and quests.

It's reached from **Game Center → Door Games**, same as any other
built-in game (e.g. ANetCRAFT) — not a separate top-level menu item.

## Playing

| Command | Does |
|---|---|
| `north`/`n`, `south`/`s`, `east`/`e`, `west`/`w`, `up`/`u`, `down`/`d`, `northeast`/`ne`, `northwest`/`nw`, `southeast`/`se`, `southwest`/`sw` | Move |
| `look` / `l` | Look around |
| `inventory` / `i` / `inv` | List what you're carrying |
| `equipment` / `eq` | List what you have equipped |
| `get <item>` / `drop <item>` | Pick up / drop |
| `wear <item>` / `remove <item>` | Equip / unequip a weapon or armor |
| `use <item>` | Drink a potion or similar |
| `attack` / `kill` | Fight whatever's in the room |
| `shop` | Browse the shop (where available) |
| `heal` | Visit the healer (where available) |
| `say <text>` / `yell <text>` | Talk — yell reaches the whole realm |
| `tell <player> <text>` | Private message |
| `emote <text>` | Perform an action |
| `who` | See who else is online |
| `score` / `stats` | Your character sheet (shows class, and mana if your class uses it) |
| `quests` | List your accepted quests, and any quest offered in your current room |
| `accept <slug>` | Accept a quest offered where you're standing |
| `turnin <slug>` | Turn in a completed quest where you accepted it |
| `quit` / `q` | Leave |

A character is created automatically the first time you play — you'll
be asked for a name and then a **class** — and is permanent: there's
no save-slot system like the single-player games, your character
persists across every future visit, the same way a real MUD account
works.

## Classes

Chosen once, at character creation, and permanent for that character.
Each has its own stat growth per level and its own combat ability
(typed at the `[A]ttack  [X]Ability  [F]lee` combat prompt):

| Class | Role | Growth | Ability |
|---|---|---|---|
| **Warrior** | Tank/melee | Highest HP, no mana | **Power Attack** — bonus damage, usable every 3rd turn |
| **Mage** | Caster | Lowest HP, a real mana pool | **Fireball** — strong magic damage that ignores physical defense, costs mana |
| **Rogue** | Skirmisher | No mana, best flee chance | **Backstab** — bonus damage, but only as the opening move of a fight |
| **Cleric** | Support | A mana pool | **Heal** — restores HP instead of attacking that turn, costs mana |

Some higher-tier weapons (notably the Ruined Citadel's signature
weapons — see below) are restricted to one class; `score`/`wear` tell
you plainly if an item isn't usable by your class.

## Combat

Turn-based: attack, use your class ability, or flee each round,
repeated until the monster (or you) goes down. Dying has no permadeath
or item-loss penalty — you wake up back in the village square at 1 HP
and can carry on. A defeated monster "respawns" for you specifically
after a short while (other players aren't affected either way — see
**Multiplayer** below). Mana (for Mage/Cleric) regenerates slowly as
you move between rooms, and fully refills at the healer.

## Quests

Standing in a quest giver's room and typing `look` (or `quests`) shows
any quest offered there you haven't yet accepted. `accept <slug>`
takes it on; killing the target monster anywhere in the world (not
just that room) counts toward it automatically. Once complete, return
to the same giver's room and `turnin <slug>` for the XP/gold/item
reward. The built-in "Cleanse the Citadel" quest (village chapel →
kill 3 wraiths → Citadel Plate armor) is a working example of the full
loop.

## Multiplayer

You'll see other players currently in your room, and can talk with
`say`/`yell`/`tell`/`emote`:
- `say` and `emote` reach players in the same room;
- `yell` reaches everyone in the realm;
- `tell` reaches only the named player.

Messages arrive within about a second, while you're at the prompt. You
see only what's said after you enter, not earlier history. Monster encounters, though, are
**instanced per player** — your fight with a goblin is yours alone,
not a shared fight every other player in the room also sees mid-combat.
This is a deliberate design choice, not a missing feature: a true
shared-mob-HP system (the way ANetBBS's own ANetCRAFT door works)
needs real authority-election and conflict-resolution machinery that a
fantasy dungeon-crawl doesn't actually need to be fun — you still see
and talk to everyone else exploring the same world, in real time,
across every connected session (telnet, SSH, rlogin, and the web
terminal all share the same live world).

There is no player-vs-player combat in this build — a deliberate scope
decision (PvP needs its own consent/balance/safe-zone design, which is
a real separate project, not a gap in this one).

## A real limitation worth knowing about

The web UI's own Game Center (clicking Play in a browser) launches
every door game, including this one, through the same subprocess+PTY
bridge every other door game type uses — not the in-process path a
real telnet/SSH/rlogin session gets. The same is true if this game is
wired in via **Admin → BBS Menus** as a direct `door` action instead
of being reached through Game Center at all. Either way, play itself
is completely normal; the only thing missing is the MUD telnet
protocol extensions (MSSP/MCCP2/GMCP — see below) to a real MUD
client, since that subprocess path has a narrower connection to the
terminal than the in-process telnet/SSH/rlogin Game Center path gives
it. Only a real MUD client's extra integrations (server-status
queries, compressed output, out-of-band game data) are affected.

## MUD telnet protocol support

When reached through Game Center, The Shattered Realm negotiates:

- **MSSP** — so MUD crawlers/listing sites and MUD clients can query
  live server status (name, player count, uptime).
- **MCCP2** — compressed output for a real MUD client that asks for it.
- **GMCP** — out-of-band game data for a client that wants to render
  stats/maps outside the plain text stream.

## Admin: building the world

**Admin → MUD** has three sections:

- **Rooms** — name, description, zone, an optional wandering mob, and
  an optional special feature (`shop` or `healer` — put either on any
  room you like, there's nothing hardcoded about which room has them).
  Each room's **exits** are managed on its own edit page: pick a
  direction and a target room. Exits are one-way by default (a
  two-way corridor needs an exit row in both rooms, same as building a
  real MUD area with any other MUD-building tool) — this lets you
  build one-way drops/locked passages deliberately.
- **Items** — weapons/armor (attack/defense bonus), consumables (heal
  amount), whether it's sold at the shop, and an optional **required
  class** (leave as "Any class" unless this item should only be
  equippable by one of the four classes).
- **Mobs** — a monster template: level, HP, attack, defense, and the
  XP/gold it rewards. A mob template can't be deleted while a room
  still spawns it — update those rooms first.
- **Quests** — name, the room where a player can `accept`/`turnin` it,
  the target mob (a kill anywhere in the world counts, not just one
  room), how many kills are required, and the XP/gold/optional item
  reward.

There's no visual map/graph editor — rooms and their exits are managed
as plain lists, the same way ANetBBS's own BBS-menu tree is (no
existing visual graph editor exists anywhere in this codebase to build
one from, and a plain list works fine for this at any reasonable
world size).

## Credits / design notes

- Combat resolution and the leveling curve are adapted directly from
  the `robot_rpg` door's own proven numbers (`data/mods/doors/
  robot_rpg/combat.py`/`player.py`).
- Cross-process multiplayer presence/chat uses the same file-based
  sync pattern ANetCRAFT's own multiplayer already established
  (`anetbbs/features/anetcraft.py`), scoped down to presence + chat
  only (no shared world-state sync needed, per the instanced-combat
  design above).
