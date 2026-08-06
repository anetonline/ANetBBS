# Vendored file: mrc_client.py

Source: `pn-mrc137-alpha.zip`, "Multi Relay Chat" client v1.3.7 Alpha,
author StackFault / Phenom Productions (<https://www.phenomprod.com>),
release date 2024-09-01. Reference implementation written for Mystic
BBS, distributed for use against the real MRC ("Multi Relay Chat")
network that ANetBBS's own `mrc/bridge/` also connects to -- both speak
the identical tilde-delimited wire protocol (confirmed by diffing this
file's packet handling against `mrc/bridge/mrc_protocol.py`).

**This file is unmodified from the original release** -- same
convention as other vendored third-party code in this project (e.g.
`anetbbs/games/sbbs_doors/`). Do not edit it directly; if a real bug
needs a workaround, patch around it in `mrc/bridge/mystic_connection.py`
instead (the layer that supervises this script as a subprocess and
translates its file-based IPC to/from ANetBBS's own bridge session
model), the same way third-party door bugs are worked around in the
Node compat shim rather than by hand-editing vendored door JS.

Used as an alternate connection backend for `mrc/bridge/main.py`
(`mrc_backend: "mystic"` in `mrc/bridge/config.json`) -- run as a real
subprocess against a synthetic Mystic-shaped directory tree (see
`fake_bbs.py`), instead of ANetBBS's own hand-rolled socket client
(`MRCConnection` in `main.py`). ANetBBS's own bridge session/room/
identify/CTCP logic is unchanged either way; only the lowest-level
upstream transport differs.
