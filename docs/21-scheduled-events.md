# Scheduled events

**Admin → Scheduled Events** (`/admin/events/`). A built-in cron
replacement — runs maintenance jobs inside the same process as the
web app, on a one-minute tick, with results visible in the admin UI
(no need to grep syslog).

## How it works

A background thread wakes every 60 seconds, loads all rows where
**Enabled** is checked, and fires any whose schedule says they're
due. Each row records `last_run_at`, `last_status` (`ok`/`fail`),
`last_duration_ms`, and the first 4KB of output — all shown in the
admin list. Editing or disabling a row takes effect on the next tick
(within a minute), no restart needed.

There's no per-event timeout enforced by the scheduler — a handler
that hangs blocks the next tick's events. Handlers that shell out to
something that could run long (like `shell`) enforce their own
timeout instead (see below).

## Creating an event

Fields on the New/Edit form:

- **Name** — free text, shown in the admin list only.
- **Handler** — picks the built-in function that runs (see table
  below). Each option shows its description and expected params
  once selected.
- **Schedule** — one of:
  - **Daily** — pick a time (`HH:MM`, **UTC**).
  - **Hourly** — pick a minute-of-hour (0–59).
  - **Weekly** — pick a day (Mon–Sun) + time, UTC.
  - **Every N min** ("interval") — repeats N minutes after the
    *previous run finished*, not wall-clock-aligned.
- **Params (JSON object)** — handler-specific arguments. Must be a
  JSON object, even if empty (`{}`). This trips people up most
  often — the box does **not** accept a bare string or path. If
  your handler takes one argument called `command`, you write
  `{"command": "/path/to/script.sh"}`, not just the path.
- **Enabled** — unchecked rows never fire, but stay in the list.

There's also a **Run now** button per row for testing without
waiting for the schedule — same code path either way, so output
shown after a manual run matches exactly what a scheduled fire would
produce.

## Built-in handlers

| Handler key | Label | Params | Notes |
|---|---|---|---|
| `noop` | No-op (test) | none | Does nothing; returns ok. Use to confirm the scheduler thread is alive. |
| `tw2_maint` | Trade Wars 2002 maint | none | Runs TW2002's headless daily maintenance (Cabal move, inactive-player sweep) via the existing `tw2_maint` runner. |
| `db_vacuum` | SQLite VACUUM | none | Reclaims free pages, defragments, refreshes planner stats. Skips (returns ok) on non-SQLite backends — Postgres autovacuums itself. |
| `log_rotate` | Rotate large logs | `max_mb` (default 50) | Any `logs/*.log` over the threshold is renamed to `.1` and a fresh empty file is created in its place. |
| `security_check` | Security update check | none | Scans `apt list --upgradable` and the venv's `pip list --outdated`, tags Ubuntu `-security` rows, writes `logs/security-report.json` consumed by **Admin → Security**. Always returns ok=True even on a non-Ubuntu box, so a missing `apt` doesn't permanently red-flag the row. |
| `shell` | Shell command | `command` (required), `timeout` (default 60s) | Runs an arbitrary command as the service user via `subprocess.run(..., shell=True)`. **No sudo** — anything needing root privileges will silently fail at that step. Output is captured as raw bytes and decoded with `errors='replace'`, so non-UTF8 output (e.g. CP437 from DOS programs) doesn't crash the handler — it just shows replacement characters in the log. |

### `shell` handler — worked example

Running a nightly TW2002 external-events script via dosemu2:

**Params (JSON object):**
```json
{"command": "/opt/anetbbs/doors/dos/tw/event.sh", "timeout": 120}
```

**`event.sh`:**
```bash
#!/bin/bash
export TERM=linux
cd /opt/anetbbs/doors/dos/tw
/usr/libexec/dosemu2/dosemu2.bin -td EXTERN.EXE
```

The `export TERM=linux` and `-td` (dumb-terminal mode) are required
because the scheduler runs the command with no controlling tty —
dosemu2's normal door launches get a real PTY from `door_runner.py`,
but a scheduled shell command doesn't. Without these, dosemu2 fails
with `ERROR: TERM environment variable needs set.` before it ever
reaches your DOS program.

## Default events on a fresh install

Seeded once (idempotent — re-running the seeder skips handler keys
that already have a row):

| Name | Schedule | Handler |
|---|---|---|
| TW2 daily maintenance | Daily 03:30 UTC | `tw2_maint` |
| Weekly SQLite VACUUM | Weekly, Sunday 04:15 UTC | `db_vacuum` |
| Rotate oversize logs | Daily 04:45 UTC | `log_rotate` (`max_mb: 50`) |
| Daily security update check | Daily 04:00 UTC | `security_check` |

Any of these can be disabled, deleted, or have their schedule
changed — they're normal rows, not special-cased.

## Troubleshooting

- **"Params (JSON object)" rejects what I typed** — it must parse as
  a JSON object. `/path/to/script.sh` is not valid JSON; wrap it as
  `{"command": "/path/to/script.sh"}`.
- **`shell` handler crashes with `UnicodeDecodeError`** — fixed as
  of v1.0a2.116+; older builds decoded captured output as strict
  UTF-8 and crashed on non-UTF8 bytes (e.g. CP437 from DOS programs
  run via dosemu2/dosbox). Update if you still see this.
- **dosemu2 commands fail with `TERM environment variable needs
  set`** — see the worked example above; export `TERM` and pass
  `-td` in your script.
- **An event always fails the same way** — click **Run now** and
  read the captured output; it's the same `stdout`/`stderr` you'd
  get running the command by hand over SSH, just minus your
  interactive shell's environment (PATH, TERM, etc. may differ from
  your login shell — set them explicitly in the script if needed).
