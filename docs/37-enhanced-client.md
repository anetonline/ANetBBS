# Enhanced Client (optional add-on, browser Canvas + WebSocket terminal)

**Status: test version.** Shaken out across multiple real Pi3 test
sessions (font legibility, menu-overlay bugs, canvas sizing all found
and fixed live), but still actively evolving. Not part of any numbered
ANetBBS release yet.

A browser-based, mouse-driven front door to ANetBBS -- real clickable
menus, color ANSI art rendered pixel-perfect on a `<canvas>`, and a
bolder built-in font, running *alongside* every existing client
(telnet, SSH, rlogin, PETSCII, SyncTerm, NetRunner, MagiTerm) rather
than replacing any of them. A caller connecting through it sees the
exact same BBS, menus, boards, doors, and chat as everyone else --
`BbsSession`'s `term_mode == 'enhanced'` is just one more terminal
mode alongside `ansi`/`ascii`/`petscii`/`wide`, in the same place
those are already handled.

This is a different thing from the existing plain browser ANSI
terminal at `/terminal/` (served by the `anetbbs-web` app, a classic
text-stream xterm.js-style view with no mouse-driven menus) -- that
stays exactly as it is; the Enhanced Client is a new, separate,
richer option next to it, not a replacement.

## It's a separate, optional download -- not part of the stock release

Unlike most ANetBBS features, this does **not** ship inside the normal
release tarball `build-release.sh` produces. A sysop who wants it
fetches a small add-on package and applies it on top of an existing
install; everyone else's stock ANetBBS never carries any of these
files at all. Same reasoning as keeping the RDQ3/ANetCHESS door games
as their own separate projects rather than bundling their release
archives into ANetBBS's own distribution.

**What's in the add-on** (built by
`tools/build_enhanced_client_addon.sh`, producing
`/tmp/ANetBBS-EnhancedClient-addon-<version>.tar.gz`):

- `anetbbs/core/enhanced_server.py` -- the WebSocket listener.
- `anetbbs/features/enhanced_protocol.py` -- the JSON message protocol
  between server and client.
- `anetbbs/enhanced_client/` -- the client itself: a single static
  `index.html`, no build step, no framework. Its own
  `anetbbs/enhanced_client/README.md` has the sysop-facing install
  walkthrough.
- `anetbbs/static/fonts/Flexi_IBM_VGA_False.ttf` / `.woff` -- the
  client's embedded font (see "Font" below).

**What's NOT in the add-on, because it can't be cleanly separated:**
the small `if self.term_mode == 'enhanced':` branches threaded through
`anetbbs/core/session.py`, `anetbbs/features/menu_engine.py`,
`anetbbs/features/games.py`, `anetbbs/main.py`, and
`anetbbs/config.py` (`ENHANCED_ENABLED`/`ENHANCED_HOST`/
`ENHANCED_PORT`) -- these are the same files every other protocol's
own term_mode dispatch already lives in, so there's no way to ship
"session.py minus the enhanced branches" without maintaining a second,
diverging copy of that file. They ship with every stock ANetBBS
install already, same as PETSCII's own always-present-but-disabled-
by-default support -- completely inert unless **both**
`ENHANCED_ENABLED=true` is set **and** the add-on's files are actually
present. If a sysop sets that flag without having fetched the add-on,
`anetbbs.service` logs a clear warning and skips just that one
listener; telnet/SSH/rlogin/PETSCII/FTP all start normally regardless.

## Installing

1. Build or obtain `ANetBBS-EnhancedClient-addon-<version>.tar.gz`.
2. Extract it over an existing ANetBBS install, same workflow as any
   other update:
   ```
   tar -xzf ANetBBS-EnhancedClient-addon-<version>.tar.gz --strip-components=1 -C /path/to/anetbbs
   ```
3. Set `ENHANCED_ENABLED=true` in your config (defaults:
   `ENHANCED_HOST=0.0.0.0`, `ENHANCED_PORT=6402` -- override either if
   that doesn't fit).
4. Restart `anetbbs.service`.
5. Open `http://<your-host>:6402/` in a browser.

### Uninstalling / reverting to stock

Set `ENHANCED_ENABLED=false` and restart. The add-on's files can stay
in place (inert) or be deleted -- neither affects any other client.

## Architecture

Runs inside the **same asyncio event loop** as telnet/SSH/rlogin/
PETSCII (`anetbbs.service`), not inside the separate `anetbbs-web`
app's eventlet-monkey-patched process -- running real asyncio
`BBSSession` objects inside an eventlet process would be a real
cross-event-loop risk for no benefit. The WebSocket transport is
built on aiohttp (already a hard dependency for the MRC bridge's own
browser-facing WebSocket path), so no new dependency is introduced.

A thin reader/writer-shaped adapter (`_WSReaderAdapter`/
`_WSWriterAdapter` in `enhanced_server.py`) bridges aiohttp's
message-oriented WebSocket API into the same byte-stream
`read(n)`/`write(data)` + stored-exception contract every other
`BBSSession` transport already satisfies -- `BBSSession` itself needed
zero changes to its I/O model to support a fourth transport.

Output is a small JSON protocol (`enhanced_protocol.py`): `screen`
messages carry a cursor-positioned grid of styled text runs (the same
persistent virtual-screen buffer every `write()` call in enhanced mode
feeds, so ANSI cursor-positioned partial redraws compose correctly
across separate calls, not just appended as flat scrolling text);
`menu` messages carry a structured button list
(`{hotkey, label, send}`) for auto-generated clickable menus -- a
mouse click is synthesized server-side into the exact keystroke(s) a
typed choice would have produced, so menu dispatch logic elsewhere in
the codebase needs no awareness that a WebSocket is involved at all.

CP437 Block Elements (`█▀▄▌▐░▒▓`) are drawn as direct canvas geometric
fills, never through the font -- a font glyph's ink doesn't reliably
fill its cell edge-to-edge regardless of font choice, confirmed live
via a side-by-side SyncTerm comparison. Every other glyph (box-drawing,
text) renders through the embedded font normally.

## Font

`Flexi_IBM_VGA_False.ttf`/`.woff` -- a real scalable TrueType IBM VGA
face from int10h.org's Ultimate Oldschool PC Font Pack, CC BY-SA 4.0,
(c) VileR / Screwtape. Chosen over the bitmap-derived `Ac437_IBM_VGA`
font used elsewhere in this project (web ANSI editor PNG export) after
a live comparison against NetRunner's own bundled font turned up the
bitmap font looking thin/blurry at this client's render size -- a
real vector face stays crisp. "False" (square-pixel, matching this
project's existing 9:16 cell convention) and the unmarked/extended-
Unicode-charset variant (matching how this project already decodes
CP437 bytes to real Unicode codepoints rather than raw CP437
byte-position glyph mapping) specifically -- see
`anetbbs/static/fonts/LICENSE.txt` for the full attribution and the
reasoning behind those two variant choices.

## Known limitations / deferred

- RIP-style sprite/image graphics, half-block art rendering beyond the
  geometric block-fill handling above, and custom per-user
  fonts/themes in the client are not implemented.
- Mouse support is limited to clicking auto-generated menu buttons --
  no drag-select, no right-click context menus.
- No packaged/installable client -- it stays a URL a caller opens, not
  a downloadable app.
