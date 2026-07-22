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
browsing (no transfer), who's-online, profile, logoff. Games/doors/
ANetCRAFT/DarkForces/MRC chat/IRC are intentionally never offered here
at all -- not shown, not marked "unavailable," just absent, matching
how the session-level logon/logoff modules (Wall, ANSI art) and Games
menu are already skipped entirely for term_mode == 'petscii'.

No lightbar, no ANEdit -- every screen is a plain numbered list read
via read_line()/int(), and message bodies use a simple line-by-line
composer (a lone "/send" or "/abort" line to finish) since neither of
those cursor-addressed editors has any PETSCII-aware rendering to reuse.
"""
import textwrap
from datetime import datetime, timedelta

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


async def _paginate(session, lines):
    """Print lines PAGE_LINES at a time, pausing between pages -- the
    plain-text stand-in for ANEdit/ANView's full-screen reader."""
    if not lines:
        await session.write('(nothing here)\r\n')
        return
    for i in range(0, len(lines), PAGE_LINES):
        for line in lines[i:i + PAGE_LINES]:
            await session.write(line + '\r\n')
        if i + PAGE_LINES < len(lines):
            await session.read_line('-- More -- Press ENTER --')


def _wrap_body(text, width):
    out = []
    for para in (text or '').replace('\r\n', '\n').split('\n'):
        out.extend(textwrap.wrap(para, width) or [''])
    return out


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


# ─── Main menu ──────────────────────────────────────────────────────────

async def run_petscii_menu(session):
    """Entry point called from BBSSession right after login when
    term_mode == 'petscii'."""
    while True:
        await _header(session, 'ANetBBS Main Menu')
        await session.write(
            "  1. Message Boards\r\n"
            "  2. Echomail\r\n"
            "  3. Private Messages\r\n"
            "  4. File Areas\r\n"
            "  5. Who's Online\r\n"
            "  6. Your Profile\r\n"
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
        elif choice == 'Q':
            await session.write('\r\nGoodbye!\r\n')
            return


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

        await _header(session, 'Message Boards')
        if not rows:
            await session.write('No boards available.\r\n')
            await session.read_line('\r\nPress ENTER...')
            return
        for i, (_bid, name) in enumerate(rows, 1):
            await session.write(f'{i:>3}. {name}\r\n')

        pick = await _pick(session, '\r\n#, or Q: ', len(rows))
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

        await _header(session, f'Board: {board_name}')
        w = _width(session)
        for i, (_pid, subj, nrep) in enumerate(rows, 1):
            subj_w = max(10, w - 14)
            await session.write(f'{i:>3}. {subj[:subj_w]:<{subj_w}} ({nrep})\r\n')

        pick = await _pick(session, '\r\n#=read, N=new post, Q=back: ', len(rows))
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
        if root is None:
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

    await _header(session, f'Board: {board_name}')
    await _paginate(session, lines)
    choice = (await session.read_line('\r\nR=reply, Q=back: ') or '').strip().upper()
    if choice == 'R':
        await _board_post(session, board_id, parent_id=post_id, default_subject=subject)


async def _board_post(session, board_id, parent_id=None, default_subject=''):
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
        post = Post(board_id=board_id, author_id=_user_id(session),
                    parent_id=parent_id, subject=subject[:200], content=body)
        db.session.add(post)
        db.session.commit()
    await session.write('\r\nPosted.\r\n')
    await session.read_line('Press ENTER...')


# ─── Echomail ───────────────────────────────────────────────────────────

async def _echomail_menu(session):
    level = _access_level(session)
    while True:
        with _app_ctx():
            from ..models import EchoArea, EchomailNetwork
            areas = (EchoArea.query
                    .filter_by(is_active=True, is_subscribed=True, is_sysop_only=False)
                    .filter(EchoArea.min_access_level <= level)
                    .order_by(EchoArea.category, EchoArea.order, EchoArea.name).all())
            rows = []
            for a in areas:
                net = EchomailNetwork.query.get(a.network_id)
                rows.append((a.id, a.name, net.name if net else '?'))

        await _header(session, 'Echomail Areas')
        if not rows:
            await session.write('No echomail areas available.\r\n')
            await session.read_line('\r\nPress ENTER...')
            return
        w = _width(session)
        for i, (_aid, name, netname) in enumerate(rows, 1):
            name_w = max(10, w - 16)
            await session.write(f'{i:>3}. {name[:name_w]:<{name_w}} [{netname}]\r\n')

        pick = await _pick(session, '\r\n#, or Q: ', len(rows))
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

        await _header(session, f'Area: {area_name}')
        w = _width(session)
        for i, (_mid, frm, subj) in enumerate(rows, 1):
            subj_w = max(10, w - 22)
            await session.write(f'{i:>3}. {subj[:subj_w]:<{subj_w}} {frm}\r\n')

        pick = await _pick(session, '\r\n#=read, N=new, Q=back: ', len(rows))
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

    await _header(session, f'Area: {area_name}')
    await _paginate(session, lines)
    choice = (await session.read_line('\r\nR=reply, Q=back: ') or '').strip().upper()
    if choice == 'R':
        await _echo_compose(session, area_id, area_name,
                            reply_to_name=from_name, reply_subject=subject)


async def _echo_compose(session, area_id, area_name, reply_to_name=None, reply_subject=None):
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
        from ..models import db, EchoArea, EchomailNetwork, EchomailMessage
        area = EchoArea.query.get(area_id)
        network = EchomailNetwork.query.get(area.network_id) if area else None
        msg = EchomailMessage(
            area_id=area_id,
            network_id=area.network_id if area else None,
            from_name=_username(session)[:100],
            to_name=to_name[:100],
            subject=subject[:200],
            body=body,
            direction='outbound',
        )
        db.session.add(msg)
        db.session.commit()
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

        await _header(session, 'Private Messages')
        if not rows:
            await session.write('No messages.\r\n')
        w = _width(session)
        for i, (_mid, subj, sender, unread) in enumerate(rows, 1):
            mark = '*' if unread else ' '
            subj_w = max(10, w - 24)
            await session.write(f'{mark}{i:>3}. {subj[:subj_w]:<{subj_w}} {sender}\r\n')

        pick = await _pick(session, '\r\n#=read, N=new, Q=back: ', len(rows))
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
        sender = User.query.get(msg.sender_id)
        sender_name = sender.username if sender else '?'
        lines = [f'From: {sender_name}', f'Subject: {msg.subject}', '']
        lines.extend(_wrap_body(msg.body, _width(session)))
        subject = msg.subject
        if msg.read_at is None:
            msg.read_at = datetime.utcnow()
            db.session.commit()

    await _header(session, 'Private Message')
    await _paginate(session, lines)
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

        await _header(session, 'File Areas')
        if not rows:
            await session.write('No file areas available.\r\n')
            await session.read_line('\r\nPress ENTER...')
            return
        for i, (_aid, name) in enumerate(rows, 1):
            await session.write(f'{i:>3}. {name}\r\n')

        pick = await _pick(session, '\r\n#, or Q: ', len(rows))
        if pick == 'Q':
            return
        if isinstance(pick, int):
            await _files_browse(session, rows[pick - 1][0], rows[pick - 1][1])


async def _files_browse(session, area_id, area_name):
    with _app_ctx():
        from ..models import FileArea, FileUpload
        area = FileArea.query.get(area_id)
        if area is None:
            return
        rows = []
        if area.storage_path:
            from ..web.file_areas import _scan_area
            for f in _scan_area(area):
                rows.append((f['name'], f['size'], f['description']))
        else:
            uploads = (FileUpload.query.filter_by(file_area_id=area_id)
                      .filter(FileUpload.is_public.isnot(False))
                      .order_by(FileUpload.created_at.desc()).all())
            for u in uploads:
                rows.append((u.original_filename or u.filename, u.file_size, u.description or ''))

    w = _width(session)
    lines = []
    name_w = max(10, w - 16)
    for name, size, desc in rows:
        size_kb = (size or 0) // 1024
        lines.append(f'{name[:name_w]:<{name_w}} {size_kb:>6}K')
        if desc:
            lines.extend(textwrap.wrap(desc, w - 2))
    await _header(session, f'Files: {area_name}'[:w])
    await _paginate(session, lines)
    await session.read_line('\r\nPress ENTER...')


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
    with _app_ctx():
        from ..models import User
        u = User.query.get(my_id)
        if u is None:
            return
        lines = [
            f'Username: {u.username}',
            f'Display name: {u.display_name or "-"}',
            f'Location: {u.location or "-"}',
            f'Member since: {u.created_at.strftime("%Y-%m-%d") if u.created_at else "-"}',
            f'Last login: {u.last_login.strftime("%Y-%m-%d %H:%M") if u.last_login else "-"}',
            f'Logins: {u.login_count or 0}',
            '',
            'Bio:',
        ]
        lines.extend(_wrap_body(u.bio or '(none)', _width(session)))

    await _header(session, 'Your Profile')
    await _paginate(session, lines)
    await session.read_line('\r\nPress ENTER...')
