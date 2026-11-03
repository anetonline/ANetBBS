# ANetBBS v1.1.65 — Door-game SSH/telnet write-path fix, plus a follow-up audit (November 2026)

Door games launched over SSH, telnet, or rlogin could fail to save player progress, scores, or other persistent data — or in some cases fail to launch at all — even though the same game worked fine through the web interface. The cause was a mismatch between what the two access methods are allowed to write to on disk: the SSH/telnet/rlogin service only grants write access to the application's own data and log directories, not to a door's own install directory.

Native door games now get a dedicated, always-writable storage location automatically, with no sysop configuration needed. DOS- and DOSEMU-emulated door games that aren't installed inside the writable data directory now report a clear, actionable error at launch instead of failing silently or unpredictably; see `docs/14-door-games.md` for the recommended install location if you see this error.

Also: a routine follow-up security/performance audit hardened a handful of other areas, including network message-size limits and the sandboxing applied to an optional add-on service. No sysop action is required; applying this release picks these up automatically.
