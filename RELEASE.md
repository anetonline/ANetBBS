# ANetBBS v1.0a2.80 — Fix: duplicate Admin Caller Log entry + DOSBox-X detection on headless servers

## What's fixed

### Duplicate "Caller Log" entry in Admin menu

The Admin dropdown menu showed two "Caller Log" links side-by-side — one
plain-text and one with the clock icon. The plain-text duplicate has been
removed. Only the icon version remains.

### DOSBox-X not detected on headless servers (DOS door games)

When the BBS service runs without a graphical display (the typical case
for a Linux server), `dosbox-x --version` could hang trying to initialize
SDL's display subsystem. The 5-second runability check would time out,
marking dosbox-x as "not usable" even when it was correctly installed via
`apt install dosbox-x`.

Fixed: the detection subprocess now sets `SDL_VIDEODRIVER=dummy` and
clears `DISPLAY`, letting SDL skip the display driver entirely for the
version probe.

The "No usable DOSBox found" error message now also shows which paths were
actually tried, making it easier for sysops to diagnose detection failures.
`dosbox-x` has been added as the first recommendation in the install hint.
