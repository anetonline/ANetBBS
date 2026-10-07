# fsxNet IBOL / IBLC (real InterBBS Oneliners + Last Callers)

Two optional features that speak the *real*, externally-defined
Synchronet mod protocols used on [fsxNet](https://fsxnet.nz) (zone 21)
to exchange data over its shared `FSX_DAT` echo area:

- **IBOL (InterBBS Oneliners)** — post a short message and it gets
  broadcast to every other BBS on the network; everyone's posts merge
  into one shared wall you can browse and add to.
- **IBLC (InterBBS Last Callers)** — every login gets broadcast to the
  network, and you see a feed of everyone else's logins plus a
  directory of known BBSes (calls count, last call, OS, telnet
  address) you can connect straight to.

This is **not** the same thing as ANetBBS's own InterBBS Wall/Last
Callers (Admin → InterBBS Graffiti Wall / InterBBS Last Callers) —
that feature is a private format only another ANetBBS install
understands. IBOL/IBLC speak the real wire format other BBS software
(Synchronet, Mystic) on fsxNet already reads and writes, so posts and
logins round-trip with the wider network, not just other ANetBBS
installs. The two features are fully independent — you can run either,
both, or neither, and local data never mixes between them.

## Credits

IBOL and IBLC for Synchronet are real, actively-maintained mods — this
ANetBBS port implements the same wire format by reading their actual
source, not a reinvented approximation:

- **Craig Hendricks** (codefenix@conchaos.synchro.net, ConstructiveChaos
  BBS) — current author/maintainer of both `iblc.js` and `ibol.js`.
- **Andrew Pamment** (apam, Talisman BBS) — original author of IBOL.

Thanks to both for building and maintaining the real reference
implementations this port's wire format was verified against.

## Prerequisites

Membership in fsxNet (or a compatible network) with the `FSX_DAT`
InterBBS Data echo area configured — see fsxNet's own infopack for how
to join. Set this up the normal way, same as any other echomail
network:

1. **Admin → Echomail Networks** — add fsxNet as a BinkP network with
   your real assigned zone-21 address, hub address, and password.
2. **Add an area** with tag `FSX_DAT` (or whatever your hub calls it)
   on that network and subscribe to it.

ANetBBS does not invent or guess any of this — you configure it with
your own real fsxNet credentials, exactly like joining any other
network.

## Enabling IBOL / IBLC

**Admin → Messages → fsxNet IBOL/IBLC** (`/admin/fsxnet/`):

- Enable IBOL and/or IBLC independently.
- Pick the network you configured fsxNet on.
- **Area tag** — must match your `EchoArea`'s internal tag for FSX_DAT
  (default `FSX_DAT`; change it if your hub uses a different internal
  code, mirroring how the real `iblc.ini`/`ibol.ini` let a sysop set
  `messageBase`).
- **System name override** — how your BBS is identified to other
  systems; blank uses your normal BBS name.
- **Telnet port** — only needed if you don't use the standard port 23;
  this is what gets posted so other BBSes can connect back to you.
- **Hide sysop logins from IBLC** — same idea as the existing "Hide
  Sysop" setting for the local Last Callers list, scoped to this
  feature.

Enabling either toggle automatically creates the `FSX_DAT` area (if
it doesn't exist yet) and a recurring background job that imports new
network traffic every 15 minutes.

## Verifying it's working

Both features depend on your fsxNet network actually exchanging mail
with your hub — ANetBBS can't fake or shortcut that part, so it's
worth confirming the mechanics are flowing before assuming anything is
broken:

- **Admin → Echomail → your fsxNet network's Poll Log** is the ground
  truth. Each entry shows `messages_sent`/`messages_received` for that
  poll, with a transcript link to the raw BinkP session if you want to
  see exactly what was exchanged.
- **A post only counts as sent once the hub ACKs it** — ANetBBS only
  marks an outbound message `sent` after the hub actually accepts the
  transfer, never speculatively. If a poll shows `messages_sent > 0`
  after you post or log in, your content left your BBS successfully;
  getting it from there to the rest of the network is your hub's job,
  not something ANetBBS can control or verify further.
- **IBOL (Oneliners) is naturally low-traffic.** Unlike IBLC, which
  broadcasts automatically on every login, a oneliner only gets posted
  when a user explicitly chooses to add one — seeing just your own
  post and a couple of others on a quiet network is completely normal,
  not a sign anything's stuck.
- **IBLC (Last Callers) should fill up quickly** on any network with
  real traffic. If it stays sparse despite a healthy poll log, double-
  check your **Area Tag** setting actually matches your hub's real
  FSX_DAT tag.

## Using it

- **As a BBS menu item**: a sysop adds `fsxnet_ibol` and/or
  `fsxnet_iblc` as action types on a menu item (Admin → BBS Menus),
  the same way Wall and Last Callers already work as menu items.
- **As a Logon/Logoff Module**: `fsxnet_ibol`/`fsxnet_iblc` are also
  real module types at Admin → Logon/Logoff Modules (or the
  `anetbbs-cfg` terminal tool's Login Modules section) — pick either
  to show the screen automatically on every login, the same way the
  existing Graffiti Wall and Last Callers login modules do. **This is
  display only** — whether or not you add either as a login module,
  and regardless of a user choosing Fast Logon, every login is still
  recorded and posted to fsxNet IBLC unconditionally, via the same
  always-runs hook that already writes the local Caller Log row (same
  precedent as the existing Last Callers InterBBS relay).
- **IBOL**: shows the shared wall with pipe-color support, and a
  `(V)` View All mode that scrolls through the full history (arrow
  keys, PgUp/PgDn, Home/End). `(A)` posts up to 10 lines of your own.
- **IBLC**: shows the last-caller feed — press `M` to toggle between a
  Date/Time/Location view and a Telnet-Address/OS view. Press `B` for
  the BBS directory: a real scrollable, selectable list (arrow keys to
  move, Enter to connect, `S` to cycle sort by name/calls/last-call),
  reusing the same SSRF-guarded dial-out gateway the "Dial Out" menu
  item already uses. Press `A` on either screen for an About page with
  full credits.
- **Admin**: `/admin/fsxnet/` lists everything imported, with a
  per-row Delete for moderation.

## Design notes

Unlike ANetBBS's own InterBBS Wall/Last Callers — which relay what
they receive back out (and need an explicit loop-prevention check
because of it) — IBOL/IBLC are not relay protocols. You post your own
content exactly once (on explicit action for IBOL, automatically on
login for IBLC); the ordinary FTN echomail transport propagates it to
the rest of the network the same way any other echo area works.
ANetBBS's side only ever *reads* `FSX_DAT` to build a local display
cache — it never writes back anything it reads.

The real IBLC script deliberately does not trust the date/time strings
embedded in another BBS's own post (no timezone/locale normalization)
— ANetBBS does the same thing, showing those fields as plain display
text while sorting and filtering by the message's own arrival time.
