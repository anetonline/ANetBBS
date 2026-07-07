# Synchronet door compatibility

Status of the ANetBBS Synchronet `.js` door compatibility shim
(`anetbbs/games/synchronet_compat.py` — generates a Node.js wrapper
that's prepended to the door script).

The "real" path: if `jsexec` is on the host (typically at
`/sbbs/exec/jsexec` on a Synchronet install, or via `SBBS_JSEXEC` env
override), we use it directly. That gives 100% Synchronet API coverage.

The "shim" path: when no `jsexec` is found, we fall back to Node + our
compat. This doc tracks how much of Synchronet's API the shim covers.

## What's known to work

These door categories are confirmed running through the shim:

| Door                     | Status      | Notes                                       |
|--------------------------|-------------|---------------------------------------------|
| **LORD** (Synchronet JS port) | ✅ Working end-to-end (v287) | Bundled in `anetbbs/games/sbbs_doors/lord/`. Full play: character creation, town square, Inn, Violet flirt, save/load roundtrip, multi-session persistence. |
| BotWars                  | ✅ Working  | Bundled in `vendor/games/botwars/`          |
| `dd_lightbar_menu` doors | ✅ Working  | Lightbar UI works, mouse_getkey covered     |
| Doors that only use `console.*` for I/O | ✅ Working | The largest single category   |
| ANetSIMS                 | ✅ Working  | Bundled. Original ANET door.                |

## API surface (post-v1.0b coverage)

| Top-level object | Fields shimmed | Synchronet has | Coverage |
|------------------|---------------:|---------------:|---------:|
| `console`        | ~60            | ~80            | ~75%     |
| `bbs`            | ~22            | ~80            | ~28%     |
| `system`         | ~14            | ~50            | ~28%     |
| `user`           | ~30            | ~50            | ~60%     |
| `js`             | ~10            | ~12            | ~85%     |
| `server`         | stub           | rich           | minimal† |
| `client`         | stub           | rich           | minimal† |
| `File` class     | ~15 methods (seek/lock/flush/truncate, binary+string I/O, iniGet/iniSet) | full | ~85% |
| `Queue` class    | full (name-cached IPC) | full | done |

† Enough for dorkit's `sbbs` mode.

### Notable v287 additions for LORD

- `Queue` — name-cached, drives dorkit input via `dk.console.input_queue_callback` on a single-threaded Node event loop
- `strftime(fmt, unix_seconds)` — full C-strftime spec
- `file_mutex(filename, contents?)` — single-node always-grant + persist
- `require()` accepts the scope-prefix form `require(scope, "file.js", "Obj")`
- `load(true, "file.js", args)` background form returns a stub Queue
- `load_path_list`, `on_exit(code)`, `js.exec()`, `js.gc`, `js.global`,
  `js.terminate_signaled`
- `File.readBin/writeBin/readStr/writeStr` — fixed-width LE int + string I/O
- `File.lock/unlock/flush/truncate` — locks always-grant (single node);
  truncate persists to disk
- `File.open(mode)` parses the full fopen mode language (`r/w/a/r+/w+/a+`,
  `b` ignored)
- **Critical** `File.write(str, len)` overwrites at `_pos` (not append-to-EOF)
- `console.right/left/up/down(n)` aliases (sbbs_console.js doesn't use the
  `cursor_*` names)
- Load resolver prefers `<stubs_dir>/dorkit/<file>` over flat
  `<stubs_dir>/<file>` so dorkit's Screen / Graphic land with the right
  prototype methods
- Node-friendly `sbbs_input.js` replacement: callback on
  `dk.console.input_queue_callback`, one-time `stty min 0 time 1`,
  no per-iteration thrash

The shim also exposes vendored Synchronet `.js` libraries from
`anetbbs/games/sbbs_stubs/` (187 files) when a door does
`load("dd_lightbar_menu.js")` etc. The upstream `xtrn/dorkit/` subtree
ships under `sbbs_stubs/dorkit/`. Heavy lifting is often done by these
libraries rather than the shim itself.

## What's missing — and what it blocks

Priority is "how often do real doors hit it":

### High priority (many doors fail without these)

- **`bbs.exec()`** — a stub exists (shells out to the command line via
  Node's `child_process.execSync`), but it doesn't implement
  Synchronet's exec-string mini-language or in-process JS-door
  chaining. Doors that expect `bbs.exec()` to launch another `.js`
  door through the real dispatcher (rather than just running an OS
  command) still fail.
- **`bbs.menu()`** — a stub exists that dumps a static `<name>.msg`
  file straight to stdout, but it isn't Synchronet's real interactive
  menu engine (no hotkey capture, no return value). Hub-style door
  packs that rely on the actual menu engine still fail.
- **`user.security`** — `level`, `flags1`–`flags4`, `exempt`,
  `restrictions`, and `password` are all present as fields, so doors
  reading them won't crash. They're hardcoded stub values though
  (`flags1`/`flags2`/`exempt` = all-bits-set), not derived from real
  per-user data — so a door gating a feature on a specific security
  flag will see it as always-granted rather than reflecting the
  logged-in user's actual permissions.
- **`user.stats.*`** — call/post/upload counters. Doors that pull these
  for ranking displays show zeros.

### Medium priority (some doors fail)

- **`msg_area.*`** — message-base access. Doors that announce their
  results to a board (high-score broadcasts, etc.) silently fail to
  post. Doors that READ message bases (mail readers ported as doors)
  don't work at all.
- **`file_area.*`** — file-base access. Same pattern as above for file
  echoes.
- **`xbase` / `recordfile`** — Synchronet's primitive record-file API.
  Some older doors store their state via this.
- **`bbs.start_xfer*`** — file transfer. Doors that hand-off into a
  Z-Modem upload won't.
- **`client.socket`** — direct socket access. Only matters for
  doors that talk to remote servers from inside the door (rare).

### Low priority (edge cases)

- **`bbs.menu_*`** state-mutation functions (we don't have a Synchronet
  menu, so most of these are no-ops anyway).
- **`bbs.start_conf` / `bbs.end_conf`** — conference switching.
- **`bbs.goto_xtrn(slug)`** — would need to bridge into our
  `Game.query.filter_by(slug=…)` loader.

## Survey of common Synchronet xtrn doors

This is a recommended testing matrix — pick a handful, install them
through the shim, see what happens. The Synchronet xtrn directory is
at <https://github.com/SynchronetBBS/sbbs/tree/master/xtrn>.

| Door                     | Likely status with shim | Reason                              |
|--------------------------|-------------------------|-------------------------------------|
| `oneliner` (oneliner.js) | ✅ Should work          | Pure `console.*` + `File`           |
| `slyedit`                | ✅ Should work          | Editor — uses console + load()      |
| `slyvote`                | ✅ Should work          | Self-contained vote system          |
| `digdist`                | ⚠️ Probably broken      | Heavy `bbs.*` use                   |
| `bbslist`                | ⚠️ Mixed                | Reads/writes `bbslist.json` via File — should work; bbs.exec to chain into sub-views might break |
| `chksetup`               | ❌ Won't work           | Synchronet-internal setup checker   |
| `msgmaint`               | ❌ Won't work           | Touches `msg_area` heavily          |
| `pdo` (post-door object) | ❌ Won't work           | `xbase`-based                       |
| `tw2002` (TradeWars JS)  | ⚠️ Untested             | `xbase` storage; would need testing |
| `lord-style` doors       | ⚠️ Mixed                | Most use `console + user.handle` only |

## How to extend the shim

Add a new function in `anetbbs/games/synchronet_compat.py`:

1. Find the top-level object you want to extend (`console = { … }`).
2. Add the field name + a Node implementation that mimics Synchronet's
   behaviour. Synchronet docs at <http://wiki.synchro.net/jsobj:start>.
3. If the function maps to BBS data the user runs as has access to, hit
   the Flask app context (the shim is generated per-launch so it sees
   the user / game / node).

For frequently-needed missing pieces, prefer to use a real `jsexec` —
much less work than re-implementing Synchronet's runtime.

## Recommended test pack

Once you've added a door, send a real user through `Admin → Door Games
→ <slug> → Test Launch` to see the JS error trace if anything blows up.
The shim wraps the user script in try/catch and prints the stack,
so silent-fail is rare.
