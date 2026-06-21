# ANetBBS v1.0a2.149 — Echomail area sysop-only flag + security levels

## Changes

### Echomail Area Access Control

Each echo area now has three independent access controls visible directly in the areas list table:

- **Active** ✓/✗ — area is enabled and visible (was already present)
- **Subscribed** ✓/✗ — area receives messages from upstream hub (was already present)
- **Sysop Only** 🔒 — hides the area from and blocks access by all non-admin users
- **Min Level** badge — minimum `user.access_level` required to see and enter the area
  (0 = all registered users, 10 = default registered, 50 = VIP, 100 = sysop-level)

The area edit form now shows all four flags together in a clearly labelled flags box, with the access level field alongside.

Access is enforced in:
- Area list (index) — hidden from users who fail the level or sysop check
- Area view, thread view, read, next-unread — 403 if access fails
- Compose — area dropdown only shows areas the user can post to
- All admin routes remain unrestricted for admins regardless of area settings

## Files changed

`anetbbs/__init__.py`, `setup.py`, `VERSION`, `FILE_ID.DIZ`, `RELEASE.md`, `docs/CHANGELOG.md`, `README.md`,
`anetbbs/web/echomail_admin.py` (form + new_area handler),
`anetbbs/web/echomail.py` (_check_area_access helper; index, compose filtered),
`anetbbs/templates/echomail/admin/areas.html` (new columns),
`anetbbs/templates/echomail/admin/area_form.html` (new fields)
