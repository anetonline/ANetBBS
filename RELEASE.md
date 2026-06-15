# ANetBBS v1.0a2.116 — Add per-game FOSSIL driver checkbox; fix TW2002 black screen

## Changes

### Add: "Requires FOSSIL Driver" checkbox in Game admin

A new **Requires FOSSIL Driver (BNU.COM / X00.COM)** checkbox has been added to
the dosemu2 door game admin form. Previously, ANetBBS auto-detected FOSSIL driver
files in the game's working directory and loaded them automatically. This caused
TW2002 to show a black screen because dosemu2's virtual COM1 (`$_com1 = "virtual"`)
conflicts with any loaded FOSSIL driver.

The auto-detection has been replaced with an explicit opt-in per game:

- Check **Requires FOSSIL Driver** for games like Zombie Slots / Mega Slots that
  ship with BNU.COM or similar and need it loaded before launch.
- Leave it unchecked for TW2002 and any other game that uses dosemu2 virtual COM.
- The first matching FOSSIL driver found in the game's working directory is loaded
  (`BNU.COM`, `FOSSIL.COM`, `X00.COM`, `FOSDRV.COM`, or `BNU2.COM`).

The `needs_fossil_driver` column is added to the `games` table automatically by
`update.sh` (Step 7 schema migration — `ALTER TABLE games ADD COLUMN`).

### Fix: TW2002 black screen (regression from v1.0a2.113)

TW2002 is no longer affected by FOSSIL auto-detection. With the checkbox approach,
TW2002 (unchecked) gets no FOSSIL driver loaded and dosemu2 virtual COM1 works
correctly again.

## Files changed

- `anetbbs/models.py` — `needs_fossil_driver` column added to Game model
- `anetbbs/web/games_admin.py` — `needs_fossil_driver` BooleanField in GameForm + save block
- `anetbbs/templates/games/admin/form.html` — checkbox in dosemu2 section
- `anetbbs/games/door_runner.py` — replace auto-detection with `game.needs_fossil_driver` opt-in
- `anetbbs/__init__.py`, `setup.py`, `VERSION`, `FILE_ID.DIZ`, `RELEASE.md`, `docs/CHANGELOG.md` — version bump
