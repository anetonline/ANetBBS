# ANetBBS v1.0a2.117 — Fix: @-code / display code substitution in ANSI screens

## Changes

### Fix: Synchronet @-codes now resolve correctly in ANSI screens

`@ALIAS@`, `@USER@`, `@NAME@`, `@HANDLE@`, `@REAL@`, `@FIRST@`, `@EMAIL@`,
`@LOCATION@`, `@CALLS@`, and `@SECURITY@` were all resolving to blank (empty
string) in ANSI files stored under `text/menus/` or set via Admin → Screens.

Root cause: `session.user` is a dict (not a SQLAlchemy ORM object), but the
`_u()` helper in `display_codes.py` used `getattr(user, attr, '')` — which
does not read dict keys. Only codes that read directly from the context dict
(e.g. `@BBS@`, `@TIME@`, `@DATE@`) were working.

Fixed `_u()` to detect dict vs. ORM object and use `.get()` accordingly.

Also added `display_name` and `location` to the user dict returned by
`user_manager._user_to_dict()` so `@NAME@`/`@REAL@`/`@FIRST@` and
`@LOCATION@` resolve to the correct values.

Fixed `@SECURITY@` — `is_admin` in the dict is a Python `bool`, not the
string `'True'`; the old string comparison always returned `50`.

Fixed `@VER@` / `@VERSION@` — the version string was hardcoded `'v1.0a'`
at the two call sites in `session.py` and `menu_engine.py`; now reads
`anetbbs.__version__` at runtime.

### Fix: Parametric @CODE:value@ codes no longer print as literal text

`@BPS:19200@` and similar Synchronet parametric codes contain a colon which
the existing `@CODE@` regex doesn't match, so they printed as-is in the
rendered ANSI. These codes are now stripped before substitution.
`@BPS:NNNN@` (baud-rate slow-draw) is not modelled — stripping it is the
correct behavior.

## Files changed

- `anetbbs/features/display_codes.py` — fix `_u()` dict access; fix `@SECURITY@` bool check; add `_AT_PARAM_RE` to strip parametric codes
- `anetbbs/core/user_manager.py` — add `display_name` and `location` to `_user_to_dict`
- `anetbbs/core/session.py` — pass real `anetbbs.__version__` to `_apply_codes`
- `anetbbs/features/menu_engine.py` — pass real `anetbbs.__version__` to `_apply_codes`
- `anetbbs/__init__.py`, `setup.py`, `VERSION`, `FILE_ID.DIZ`, `RELEASE.md`, `docs/CHANGELOG.md` — version bump
