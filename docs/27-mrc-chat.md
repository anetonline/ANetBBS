# MRC chat (Multi Relay Chat)

MRC is a real inter-BBS chat network -- callers on ANetBBS can chat live
with users on other BBSes (any software) connected to the same MRC hub.
Available from the terminal (Chat Systems → MRC Chat) and the web UI
(Chat → MRC Chat).

## Enabling / disabling

Installed and started based on the `ENABLE_MRC` prompt during
`install.sh` (writes `MRC_BRIDGE_ENABLED` to `.env` and installs the
`anetbbs-mrc-bridge` systemd service). When disabled, "MRC Chat" is
hidden from both the web Chat dropdown and the terminal Chat Systems
menu -- only IRC shows. This includes the stock chat-menu ANSI art
(`anetbbs/screens/menus/chat*.ans`), which is skipped entirely (falling
back to the generated menu) whenever MRC is off, since the art itself
hardcodes an MRC line with no way to conditionally render inside static
ANSI. A sysop-supplied custom `data/text/menus/chat.ans` override needs
updating by hand if MRC is turned off after the fact.

## Credit

The `mystic` backend and the five Mystic-named chrome themes below are
both built on `pn-mrc137-alpha.zip`, developed by **StackFault** of
**The Bottomless Abyss** (`telnet://bbs.bottomlessabyss.net:2023`,
SSH on 2222), released 2024-09-01 under **Phenom Productions**
(<https://www.phenomprod.com>). StackFault's own header on
`mrc_client.py` credits it as based on earlier work by **Gryphon of
Cyberia BBS**. See `mrc/mystic_client/vendor/PROVENANCE.md` for the
full unmodified-vendoring details.

Native `umrc-client` support (below) interoperates with
[uMRC](https://github.com/codefenix-dev/uMRC), developed by
**Codefenix** of **ConChaos BBS** -- see that section for download
and setup details.

## Connection backends

The actual upstream connection to the MRC hub is handled by
`mrc/bridge/main.py` (`anetbbs-mrc-bridge` service) -- a persistent
process shared by every local caller, exposing a local WebSocket API
(`/mrcws`) that both the terminal and web clients connect to. Set via
`mrc_backend` in `mrc/bridge/config.json`:

| `mrc_backend` | What it does |
| ------------- | ------------ |
| `native` (default) | ANetBBS's own hand-rolled async socket client (`MRCConnection`) -- no external dependencies, the only option that's been running in production. |
| `mystic` | Runs the real vendored Mystic BBS multiplexer client (`mrc_client.py`, StackFault/Phenom Productions) as a subprocess against a synthetic Mystic-shaped directory, instead of opening our own socket. |

Either way, everything above the transport layer -- session tracking,
identify-gating, CTCP, DM routing, join/leave announcements, userlist --
is the exact same `BridgeApp` code and behaves identically. Switching
backends only changes how the raw bytes get to the hub.

## Native `umrc-client` support (one shared bridge, no separate `umrc-bridge`)

**Status: working, verified live against a real `umrc-client` build
(2026-09-21) -- but still on the `umrc-bridge-wip` branch, not yet
merged to `main` or shipped in a numbered release.** Everything below
reflects what was actually confirmed working during that testing
session, including several real bugs found and fixed along the way --
not a design document written in advance.

[uMRC](https://github.com/codefenix-dev/uMRC) -- **download and full
source at that link** -- is a separate, OpenDoors-based MRC door
(`umrc-client`), developed by **Codefenix** of **ConChaos BBS**, that
normally needs its own `umrc-bridge` multiplexer daemon running
alongside it. `mrc/bridge/main.py` can act as a **drop-in replacement
for `umrc-bridge`**, so a sysop running both ANetBBS's own MRC client
(terminal/web) and uMRC can point `umrc-client`'s bridge-host/
bridge-port config at ANetBBS's own bridge instead -- one shared
process serves both, with **zero changes to uMRC itself**. Session
state, room/DM routing, and every real wire packet are fully shared
with ANetBBS's own terminal/web clients -- a `umrc-client` caller and
an ANetBBS caller in the same room see each other and can send each
other private messages correctly, on either side.

### Setup, step by step

This has more moving parts than most ANetBBS features, because two
completely separate pieces of software (ANetBBS's own bridge and
uMRC's own binary, each with their own config) both need to agree on
the same host/port. Follow this in order.

**1. Enable the listener in `mrc/bridge/config.json`** (off by
default -- a second open listener is extra attack surface a sysop who
doesn't run uMRC doesn't need):

```json
"mrc_tcp_enabled": true,
"mrc_tcp_listen_host": "127.0.0.1",
"mrc_tcp_listen_port": 5010
```

**Leave `mrc_tcp_listen_host` as `127.0.0.1`.** This is the single
most common way to break this feature, confirmed live: it's tempting
to set it to the box's real LAN IP the same way you might set
`web_listen_host` for the browser-based MRC client to be reachable
from other machines. **Don't** -- `umrc-client`'s own connect call is
hardcoded in its C source to the literal string `"localhost"` (its
own "bridge host" config field is display-only, never actually used
to connect). A listener bound only to a LAN IP will never accept a
connection arriving on the loopback interface, and `umrc-client` will
just report "Unable to connect to bridge" with no further detail.
Only change this to `0.0.0.0` (which still includes loopback) if
`umrc-client` runs on a *different* machine than ANetBBS itself --
uncommon, since it's normally a door running locally under the same
BBS process. The bridge itself now warns in its log at startup if
this is set to anything that can't include loopback, specifically
because this mistake is so easy to make.

**2. Point `umrc-client`'s own setup at that port.** In the uMRC door's
own directory (wherever it's installed -- e.g. `/opt/anetbbs/doors/umrc/`
on a Linux install), run its setup utility directly (not through the
BBS):

```
cd /opt/anetbbs/doors/umrc
./setup
```

Find the local bridge port setting and set it to **5010** (or
whatever you chose for `mrc_tcp_listen_port` above) -- this is
`cfg.port` in uMRC's own source, a completely separate value from
anything in ANetBBS's config, and the two must match by hand. The
"bridge host" field in the same setup can be left at whatever default
it has -- as covered above, it's cosmetic only.

**3. Restart the bridge:**

```
sudo systemctl restart anetbbs-mrc-bridge
```

**4. Verify the listener actually started:**

```
sudo journalctl -u anetbbs-mrc-bridge -n 50 --no-pager | grep -i "tcp listener"
sudo ss -tlnp | grep 5010
```

You should see a log line like `MRC TCP listener (umrc-client
compatible) on 127.0.0.1:5010` and a `LISTEN` entry on that port. If
neither shows up, `mrc_tcp_enabled` likely isn't actually `true` in
the config the running service loaded, or the service didn't restart
cleanly -- check `sudo systemctl status anetbbs-mrc-bridge`.

At this point, launching `umrc-client` and choosing "Enter chat"
should work -- chat, DMs, and room changes are all fully live and
routed correctly against both ANetBBS's own clients and other real
BBSes on the network.

### Stats file (`mrcstats.dat`) -- optional, cosmetic only

`umrc-client`'s own main-menu screen reads a small local file at
startup to show BBSes/Rooms/Users/Activity counts and an
ONLINE/OFFLINE indicator, normally written by the standalone
`umrc-bridge` daemon this feature replaces. Without it, chat still
works completely normally -- this only affects that one cosmetic
display. To enable it:

**1. Find the exact filename `umrc-client` expects** -- this varies by
uMRC version and matters exactly on a Linux filesystem (unlike
DOS/Windows, case is significant):

```
strings /opt/anetbbs/doors/umrc/umrc-client | grep -i stats
```

(On the build tested 2026-09-21, this was lowercase `mrcstats.dat`.)

**2. Add the full path to `mrc/bridge/config.json`**, pointing at that
exact filename inside uMRC's own directory:

```json
"mrc_stats_file_path": "/opt/anetbbs/doors/umrc/mrcstats.dat"
```

**3. Restart the bridge** (`sudo systemctl restart anetbbs-mrc-bridge`)
and give it a couple of minutes.

**How the numbers get populated:** the bridge relays the real MRC
hub's own `STATS:` reply -- a genuine network-wide count (BBSes,
rooms, users, activity across the *whole* MRC network, not just this
BBS), the same data source the real reference `umrc-bridge` daemon
uses. This is requested automatically (`stats_refresh_interval_seconds`,
120s by default) **but only while at least one session is actually
joined to a room on THIS bridge** -- a quiet BBS with nobody
currently in MRC chat won't trigger it, and the file will just show
`0 0 0 0` until someone is. This tripped up testing more than once:
the MRC network itself always has activity somewhere, but that's
irrelevant if nobody's connected through *this specific* bridge right
now. Have someone (or yourself) actually join a chat room, wait ~2
minutes, then check:

```
cat /opt/anetbbs/doors/umrc/mrcstats.dat
```

A real, working file looks like `176 14 51 2` -- BBSes, rooms, users,
activity (0-3: none/low/medium/high), in that order. `umrc-client`
also treats the file as "bridge ONLINE" purely based on its own
modification time being under 60 seconds old, which is why this needs
to keep getting rewritten periodically rather than written once.

### Debug logging (wire-level troubleshooting)

If chat isn't connecting or the stats file stays empty and the steps
above don't explain why, a full raw-packet trace (every outbound send
and every inbound line, tagged `MRC RAW OUT`/`MRC RAW IN`) is the
fastest way to see exactly what's actually happening on the wire.
**This logs full chat content in plaintext** -- deliberately off by
default, meant to be temporary.

Set `"log_level": "DEBUG"` in `mrc/bridge/config.json` and restart --
this now actually works (a real, separate bug: this config key
existed and was documented for a long time but silently did nothing
at all, fixed alongside this feature). Alternatively, for a one-off
trace without touching the config file, use a systemd override:

```
sudo mkdir -p /etc/systemd/system/anetbbs-mrc-bridge.service.d
printf '[Service]\nEnvironment=MRC_BRIDGE_LOG_LEVEL=DEBUG\n' | sudo tee /etc/systemd/system/anetbbs-mrc-bridge.service.d/debug.conf
sudo systemctl daemon-reload
sudo systemctl restart anetbbs-mrc-bridge
```

Then: `sudo journalctl -u anetbbs-mrc-bridge --since "5 minutes ago" | grep -i "RAW OUT\|RAW IN"`.

**Always turn this back off once done** -- it stays on across restarts
otherwise, permanently logging every private message on the BBS:

```
# if you used the config.json key:
# set "log_level" back to "INFO" and restart

# if you used the systemd override:
sudo rm -rf /etc/systemd/system/anetbbs-mrc-bridge.service.d
sudo systemctl daemon-reload
sudo systemctl restart anetbbs-mrc-bridge
```

### Troubleshooting checklist

| Symptom | Likely cause |
| ------- | ------------ |
| `umrc-client` says "Unable to connect to bridge" | `mrc_tcp_enabled` isn't `true`, or the service wasn't restarted after enabling it -- check step 4 above. |
| Same, even with the listener confirmed running | `umrc-client`'s own `cfg.port` (set via its `./setup`) doesn't match `mrc_tcp_listen_port` -- these are two independent settings in two different pieces of software. |
| Chat connects, then immediately drops back to the main menu | `mrc_tcp_listen_host` is set to a LAN IP instead of `127.0.0.1`/`0.0.0.0` -- see step 1. |
| Stats screen shows blank/zero and never updates | `mrc_stats_file_path` isn't set, or no one is currently joined to a room on *this* bridge (see the stats section above) -- not necessarily a bug. |
| Stats show real-looking but oddly small/zero numbers (e.g. 0 BBSes) | Running an older build from before this feature was reworked to relay the hub's real `STATS:` reply -- redeploy the current code. |

### Known simplifications

A few uMRC-specific behaviors are intentionally simplified rather than
faithfully replicated: `umrc-client`'s own join/exit announcement text
(`/quit`'s exit message, the connect-time join message) isn't
forwarded -- the bridge's own configured `join_message`/`exit_message`
templates apply uniformly to every session regardless of transport,
same as they already do for ANetBBS's own clients. `/b` (uMRC's
network-wide broadcast, addressed to no particular room) is treated as
an ordinary current-room broadcast, since the rest of the bridge has
no broader concept of "everywhere" to route it to.

### Why the `mystic` backend exists

MRC's wire protocol (tilde-delimited packets, `IMALIVE`/`CAPABILITIES`/
`IDENTIFY`/etc.) is shared across every BBS platform that speaks it --
ANetBBS's own client and Mystic BBS's official one connect to the exact
same hubs. `mystic` mode runs Mystic's own, years-in-production
reference client verbatim rather than reimplementing its wire handling,
useful for isolating whether an issue is protocol-level (would also
affect the reference client) or specific to ANetBBS's own socket code.

The vendored file lives at `mrc/mystic_client/vendor/mrc_client.py`,
**unmodified** from the original release (see `PROVENANCE.md` next to
it) -- same convention as other vendored third-party code in this
project. It does its own IPC via files (outbound: numbered `.mrc` files
dropped into `data/mrc/`; inbound: numbered `.mrc` files written into
`temp/<room>/` for any room with a `tchat.inuse` marker present).
`mrc/bridge/mystic_connection.py` supervises it as a subprocess and
translates that file IPC to/from `BridgeApp`'s normal session model --
`mrc/mystic_client/fake_bbs.py` builds the synthetic directory tree
(`data/users.dat` stub, `mrc_config.py` generated from the same
`config.json` fields the native backend already uses) so the vendored
script believes it's running inside a real Mystic BBS install.

### No real Mystic install, no manual setup, no `.mps` compiling

The original release (`pn-mrc137-alpha.zip`) ships two genuinely
different pieces, documented in its own `installation.asc` as separate
steps:

1. **The multiplexer client** (`mrc_client.py` + `mrc_config.py`) --
   the standalone process that actually talks to the MRC hub over the
   network. This is the *only* piece ANetBBS uses.
2. **The Mystic MPL chat UI** (`scripts/mrc_client.mps`, the theme
   `.ini` files, the `text/mrc-*.ans` art) -- the in-BBS screen a
   *Mystic* caller sees when they enter the chatroom. This requires a
   real Mystic install, Mystic's own `mplc` compiler, and a `scripts/`+
   `text/` directory layout under Mystic's theme folder.

ANetBBS only ever needs piece 1. We already have our own chat UI (the
terminal `mrc_chat.py` client, the web client) -- piece 2 is entirely
Mystic-specific display code with nothing to plug into on our side, so
none of it is used, vendored, or documented here. A sysop enabling
`mrc_backend: "mystic"` does **not** need to install Mystic, create a
`scripts/`/`text/` directory anywhere, or run `mplc` -- there is no
manual setup step at all. `mrc/mystic_client/fake_bbs.py` builds
everything piece 1 needs (a `data/users.dat` stub, a generated
`mrc_config.py`, the `data/mrc/`+`temp/` directories) automatically,
from scratch, on every bridge startup -- the only sysop-facing action
is picking `mystic` at the `install.sh` prompt (or editing
`mrc_backend` in `config.json` on an existing install).

### Requirements for `mystic` mode

- `bridge_bbs` in `config.json` must be at least 5 characters after
  stripping `|NN` color codes -- the vendored client's own startup
  check refuses to run otherwise (`ensure_fake_bbs_tree` raises early
  with a clear error rather than letting it fail inside the subprocess).
- No separate systemd unit or install step needed -- the subprocess is
  spawned and supervised entirely from inside the existing
  `anetbbs-mrc-bridge` service.
- Graceful restart/stop sends the subprocess `SIGINT` (not `SIGTERM`),
  so its own graceful shutdown path runs (a `SHUTDOWN` notice to the
  hub, its pidfile cleanup) -- matches `MRCConnection.stop()`'s own
  best-effort `SHUTDOWN` packet before disconnecting.

## Mystic-inspired chrome themes (web) and full Mystic screen recreation (terminal)

Both the terminal and web MRC clients have theme pickers offering five
options named after the themes bundled in `pn-mrc137-alpha.zip`
(`original`, `minimal`, `bitchx`, `2leet4u`, `least`), alongside
ANetBBS's own pre-existing five (`default`/`green`, `amber`, `cyan`,
`mono`/`ibmblue`) -- unrelated to the `mrc_backend` connection choice
above, available regardless of which backend is active.

**Terminal** (`/set palette <name>`, e.g. `/set palette bitchx`, saved
per-handle server-side via the same set_prefs/BridgeDB profile
round-trip as prefix/suffix/ticker/clockformat/etc -- restored
automatically on your next connect): the five Mystic-named palettes
trigger a
full recreation of the real Mystic MRC screen, not just a color swap.
`mrc/mystic_client/vendor/text/mrc-*.ans` (the actual bundled border
art) and `mrc/mystic_client/vendor/scripts/mrctheme-*.ini` (the actual
bundled element coordinates -- room/topic/nick-list/latency/clock/
input positions) are vendored unmodified and parsed by
`theme_layout.py`; `mrc_chat.py`'s renderer draws that real border art
as a static frame and positions every dynamic element (chat text, nick
strip, room/topic, latency, clock, input line, chatters count, char-
count-remaining buffer) at the theme's own declared coordinates
instead of ANetBBS's own generated layout. Elements the theme doesn't
define (BBSES/ROOMS/ACTIVITY/HEARTBEAT -- genuine hub-wide stats) are
skipped rather than faked, still true as of this writing though no
longer for lack of data: the bridge now parses the hub's real `STATS:`
reply for the umrc-client stats-file feature above
(`BridgeApp._last_umrc_stats`) -- wiring that same value into this
theme rendering path instead of skipping these elements would be a
reasonable small follow-up, just not done yet. Falls back to ANetBBS's own generated
layout if a theme file is missing or malformed. The other 5 palettes
(`default`/`green`/`amber`/`cyan`/`mono`) keep the original light
chrome-color-only behavior -- `_TERM_PALETTES` in
`anetbbs/features/mrc_chat.py`.

**Web** (theme dropdown in Settings, saved to `localStorage`): still a
CSS custom-property color swap only (`body.theme-original`,
`.theme-minimal`, `.theme-bitchx`, `.theme-2leet4u`, `.theme-least` in
`anetbbs/templates/mrc/index.html`) -- the terminal's full border-art
recreation has no web equivalent yet, since the web client's HTML/CSS
layout has no analog to fixed-position ANSI art placement.

### Choosing a backend at install time

`install.sh` asks "MRC connection backend: 'native' ... or 'mystic'
..." right after the "Install MRC bridge service?" prompt, and writes
the choice into the generated `config.json`'s `mrc_backend` field. To
switch later on an existing install, edit `mrc/bridge/config.json`
directly and restart `anetbbs-mrc-bridge`.
