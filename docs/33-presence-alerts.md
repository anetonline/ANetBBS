# Presence Alerts

Classic multi-node BBS behavior: when another user logs in or out,
everyone else currently on the system sees it live — "*** Firehawke
just logged in ***" on a terminal, a toast notification in the web UI.
This is cross-protocol and unconditional (not opt-in, not scoped to a
chat room) — telnet, SSH, rlogin, PETSCII, and web users all see every
OTHER user's login/logout, regardless of which protocol that user
connected on.

This is broader than [multinode chat](09-multinode-nodespy.md)'s own
join/part announcements, which only fire for users actually inside the
same chat room. Presence alerts fire no matter where you are in the
BBS — reading a message board, browsing files, sitting at a menu.

## What triggers an alert

- A user logging in (any protocol)
- A user logging out cleanly (any protocol)

A connection that drops without a clean logout (carrier loss, killed
process, network failure) does **not** generate a logout alert — there
was no clean disconnect to react to. That session's `UserSession` row
is instead caught later by the same stale-session cleanup job that
backs the who's-online list, so it eventually stops appearing as
online, just without a "just logged out" announcement.

You never see an alert about your own login/logout.

## How it works

Every login/logout writes a `PresenceEvent` row (`anetbbs/core/
presence.py`'s `SessionPresence.heartbeat()`/`.disconnect()`) —
a short-lived delivery queue, not an audit log (`CallerLog` and
`UserActivity` already cover that).

Two independent consumers poll this queue:

- **Each terminal session's own watchdog**
  (`core/session.py`'s `_start_presence_alert_watchdog()`) — polls
  every 5 seconds, prints the colored `*** username just logged in/out
  ***` line directly into that session's own output stream.
- **A background relay thread in the web process**
  (`core/presence.py`'s `start_presence_alert_relay()`) — polls every
  2 seconds and re-emits new events over Socket.IO as a
  `presence_alert` event, which `base.html`'s client-side JS turns
  into a dismissible Bootstrap toast (green for login, grey for
  logout, auto-dismisses after 15 seconds).

The relay thread exists specifically because telnet/SSH/rlogin run in
a **separate process** (`anetbbs.service`) from the web app
(`anetbbs-web.service`) in a real deployment — a terminal-originated
login has no other way to reach a connected browser tab's live socket
than a poller running inside the web process itself.

Both consumers track their own "last seen event id" starting at
whatever the current maximum is when they start — a freshly-connected
session or a freshly-started web process never replays alert history
from before it was watching.

## Performance

Both pollers are cheap by design: a single indexed `id > last_id`
range query, capped to at most 20 (terminal) or 50 (web relay) rows
per tick. `PresenceEvent` is a pure delivery queue, not history — a
scheduled event, `cleanup_stale_presence_events`
(`anetbbs/events/handlers.py`), deletes rows older than
`stale_minutes` (default 60) so the table never grows unbounded. That
window is intentionally generous: long enough to tolerate a temporary
consumer outage (e.g. the web process restarting) without losing an
alert before anything ever saw it, not a suggestion that these rows
are worth keeping around for an hour on purpose.

## If you don't want this

There's no per-user opt-out today — it's unconditional, matching how
most classic BBS software (Synchronet, Mystic, WWIV) has always
behaved. A sysop who wants to disable it entirely would need to stop
`_start_presence_alert_watchdog()`/`start_presence_alert_relay()` from
starting, which currently means a code change, not a config flag.
