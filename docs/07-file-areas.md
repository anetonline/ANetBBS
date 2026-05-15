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

If you've subscribed to file echos via FidoNet, the TIC processor
auto-files incoming `.tic` packets into the matching area. Audit at
**Admin → TIC Files**.

## Shareable links

Any user can mint an expiring share link for a file in an area they
have access to. **Tools → My File Shares**.
