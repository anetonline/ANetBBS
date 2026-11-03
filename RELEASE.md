# ANetBBS v1.1.67 — Orphaned door session cleanup fix (November 2026)

A door game session whose underlying process had already exited (for example, after a service restart following an unrelated crash) could be left permanently stuck in the "active" state, shown as still connected with no way for a sysop to disconnect it from the admin UI — the only automatic recovery was a background sweep that only considers sessions over an hour old.

The sysop-initiated disconnect/kick path now finishes cleaning up a door session itself whenever it finds the recorded process already gone, instead of assuming another process will eventually do it.
