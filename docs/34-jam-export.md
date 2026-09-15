# JAM message base export (for classic door games)

An opt-in, per-echo-area feature that regenerates a real JAM-format
message base (`<tag>.jhr`/`.jdt`/`.jdx` files) from ANetBBS's own
echomail, so a classic door game written against the JAM API can read
that area's messages directly — without ANetBBS's own database
becoming something the door can write into.

## Why read-only, one-way export

ANetBBS's SQL database is the actual, authoritative store for every
echomail message — the same rows every other part of the app (web
boards, terminal menus, BinkP tossing, QWK packets) reads and writes.
Rather than teach the whole echomail subsystem to also read and write
a *second*, independent storage format live, this exports a fresh
snapshot on a schedule. A JAM-reading door only ever sees that
snapshot; nothing it does to the exported files is read back into
ANetBBS. This avoids a real class of bug (two live-writable copies of
the same data quietly drifting apart) in exchange for messages in the
export lagging behind the database by however long the schedule's
interval is — acceptable for the door games this exists for (nothing
here needs sub-second freshness).

## Enabling it

Per echo area, not global: **Admin → Echomail → (pick a network) →
edit an area → "Export to JAM Message Base"**. Off by default.

The actual export only runs when something schedules it —
**Admin → Scheduled Events**, handler **`export_jam_message_bases`**.
Not seeded automatically on any install (unlike the built-in cleanup
jobs), since most installs will never turn this on at all; add an
event yourself once you've enabled the toggle on at least one area.
A daily or hourly schedule is reasonable for most uses; there's no
harm in running it more often — a run with nothing changed just
rewrites the same bytes.

Params (JSON object, all optional):

```json
{"output_dir": "/opt/anetbbs/data/jam_export"}
```

`output_dir` defaults to `<DATA_DIR>/jam_export` if omitted.

## What gets exported

For each area with the toggle on, `<output_dir>/<tag>.jhr` (headers),
`<tag>.jdt` (message text), and `<tag>.jdx` (index) — `<tag>` is the
area's own tag, lowercased. Every run **regenerates the whole base
from scratch**; it's not an incremental append. Point your door's JAM
message-base path configuration at `output_dir` and the area's tag.

Per message: sender, recipient, subject, and body are exported as-is;
`MSGID`/`REPLY` kludges (if present) are preserved as JAM's own
`MSGIDcrc`/`REPLYcrc` fields, so a JAM reader that threads replies by
CRC comparison works correctly across ANetBBS-originated and
network-received messages alike. Outbound (locally authored) messages
get JAM's `MSG_LOCAL` attribute bit set; every message gets
`MSG_TYPEECHO` (conference-distribution) since this only ever exports
echomail areas, never private mail.

**No `.jlr` (last-read) file is written.** That file is a *reader's*
own per-user bookmark, not exportable message data — any real JAM
library creates its own on first open when one is missing.

## What this doesn't do

- **Not a live sync.** A door writing new messages into the exported
  JAM files doesn't create anything in ANetBBS — there's no import
  path back in. If you want inbound-from-a-door messaging, that's a
  different, unrelated feature.
- **Not netmail.** Only echomail (conference) areas can be exported;
  ANetBBS's private-message and netmail stores have no JAM export.
- **No password-protected message bases.** The base header's
  password-CRC field is always written as "none set."

## Implementation notes (for anyone extending this)

The byte format lives in `anetbbs/echomail/jam_export.py`, built and
verified directly against the real JAM specification (Joaquim
Homrighausen / Andrew Milner / Mats Wallin, 1993) rather than from
memory — every struct's field list is declared as an ordered
`(name, format_char, size)` list with a self-checking assertion that
`struct.calcsize()` matches the declared byte total, specifically to
catch a wrong field size immediately rather than silently producing a
misaligned file. The CRC-32 variant JAM uses for its `.jdx` index and
`MSGIDcrc`/`REPLYcrc` fields (commonly catalogued as CRC-32/JAMCRC —
the same polynomial as the standard zlib/zip CRC-32, but with no final
XOR step) is verified against that variant's own published check
value in `tests/test_jam_export.py`.
