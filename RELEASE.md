# ANetBBS v1.0a2.119 — Security levels, graffiti wall, logon/logoff modules, fast logon

## Changes

### Add: Security levels on all areas

All content areas now have a `min_access_level` field (0–255, default 10).
The sysop can restrict any area to VIP users (50) or sysop-only (100) via the
admin panel. File areas, message boards, echomail, and RSS feeds all support
this. Default new-user access level is 10 — they can access everything not
explicitly restricted.

### Add: Graffiti Wall

A retro-style graffiti wall, available as a logon/logoff module or menu action
(`action_type = wall`). Features:

- Pipe color codes (`|15HELLO` = bright white HELLO)
- 2-line posts per user, 200 chars per line
- Paginated display (6 posts/page, `[N]ewer [O]lder` navigation)
- `[W]rite` to post, `[D]el` for sysop delete by post ID
- ANSI box-drawing header/footer
- Sysop admin panel at `/admin/wall/` with soft-delete + restore + clear-all

### Add: Logon/Logoff Module system

Sysops can configure modules that run automatically at logon or logoff.
Admin panel at `/admin/login-modules/`. Each module has:

- Event type: `logon` or `logoff`
- Module type: `wall`, `ansi`, `shell`, `door_native`, `door_python`
- Min access level (skip for low-level users)
- Sort order (lower = runs first)
- Skip on fast logon option

### Add: Fast Logon option

When enabled via `FAST_LOGON_ENABLED` config key, users are prompted at login:

```
[F]ast logon — skip intro modules? [y/N]:
```

Modules flagged "skip on fast logon" are bypassed for users who say yes.

### Add: `wall` menu action type

Menus can now include `action_type = wall` items that open the Graffiti Wall
directly from any BBS menu.

## Files changed

- `anetbbs/models.py` — add `min_access_level` to `Board`, `FileArea`, `EchoArea`, `RssFeed`; add `WallPost` and `LoginModule` models
- `anetbbs/features/wall.py` — NEW: graffiti wall terminal feature
- `anetbbs/features/login_modules.py` — NEW: logon/logoff module runner
- `anetbbs/features/menu_engine.py` — add `_act_wall` and register `wall` action type
- `anetbbs/features/bbs_ui.py` — filter areas by `min_access_level`
- `anetbbs/core/session.py` — fast logon prompt; logon/logoff module hooks
- `anetbbs/web/login_modules_admin.py` — NEW: admin CRUD for login modules
- `anetbbs/web/wall_admin.py` — NEW: admin for wall posts
- `anetbbs/web/rss_admin.py` — save `min_access_level` for feeds
- `anetbbs/web/echomail_admin.py` — add `min_access_level` to EchoAreaForm
- `anetbbs/web/admin.py` — add `min_access_level` to BoardForm + FileArea update
- `anetbbs/web_app.py` — register login_modules_admin_bp and wall_admin_bp
- `anetbbs/templates/admin/login_modules.html` — NEW
- `anetbbs/templates/admin/login_module_form.html` — NEW
- `anetbbs/templates/admin/wall.html` — NEW
- `anetbbs/templates/admin/board_form.html` — add min_access_level field
- `anetbbs/templates/admin/file_areas.html` — add min_access_level field
- `anetbbs/templates/rss_admin/edit.html` — add min_access_level field
- `anetbbs/templates/base.html` — add Logon Modules + Graffiti Wall to admin nav
- `anetbbs/__init__.py`, `setup.py`, `VERSION`, `FILE_ID.DIZ`, `RELEASE.md`, `docs/CHANGELOG.md` — version bump
