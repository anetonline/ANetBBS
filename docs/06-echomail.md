# Echomail (FidoNet + Dove-Net)

Echomail = old-school message networks shared across BBSes.

## Two transports

- **BinkP** — the FidoNet TCP/IP standard. ANetBBS speaks BinkP
  natively (poll out, accept inbound).
- **QWK** — packet-based, popular for Dove-Net. ANetBBS can
  download / upload QWK packets over HTTP.

## Setting up a network

**Admin → Echomail Networks** (`/admin/echomail/`):

1. **Add Network** — pick BinkP or QWK, give it a name, set your
   FTN address (e.g. `1:142/0`), uplink address, password.
2. **Add Areas** to the network — each `EchoArea` has a tag like
   `FIDO.GENERAL`, a name, optional moderator address.
3. **Subscribe** to areas you want to read.

## Per-user QWK offline reader

Anyone can grab their unread echomail as a `.QWK` packet, read it
offline in MultiMail / BlueWave / OLX, then upload a `.REP` packet
with replies.

URL: `/qwk/`. Download builds a fresh packet on demand. Upload
extracts MESSAGES.DAT and posts each reply as outbound echomail in
the matching area.

## Per-board ↔ per-area

Boards and echomail areas are intentionally separate concepts:

- **Boards** — local discussion, threaded, with reactions.
- **Echomail areas** — networked message bases, FidoNet-style with
  origin/path lines, propagated to peers.

You can post to either independently.

## Polling

The `anetbbs-web` process spins up a background poller that:

1. Reads `EchomailNetwork` rows.
2. For BinkP: polls each uplink at interval, exchanges packets.
3. For QWK: fetches new `.QWK` URLs on schedule.

Poll status visible at **Admin → Echomail Networks → Polls**.
