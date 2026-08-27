# Postcards — shareable ANSI/PETSCII art

Any logged-in user (not just admins) can compose retro CP437 art in
the browser and get back a public, no-login share page plus a real
downloadable PNG — something that pastes cleanly into a social media
post, unlike a terminal screenshot.

This is a separate, user-facing feature from the admin **ANSI
Editor** (`/admin/ansi/`, see [`04-ansi-screens.md`](04-ansi-screens.md))
even though it's built on the exact same grid-editing engine. The
ANSI Editor manages official, admin-curated BBS assets (menu banners,
welcome/goodbye screens); Postcards is regular users making their own
shareable art. The two are kept in separate database tables
(`AnsiArt` vs `Postcard`) specifically so a flood of user postcards
never clutters the admin art library sysops use to manage real BBS
assets.

## Using it

**Tools → My Stuff → Postcards** (`/postcards/`, requires login).
"New Postcard" opens the same pencil/line/rect/fill/text/undo-redo
grid editor as the admin ANSI Editor. Save, then click "Share Page"
to get:

- A public link (`/postcards/<slug>`) anyone can open — no BBS
  account needed to view it.
- A downloadable PNG (`/postcards/<slug>.png`), rendered server-side
  from the same vendored VGA font used throughout the BBS's retro
  styling, upscaled with nearest-neighbor so it stays crisp rather
  than blurring a pixel font.
- A downloadable `.ans` file, compatible with Pablo Draw / Moebius /
  SyncTERM.

Only the postcard's own creator (or an admin) can edit or delete it.
Viewing, downloading the PNG, and downloading the `.ans` are all
public with no login — that's the entire point of a shareable link.

## No feature flag

Unlike Watch It Live and the social-posting queue, Postcards has no
on/off switch — it doesn't broaden any existing data's exposure the
way those two do (a user is choosing, per-postcard, to publish
something), so there's nothing to default off.

## Related files

- `anetbbs/web/postcards.py` — the blueprint.
- `anetbbs/templates/ansi_editor/_editor_widget.html` — the shared
  grid-editor engine (CSS + canvas/tools panel + JS), included by both
  the admin ANSI Editor and Postcards so there is exactly one copy of
  the editing logic, not two that can drift apart.
- `anetbbs/features/ansi_png.py` — the grid-to-PNG renderer.
- `anetbbs/models.py`'s `Postcard` model.
