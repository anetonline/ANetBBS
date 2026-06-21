# ANetBBS v1.0a2.148 — Bug fixes + 2 new web games (Tetris, Breakout)

## Changes

### SMTP Relay + Email Verification + Password Reset by Email

New sysop-configurable outbound SMTP relay. **Off by default.** Configure at `/admin/smtp`.

- **SmtpConfig** and **EmailVerifyToken** models added (new tables, `db.create_all()` safe)
- **`anetbbs/mailer.py`** — stdlib `smtplib` sender; no new dependencies
- **Email verification on registration**: when enabled, new users receive a verification link
  and cannot log in until they click it. Link expires in 24 hours. Resend page at `/auth/verify/resend`
- **Password reset by email**: if SMTP is configured, the reset link is emailed automatically
  instead of only being logged to the server journal
- **Admin UI**: `/admin/smtp` page with host/port/TLS/credentials form, test-send button, and setup guide
- Supports Gmail App Passwords, any SMTP relay, or your own mail server
- Existing stale email-server setup files left intact

### Bug Fixes (v1.0a2.148)

- **Solitaire** — Complete rewrite of click model. Cards now correctly attempt a move when another card is already selected, rather than always re-selecting. Ghost highlight on selected cards.
- **Galaga** — Initial enemy shoot cooldown was far too short (1.7–5 sec); bumped to 10–15 sec at level 1, staggered by enemy index so no mass simultaneous fire. Dive interval increased from every 1.5 sec to every 5 sec at level 1, scales with level. Max simultaneous divers capped (1 at level 1). Enemy bullet count capped per level.
- **Slots** — Tab switching (Lucky and Retro machines) was broken by a JavaScript strict-mode `ReferenceError`. Fixed.

### 2 New Web Games

| Game | Category | Highlights |
|---|---|---|
| Tetris | Puzzle | Full Tetris with ghost piece, hard drop, wall-kick rotation, level scaling, hi-score, touch controls |
| Breakout | Action | Arkanoid-style brick breaker; 5 power-ups (wide paddle, slow ball, multi-ball, laser, +life), level progression, glowing ball trail |

## Files changed

`anetbbs/__init__.py`, `setup.py`, `VERSION`, `FILE_ID.DIZ`, `RELEASE.md`, `docs/CHANGELOG.md`, `README.md`,
`anetbbs/games/web_games.py`,
`anetbbs/templates/games/web/solitaire.html` (rewrite),
`anetbbs/templates/games/web/galaga.html` (difficulty fix),
`anetbbs/templates/games/web/slots.html` (tab fix),
`anetbbs/templates/games/web/tetris.html` (new),
`anetbbs/templates/games/web/breakout.html` (new)
