# ANetBBS v1.1.74 — security and reliability maintenance release

Security maintenance release from a full audit after v1.1.73: hardening
across installation and upgrades, the web interface, accounts and
sessions, file transfers, echomail and netmail, Internet Mail, MRC chat,
the Docker deployment, and outbound connections. All sysops are encouraged
to update promptly.

Fixes and improvements:

- **Web auto-update keeps working for private release feeds.** Your
  first `update.sh` run after this version carries your release-feed host
  over automatically.
- **Signed releases.** Releases can carry an OpenSSH signature, and an
  install that trusts the signing key accepts a signed release from any
  https mirror. See docs/02-sysop-daily-ops.md.
- **Git-clone installs** now follow an upstream history rewrite safely,
  instead of silently stopping updates. Local changes are never
  overwritten.
- **MUD:** `say`, `yell`, `tell` and `emote` now reach other players.
  Before, they were recorded but never delivered.
- **Log out everywhere** is in the user menu.
- **Internet Mail:** mail for an account deleted mid-delivery now fails
  permanently, so the sender doesn't retry for days.
- **Deleted private messages** stay hidden from the side that deleted
  them, including in conversation view and quoting.
- **Feeds** skip inactive boards and expired bulletins.
- **Add-on downloads** accept an optional SHA-256 pin.

Upgrade notes:

- **Docker (compose):** use the `docker-compose.yml` from this release,
  which adds a small `control` service, then run `docker compose up -d`.
  See docs/22-containers.md.
- **Every process must share one `SECRET_KEY`** (web, terminal server and
  MRC bridge). That is already the case when they read the same `.env`.
- Changing your email address or security questions now asks for your
  current password.
- Existing web sessions end once when an account's password changes or
  the account is locked.
