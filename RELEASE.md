# ANetBBS v1.1.68 — Password-change session revocation, consistent backups, Spanish localization (November 2026)

A password change now revokes every other active session for that account at once, across every front-end: web (including Socket.IO), terminal (telnet/SSH/rlogin), FTP, and the MRC bridge's shared cookie-based authentication. Previously a session that had logged in under the old password could stay connected after the password changed. Revocation now also reaps an orphaned door-game subprocess still attached to a revoked session on every transport, not only the common ones. Password-recovery links preserve the canonical request origin, including IDN and IPv6 hosts, and a login renewal can no longer race a concurrent password reset.

Web database backups now take a consistent snapshot through SQLite's own online backup API and verify it before streaming, instead of potentially reading pages that are mid-write, and backup retries under contention are now bounded instead of retrying indefinitely.

Added Spanish localization for the core web and terminal flows — navigation, login/registration/recovery, boards, private messages, profile, the built-in terminal menus, and the Admin translations editor — selectable from the navigation language selector or a signed-in account's profile. The web IRC client's layout was improved for mobile screens and gained its own Spanish translations.

A CI-only timing race in the MRC bridge's test suite was fixed by waiting deterministically for the join sequence's final packet before measuring sent packets; this is test/CI hardening only, with no runtime behavior change.
