# ANetBBS v1.0a2.73 — ANSI file drop-in, bulletin word-wrap, screen-slot link, sysop name fix

## ANSI file drop-in system (`data/text/`)

Sysops can now drop `.ans` files directly into the install directory to override
any screen — no admin UI, no copy-paste. Resolution order: file → DB slot → built-in.

| File | Screen |
|------|--------|
| `data/text/welcome.ans` | Pre-login / logon screen |
| `data/text/goodbye.ans` | Logoff screen |
| `data/text/newuser.ans` | New user registration screen |
| `data/text/menus/main.ans` | Main menu ANSI header |
| `data/text/menus/games.ans` | Games menu ANSI header |
| `data/text/menus/<name>.ans` | Any menu by its internal name |

Display codes (`@USER@`, `@BBS@`, `|UN`, etc.) are applied to file-based screens.
The directories are created on fresh install and on upgrade via `update.sh`.

## ANSI editor → screen slot link

The ANSI editor (`/admin/ansi/`) now has an **Apply to Screen Slot** card
alongside the existing Apply to Menu card. Select welcome, goodbye, or newuser
from the dropdown and click Apply — the art is written directly to the DB slot.
No more copy-paste into the screen editor.

## Bulletin word-wrap

Bulletin text in the terminal pager now word-wraps to the negotiated terminal
width (NAWS) instead of truncating every line at 78 characters. Long lines
reflow correctly; ANSI-escaped lines pass through unchanged.

## Sysop name as admin username default

The installer now defaults the admin login username to the sysop display name
entered earlier in the wizard. If you type "Firehawke" as sysop name, the admin
account username defaults to "Firehawke" instead of "admin". `install.sh` also
now asks for a sysop display name separately and writes `SYSOP_NAME` to `.env`.

## Logged-out intro screen uses BBS_NAME

The fallback login menu (shown when no custom welcome ANSI screen is configured)
now shows "Welcome to <BBS_NAME>" using the configured BBS name instead of the
hardcoded "Welcome to AnetBBS".
