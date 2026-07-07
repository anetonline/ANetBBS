# File areas

File areas are the BBS's download library. Each area lives on disk
under a configurable storage path.

## Creating an area

**Admin → File Areas → Add Area**:

| field               | purpose                                          |
| ------------------- | ------------------------------------------------ |
| `tag`               | unique key (e.g. `FILES.GAMES`)                  |
| `name`              | display name                                     |
| `description`       | shown in the area listing                        |
| `storage_path`      | absolute path on disk where files live           |
| `upload_permission` | `none` / `users` / `sysop`                       |
| `is_sysop_only`     | hide entirely from non-admins                    |
| `password`          | optional — area lock                             |

## Where files live

Each area stores binaries in its `storage_path`. Sysop can drop files
in directly (e.g. via SFTP) or users can upload via the web or FTP.

## FTP access

When `FTP_ENABLED=true` in `.env` (or via **Admin → Settings**), the
ANetBBS FTP server publishes every active `FileArea` as a top-level
directory. Three views are served from the same port:

- **Anonymous** (`anonymous` / blank password) — read-only, sees only
  active areas with `is_sysop_only=false`. Earns you the FTN nodelist
  `IFC` flag once advertised.
- **Authenticated users** — same login as web / telnet / SSH. Read
  everything not sysop-only, upload subject to each area's
  `upload_permission` (`none` / `users` / `sysop`).
- **Sysop** — read + write everything, including `is_sysop_only` areas.

Uploads via FTP create the same `FileUpload` row a web upload would,
so they show up in the web file-area browser as well. Per-area
`upload_permission` is enforced post-write: a denied upload is
deleted from disk and the violation is logged.

Ports to open on the firewall: `21/tcp` (control) and the configured
`FTP_PASV_PORTS` range (default `40000-40050`, data channels). See
`docs/PORTS.md` for the full table. Optional FTPS by setting
`FTP_TLS_CERTFILE` + `FTP_TLS_KEYFILE` (reuse your nginx
Let's Encrypt cert).

## Upload moderation queue

Set `FILE_MOD_QUEUE_ENABLED=true` in `.env` to require sysop approval
for non-admin uploads.

When on:
- User uploads → file lands in `data/file-queue/`, a
  `FileQueueEntry` row marks it pending.
- **Admin → File Queue** (`/admin/file-queue/`) lets sysop approve
  (moves to area `storage_path`) or reject (deletes).

## File ratios

Mystic/Synchronet-style ratio control:

```
RATIO_MIN=0.05    # require uploads to be at least 5% of downloads
```

When set:
- Each download bumps `bytes_downloaded` on `FileRatio` per user.
- Each upload bumps `bytes_uploaded`.
- A new download is **blocked** if the user's projected ratio
  (after the download) is below `RATIO_MIN`. Sysops always exempt.

Set `RATIO_MIN=0` (default) to disable.

## Hatched files (TIC)

TIC ("Ticket File") is FidoNet's file-echo distribution mechanism —
the file-area equivalent of echomail. It runs in both directions:

### Inbound

If you've subscribed to file echos via FidoNet, the TIC processor
(`anetbbs/echomail/tic.py`) auto-files incoming `.tic` packets into the
matching area. Audit at **Admin → TIC Log** (`/admin/tic-log`).

### Outbound — automatic, no manual step

This is the part that's easy to miss: **uploading a file to any file
area that has a network attached (`network_id` is not `None`) queues
that file for outbound distribution to every peer subscribed to that
file echo — automatically, with no extra action.** This includes all
9 of the bundled `ANN.FILES.*` areas (see [doc 6 —
Echomail](06-echomail.md) for the full list) as well as any custom
file-echo area you attach to a network yourself.

Concretely: `_hatch_if_network_area()` in `anetbbs/web/file_areas.py`
runs after every successful upload — the regular **Upload** form, the
sysop's **Manage → Upload**, and **Smart Upload** all call it, as does
the moderation queue's **Approve** action in
`anetbbs/web/file_queue.py` (so a queued upload only hatches out once
a sysop actually approves it, not the moment a user submits it). It
calls `hatch_local_file()` in `tic.py`, which queues one `HatchQueue`
row per subscribed peer (`FileEchoSubscription`) with a fresh CRC32
and size — the same fan-out `process_tic()` does for a TIC that
arrived from elsewhere, except this file *is* the origin hop, so there's
no prior SEEN-BY/PATH to inherit.

**In short: if you upload to a network-attached area, you are
broadcasting that file to every peer on that file echo, not just
filing it locally.** Purely local file areas (`network_id` is `None`)
are unaffected — nothing is queued for those.

Delivery happens on the next BinkP poll per peer. A sysop running this
BBS as a network hub can see the outbound queue's current
pending/failed counts on the **TIC / File Distribution** tab of **Admin
→ Echomail Networks → Hub Management** (`/admin/echomail/hub/`) — see
[doc 6](06-echomail.md) for the full Hub Management writeup. That view
is hub-operator-only (gated by `REGISTRY_MODE_ENABLED`); the plain
**Admin → TIC Log** audit page above is available on every install.

## Shareable links

Any user can mint an expiring share link for a file in an area they
have access to. **Tools → My File Shares**.
