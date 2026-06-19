# ANetBBS v1.0a2.131 — ANEdit wired into message boards, PMs, echomail; board/thread coloring

## Changes

### ANEdit wired into all terminal message composition

`bbs_ui.py` — all three compose paths now launch ANEdit instead of the old
line-by-line "enter your text, end with `.`" prompt:

- **Board posts / replies** (`_post_compose`) — full ANEdit editor. Reply flow
  fetches the parent post's body, passes it as quote text (pre-formatted with `> `
  prefixes and word-wrap), and pre-fills the subject with `Re: <original subject>`.
- **Private messages** (`_send_pm`) — full ANEdit editor.
- **Echomail** (`_compose_echomail`) — full ANEdit editor.

### Thread list now fully colorized

`_list_threads_v2` — board message list now clears the screen, shows the
board-name banner, and renders each thread row with ANSI color:
- Yellow bold index number
- Cyan reply count badge
- White subject
- Green author
- Grey timestamp and separator

### Thread reader now fully colorized

`_read_thread_v2` — full ANSI treatment for reading posts:
- Yellow `[OP]` / grey `[Reply N]` tags
- Cyan bold subject line
- Green author name, grey date
- Grey separator line
- Quoted lines (starting with `>`) rendered in grey to distinguish from body text
- Footer + colored prompt at bottom
