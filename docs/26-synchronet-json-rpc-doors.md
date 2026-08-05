# Synchronet Door Games

ANetBBS includes a Node.js compat shim (`synchronet_compat.py`) that runs
real, unmodified Synchronet `.js` door games. During development we
tested this compat shim against 17 real Synchronet doors and confirmed
all 17 run correctly: 16 use Synchronet's real JSON-RPC "JSON DB"
service (conventionally port 10088) for shared, cross-BBS game state
and scoreboards (including real cross-BBS data exchange against live
game servers); the 17th, Minesweeper, is a different kind of door
entirely — see its own section below.

**None of these doors are included in the ANetBBS release.** They're
real, free, open-source software written by their own original
authors (not ANetBBS) — the point of testing against them was to prove
the compat shim runs real Synchronet doors correctly, not to
redistribute copies of other people's work. If you want to run one,
download it from its own author, following the steps below.

## The 16 confirmed-working JSON-RPC doors

| Door | Genre | Author | Source |
|---|---|---|---|
| Chicken Delivery | Real-time delivery arcade | echicken (bbs.electronicchicken.com) | Synchronet CVS, `xtrn/chickendelivery/` |
| Bubble Boggle | Word-search puzzle | Matt Johnson / mcmlxxix (bbs.thebrokenbubble.com) | Synchronet CVS, `xtrn/bublbogl/` |
| Synchronetris | Real-time multiplayer Tetris-style | Matt Johnson / mcmlxxix | Synchronet CVS, `xtrn/synchronetris/` |
| Jeopardized | Trivia game show, live rankings | echicken | Synchronet CVS, `xtrn/jeopardized/` |
| Gooble Gooble | Real-time Pac-Man-style chase | echicken | Synchronet CVS, `xtrn/gooble/` |
| Synkroban | Sokoban warehouse puzzle | art (Fatcats BBS) | Synchronet CVS, `xtrn/synkroban/` |
| Star Trek | Real-time space combat arcade | echicken | Synchronet CVS, `xtrn/startrek/` |
| Fat Fish | Fishing simulation | Matt Johnson / mcmlxxix | Synchronet CVS, `xtrn/fatfish/` |
| Dice Warz ][ | Territory-conquest strategy (Risk-like) | Matt Johnson / mcmlxxix | Synchronet CVS, `xtrn/dicewarz2/` |
| Maze Race | Real-time multiplayer maze racing | Matt Johnson / mcmlxxix | Synchronet CVS, `xtrn/maze/` |
| Thirstyville | Café-owner economic simulation | echicken | Synchronet CVS, `xtrn/thirsty/` |
| Good Time Trivia | Trivia with multiple categories | Eric Oulashin / Nightfox (digitaldistortionbbs.com) | Author's own BBS / Synchronet CVS, `xtrn/gttrivia/` |
| Lemons | "Lemmings"-style puzzle | echicken | Synchronet CVS, `xtrn/lemons/` |
| Star Stocks | Galactic investment strategy | Matt Johnson / mcmlxxix | Synchronet CVS, `xtrn/starstocks/` |
| DrugLord | "Dope Wars"-style economic sim | art (Fatcats BBS, art.poorcoding.com/druglord) | Synchronet CVS, `xtrn/druglord/` |
| Uber Blox | Block-clearing puzzle (like Super Collapse) | Matt Johnson / mcmlxxix | Synchronet CVS, `xtrn/uberblox/` |

The Synchronet CVS repository browser is at
`http://cvs.synchro.net/cgi-bin/viewcvs.cgi/xtrn/` — if a direct link
to a specific door's folder is stale, browse from there to find the
current path, or check the author's own BBS (listed above) for a
direct download. Several of these authors (Matt Johnson especially)
periodically update their doors on the CVS, so it's worth checking for
newer versions before installing.

## Installing a JSON-RPC door

1. **Download** the door's files from its source (above) to your own
   machine, then upload them to your ANetBBS server at
   `anetbbs/games/sbbs_doors/<name>/` (create the directory — pick any
   short slug for `<name>`, it doesn't need to match the table). This
   path is safe across `update.sh` runs — ANetBBS's own updater never
   touches or deletes anything under `sbbs_doors/` that it didn't ship
   itself.
2. **Configure `server.ini`** (or whichever file the door's own readme
   names — most use `server.ini`, a couple use `settings.ini`) inside
   that directory with the JSON-RPC host/port you want the door to use
   for its shared scoreboard/game state:
   ```ini
   host=your-json-server-host
   port=10088
   ```
   You're welcome to point this at StingRay's own live game server —
   it hosts real, active cross-BBS game state for several of these
   doors already:
   ```ini
   host=game.a-net-online.lol
   port=10088
   ```
   Some doors (Good Time Trivia, DrugLord) default to their own
   author's public score-sharing hub instead — that's a legitimate
   choice too (a bigger existing community of scores), or point it at
   your own JSON service (see "Hosting your own JSON-RPC service"
   below) if you'd rather not depend on any external server.
3. **Register the door** in ANetBBS's admin panel: **Admin → Games →
   Add Game**. Set:
   - **Game Type**: `Synchronet JS Game`
   - **Synchronet Script Path**: the full path to the door's entry
     `.js` file (e.g. `/home/stingray/anetbbs/anetbbs/games/sbbs_doors/lemons/lemons.js`)
   - **Synchronet Exec Dir**: the door's own directory (e.g.
     `/home/stingray/anetbbs/anetbbs/games/sbbs_doors/lemons/`)
4. Save, then launch it from the Games menu to confirm it works.

## Hosting your own JSON-RPC service

If you'd rather not depend on any external server for shared
scoreboards, you can run your own. Each door's own readme documents
this (`ctrl/services.ini` + `ctrl/json-service.ini` entries pointing
at `json-service.js`, a real Synchronet script) — that part is
identical to how it's always worked on real Synchronet, and out of
scope for ANetBBS's own compat shim (ANetBBS doesn't implement a JSON
service host, only the client side doors connect through). If you want
other BBSes to be able to connect to your own hosted scoreboard,
you'll need to open the port you configure (10088 by default) to
them.

## Minesweeper (a different kind of door)

Synchronet's own official Minesweeper — by Digital Man (Rob Swindell),
the author of Synchronet itself — is a real, classic minefield-clearing
puzzle game with personal-best time tracking. Unlike the 16 doors
above, it doesn't use the JSON-RPC service at all. Its only InterBBS
feature is optionally posting wins to a shared "syncdata" message area
(a real Synchronet MsgBase, networked over DOVE-Net or FidoNet) — it
auto-detects whether such an area is configured on your system and
silently skips that feature if not, so it works standalone with zero
extra setup.

- **Author**: Digital Man / Rob Swindell
- **Source**: `git.synchro.net` (Digital Man's own doors live on
  Synchronet's modern git repo, not the community `cvs.synchro.net`
  tree the other 16 doors above come from)

**Installing it:**

1. Download `minesweeper.js` and its `.hlp`/`.bin`/`.ppm`/`.pbm` asset
   files from the source above, and upload them to
   `anetbbs/games/sbbs_doors/<name>/` (same safe-across-`update.sh`
   location as every other door here — pick any slug you like for
   `<name>`).
2. No `server.ini`/JSON-RPC config needed — skip straight to
   registering it.
3. **Register the door** in ANetBBS's admin panel: **Admin → Games →
   Add Game**. Set:
   - **Game Type**: `Synchronet JS Game`
   - **Synchronet Script Path**: the full path to `minesweeper.js`
   - **Synchronet Exec Dir**: the door's own directory
4. Save, then launch it from the Games menu to confirm it works.

If you want the optional InterBBS score-sharing, set up a real
"syncdata" message area on your system (networked via DOVE-Net or
FidoNet, matching real Synchronet's own convention) — Minesweeper finds
it automatically by name, no additional door configuration needed.

## If a door doesn't work

The compat shim (`anetbbs/games/synchronet_compat.py`) implements a
large, but not complete, subset of the real Synchronet JS API surface
— see [`15-synchronet-compat.md`](15-synchronet-compat.md) for what's
covered and known gaps. If a door you install crashes, check
`logs/door-errors.log` for the real JavaScript stack trace (every
door crash writes here, with the door slug and the user who hit it) —
that's usually enough to tell whether it's a genuine compat-shim gap
worth reporting, or something specific to that door's own
configuration.
