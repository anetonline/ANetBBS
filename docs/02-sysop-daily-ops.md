# Sysop daily ops

Your two main panels:

## /admin — admin home

Shows total/active users, post counts, online users, 24-hour activity
totals, recent callers, file queue depth, and quick-action buttons.

Use this for: a quick "is everything OK" check.

## /admin/control — control panel

Shows:

- **Every anetbbs-* systemd service** with green/red badges plus
  Start / Stop / Restart buttons and a one-click journal viewer.
- **NodeSpy** — every active telnet/SSH/rlogin session, with live
  page + last action + idle time. Click the eye icon for a per-node
  detail screen with the last screen snapshot.
- **Live who's-online** for both web and terminal users, refreshed
  every 5 seconds.

For service start/stop/restart from the web to actually work, the
user `anetbbs-web` runs as needs sudoers permission for the units.
Install the supplied template:

```
sudo cp /opt/anetbbs/deploy/sudoers.anetbbs /etc/sudoers.d/anetbbs
sudo chmod 0440 /etc/sudoers.d/anetbbs
sudo visudo -cf /etc/sudoers.d/anetbbs    # syntax check
```

(The template uses the `anetbbs` service account. Edit it if your install runs under a different username.)

## /admin/checklist — first-launch checklist

Walks through what you've configured vs not (admin password changed,
custom board added, BBS_NAME customized, MOTD pool, default theme,
file area, bulletin posted). Re-runnable, read-only.

## /admin/users + /admin/users/:id/manage

The richer user editor — set access level (Mystic-style 0–255), time
budget, lock/verify toggles, sysop notes, password reset, file ratio
view. The basic edit page is fine for renames; use Manage for
fine-grained control.

## /admin/inactive-users

Bulk-cleanup interface for stale accounts. Pick a day cutoff, check
the boxes, mass-PM "we miss you" / soft-deactivate / hard-delete.

## /admin/pending-users (NUV)

When `NUV_ENABLED=true`, new users land here for approval.
One-click Approve sends a welcome PM automatically.

## /admin/newsletter

Compose a one-time newsletter. Fans out as a PM to every active
verified user. History kept.

## /admin/broadcast

Push a real-time toast to every connected web tab AND every
telnet/SSH/rlogin user's terminal. Optional TTL.

## Journals

Every `anetbbs-*` service writes to systemd journal. From the
control panel each service has a "list" icon showing the last 200
lines. Faster than `journalctl` alt-tab.
