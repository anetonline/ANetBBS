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

## Mystic-inspired chrome themes

Both the terminal and web MRC clients have cosmetic theme pickers for
the chrome (borders, sidebar, status bar) -- unrelated to the
`mrc_backend` connection choice above, available regardless of which
backend is active. Five of the options are named after (and
color-inspired by) the five themes bundled in `pn-mrc137-alpha.zip`
(`original`, `minimal`, `bitchx`, `2leet4u`, `least`), alongside
ANetBBS's own pre-existing five (`default`/`green`, `amber`, `cyan`,
`mono`/`ibmblue`).

These are **not** a port of that package's actual `.ans` art -- Mystic's
theme system composites a static full-screen background image with
UI elements placed at fixed X/Y coordinates from an `.ini` file, a
rendering model with no equivalent in either of ANetBBS's own MRC
clients (both use a simpler fixed-region layout: status bar, optional
sidebar, ticker, scroll area). The five Mystic-named options give a
matching color identity in that same spirit, not a pixel copy.

- Terminal: `/set palette <name>` (e.g. `/set palette bitchx`) --
  local to the session, not saved. `_TERM_PALETTES` in
  `anetbbs/features/mrc_chat.py`.
- Web: the theme dropdown in Settings, saved to `localStorage`. CSS
  custom properties in `anetbbs/templates/mrc/index.html`
  (`body.theme-original`, `.theme-minimal`, `.theme-bitchx`,
  `.theme-2leet4u`, `.theme-least`).

### Choosing a backend at install time

`install.sh` asks "MRC connection backend: 'native' ... or 'mystic'
..." right after the "Install MRC bridge service?" prompt, and writes
the choice into the generated `config.json`'s `mrc_backend` field. To
switch later on an existing install, edit `mrc/bridge/config.json`
directly and restart `anetbbs-mrc-bridge`.
