# ANetBBS v1.0a2.72 — Fix: login fails with CSRF error on HTTP-only installs

`ProductionConfig` hardcoded `SESSION_COOKIE_SECURE = True`. When a sysop runs
ANetBBS without nginx/TLS (direct HTTP access on port 5000), the browser never
sends the session cookie back because it's flagged Secure-only. Flask-WTF finds
no CSRF token in the session and rejects every login POST with a 400 Bad Request.

Fix: `SESSION_COOKIE_SECURE` is now controlled by the `.env` variable of the same
name (default `false`). New installs set it to `true` only when `ENABLE_SSL=y`
was chosen during `install.sh`. Existing installs without the key in `.env`
automatically get `false` via the env-var default — no manual `.env` edit needed
after upgrading.

**Immediate workaround for v1.0a2.71 installs already affected:**
Add `SESSION_COOKIE_SECURE=false` to `.env` and restart:

```bash
echo "SESSION_COOKIE_SECURE=false" | sudo tee -a /home/stingray/anetbbs/.env
sudo systemctl restart anetbbs-web
```

Installs behind nginx+TLS should set `SESSION_COOKIE_SECURE=true` in `.env`.
