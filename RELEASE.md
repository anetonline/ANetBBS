# ANetBBS v1.0a2.82 — Fix: custom ANSI menu screens showing garbled CP437 block characters

## What's fixed

### Custom ANSI menu screens displaying wrong characters (Ü, ß instead of block graphics)

ANSI art files created in Moebius, PabloDraw, or any CP437 editor were
displaying correctly in those editors but showing corrupted block-graphic
characters when uploaded to the BBS. Specifically, characters like `▄` (lower
half block, CP437 0xDC) appeared as `Ü`, and `▀` (upper half block, 0xDF)
appeared as `ß`.

**Root cause:** The menu engine read the `.ans` file as raw bytes decoded with
`latin-1` (correct — this is a lossless round-trip for any byte value 0x00–0xFF).
However, it then passed the resulting string to `session.write()`, which
re-encodes strings as CP437. The latin-1 decode produces Unicode codepoints that
don't map back to the same CP437 byte positions:

- File byte `0xDC` (CP437: `▄`) → latin-1 decode → `U+00DC` (Ü) → CP437
  encode → byte `0x9A` (CP437: `Ü`) ← **wrong glyph**
- File byte `0xDF` (CP437: `▀`) → latin-1 decode → `U+00DF` (ß) → CP437
  encode → byte `0xE1` (CP437: `β`) ← **wrong glyph**

**Fix:** The menu engine now writes the ANSI content as raw bytes via
`session.writer.write(rendered.encode('latin-1'))`, exactly as
`session._show_ansi_screen()` already did. This sends the original CP437 bytes
directly to the terminal without any re-encoding.

**For sysops:** place your custom `.ans` files at
`data/text/menus/<menu-name>.ans` (e.g. `data/text/menus/main.ans`) — this
takes priority over the DB field set via the web admin.
