# anetbbs/features/petscii_ui.py
"""
PETSCII terminal-mode menu loop for Commodore 64/128 users (see the
"PETSCII Terminal Support (Phase 1)" plan). Deliberately a fully separate
rendering path from anetbbs/features/menu_engine.py's run_menu() /
bbs_ui.py's BBSMenuUI -- those are cursor-addressed-ANSI-native with no
plain-text fallback anywhere, so this is a hand-built, hardcoded menu
using only plain text + anetbbs/features/petscii_codec.py's control-code
constants, reusing the SAME underlying data-layer models/queries the
ANSI screens use (Board/Post, EchoArea/EchomailMessage, PrivateMessage,
FileArea/FileUpload, UserSession, User), never their rendering methods.

Phase 1 scope: message boards, echomail, private messages, file-area
browsing (no transfer), who's-online, profile, logoff, plus ONE
built-in game (Number Guessing -- plain session I/O, no ANSI codes at
all, so features.games.GameManager.play_number_guess() is reused
directly rather than reimplemented). Door games/ANetCRAFT/DarkForces/
MRC chat/IRC are still intentionally never offered here -- not shown,
not marked "unavailable," just absent, matching how the session-level
logon/logoff modules (Wall, ANSI art) are already skipped entirely for
term_mode == 'petscii'. Only games that are pure plain-text (no cursor
addressing) are candidates for this exception; each one needs the same
"reuses session I/O only" check Number Guessing passed before adding it.

No lightbar, no ANEdit -- every screen is a plain numbered list read
via read_line()/int(), and message bodies use a simple line-by-line
composer (a lone "/send" or "/abort" line to finish) since neither of
those cursor-addressed editors has any PETSCII-aware rendering to reuse.
"""
import re
import textwrap
from datetime import datetime, timedelta
from ..core.tz import fmt_eastern

from .petscii_codec import REVERSE_ON, REVERSE_OFF

# Leaves room for a 2-line header + a prompt line on a real 25-row C64
# screen, regardless of whether the session is 40 or 80 columns wide
# (column count and row count are independent on real hardware).
PAGE_LINES = 18


def _app_ctx():
    from .bbs_ui import _app
    return _app().app_context()


def _access_level(session):
    from .menu_engine import _user_access_level
    return _user_access_level(session.user)


def _width(session):
    return getattr(session, 'petscii_width', 40)


def _username(session):
    u = session.user
    return u.get('username', '?') if isinstance(u, dict) else '?'


def _user_id(session):
    u = session.user
    return u.get('id') if isinstance(u, dict) else None


async def _header(session, title):
    await session.clear_screen()
    w = _width(session)
    inner = max(1, w - 2)
    await session.write(f'{REVERSE_ON} {title[:inner].ljust(inner)}{REVERSE_OFF}\r\n\r\n')


async def _paginate(session, lines, header_title=None):
    """Print lines PAGE_LINES at a time, pausing between pages -- the
    plain-text stand-in for ANEdit/ANView's full-screen reader.

    header_title, if given, is redrawn (via _header(), which clears the
    screen) at the top of every page -- required for a clean B=back
    (typing B re-shows the PREVIOUS page's already-scrolled-past content).
    If omitted, pages are just printed one after another with no
    clear/redraw (legacy behavior) and B=back still works but reprints
    the previous chunk below the current scroll rather than on a fresh
    screen.

    Returns True if every line was shown, False if the reader quit early
    (typed Q at a -- More -- prompt) -- callers use this to skip a
    redundant trailing "Press ENTER" prompt when the reader already bailed.
    Real report: reading a long message/file listing forced the reader
    through every single page with no way to abort back to the menu --
    the -- More -- prompt read a line but never checked what was typed.
    Second real report: once past page 1, there was no way back to an
    earlier page short of re-opening the whole screen from scratch.
    """
    if not lines:
        if header_title is not None:
            await _header(session, header_title)
        await session.write('(nothing here)\r\n')
        return True
    page = 0
    while True:
        start = page * PAGE_LINES
        if header_title is not None:
            await _header(session, header_title)
        for line in lines[start:start + PAGE_LINES]:
            await session.write(line + '\r\n')
        if start + PAGE_LINES >= len(lines):
            return True
        prompt = '-- More -- ENTER=continue'
        if page > 0:
            prompt += ', B=prev'
        prompt += ', Q=stop: '
        resp = (await session.read_line(prompt) or '').strip().upper()
        if resp == 'Q':
            return False
        if resp == 'B' and page > 0:
            page -= 1
            continue
        page += 1


_NON_PETSCII_RUN_RE = re.compile(r'[^\x20-\x7E\r\n]+')
_MULTI_SPACE_RE = re.compile(r' {2,}')


def _strip_for_petscii(text):
    """Collapse any run of characters with no PETSCII representation
    (CP437/Unicode box-drawing art, etc.) into a single space, rather
    than letting them reach session.write()'s encode() -- which falls
    back to a literal '?' PER CHARACTER, turning a FILE_ID.DIZ-style
    ANSI-art banner into a wall of question marks. ANSI escape SEQUENCES
    are already stripped earlier by session.write() itself; this handles
    the raw art characters written directly in the text (very common in
    file descriptions, and possible in echomail bodies from ANSI-heavy
    networks) that aren't part of any escape sequence at all. Also
    collapses the resulting runs of spaces (adjacent stripped chunks
    otherwise leave doubled-up spacing) and trims each line.
    """
    cleaned = _NON_PETSCII_RUN_RE.sub(' ', text or '')
    lines = (_MULTI_SPACE_RE.sub(' ', ln).strip()
            for ln in cleaned.replace('\r\n', '\n').split('\n'))
    return '\n'.join(lines)


def _wrap_body(text, width):
    out = []
    for para in _strip_for_petscii(text).split('\n'):
        out.extend(textwrap.wrap(para, width) or [''])
    return out


def _truncate_words(text, width):
    """Truncate `text` to at most `width` chars, breaking at the last
    word boundary rather than mid-word -- real report: subject/label
    columns in listing screens were cutting off words (e.g. "voluntarily"
    -> "volun"). Falls back to a hard cut only when a single word alone
    exceeds the whole budget (nothing to break on)."""
    text = text or ''
    if len(text) <= width:
        return text
    cut = textwrap.wrap(text, width)
    return cut[0] if cut else text[:width]


async def _compose_body(session):
    """Simple line-by-line composer -- no ANEdit. A lone "/send" line
    finishes and saves; a lone "/abort" line cancels. Blank lines within
    the message are fine (just extra empty lines), unlike a scheme where
    a blank line itself ends input -- that would make it impossible to
    leave a blank line between paragraphs."""
    await session.write(
        "Type your message below.\r\n"
        "On its own line: /send to finish, /abort to cancel.\r\n\r\n")
    lines = []
    while True:
        line = await session.read_line('')
        if line is None:
            return None
        stripped = line.strip()
        if stripped == '/abort':
            return None
        if stripped == '/send':
            break
        lines.append(line)
    return '\r\n'.join(lines)


async def _pick(session, prompt, count):
    """Read a line, returning an int index in [1, count] or a upper-cased
    command string (e.g. 'Q', 'N') if it's not a valid number."""
    choice = (await session.read_line(prompt) or '').strip()
    if not choice:
        return None
    try:
        idx = int(choice)
    except ValueError:
        return choice.upper()
    return idx if 1 <= idx <= count else None


async def _paginated_pick(session, title, rows, render_row, prompt_label):
    """Listing screen for `rows` PAGE_LINES at a time, with a picker
    prompt -- the listing-screen counterpart of _paginate() (which is for
    read-only body text). Real report: echo/board/PM listings printed up
    to 50 rows in one unbroken dump with no page break at all, unlike
    file-area browsing which already paginated.

    render_row(absolute_1based_index, row) -> line string (no \\r\\n).
    prompt_label is the picker text before ", Q=back:", e.g. 'read, N=new'.
    Row numbers stay globally consecutive across pages (not reset per
    page), same as a real numbered list -- picking "23" always means the
    23rd row regardless of which page it's currently showing on.

    'B' (only offered past page 1) steps back one page -- real report:
    once you'd paged forward with M, the only way "back" was Q, which
    exits the whole menu rather than just returning to the previous page.

    Returns the same contract as _pick(): an int index (1-based into
    `rows`), an upper-cased command string, or None.
    """
    page = 0
    while True:
        start = page * PAGE_LINES
        page_rows = rows[start:start + PAGE_LINES]
        await _header(session, title)
        if not rows:
            await session.write('(nothing here)\r\n')
        for offset, row in enumerate(page_rows):
            await session.write(render_row(start + offset + 1, row) + '\r\n')
        has_more = start + PAGE_LINES < len(rows)
        prompt_text = f'#={prompt_label}'
        if has_more:
            prompt_text += ', M=more'
        if page > 0:
            prompt_text += ', B=prev'
        prompt_text += ', Q=back'
        # Real report: a long prompt_label (e.g. file browsing's
        # 'download, E=extended info') combined with M=more/B=prev could
        # exceed 40 columns, hard-wrapping mid-word ("Q=back" -> "Q=b" /
        # "ack"). _wrap_body() no-ops (returns one line) for anything
        # that already fits, so this is a no-op for every other caller.
        prompt = ('\r\n' + '\r\n'.join(_wrap_body(prompt_text, _width(session)))
                  + ': ')
        choice = await _pick(session, prompt, len(rows))
        if choice == 'M' and has_more:
            page += 1
            continue
        if choice == 'B' and page > 0:
            page -= 1
            continue
        return choice


# ─── Main menu ──────────────────────────────────────────────────────────

async def run_petscii_menu(session):
    """Entry point called from BBSSession right after login when
    term_mode == 'petscii'. If a sysop has built a custom PetsciiMenu
    tree (opt-in, see models.PetsciiMenu), use that; otherwise fall back
    to the hardcoded Phase 1 menu unchanged.

    The custom-menu lookup is wrapped in try/except -- this is the very
    first DB touch of a PETSCII session, and any failure here (table not
    yet migrated on an upgrading install, transient DB error) must not
    break login entirely. Fail safe into the hardcoded menu, which never
    touches the DB until a submenu needs it.
    """
    custom_name = None
    try:
        with _app_ctx():
            from ..models import PetsciiMenu
            custom_default = PetsciiMenu.query.filter_by(is_default=True).first()
            custom_name = custom_default.name if custom_default else None
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            'PETSCII: failed to check for a custom default menu -- '
            'falling back to the hardcoded menu')
        custom_name = None
    if custom_name:
        await _run_custom_petscii_menu(session, custom_name)
        return
    await _run_default_petscii_menu(session)


async def _run_default_petscii_menu(session):
    """The hardcoded Phase 1 menu -- also the fallback for sysops who
    haven't built a custom PetsciiMenu tree."""
    while True:
        await _header(session, 'ANetBBS Main Menu')
        await session.write(
            "  1. Message Boards\r\n"
            "  2. Echomail\r\n"
            "  3. Private Messages\r\n"
            "  4. File Areas\r\n"
            "  5. Who's Online\r\n"
            "  6. Your Profile\r\n"
            "  7. Games\r\n"
            "  Q. Logoff\r\n")
        choice = (await session.read_line('\r\nChoice: ') or '').strip().upper()
        if choice == '1':
            await _boards_menu(session)
        elif choice == '2':
            await _echomail_menu(session)
        elif choice == '3':
            await _pm_menu(session)
        elif choice == '4':
            await _files_menu(session)
        elif choice == '5':
            await _whos_online(session)
        elif choice == '6':
            await _profile(session)
        elif choice == '7':
            await _games_menu(session)
        elif choice == 'Q':
            await session.write('\r\nGoodbye!\r\n')
            return


# Action types a sysop-built PetsciiMenu item can invoke -- deliberately
# a small subset of BbsMenuItem's action_type list (see menu_admin.py's
# ACTION_TYPES), matching what petscii_ui.py has an actual plain-text
# handler for. 'goto' and 'logoff' are handled specially in
# _run_custom_petscii_menu(), not through this dict.
_CUSTOM_MENU_ACTIONS = {}


def _register_custom_menu_actions():
    """Deferred population -- the handler functions below are defined
    later in this module, so this runs once at import time, after all of
    them exist, rather than inline at class-body/module-top time."""
    _CUSTOM_MENU_ACTIONS.update({
        'boards': _boards_menu,
        'echo': _echomail_menu,
        'pm': _pm_menu,
        'files': _files_menu,
        'who': _whos_online,
        'profile': _profile,
        'games': _games_menu,
    })


async def _run_custom_petscii_menu(session, start_name):
    """Interpreter for a sysop-built PetsciiMenu tree -- the PETSCII
    counterpart of menu_engine.run_menu(), but rendering as a plain
    numbered list (no cursor addressing/lightbar) and dispatching only
    through _CUSTOM_MENU_ACTIONS's small PETSCII-safe action set.
    """
    from ..models import PetsciiMenu, PetsciiMenuItem
    level = _access_level(session)
    current = start_name
    while True:
        with _app_ctx():
            menu = PetsciiMenu.query.filter_by(name=current).first()
            if menu is not None:
                items = (menu.items.order_by(PetsciiMenuItem.sort_order,
                                             PetsciiMenuItem.id).all())
                rows = [(it.hotkey, it.label, it.action_type, it.action_args)
                       for it in items
                       if it.is_visible and level >= (it.min_access or 0)]
                title = menu.title
                prompt = menu.prompt or 'Choice: '
        if menu is None:
            # Broken 'goto' target, or the default menu got deleted
            # mid-session -- fail safe into the always-working hardcoded
            # menu rather than dead-ending the session.
            await _run_default_petscii_menu(session)
            return

        await _header(session, title)
        for hotkey, label, _at, _aa in rows:
            await session.write(f'  {hotkey}. {label}\r\n')
        choice = (await session.read_line(f'\r\n{prompt}') or '').strip().upper()

        match = next((r for r in rows if r[0].upper() == choice), None)
        if match is None:
            continue
        _hotkey, _label, action_type, action_args = match
        if action_type == 'goto':
            if action_args:
                current = action_args
            continue
        if action_type == 'logoff':
            await session.write('\r\nGoodbye!\r\n')
            return
        handler = _CUSTOM_MENU_ACTIONS.get(action_type)
        if handler is None:
            await session.write('\r\nThat option is not available.\r\n')
            await session.read_line('Press ENTER...')
            continue
        await handler(session)


# ─── Message boards ─────────────────────────────────────────────────────

async def _boards_menu(session):
    level = _access_level(session)
    while True:
        with _app_ctx():
            from ..models import Board
            boards = (Board.query.filter_by(is_active=True)
                     .filter(Board.min_access_level <= level)
                     .order_by(Board.order, Board.name).all())
            rows = [(b.id, b.name) for b in boards]

        if not rows:
            await _header(session, 'Message Boards')
            await session.write('No boards available.\r\n')
            await session.read_line('\r\nPress ENTER...')
            return

        name_w = max(10, _width(session) - 7)
        pick = await _paginated_pick(
            session, 'Message Boards', rows,
            lambda i, row: f'{i:>3}. {_truncate_words(row[1], name_w)}', 'select')
        if pick == 'Q':
            return
        if isinstance(pick, int):
            await _board_threads(session, rows[pick - 1][0], rows[pick - 1][1])


async def _board_threads(session, board_id, board_name):
    level = _access_level(session)
    while True:
        with _app_ctx():
            from ..models import Board, Post
            board = Board.query.get(board_id)
            if board is None:
                return
            can_write = level >= (board.min_write_level or 0)
            threads = (Post.query.filter_by(board_id=board_id, parent_id=None)
                      .order_by(Post.created_at.desc()).limit(50).all())
            rows = [(t.id, t.subject, t.replies.count()) for t in threads]

        w = _width(session)
        max_nrep_digits = max((len(str(nrep)) for _, _, nrep in rows), default=1)
        reply_w = max_nrep_digits + 2  # "(" + digits + ")"
        subj_w = max(10, w - 8 - reply_w)
        def _render(i, row):
            _pid, subj, nrep = row
            return f'{i:>3}. {_truncate_words(subj, subj_w):<{subj_w}} ({nrep})'

        pick = await _paginated_pick(session, f'Board: {board_name}', rows,
                                     _render, 'read, N=new post')
        if pick == 'Q':
            return
        if pick == 'N':
            if can_write:
                await _board_post(session, board_id)
            else:
                await session.write("\r\nYou don't have permission to post here.\r\n")
                await session.read_line('Press ENTER...')
            continue
        if isinstance(pick, int):
            await _thread_read(session, rows[pick - 1][0], board_id, board_name)


async def _thread_read(session, post_id, board_id, board_name):
    with _app_ctx():
        from ..models import Post, User
        root = Post.query.get(post_id)
        if root is None or root.board_id != board_id:
            return
        root_author = User.query.get(root.author_id)
        lines = [f'Subject: {root.subject}',
                f'From: {root_author.username if root_author else "?"}', '']
        lines.extend(_wrap_body(root.content, _width(session)))
        subject = root.subject
        replies = root.replies.order_by(Post.created_at).all()
        for r in replies:
            r_author = User.query.get(r.author_id)
            lines.append('')
            lines.append(f'--- Reply from {r_author.username if r_author else "?"} ---')
            lines.extend(_wrap_body(r.content, _width(session)))

    if not await _paginate(session, lines, f'Board: {board_name}'):
        return
    choice = (await session.read_line('\r\nR=reply, Q=back: ') or '').strip().upper()
    if choice == 'R':
        await _board_post(session, board_id, parent_id=post_id, default_subject=subject)


async def _board_post(session, board_id, parent_id=None, default_subject=''):
    # Access control: single choke point for both the 'N' new-thread
    # caller (_board_threads, which already gated this on can_write) and
    # 'R' reply (_thread_read, which previously did NOT) -- checked here
    # too so neither caller can be the one weak link. Mirrors web/
    # boards.py's new_post() min_write_level gate and reply_post()'s
    # is_locked gate, found missing from the reply path in a full
    # message-boards security audit.
    level = _access_level(session)
    with _app_ctx():
        from ..models import Board, Post
        board = Board.query.get(board_id)
        if board is None:
            return
        if level < (board.min_write_level or 0):
            await session.write("\r\nYou don't have permission to post here.\r\n")
            await session.read_line('Press ENTER...')
            return
        if parent_id is not None:
            root = Post.query.get(parent_id)
            if root is not None and root.is_locked and level < 100:
                await session.write('\r\nThis thread is locked.\r\n')
                await session.read_line('Press ENTER...')
                return

    await _header(session, 'New Post' if parent_id is None else 'Reply')
    default_subj = f'Re: {default_subject}' if default_subject else ''
    prompt = f'Subject{f" [{default_subj}]" if default_subj else ""}: '
    subject = (await session.read_line(prompt) or '').strip() or default_subj
    if not subject:
        await session.write('No subject -- cancelled.\r\n')
        await session.read_line('Press ENTER...')
        return
    body = await _compose_body(session)
    if body is None:
        await session.write('Cancelled.\r\n')
        await session.read_line('Press ENTER...')
        return
    with _app_ctx():
        from ..models import db, Post
        # Sysop-configured word-filter blocklist -- see bbs_ui.py's
        # _post_compose for the matching ANSI-side fix/comment. Must
        # run INSIDE the app context -- apply() queries WordFilter.
        try:
            from .word_filter import apply as _wf_apply
            subject = _wf_apply(subject)
            body = _wf_apply(body)
        except Exception:
            pass
        post = Post(board_id=board_id, author_id=_user_id(session),
                    parent_id=parent_id, subject=subject[:200], content=body)
        db.session.add(post)
        db.session.commit()
    await session.write('\r\nPosted.\r\n')
    await session.read_line('Press ENTER...')


# ─── Echomail ───────────────────────────────────────────────────────────

async def _echomail_menu(session):
    """Network picker -- real report: a sysop subscribed to several real
    networks (tqwnet, Fidonet, DOVE-Net, sp00knet, ANotherNetwork...) saw
    every area from every network combined into one flat list ("not all
    combine"). Pick a network first, then _echomail_areas_menu() shows
    only that network's areas."""
    level = _access_level(session)
    while True:
        with _app_ctx():
            from ..models import EchoArea, EchomailNetwork
            visible_areas = (EchoArea.query
                             .filter_by(is_active=True, is_subscribed=True, is_sysop_only=False)
                             .filter(EchoArea.min_access_level <= level))
            net_ids = sorted({nid for (nid,) in
                              visible_areas.with_entities(EchoArea.network_id).distinct()})
            nets = (EchomailNetwork.query.filter(EchomailNetwork.id.in_(net_ids)).all()
                   if net_ids else [])
            rows = sorted(((n.id, n.name) for n in nets), key=lambda r: r[1])

        if not rows:
            await _header(session, 'Echomail Networks')
            await session.write('No echomail areas available.\r\n')
            await session.read_line('\r\nPress ENTER...')
            return

        w = _width(session)
        name_w = max(10, w - 8)
        pick = await _paginated_pick(
            session, 'Echomail Networks', rows,
            lambda i, row: f'{i:>3}. {_truncate_words(row[1], name_w)}', 'select')
        if pick == 'Q':
            return
        if isinstance(pick, int):
            await _echomail_areas_menu(session, rows[pick - 1][0], rows[pick - 1][1])


async def _echomail_areas_menu(session, network_id, network_name):
    level = _access_level(session)
    while True:
        with _app_ctx():
            from ..models import EchoArea
            areas = (EchoArea.query
                    .filter_by(is_active=True, is_subscribed=True, is_sysop_only=False,
                              network_id=network_id)
                    .filter(EchoArea.min_access_level <= level)
                    .order_by(EchoArea.category, EchoArea.order, EchoArea.name).all())
            rows = [(a.id, a.name) for a in areas]

        title = f'Areas: {network_name}'
        if not rows:
            await _header(session, title)
            await session.write('No areas available.\r\n')
            await session.read_line('\r\nPress ENTER...')
            return

        w = _width(session)
        name_w = max(10, w - 8)
        pick = await _paginated_pick(
            session, title, rows,
            lambda i, row: f'{i:>3}. {_truncate_words(row[1], name_w)}', 'select')
        if pick == 'Q':
            return
        if isinstance(pick, int):
            await _echo_messages(session, rows[pick - 1][0], rows[pick - 1][1])


async def _echo_messages(session, area_id, area_name):
    while True:
        with _app_ctx():
            from ..models import EchomailMessage
            msgs = (EchomailMessage.query.filter_by(area_id=area_id)
                   .order_by(EchomailMessage.created_at.desc()).limit(50).all())
            rows = [(m.id, m.from_name, m.subject) for m in msgs]

        # Real report: subject preview cut off mid-word ("voluntarily" ->
        # "volun") with a fixed frm-name reservation leaving unused space
        # when names are short. Size the from-name column from the actual
        # longest name present (same technique as _echomail_menu's
        # network-name fix) and truncate the subject at a word boundary.
        w = _width(session)
        frm_w = min(max((len(f) for _, f, _ in rows), default=4), max(4, w - 8 - 10))
        subj_w = max(10, w - 8 - frm_w)
        def _render(i, row):
            _mid, frm, subj = row
            subj_display = _truncate_words(subj, subj_w)
            return f'{i:>3}. {subj_display:<{subj_w}} {frm[:frm_w]}'

        pick = await _paginated_pick(session, f'Area: {area_name}', rows,
                                     _render, 'read, N=new')
        if pick == 'Q':
            return
        if pick == 'N':
            await _echo_compose(session, area_id, area_name)
            continue
        if isinstance(pick, int):
            await _echo_message_read(session, rows[pick - 1][0], area_id, area_name)


async def _echo_message_read(session, msg_id, area_id, area_name):
    with _app_ctx():
        from ..models import EchomailMessage
        msg = EchomailMessage.query.get(msg_id)
        if msg is None:
            return
        lines = [f'From: {msg.from_name}', f'To: {msg.to_name}',
                f'Subject: {msg.subject}', '']
        lines.extend(_wrap_body(msg.body, _width(session)))
        from_name, subject = msg.from_name, msg.subject

    if not await _paginate(session, lines, f'Area: {area_name}'):
        return
    choice = (await session.read_line('\r\nR=reply, Q=back: ') or '').strip().upper()
    if choice == 'R':
        await _echo_compose(session, area_id, area_name,
                            reply_to_name=from_name, reply_subject=subject)


async def _echo_compose(session, area_id, area_name, reply_to_name=None, reply_subject=None):
    from .access_control import resolve_post_name
    with _app_ctx():
        from ..models import EchoArea as _EchoArea
        _area = _EchoArea.query.get(area_id)
        post_name, name_error = resolve_post_name(
            session.user, bool(_area and _area.require_real_name))
    if name_error:
        await _header(session, f'New message: {area_name}'[:_width(session)])
        await session.write(name_error + '\r\n')
        await session.read_line('Press ENTER...')
        return

    await _header(session, f'New message: {area_name}'[:_width(session)])
    default_to = reply_to_name or 'All'
    to_name = (await session.read_line(f'To [{default_to}]: ') or '').strip() or default_to
    default_subj = f'Re: {reply_subject}' if reply_subject else ''
    prompt = f'Subject{f" [{default_subj}]" if default_subj else ""}: '
    subject = (await session.read_line(prompt) or '').strip() or default_subj
    if not subject:
        await session.write('No subject -- cancelled.\r\n')
        await session.read_line('Press ENTER...')
        return
    body = await _compose_body(session)
    if body is None:
        await session.write('Cancelled.\r\n')
        await session.read_line('Press ENTER...')
        return

    with _app_ctx():
        from flask import current_app
        from ..models import (db, EchoArea, EchomailNetwork, EchomailMessage,
                              maybe_tag_ansi_subject)
        area = EchoArea.query.get(area_id)
        network = EchomailNetwork.query.get(area.network_id) if area else None
        msg = EchomailMessage(
            area_id=area_id,
            network_id=area.network_id if area else None,
            from_name=post_name[:100],
            to_name=to_name[:100],
            subject=maybe_tag_ansi_subject(subject, body)[:200],
            body=body,
            tear_line=current_app.config.get('ECHOMAIL_TEAR_LINE', '--- ANetBBS v1.0'),
            origin_line=current_app.config.get('ECHOMAIL_ORIGIN_LINE', 'ANetBBS'),
            direction='outbound',
        )
        db.session.add(msg)
        db.session.commit()
        # Real gap found live: a hub with real downstream nodes -- see
        # the identical fix + full explanation in bbs_ui.py's
        # _compose_echomail and web/echomail.py's compose(). This is the
        # third of three local composers that never called toss_message().
        from ..echomail.tosser import toss_message
        toss_message(msg.id)
        if area is not None and network is not None:
            from ..echomail.notify_reply import maybe_notify_recipient
            maybe_notify_recipient(msg, area, network)
    await session.write('\r\nMessage posted.\r\n')
    await session.read_line('Press ENTER...')


# ─── Private messages ───────────────────────────────────────────────────

async def _pm_menu(session):
    my_id = _user_id(session)
    while True:
        with _app_ctx():
            from ..models import PrivateMessage, User
            msgs = (PrivateMessage.query
                   .filter_by(recipient_id=my_id, is_deleted_recipient=False)
                   .order_by(PrivateMessage.created_at.desc()).limit(50).all())
            sender_names = {}
            rows = []
            for m in msgs:
                if m.sender_id not in sender_names:
                    u = User.query.get(m.sender_id)
                    sender_names[m.sender_id] = u.username if u else '?'
                rows.append((m.id, m.subject, sender_names[m.sender_id], m.read_at is None))

        w = _width(session)
        sender_w = min(max((len(s) for _, _, s, _ in rows), default=4),
                      max(4, w - 9 - 10))
        subj_w = max(10, w - 9 - sender_w)
        def _render(i, row):
            _mid, subj, sender, unread = row
            mark = '*' if unread else ' '
            return (f'{mark}{i:>3}. {_truncate_words(subj, subj_w):<{subj_w}} '
                    f'{sender[:sender_w]}')

        pick = await _paginated_pick(session, 'Private Messages', rows,
                                     _render, 'read, N=new')
        if pick == 'Q':
            return
        if pick == 'N':
            await _pm_compose(session)
            continue
        if isinstance(pick, int):
            await _pm_read(session, rows[pick - 1][0])


async def _pm_read(session, msg_id):
    with _app_ctx():
        from ..models import db, PrivateMessage, User
        msg = PrivateMessage.query.get(msg_id)
        if msg is None:
            return
        my_id = _user_id(session)
        if msg.recipient_id != my_id and msg.sender_id != my_id:
            return
        sender = User.query.get(msg.sender_id)
        sender_name = sender.username if sender else '?'
        lines = [f'From: {sender_name}', f'Subject: {msg.subject}', '']
        lines.extend(_wrap_body(msg.body, _width(session)))
        subject = msg.subject
        if msg.read_at is None:
            msg.read_at = datetime.utcnow()
            db.session.commit()

    if not await _paginate(session, lines, 'Private Message'):
        return
    choice = (await session.read_line('\r\nR=reply, Q=back: ') or '').strip().upper()
    if choice == 'R':
        await _pm_compose(session, reply_to_username=sender_name, reply_subject=subject)


async def _pm_compose(session, reply_to_username=None, reply_subject=None):
    await _header(session, 'New Private Message')
    prompt = f'To{f" [{reply_to_username}]" if reply_to_username else ""}: '
    to_username = (await session.read_line(prompt) or '').strip() or (reply_to_username or '')
    if not to_username:
        await session.write('No recipient -- cancelled.\r\n')
        await session.read_line('Press ENTER...')
        return
    default_subj = f'Re: {reply_subject}' if reply_subject else ''
    subj_prompt = f'Subject{f" [{default_subj}]" if default_subj else ""}: '
    subject = (await session.read_line(subj_prompt) or '').strip() or default_subj
    if not subject:
        await session.write('No subject -- cancelled.\r\n')
        await session.read_line('Press ENTER...')
        return
    body = await _compose_body(session)
    if body is None:
        await session.write('Cancelled.\r\n')
        await session.read_line('Press ENTER...')
        return

    my_id = _user_id(session)
    my_username = _username(session)
    with _app_ctx():
        from sqlalchemy import func
        from ..models import db, User, PrivateMessage
        recipient = User.query.filter(func.lower(User.username) == to_username.lower()).first()
        if recipient is None:
            await session.write(f'\r\nNo such user: {to_username}\r\n')
            await session.read_line('Press ENTER...')
            return
        pm = PrivateMessage(sender_id=my_id, recipient_id=recipient.id,
                            subject=subject[:200], body=body)
        db.session.add(pm)
        db.session.commit()
        try:
            from ..features.notify import notify
            notify(recipient.id, 'pm', title=f'New PM from {my_username}',
                  body=subject[:120], target_url=f'/messages/{pm.id}')
        except Exception:
            pass
    await session.write('\r\nSent.\r\n')
    await session.read_line('Press ENTER...')


# ─── File areas (browse only, Phase 1) ──────────────────────────────────

async def _files_menu(session):
    level = _access_level(session)
    while True:
        with _app_ctx():
            from ..models import FileArea
            areas = (FileArea.query.filter_by(is_active=True, is_sysop_only=False)
                    .filter(FileArea.min_access_level <= level)
                    .order_by(FileArea.name).all())
            rows = [(a.id, a.name) for a in areas]

        if not rows:
            await _header(session, 'File Areas')
            await session.write('No file areas available.\r\n')
            await session.read_line('\r\nPress ENTER...')
            return

        name_w = max(10, _width(session) - 7)
        pick = await _paginated_pick(
            session, 'File Areas', rows,
            lambda i, row: f'{i:>3}. {_truncate_words(row[1], name_w)}', 'select')
        if pick == 'Q':
            return
        if isinstance(pick, int):
            await _files_browse(session, rows[pick - 1][0], rows[pick - 1][1])


async def _files_browse(session, area_id, area_name):
    """Brief listing (name+size only) with an immediate numbered picker
    -- real report: showing every file's full FILE_ID.DIZ-derived
    description inline (even after stripping ANSI art) meant paging
    through several files' worth of description text before ever
    reaching a download prompt, and quitting early with Q at any
    -- More -- along the way exited the whole area instead of just
    skipping the descriptions ("there are numbers, but there is no way
    to download"). Extended/full description is now opt-in via E."""
    while True:
        with _app_ctx():
            import os
            from ..models import FileArea, FileUpload
            area = FileArea.query.get(area_id)
            if area is None:
                return
            rows = []
            if area.storage_path:
                from ..web.file_areas import _scan_area
                for f in _scan_area(area):
                    rows.append((f['name'], f['size'], f['description'],
                               os.path.join(area.storage_path, f['name'])))
            else:
                uploads = (FileUpload.query.filter_by(file_area_id=area_id)
                          .filter(FileUpload.is_public.isnot(False))
                          .order_by(FileUpload.created_at.desc()).all())
                for u in uploads:
                    rows.append((u.original_filename or u.filename, u.file_size,
                               u.description or '', u.file_path or ''))

        title = f'Files: {area_name}'[:_width(session)]
        if not rows:
            await _header(session, title)
            await session.write('No files available.\r\n')
            await session.read_line('\r\nPress ENTER...')
            return

        w = _width(session)
        name_w = max(10, w - 16)
        def _render(i, row):
            name, size, _desc, _path = row
            size_kb = (size or 0) // 1024
            return f'{i:>3}. {name[:name_w]:<{name_w}} {size_kb:>6}K'

        pick = await _paginated_pick(session, title, rows, _render,
                                     'download, E=extended info')
        if pick == 'Q':
            return
        if pick == 'E':
            num = await _pick(session, 'File # for extended info: ', len(rows))
            if isinstance(num, int):
                await _files_show_description(session, rows[num - 1])
            continue
        if isinstance(pick, int):
            await _files_download(session, rows[pick - 1])


async def _files_show_description(session, row):
    """Full/extended description, on demand (E from _files_browse) --
    the brief listing shows name+size only."""
    name, size, desc, _path = row
    w = _width(session)
    size_kb = (size or 0) // 1024
    lines = [f'{name}', f'Size: {size_kb}K', '']
    lines.extend(_wrap_body(desc, w - 2) if desc else ['(no description)'])
    await _paginate(session, lines, f'File Info: {name}'[:w])
    await session.read_line('\r\nPress ENTER...')


async def _write_wrapped(session, text):
    """Word-wrap `text` to the session's width before writing -- real
    report: a long status line (filename + fixed message text) written
    unwrapped hard-broke mid-word at the terminal's column boundary on
    a 40-col screen ("A-NET<-door-score / s1.5.zip")."""
    w = _width(session)
    await session.write('\r\n' + '\r\n'.join(_wrap_body(text, w)) + '\r\n')


async def _files_download(session, row):
    """XMODEM-only, per sysop's choice -- the simplest protocol real C64
    terminal software (Novaterm, CCGMS, 64NIC+) most reliably supports.
    Reuses features.xfer.send_file() as-is: PETSCII connections are
    plain telnet sockets (see core/petscii_server.py's own docstring),
    so xfer.py's existing telnet IAC-escaping already applies correctly
    with no PETSCII-specific changes needed there."""
    import os
    name, size, _desc, path = row
    if not path or not os.path.isfile(path):
        await _write_wrapped(session, f'{name}: file not found on server disk.')
        await session.read_line('Press ENTER...')
        return

    from .xfer import available_protocols, send_file
    if 'xmodem' not in available_protocols():
        await _write_wrapped(
            session, 'XMODEM is not available on this server (sysop must '
                    'install lrzsz).')
        await session.read_line('Press ENTER...')
        return

    # Daily download quota (FR: Firehawke, 2026-07-24) -- see
    # features/file_quota.py. Wrapped in _app_ctx() like every other
    # DB-touching block in this module (no ambient app context here).
    from .file_quota import check_quota, consume_quota
    quota_size = size if isinstance(size, int) else 0
    with _app_ctx():
        quota_ok, quota_msg = check_quota(session.user, quota_size)
    if not quota_ok:
        await _write_wrapped(session, quota_msg)
        await session.read_line('Press ENTER...')
        return

    await _write_wrapped(
        session, f'Starting XMODEM send of {name} -- start your '
                f'terminal\'s receive now.')
    import asyncio
    await asyncio.sleep(1)
    try:
        ok = await send_file(session, path, 'xmodem')
    except Exception:
        ok = False
    if ok and quota_size:
        with _app_ctx():
            consume_quota(session.user, quota_size)
    await _write_wrapped(
        session, f'{name}: transfer complete.' if ok else
        f'{name}: transfer failed or was cancelled.')
    await session.read_line('Press ENTER...')


# ─── Who's online ───────────────────────────────────────────────────────

async def _whos_online(session):
    with _app_ctx():
        from ..models import UserSession, User
        cutoff = datetime.utcnow() - timedelta(minutes=5)
        sessions = (UserSession.query.filter(UserSession.last_seen >= cutoff)
                   .order_by(UserSession.last_seen.desc()).all())
        rows = []
        for s in sessions:
            u = User.query.get(s.user_id)
            rows.append((u.username if u else '?', s.page or ''))

    await _header(session, "Who's Online")
    w = _width(session)
    if not rows:
        await session.write('Nobody else is online right now.\r\n')
    else:
        name_w = max(8, w - 20)
        for uname, page in rows:
            await session.write(f'  {uname[:name_w]:<{name_w}} {page}\r\n')
    await session.read_line('\r\nPress ENTER...')


# ─── Profile ────────────────────────────────────────────────────────────

async def _profile(session):
    my_id = _user_id(session)
    while True:
        with _app_ctx():
            from ..models import User
            u = User.query.get(my_id)
            if u is None:
                return
            lines = [
                f'Username: {u.username}',
                f'Display name: {u.display_name or "-"}',
                f'Location: {u.location or "-"}',
                f"Member since: {fmt_eastern(u.created_at, '%Y-%m-%d', '-')}",
                f"Last login: {fmt_eastern(u.last_login, '%Y-%m-%d %H:%M', '-')}",
                f'Logins: {u.login_count or 0}',
                '',
                'Bio:',
            ]
            lines.extend(_wrap_body(u.bio or '(none)', _width(session)))

        if not await _paginate(session, lines, 'Your Profile'):
            return
        choice = (await session.read_line('\r\nE=edit, Q=back: ') or '').strip().upper()
        if choice == 'E':
            await _profile_edit(session)
            continue
        return


# Editable profile fields -- matches a subset of bbs_ui.py's
# _PROFILE_TEXT_FIELDS (the ANSI edit-profile screen's full set), kept
# small since these are the only three fields the PETSCII profile VIEW
# already shows. Other ANSI-only fields (theme, sixel, codepage --
# meaningless for a real C64) are deliberately not exposed here.
_PROFILE_EDIT_FIELDS = {
    '1': ('display_name', 'Display name', 100),
    '2': ('location', 'Location', 100),
    '3': ('bio', 'Bio', 500),
}


async def _profile_edit(session):
    my_id = _user_id(session)
    while True:
        with _app_ctx():
            from ..models import User
            u = User.query.get(my_id)
            if u is None:
                return
            current = {k: (getattr(u, attr, '') or '-')
                      for k, (attr, _label, _maxlen) in _PROFILE_EDIT_FIELDS.items()}

        await _header(session, 'Edit Profile')
        w = _width(session)
        for k, (_attr, label, _maxlen) in _PROFILE_EDIT_FIELDS.items():
            val_w = max(4, w - len(label) - 6)
            await session.write(f'  {k}. {label}: {current[k][:val_w]}\r\n')
        await session.write('  Q. Done\r\n')
        choice = (await session.read_line('\r\nChoice: ') or '').strip().upper()
        if choice == 'Q' or not choice:
            return
        field = _PROFILE_EDIT_FIELDS.get(choice)
        if field is None:
            continue
        attr, label, maxlen = field
        new_val = (await session.read_line(f'{label} (blank to clear): ') or '').strip()
        with _app_ctx():
            from ..models import db, User
            u2 = User.query.get(my_id)
            if u2 is None:
                return
            setattr(u2, attr, new_val[:maxlen] or None)
            db.session.commit()
        await session.write('Saved.\r\n')


# ─── Games ───────────────────────────────────────────────────────────────

async def _games_menu(session):
    """Only Number Guessing -- see module docstring for why door games
    and other doors aren't offered here."""
    while True:
        await _header(session, 'Games')
        await session.write('  1. Number Guessing\r\n  Q. Back\r\n')
        choice = (await session.read_line('\r\nChoice: ') or '').strip().upper()
        if choice == 'Q' or not choice:
            return
        if choice == '1':
            from .games import GameManager
            await GameManager(session).play_number_guess()


_register_custom_menu_actions()
