# ANetBBS v1.0a2.76 — Fix: web MRC chat "MRCClient is not defined"

Every user opening `/mrc/` got `ReferenceError: MRCClient is not defined`
because `anetbbs/static/mrc/client.js` was never created. Web MRC chat was
completely non-functional for all users.

## What's fixed

Added `anetbbs/static/mrc/client.js` — the browser WebSocket client class
that the `/mrc/` page template expects. The class handles:

- WebSocket connection to the MRC bridge (`connect()`)
- Room join (`joinRoom(handle, room)`)
- Sending chat messages and slash commands
- Leaving a room cleanly
- 30-second keepalive ping to keep the bridge connection alive
- Tracking room/handle state from bridge events

Web MRC chat now works end-to-end.
