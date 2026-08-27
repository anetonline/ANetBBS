# Game Center — built-in web games catalog

This is the player-facing catalog of every `builtin_web` game
registered in `anetbbs/games/web_games.py` (the `WEB_GAMES` list),
served from `/games/`. For the door-game *types* (DOS, Synchronet,
Mystic, rlogin/telnet, ANetCRAFT, DOOM/Duke3D, LORD) and the shared
casino wallet economy, see [`14-door-games.md`](14-door-games.md) —
this page only covers the "click and play in your browser" catalog.

There is a matching player-facing wiki page (`games` slug, "Game
Center" title) seeded by `anetbbs/wiki/seed.py` — same content, aimed
at players rather than sysops/developers. Add a new game to
`WEB_GAMES` and it shows up in the lobby automatically (`Game` rows
are seeded idempotently by slug in `web_app.py`'s `create_app()`); if
it's worth a real explanation update both this file and the wiki page.

## Puzzle

| Game | Slug | What it is |
|---|---|---|
| Hangman | `hangman` | Classic word guessing — guess letters before the hangman is drawn. |
| Trivia Challenge | `trivia` | Multiple-choice trivia across categories. |
| Number Guesser | `numguess` | Guess a number 1-100 in as few tries as possible. |
| Memory Match | `memory` | Flip cards, find matching pairs. |
| Minesweeper | `minesweeper` | Clear the minefield without triggering a mine. |
| 2048 | `2048` | Slide and merge tiles to reach 2048. |
| Tetris | `tetris` | Falling blocks — ghost piece, hard drop, wall-kick rotation. |

## Action

| Game | Slug | What it is |
|---|---|---|
| Snake | `snake` | Eat, grow, don't hit the walls. |
| Galaga | `galaga` | Arcade shooter — waves of aliens, dive-bombing bosses. |
| Breakout | `breakout` | Arkanoid-style brick breaker with power-ups. |
| ANetDarkForces | `darkforces` | First-person raycasting shooter — 10-sector campaign, 7 weapons, 8 enemy types, 3 save slots. |

## Strategy

| Game | Slug | What it is |
|---|---|---|
| Tic Tac Toe | `tictactoe` | Against a real AI opponent. |
| Meadowlark Valley | `meadowlark-valley` | Original town/farm-builder sim — server-side saves (3 slots tied to the player's account), auto-harvesting farmer NPCs, real-time co-op (`/mlv-coop` SocketIO namespace). By far the deepest game in the catalog; see its own `README.md` in the source repo and the `meadowlark-valley` wiki page for the full mechanics rundown. |

## Cards & Casino

| Game | Slug | What it is |
|---|---|---|
| Klondike Solitaire | `solitaire` | Drag-and-drop, Ace to King. |
| Video Poker | `videopoker` | Jacks or Better, full paytable. |
| Texas Hold'em | `holdem` | No-limit, up to 4 CPU opponents (Easy/Medium/Hard). |
| Blackjack | `blackjack` | Hit, stand, double down, split. |
| Slot Machines | `slots` | Three themed machines (Classic Bars, Lucky Fruits, Retro BBS). |

Blackjack/Video Poker/Hold'em/Slots share the `WebGameWallet` play-money
economy (per-game starting balance, weekly Monday reset on going
broke) — see [`14-door-games.md`](14-door-games.md#built-in-web-games--casino-wallet-economy)
for the model/config details.

## RPG & other

| Game | Slug | What it is |
|---|---|---|
| Text Adventure | `adventure` | Parser-based dungeon crawl. |
| Ebook Reader | `ebooks` | Search/read free public-domain books (Project Gutenberg via Gutendex), bookmarks + reading history shared between web and terminal. |
| Typing Speed Test | `typing` | Measure WPM against a passage. |

## Guest / no-login play

A game can be marked **"Playable without an account"** in its admin
edit form (Admin → Games) — when set, `/games/<slug>/play` works for
an anonymous visitor, not just a logged-in user. Off by default, and
only meaningful for a game with no server-side per-user state: never
turn it on for anything using the `WebGameWallet` casino economy
(Blackjack, Video Poker, Hold'em, Slots — real virtual currency an
anonymous "account" has nowhere to keep) or a persistent per-account
save (Meadowlark Valley, ANetDarkForces, the Ebook Reader).

As shipped, guest play is enabled for: Hangman, Trivia Challenge,
Number Guesser, Snake, Tic Tac Toe, Memory Match, Typing Speed Test,
Minesweeper, 2048, Text Adventure, Galaga, Tetris, and Breakout —
every game in the catalog with no wallet or save-slot dependency.
Klondike Solitaire is deliberately left off that list (it's arguably
just as safe, but its "personal best" framing feels more natural tied
to an account) — flip it on yourself if you'd rather include it.

A guest sees a "Playing as a guest" banner with a "Create a free
account" link on the play screen, and a "No login needed" badge on
the game's lobby card and detail page. A guest's score is never
saved — `submit_score()` responds with a friendly "create an account
to save your score" message instead of the normal save, so the
game's own JS doesn't need any special handling for the logged-out
case.

## Adding a new web game

See the "Built-in web games" subsection of
[`17-development.md`](17-development.md) for the mechanical steps
(new entry in `WEB_GAMES`, a template under
`anetbbs/templates/games/web/<slug>.html`). For a game with enough
surface area to need real static JS/CSS files rather than one inlined
template (Meadowlark Valley is the only current example), static
assets go under `anetbbs/static/js/<slug>/` and
`anetbbs/static/css/<slug>.css`, referenced via `url_for('static',
...)` from a thin template that's mostly just the page markup.
