# ANetBBS Enhanced Client (optional add-on)

A browser-based, mouse-driven terminal client for ANetBBS -- a
Canvas+WebSocket front door with real clickable menus, color ANSI art,
and a bolder built-in font, alongside (not replacing) telnet, SSH,
rlogin, PETSCII, and every other existing client. It is a separate,
optional add-on, not part of the stock ANetBBS release: a sysop who
wants it fetches and applies this package themselves; everyone else's
install never carries these files at all.

**Status: test version.** This has been shaken out on real hardware but
is still actively evolving -- expect it to change between ANetBBS
versions. See `docs/37-enhanced-client.md` in the main ANetBBS repo for
the full writeup (architecture, protocol, font licensing, known
limitations) -- this file covers add-on installation only.

## What this add-on contains

- `anetbbs/core/enhanced_server.py` -- the WebSocket listener.
- `anetbbs/features/enhanced_protocol.py` -- the JSON message
  encode/decode layer between server and client.
- `anetbbs/enhanced_client/` -- the client itself (this directory): a
  single static `index.html`, no build step, no framework.
- `anetbbs/static/fonts/Flexi_IBM_VGA_False.ttf` /
  `Flexi_IBM_VGA_False.woff` -- the client's embedded font (see
  `anetbbs/static/fonts/LICENSE.txt` for attribution).

The small `if self.term_mode == 'enhanced':` branches that make these
files actually reach a live session live in ANetBBS's own core files
(`session.py`, `menu_engine.py`, `games.py`, `main.py`, `config.py`)
and ship with every stock ANetBBS install already, the same way
PETSCII's own always-present-but-disabled-by-default support does --
completely inert until both `ENHANCED_ENABLED=true` is set AND this
add-on's files are actually present.

## Installing

1. Download/build the add-on tarball (`tools/build_enhanced_client_addon.sh`
   produces `/tmp/ANetBBS-EnhancedClient-addon-<version>.tar.gz`).
2. Extract it over an existing ANetBBS install, same as any other
   update:
   ```
   tar -xzf ANetBBS-EnhancedClient-addon-<version>.tar.gz --strip-components=1 -C /path/to/anetbbs
   ```
3. Set `ENHANCED_ENABLED=true` in your config (defaults: `ENHANCED_HOST=0.0.0.0`,
   `ENHANCED_PORT=6402` -- override either if that doesn't fit your setup).
4. Restart `anetbbs.service`.
5. Open `http://<your-host>:6402/` in a browser -- the Enhanced Client
   has its own small built-in web server (same `anetbbs.service`
   process as telnet/SSH/rlogin; no change needed to the separate
   `anetbbs-web` app) that serves the page at `/` and the live
   connection at `/ws` on that port.

If `ENHANCED_ENABLED=true` is set without this add-on's files present,
`anetbbs.service` logs a warning and skips just that listener --
telnet/SSH/rlogin/PETSCII/FTP all start normally regardless.

## Uninstalling / going back to stock

Set `ENHANCED_ENABLED=false` and restart. The add-on's files can be
left in place (inert) or deleted; neither affects any other client.

## License

The bundled font (`Flexi_IBM_VGA_False.ttf`/`.woff`) is from int10h.org's
Ultimate Oldschool PC Font Pack, CC BY-SA 4.0, (c) VileR / Screwtape --
see `anetbbs/static/fonts/LICENSE.txt` for the full attribution.
