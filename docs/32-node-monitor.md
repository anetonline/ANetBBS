# `anetbbs-monitor` — the live node monitor

A live, auto-refreshing terminal view of every BBS node — think
Synchronet's `uMonitor` or Mystic's `nodespy`/`mis server`. Run it
directly on the server (or over SSH) to see who's connected, on what
protocol, from where, and what they're currently doing, without
opening a browser.

It's a new front end, not new tracking: the same `NodeActivity` data
the web admin's Control Center → NodeSpy panel and the in-BBS Sysop
Tools → Node Monitor already show and can kick from. All three read
and write the identical database rows, so a kick from any one of them
looks the same to the other two.

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
 [Up/Down] Select  [K] Kick  [R] Refresh  [Q] Quit
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
- The screen redraws every second on its own; no key needed.

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

See [09 — Multinode + NodeSpy](09-multinode-nodespy.md) for how the
node slot pool and `NodeActivity` tracking work underneath all three
surfaces (this tool, the web NodeSpy panel, and the in-BBS Node
Monitor).

