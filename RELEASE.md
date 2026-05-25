# ANetBBS v1.0a2.78 — Fix: web MRC full UI

The web MRC chat page was rendering a stripped-down stub template instead of
the real full-featured interface that runs on bbs.a-net.fyi. Users could
connect (after v1.0a2.77 fixes) but the UI was missing themes, the user
sidebar, macros, mentions panel, reconnection, pipe color rendering, server
selection, and mobile layout.

## What's fixed

Replaced `anetbbs/templates/mrc/index.html` with the real `mrc/web/index.html`
with three minimal Flask/Jinja2 injections:

- `client.js` loaded via Flask `url_for('static', ...)` instead of hardcoded path
- `window.RETURN_TO_BBS_URL` set to the BBS home URL for the Return to BBS link
- Handle input pre-filled with the logged-in user's username

`anetbbs/static/mrc/client.js` is also the production version with
reconnection, pipe color rendering, and auto-rejoin.
