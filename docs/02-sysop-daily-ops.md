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
account the `anetbbs-web` *service* runs as (its systemd unit's
`User=`, `anetbbs` by default) needs sudoers permission for the units.

`update.sh` sets this up for you automatically on every run — it
substitutes the real service-account name into the template and
installs it, so a sysop who's run at least one update already has this
working. On a brand-new install that hasn't been updated yet, install
it yourself:

```
sudo sed "s/^__SERVICE_USER__ /anetbbs /" /opt/anetbbs/deploy/sudoers.anetbbs \
    | sudo tee /etc/sudoers.d/anetbbs > /dev/null
sudo chmod 0440 /etc/sudoers.d/anetbbs
sudo visudo -cf /etc/sudoers.d/anetbbs    # syntax check
```

Replace `anetbbs` in the `sed` command with your actual service
account if you didn't use the installer's default. Don't just `cp` the
template directly — it ships with a literal `__SERVICE_USER__`
placeholder, not a real username, so a plain copy installs a rule that
matches nobody.

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

## Admin notifications

The bell-icon notification system (`/notifications/`) also fires for
five sysop admin-review events, so you don't have to manually check
each page for new work:

- **MSP federation registry join requests** — fires once a registrant
  verifies their contact email, not at the earlier unverified
  registration step. Links to `/admin/registry/`.
- **QWK node applications** — fires when a sysop applies for a QWK
  node number, via API or the terminal wizard. Links to
  `/admin/echomail/hub/qwk/requests`.
- **New users pending NUV approval** — fires on registration when NUV
  is enabled. Plain email-verification-only signups don't trigger
  this — those are fully self-service. Links to `/admin/pending-users`.
- **Unknown/bad echomail areas** — fires once per newly-discovered
  unknown area tag (not once per message) when inbound echomail
  arrives for an area this BBS doesn't carry. Links to
  `/admin/echomail/bad_areas`.
- **Network join applications** — fires when someone submits the
  public "apply to join this network" form (`/join/`; see
  echomail/federation docs for the feature itself). Links to
  `/admin/echomail/hub/join/requests`.

Every `is_admin=True` user gets these independently, and each can
toggle any of the five off in **Notification Settings**
(`/notifications/settings`) — an admin-only section that's invisible
to non-admin accounts. Default is ON for all five.

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
