# ANetBBS v1.1.75 — security and reliability maintenance release

Security maintenance release from a full audit after v1.1.73: hardening
across installation and upgrades, the web interface, accounts and
sessions, file transfers, echomail and netmail, Internet Mail, MRC chat,
the Docker deployment, and outbound connections. All sysops are encouraged
to update promptly. (v1.1.74 was an internal build that was never
published; v1.1.75 replaces it.)

Fixes and improvements:

- **Hardened updates.** Further hardening of the install, update and
  web-upgrade paths, from a security audit.
- **Signed, forward-only web upgrades.** Releases are signed with the
  project's release key, which updating installs as a trusted key, and
  the web upgrade button now requires that signature and only installs
  newer releases. Reinstalls and downgrades are done as root with
  `sudo bash update.sh`. Web upgrades keep working when a feed
  advertises `http://` links for its `https://` address, and for a hub
  whose `REGISTRY_URL` points at itself on `localhost`. See
  docs/02-sysop-daily-ops.md.
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
- **Admin → Backups → Restore DB** now completes and brings the BBS back:
  every service stops for the swap, the backup and the result are
  integrity-checked, the previous database is kept beside it (and put back
  if anything fails), and the Backups page shows the outcome.
- **Add-on downloads** accept an optional SHA-256 pin.

Upgrade notes:

- **Run `update.sh` from a root-owned copy of the release** (for example
  extracted under `/root`). It now refuses to run from a directory the BBS
  service account can modify, such as the install directory.
- **Private release feeds with unsigned releases** stop installing from
  the web until the operator allows them once:
  `sudo ANETBBS_REQUIRE_SIGNED_UPGRADES=no ANETBBS_REGISTRY_HOST=<host> bash update.sh`.
  Feeds that redistribute official releases with their `.sig` need nothing.
- **The web page no longer offers Roll back.** Downgrade as root by running
  the older release's `update.sh`.
- **Re-running `install.sh` over an existing installation** is refused;
  use `update.sh`.
- **Release archives no longer include door-game trees (`doors/`) or the
  DOOM / Duke Nukem 3D ZIPs.** Existing installs keep theirs; updating
  never removes them. New installs can build the ZIPs with
  `tools/prepare_dos_games.py` (see docs/14-door-games.md).
- **Docker (compose):** use the `docker-compose.yml` from this release,
  which adds a small `control` service, then run `docker compose up -d`.
  See docs/22-containers.md.
- **Every process must share one `SECRET_KEY`** (web, terminal server and
  MRC bridge). That is already the case when they read the same `.env`.
- Changing your email address or security questions now asks for your
  current password.
- Existing web sessions end once when an account's password changes or
  the account is locked.
