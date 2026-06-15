# ANetBBS v1.0a2.112 — Fix: dosemu2 bat generic; TW2002 ANSI colors via DORINFO1.DEF

## Changes

### Fix: dosemu2 `_ANET.BAT` was hardcoded for TW2002 on all dosemu2 games

`_build_dosemu_command()` previously injected three TW2002-specific lines into
`_ANET.BAT` for **every** dosemu2 door game, not just TradeWars 2002:

- `SET TWNODE=1`
- `MKDIR I:\NODE2`
- `COPY I:\DOOR.SYS I:\NODE2\DOOR.SYS`

These lines are harmless for TW2002 but wrong for any other game launched via
`door_dosemu`. Fixed: the three lines are now conditional on the game slug
containing `"tw2002"` (case-insensitive). All other dosemu2 games receive a
clean generic bat with only the standard drop-file copies.

Also added `DOOR32.SYS` and `DORINFO1.DEF` to the generic copy block so games
using those drop file formats also get them inside the game directory.

### TW2002 configuration: use DORINFO1.DEF for correct ANSI colors

TradeWars 2002 detects ANSI mode differently depending on which drop file type
it reads:

- **DOOR.SYS** (line 19 = `GR`): TW2002 sends an `ESC[6n` cursor-position probe
  via COM1 and waits for the terminal's `ESC[row;colR` reply. The timing of this
  round-trip through dosemu2's pts COM1 is unreliable — TW2002 often times out
  and falls back to ASCII mode even when the user's terminal is ANSI-capable.
- **DORINFO1.DEF** (line 10 = `1`): ANSI flag is explicit, no probe needed.
  TW2002 reads it directly and enters color mode immediately.

**The fix**: set Drop File Type to `DORINFO1.DEF` in both places:

1. **ANetBBS game admin** (`/admin/games`): set *Drop File Type* = `DORINFO1.DEF`,
   *Drop File Path* = `%P`.
2. **TEDIT.EXE** (TW2002's node editor): set *BBS Drop file type* = `RBBS`
   (RBBS is what TW2002 calls the DORINFO1.DEF format).

TW2002 TEDIT.EXE confirmed working settings:
- Path to Drop file: `I:\`
- BBS Drop file type: `RBBS`
- I/O Type: `Standard`
- Comport: `1`
- Override port Addr: `03FB`
- Override port IRQ: `4`
- Active Node: `Yes`

ANetBBS game admin confirmed working settings:
- Game Type: `DOS Door Game (dosemu2)`
- Executable Path: `doors/dos/tw/TW2002.EXE`
- Working Directory: `doors/dos/tw/`
- Command Line Args: `twnode=1`
- Drop File Type: `DORINFO1.DEF`
- Drop File Path: `%P`
- Max Players: `10`

## Files changed

- `anetbbs/games/door_runner.py` — `_build_dosemu_command()`: TW2002-specific bat lines conditional on slug
