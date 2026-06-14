# ANetBBS v1.0a2.121 — Fix: TW2002 halts immediately — server-side COM1 wakeup byte

## Root cause (final, confirmed)

Two separate issues combined to produce a blank screen:

1. **TWNODE.DAT was in LOCL mode** (`0x9C = b'LOCL'`). TW2002 in LOCL mode draws the
   title screen via INT 10h (local video) only; COM1 TX receives only blank-line
   clear-screen sequences. Those blank lines scrolled the PTY, but the actual title
   screen was never sent to COM1 TX and never reached the browser.

2. **dosemu2 `-td` writes INT 10h video to fd 1 (stdout), not fd 2 (stderr).**
   Confirmed by v1.0a2.119 test: redirecting fd 1 to the log left only 340 bytes
   (dosemu2 startup errors from fd 2) in the PTY. All game content went to the log.

The session 660 hex dump (3791 bytes) clinched the fix: when TWNODE.DAT was
temporarily in DOOR mode, TW2002 sent `ESC[6n` (remote terminal query) followed by
the full ANSI block-art title screen via COM1 TX → fd 1 → PTY → browser.

## Fix

- **TWNODE.DAT patched to DOOR mode** on server: `0x9C = b'DOOR'`, `0xA4 = b'03F8'`.
  TW2002 now sends title screen + all game output via COM1 TX (BIOS INT 14h).
- **Restore `$_com1 = "virtual"`**: COM1 TX → fd 1 (PTY) so game output reaches the
  browser. COM1 RX ← fd 0 (PTY) so keyboard input reaches the game.
- **Remove `-ks`**: DOOR mode reads keyboard via COM1 RX, not INT 16h. `-ks` created
  a race between COM1 RX and INT 16h both draining fd 0.
- **fd 2 (stderr) → log**: dosemu2 startup errors (KVM, Landlock, libao) captured
  there, no longer mixed into the game output stream.
- **fd 1 (stdout) stays as PTY slave**: COM1 TX + dosemu2 `-td` INT 10h output reach
  the PTY master and the web terminal.
- **Smart startup filter** (`startup_delay_secs = 3.5`): During the first 3.5 s,
  ESC sequences from xterm.js (cursor-position query responses, resize events) are
  passed through immediately — TW2002 uses these to detect the remote terminal type
  and switches to ANSI mode, then sends the title screen via COM1 TX. Plain keyboard
  bytes (Enter, letters, digits) are discarded during the window to prevent advancing
  past `[Press ENTER]` before the user sees the title screen.

## PTY diagnostic dump

Raw PTY byte dump (first 4 KB to `/tmp/pty_debug_{session_id}.bin`) retained for
diagnostics. Will be removed once TW2002 is confirmed working.
