# ANetBBS v1.0a2.94 — ZMODEM fix, file desc view, batch downloads

## What's new

### Fix: ZMODEM/YMODEM/XMODEM "Menu action failed" after transfer

Root cause identified: `asyncio.wait_for` with a 0.15s timeout in
`_session_to_proc` cancelled and immediately restarted `StreamReader.read()`
dozens of times during a transfer.  Each cancellation left
`StreamReader._waiter` in a pending state; the cleanup callback hadn't run
yet before the next `read()` started.  After the transfer ended and the
task was torn down, the stale `_waiter` caused `RuntimeError: read() called
while another coroutine is already waiting` inside `read_line("Press
Enter...")`, which propagated to menu_engine and printed "Menu action
failed".

Fix: replaced `asyncio.wait_for` polling with a single persistent
`asyncio.ensure_future(read(4096))` + `asyncio.wait(..., timeout=0.5)`.
`asyncio.wait` does not cancel the inner future on timeout, so the
`StreamReader._waiter` is only ever set once per read cycle.  In the
`finally` block we cancel the pending read explicitly and `await` it,
guaranteeing the `_waiter` is `None` before the caller's next read.

Applied to both `send_file` and `recv_file` in `xfer.py`.

### Feature: V# — view extended file description

In the file browser, type `V` followed by a file number (e.g. `V3`) to
see the full description for that file.  Plain-text descriptions are
word-wrapped at 76 characters.  ANSI art (FILE_ID.DIZ with ESC sequences)
is passed through unmodified so it renders correctly in CP437 terminals.

### Feature: Batch downloads (1,3,5 or 1-5)

In the file browser, enter a comma-separated list (`1,3,5`) or a range
(`1-5`) to queue multiple files for download.  Protocol is selected once;
files transfer sequentially.  Mixed lists (`1-3,7`) also work.

### Fix: Area list count column alignment

"0. General / Top-level" name field now pads to 38 characters, matching
the numbered area rows.  The file count column aligns correctly.

### Web UI: expandable full file description

The file area page now shows the first line of a description inline.
Files with multi-line descriptions (FILE_ID.DIZ ANSI art, README text)
get a "full description" expander that reveals a scrollable `<pre>` block.
