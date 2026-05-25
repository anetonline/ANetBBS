# ANetBBS v1.0a2.77 — Fix: web MRC WebSocket connection error on HTTPS installs

Web MRC chat worked for the sysop but failed for all regular users on HTTPS
installs with a WebSocket connection error. Two bugs found and fixed.

## What's fixed

**`anetbbs/web/mrc_web.py`** — WebSocket URL was always `ws://` (insecure)
because Flask sees plain HTTP from nginx and `request.is_secure` is always
False behind a proxy. Now checks `X-Forwarded-Proto: https` header so HTTPS
installs correctly generate `wss://` URLs.

**`mrc/bridge/main.py`** — The bridge only registered its WebSocket handler at
`/ws`. Nginx proxies `/mrcws` to the bridge, but the bridge returned 404 for
that path. Added `/mrcws` as an alias route so both paths work.

**`deploy/anetbbs-nginx.conf.template`** — Corrected the proxy_pass target
from `http://127.0.0.1:8080/mrcws` to `http://127.0.0.1:8080/ws` for new
installs.

## Upgrading

Run `update.sh` as usual. If you already have nginx configured from the
template, update `/mrcws` proxy_pass to point to `/ws` on port 8080, then
reload nginx:

```bash
sudo sed -i 's|proxy_pass.*8080/mrcws|proxy_pass http://127.0.0.1:8080/ws|' \
    /etc/nginx/sites-available/anetbbs
sudo nginx -t && sudo systemctl reload nginx
```
