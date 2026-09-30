# `anetbbs-monitor` — the sysop console dashboard

A live, auto-refreshing terminal dashboard for the server console (or
SSH) — think Synchronet's `uMonitor` or Mystic's `nodespy`/`mis
server`: a live node list, Today/Total activity stats, a `bbs.log`
viewer, one-key access to `anetbbs-cfg`, a light/dark theme toggle,
and mouse support, all in one screen.

It's a new front end, not new tracking: the node list's data is the
same `NodeActivity` data the web admin's Control Center → NodeSpy
panel and the in-BBS Sysop Tools → Node Monitor already show and can
kick from. All three read and write the identical database rows, so a
kick from any one of them looks the same to the other two.

## Launching it

```bash
anetbbs-monitor
# or, if the console-script entry point isn't on PATH for some reason:
python -m anetbbs.monitor.app
```

Same startup shape as `anetbbs-cfg`: a lightweight, DB-only app
context, not the full web server — fast even on a Raspberry Pi. No
login prompt of its own, for the same reason `anetbbs-cfg` has none:
whoever can already run a command on the box has at least as much
access as this tool could grant them.

## What you're looking at

```
 ANetBBS Node Monitor :: 2/8 online :: 21:04:35
  Slot  User            Proto  Peer             Doing                     Since   Idle
     1  sysop           ssh    10.0.0.5         reading Fido/GENERAL      14:02    0:02
     2  guest42         telnet 203.0.113.9      door: LORD2               13:58    0:41
     3  -- waiting for call --
     4  -- waiting for call --
 ...
 Today:  Logons 2   Time 0:05:39   New Users 0   Posts 6   E-mail 0
 Total:  Logons 1096 Time 1412:37:08 New Users 136 Posts 1385 E-mail 99
 Uploads today:   0 files, 0 bytes
 Downloads today: 0 files
 [C]Cfg  [L]Logs  [T]Theme  [K]Kick  [R]Refresh  [Q]Quit
```

- **Slot** is the fixed node number (`1..BBS_NODES`, default 8 — set
  the `BBS_NODES` environment variable to change the pool size). The
  screen always shows every slot, live or empty, matching the
  multinode roster real terminal callers see.
- **Doing** shows real detail where the game/menu code reports it
  (current board, which door is running, MRC room, AFK state) — not
  just the protocol name.
- **Since** / **Idle** are how long the connection has been up, and
  how long since its last heartbeat.
- The screen redraws every second on its own; no key needed. The
  stats panel recomputes roughly every 5 seconds, not every redraw.
- **Stats panel**: Today/Total logons, connect time, new users, posts
  (board posts + echomail), e-mail (private messages + instant
  messages), and today's upload/download counts — all read from
  existing data (`CallerLog`, `User.created_at`, `Post`/
  `EchomailMessage`, `PrivateMessage`/`InstantMessage`, `FileUpload`,
  and `UserActivity(activity_type='file_download')`), nothing new to
  configure.

**One honest limitation, not something this tool regresses:** a
connection is invisible here until login succeeds — there's no
guest/anonymous browsing path in ANetBBS's tracking, so "waiting for
call" means the slot is genuinely unallocated, not "someone's sitting
at the login prompt." The web NodeSpy panel and the in-BBS Node
Monitor have this exact same limitation today.

## Kicking a node

Select a live row and press `K`. You'll be prompted for a reason
(defaults to "Disconnected by sysop" if left blank); confirming sets
the same `kick_requested`/`kick_reason` flag the web NodeSpy panel's
kick button sets. The target session's own watchdog polls that flag
every 5 seconds and disconnects itself — same mechanism, same ~5
second delay, regardless of which of the three tools requested it.

## Launching `anetbbs-cfg` from here

Press `C` to hand the terminal off to `anetbbs-cfg` without leaving
this tool or opening a second session — it runs in the same process
(no subprocess spawned), and this screen redraws cleanly the moment
you quit back out of it.

## Viewing `bbs.log`

Press `L` to open a scrollable pager over the live `bbs.log`. It seeks
from the end of the file in chunks rather than loading the whole
thing into memory, so it opens instantly on the most recent entries
even against a multi-gigabyte log. `Up`/`Down`/`PgUp`/`PgDn`/`Home`
scroll; `G` jumps to the end; `Q`/`Esc` returns to the dashboard.

## Theme

Press `T` to toggle between the dark (default) and a light color
palette. The choice is saved to a small local state file and shared
with `anetbbs-cfg` — switch it in either tool and the other picks up
the same choice next time it starts.

## Mouse support

On a terminal that reports xterm-style mouse events (most modern
terminals, including SyncTerm), clicking a node row selects it the
same as arrowing to it, and clicking a hotkey label in the footer
(`[C]Cfg`, `[L]Logs`, etc.) presses that hotkey. On a terminal that
doesn't support it, the keyboard shortcuts work exactly as before —
this is a pure addition, not a required control scheme.

See [09 — Multinode + NodeSpy](09-multinode-nodespy.md) for how the
node slot pool and `NodeActivity` tracking work underneath all three
surfaces (this tool, the web NodeSpy panel, and the in-BBS Node
Monitor).

