# ANetBBS v1.0a2.98 — Fix: DOS door games (DOSBox-X) never connect on headless servers

## What's new

### Fix: `door_dos` games — "DOSBox never connected; closing bridge" on headless servers

DOS door games (TradeWars, LORD, etc. via DOSBox) failed silently on headless
servers with the message "DOSBox failed to dial in: timed out" / "DOSBox never
connected; closing bridge". The TCP nullmodem bridge waited 60 seconds and gave
up. No crash was visible because DOSBox's output was silently discarded.

**Root cause 1 — SDL init failure (main cause):**
The DOSBox-X `[sdl]` config section had only `fullscreen=false` with no `output=`
directive. On headless servers (no DISPLAY, `SDL_VIDEODRIVER=dummy`), dosbox-x
needs an explicit `output=surface` to initialize SDL2 via the dummy driver. Without
it, dosbox-x may fail SDL init and exit before the autoexec even starts — so it
never loads BNU.COM or connects to the TCP nullmodem bridge. Added `output=surface`
to the generated conf when running headless.

**Root cause 2 — dosbox stdout/stderr silently discarded:**
DOSBox's stdout and stderr were connected to the PTY (alongside all other door
processes), but for `door_dos` the PTY reader uses a no-op lambda since actual
door I/O goes through the TCP bridge. Any dosbox crash output (SDL errors, missing
file messages, etc.) was invisibly dropped. Added per-game log file:
`logs/dosbox_<slug>_nodeN.log` — check this file when a DOS door fails to launch.

**Root cause 3 — no command/config logging:**
The exact dosbox command and generated config path were never logged, making it
impossible to reproduce or inspect a failing launch. Both are now logged at INFO
so they appear in `journalctl -u anetbbs-telnet` and the web admin log viewer.

**How to diagnose a DOS door that won't start:**
```
# See the config + command used:
journalctl -u anetbbs-telnet | grep "DOS door"

# See dosbox output (SDL errors, BNU output, etc.):
cat /home/stingray/anetbbs/logs/dosbox_<slug>_node1.log
```
