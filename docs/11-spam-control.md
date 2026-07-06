# Spam control

Five complementary layers:

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

## 3. IP bans

**Admin → IP Bans** (`/admin/ip-bans/`). Ban a single IP or a CIDR
range. Optional TTL in days (0 = permanent). Login + register routes
both refuse banned IPs.

## 4. Word filter

**Admin → Word Filter**. Sysop maintains a list of bad terms and
their replacement (e.g. `****`). Applied at write-time on:

- Shoutbox posts
- Board posts
- PMs
- Oneliners

Filter is in-memory cached; click "Reload" after edits.

## 5. File moderation queue

```
FILE_MOD_QUEUE_ENABLED=true
```

Non-admin uploads land in `data/file-queue/` with a `FileQueueEntry`
row. **Admin → File Queue** lets sysop approve (publishes to area)
or reject (deletes from quarantine).

## Bonus: per-user

Users can self-block at **User menu → Block List**. Blocked users
can't PM them or @-mention them.
