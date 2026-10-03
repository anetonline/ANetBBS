# Add-ons (Admin → Add-ons)

One admin page for the optional, not-in-the-stock-release pieces --
currently the [Enhanced Client](37-enhanced-client.md) and the
[TheDraw font pack](36-ansi-editor.md#thedraw-tdf-font-picker) -- each
fetched from a GitHub Release asset URL you configure, with a single
**Download & Install** button per item instead of a manual scp +
extract.

Nothing here is fetched automatically or on a schedule. The page just
shows, for each add-on: whether it's already installed, whether a
Release URL is configured for it, and (if configured) a button that
downloads and extracts it on click.

## Enhanced Client

Set `ENHANCED_CLIENT_ADDON_URL` to a direct-download URL for
`ANetBBS-EnhancedClient-addon-<version>.tar.gz` (see
[37 — Enhanced Client](37-enhanced-client.md) for what's in that
archive and how it's built). Installing writes the archive's files to
their real paths under the install root -- only paths matching a fixed
allowlist kept in sync with `tools/build_enhanced_client_addon.sh`'s
own file manifest; anything else in the archive is skipped and never
written, regardless of what the archive claims to contain.

**A restart is required afterward** (`sudo systemctl restart
anetbbs.service`) -- the install response says so plainly. This isn't
a file-permission limitation; it's simply that Python only imports
`enhanced_server.py` once, at process startup, so a process already
running won't pick up a just-added file. You still need to set
`ENHANCED_ENABLED=true` separately, same as a by-hand install.

## TheDraw Font Pack

Set `TDF_FONTS_PACK_URL` the same way (see
[36 — Web ANSI Editor](36-ansi-editor.md) for details) and install from
here or from the editor's own font picker panel -- both trigger the
exact same download-and-extract. No restart needed: the font picker
scans `TDF_FONTS_DIR` fresh on every request.

## Why no sudo, no service restart triggered automatically

ANetBBS's full self-upgrade mechanism (Admin → Check for Updates) uses
a privileged wrapper script invoked via `sudo systemd-run --scope`,
because a self-upgrade replaces every file in the install and must
swap atomically out from under the running service. An add-on install
only *adds* a small, fixed set of new files under well-known
subdirectories and never touches or replaces anything existing -- the
same permission level the web process already writes `TDF_FONTS_DIR`,
the database, and the log file with is enough, so this page adds no
new privilege-escalation surface at all. Where a restart genuinely
matters (Enhanced Client), the page tells you the exact command to run
yourself, the same way Admin → Settings already does for other
restart-requiring changes.
