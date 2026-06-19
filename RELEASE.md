# ANetBBS v1.0a2.132 — ANEdit CP437 fix; slash /commands replace F-keys

## Changes

### Bug fix: ANEdit CP437 character encoding

Title bar and status bar used `●` (U+25CF) and `○` (U+25CB) for the
modified/clean indicator. Neither character is in the CP437 code page, so they
displayed as `?` on telnet/SSH terminals running in IBM PC character mode.

Replaced with:
- `■` (U+25A0, CP437 0xFE) — modified indicator
- `·` (U+00B7, CP437 0xFA) — clean indicator

Also fixed em-dash `—` (U+2014, not in CP437) in flash messages and the help
overlay box title — replaced with plain hyphens.

### New: Slash /commands as primary command interface

F-keys are unreliable over SSH (key code sequences vary by terminal emulator,
and many SSH clients silently swallow them). ANEdit now treats `/` typed at
column 0 as a command prefix, matching BBS convention.

**How it works:** type `/` at the start of a line → a command prompt appears on
the status bar row → type the command word + Enter/Tab → command executes.
Unrecognized commands fall back to inserting the typed text.

**Command table:**

| Command | Action |
|---------|--------|
| `/?` or `/help` | Help overlay |
| `/t` or `/theme` | Cycle color theme (Cyan/Green/Amber) |
| `/m` or `/mark` | Toggle block mark |
| `/cc` or `/color` | Color code picker (Mystic \|XX codes) |
| `/find` or `/f` | Find dialog |
| `/replace` or `/r` | Find & Replace |
| `/undo` or `/u` or `/z` | Undo |
| `/redo` | Redo |
| `/save` or `/s` | Save draft |
| `/send` or `/w` | Send/submit message |
| `/q` or `/quit` or `/abort` | Abort (with confirmation) |

F-keys (F1, F3, F9, F10) retained as secondary bindings for terminals that
support them. Hint bar and help overlay updated to show slash commands.
