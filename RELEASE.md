# ANetBBS v1.0a2.165 — ZMODEM handshake fix; pre-transfer reader drain

## Changes

### `features/xfer.py` — remove redundant `--binary` from ZMODEM send flags

`--binary` is a no-op for ZMODEM (the protocol is always binary). Removing it
avoids any edge-case interactions with lrzsz option parsing.

### `features/bbs_ui.py` — drain reader before starting sz

After writing "Starting ZMODEM send..." and sleeping 1 second, residual bytes
could accumulate in `session.reader` (trailing `\n` from Enter, ANSI terminal
sequences) and be fed directly to sz's stdin at startup. sz parsed these as
garbage ZMODEM frames, failed the initial ZRQINIT/ZRINIT handshake, and exited
non-zero — forcing the user to retry.

Added a 0.2 s drain loop before calling `send_file` in both `_download_file`
and `_batch_download` to flush any stale bytes before sz takes over stdin.
