# ANetBBS v1.0a2.79 — Fix: nginx immutable caching + MRC-optional update + web update completion

## What's fixed

### nginx `Cache-Control: immutable` causing permanent 404 cache

The nginx `location /static/` block shipped with `Cache-Control: public,
immutable`. The `immutable` directive tells browsers the resource at that
URL will **never** change — browsers won't re-fetch it even on a forced
refresh. When users visited the MRC web page before `anetbbs/static/mrc/`
existed (pre-v1.0a2.76), nginx returned 404 for `client.js` *with* the
immutable header attached. Browsers cached that 404 and refused to fetch
the real file even after upgrading.

Fixed in:
- `deploy/anetbbs-nginx.conf.template` — `immutable` removed,
  `expires 7d` removed, replaced with `max-age=86400` (1 day)
- `install.sh` nginx heredoc — same change

`update.sh` now auto-patches the running nginx config if it has either the
`immutable` Cache-Control or the old MRC bridge `/mrcws` proxy path, then
reloads nginx.

### `install.sh`: MRC proxy path and auth_request

Fresh installs written by `install.sh` now match the production template:
- MRC WebSocket `proxy_pass` changed from `/mrcws` to `/ws` (bridge
  only listens on `/ws`)
- MRC `auth_request /mrc-auth-check` block added — unauthenticated
  browsers can no longer open the MRC WebSocket even if they know the URL
- `client_max_body_size 110m` added (was missing from fresh-install
  config; nginx's 1m default rejected avatar uploads and large file posts)

### `update.sh`: don't force-restart MRC bridge (or other optional services)

On servers that don't use MRC (or where the bridge was stopped/failed),
`update.sh` was auto-installing `anetbbs-mrc-bridge.service` and trying
to start it. The failed start created loud errors in the update log, and
if the start timed out, it could delay the web service health check enough
to trigger a false-positive rollback.

`update.sh` now records which optional services (`anetbbs-mrc-bridge`,
`anetbbs-finger`) were **actually running before the update started**, and
only restarts those. Services that were already stopped or failed are
skipped with a "start it manually if needed" message.

`systemctl reset-failed` is also called before each service restart so
that start-limit-hit services can come back cleanly.

### Web update UI: completion detection fixed

The "Check for Updates" web UI was polling forever after a successful
upgrade because the `exit N` completion marker was written by a Python
daemon thread that gets killed when gunicorn restarts mid-update (Step 3
of `update.sh`). After the new gunicorn came up, the UI saw `running=false`
but no `exit N`, so it kept polling for 30 minutes and then displayed
"gave up polling."

The UI now also accepts `[upgrade] upgrade to X complete` (written by
`run_upgrade.sh` itself) as a terminal condition, which IS present in the
log even when the Python thread was killed.

## Immediate fix for users with cached 404s (from pre-v1.0a2.76)

If you upgraded to v1.0a2.78 and still see "MRCClient is not loaded":
- **Chrome/Edge**: Ctrl+Shift+R (hard refresh)
- **Firefox**: Ctrl+Shift+R or hold Shift and click Reload
- **Or**: open the `/mrc/` page in a private/incognito window

After this update, `update.sh` also reloads nginx with the patched config
which removes the `immutable` header — future upgrades won't hit this again.
