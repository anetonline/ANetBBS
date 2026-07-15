# Spam control

Several complementary layers — each section below says where to
configure it:

## 1. Pre-login bot gate (telnet only)

Before showing the login menu, the BBS prompts:

```
Press ESC or * twice to continue...
```

Auto-scanners just dump bytes — they don't follow prompts and time
out after `BOT_GATE_TIMEOUT` seconds (default 30). Real humans tap
ESC twice and proceed. SSH and rlogin skip this since they've
already authed at the protocol layer.

## 2. NUV — New User Verification

```
NUV_ENABLED=true
```

Every new registration is marked `is_verified=False` and can't log
in until a sysop clicks Approve at **Admin → Pending Users**.

Approval triggers an automatic welcome PM.

## 3. Manual IP bans

**Admin → IP Bans** (`/admin/ip-bans`). Ban a single IP or a CIDR
range. Optional TTL in days (0 = permanent). Login + register routes
both refuse banned IPs.

## 4. Login auto-ban

The same **Admin → IP Bans** page also has an auto-ban settings
panel — this is arguably the most important layer here, since it
runs with no sysop intervention at all. Repeated failed logins from
one IP within a configurable window get a temporary ban automatically.
Defaults: 10 attempts / 5-minute window / 1-hour ban. All three are
sysop-adjustable on the same page.

## 5. Registration rate limiting

New-account registration is rate-limited per IP (default: 3 attempts
per hour) independently of the login auto-ban above — this stops
registration-form spam even from an IP that's never tried to log in.
Attempts are logged for the sysop to review.

## 6. IP whitelist

**Admin → IP Bans** also has a whitelist. An IP on it bypasses every
ban above (manual or auto) — useful for a sysop's own static IP, or a
trusted peer BBS's BinkP/QWK source address.

## 7. Country blocking

**Admin → Settings** (`/admin/settings`) has a "Blocked countries"
field — comma-separated ISO codes (e.g. `CN,RU,KP`). Blank disables
it. Looks up the caller's country via ip-api.com (no registration/API
key needed).

## 8. Word filter

**Admin → Word Filter**. Sysop maintains a list of bad terms and
their replacement (e.g. `****`). Applied at write-time on:

- Shoutbox posts
- Board posts
- PMs
- Oneliners

Filter is in-memory cached, but the cache is invalidated automatically
on every add/toggle/delete — no manual reload step needed.

## 9. File moderation queue

```
FILE_MOD_QUEUE_ENABLED=true
```

Non-admin uploads land in `data/file-queue/` with a `FileQueueEntry`
row. **Admin → File Queue** lets sysop approve (publishes to area)
or reject (deletes from quarantine).

## Bonus: per-user

Users can self-block at **User menu → Block List**. Blocked users
can't PM them or @-mention them.
