# Multinode + NodeSpy

## How nodes work

ANetBBS supports up to `BBS_NODES` (1–100, default 8) concurrent
terminal sessions across telnet/SSH/rlogin. Each connection claims a
slot 1..N at login. When all slots are full, new connections see
"all nodes are in use — try again later" and disconnect cleanly.

## Multinode chat

Wire a menu item to `action_type = multinode` and your users get
real-time chat with every other terminal user.

Commands inside the chat:

| input             | does                                |
| ----------------- | ----------------------------------- |
| _any text_        | broadcast to all other nodes        |
| `/list`           | show online nodes                   |
| `/w <slot> <msg>` | private whisper to one node         |
| `/q`              | quit chat, back to menu             |

Join/part are announced automatically. Web users do NOT appear in
multinode chat — they have IRC / MRC / shoutbox.

Every user's login/logout (any protocol, not just inside chat) also
triggers a separate, broader alert wherever they currently are in the
BBS — see [33 — Presence alerts](33-presence-alerts.md).

## NodeSpy (sysop)

**Admin → Control Panel → NodeSpy** (`/admin/control/`).

Each terminal session writes a `NodeActivity` row that's updated on
every menu render and every action. The sysop panel polls this
every 4 seconds and shows:

- Slot number
- Username + protocol (telnet/ssh/rlogin)
- Current page (`main`, `boards`, `door:lord`, ...)
- Last action label
- Idle seconds
- Eye-icon link → per-node detail with last screen snapshot
- Kick button → disconnects that node immediately
  (`/admin/control/nodespy/<slot>/kick`), logged to the admin audit
  trail as `kick_node`

Stale rows (>5 min) auto-hide.

## anetbbs-monitor (CLI)

The same `node_activity` data NodeSpy shows, live in a terminal —
`anetbbs-monitor` at a shell, no browser needed. Same kick mechanism,
same auto-hide-after-5-minutes rule. See
[32 — anetbbs-monitor](32-node-monitor.md) for launching it, reading
the screen, and kicking a node from it.

## Underlying tables

If you want to query directly:

- `node_activity` — current sessions
- `caller_log` — historical record (durable)
- `multinode chat queue` — in-process, not persisted
