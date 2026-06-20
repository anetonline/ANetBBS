# ANetBBS v1.0a2.136 — Duplicate email/username crash fix; case-insensitive usernames

## Changes

### Security/Bug fix: Duplicate email address crashes session (DoS vector)

Registering a new account with an email address already used by another account
triggered a raw SQLite `IntegrityError` that propagated unhandled and terminated
the entire BBS session — potentially disconnecting all connected users.

Root cause: `create_user()` in `user_manager.py` checked for duplicate usernames
before inserting but did not check for duplicate emails. The database unique
constraint on `users.email` fired at commit time and was never caught.

Fixes:
- Added `email_exists()` pre-check in the terminal registration loop — user is
  re-prompted for a different email with a clear message ("An account with that
  email address already exists.")
- Added `username_exists()` pre-check in the username loop — user is re-prompted
  immediately instead of failing after collecting all input.
- `create_user()` now catches `IntegrityError` as defense-in-depth and returns
  a string result code (`'ok'` / `'username_taken'` / `'email_taken'`) instead
  of a bool, so callers can display the right error.
- Web registration form `validate_email` and `validate_username` validators now
  use case-insensitive queries (SQLite `lower()`) — already showed user-friendly
  errors but was technically case-sensitive before.

### New: Case-insensitive username uniqueness

All username lookups (registration check, login, `get_user`) now use
`func.lower(User.username) == username.lower()` so `StingRay` and `stingray`
are treated as the same account. Existing usernames are not renamed — only the
lookup and uniqueness check are case-folded.

This applies to:
- Terminal registration (can't register a name that differs only in case)
- Terminal login (login with any case variation of your username)
- Web registration form duplicate check
- Web login lookup
