"""
Telnet/SSH/rlogin BBS UI menus that read from the SAME tables the web uses.

Currently provides view-mode for:
  - Online users (web + telnet/SSH/rlogin combined, via UserSession)
  - Message boards (browse boards → list threads → read thread)
  - Bulletins (read pinned/announcement messages)
  - Private messages (inbox + sent)
  - Echomail areas (browse areas → list messages → read)
  - File library (list files)
  - User profile (view own info)

All operations push a transient Flask app context so they can use the
SQLAlchemy-mapped models without needing to be inside a Flask request.
"""
import asyncio
import os
import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _app():
    """Make a transient Flask app + db context. Call inside `with`."""
    from flask import Flask
    from anetbbs.config import get_config
    from anetbbs.models import db
    app = Flask(__name__)
    app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
    db.init_app(app)
    return app


def _parse_file_selection(text, lo, hi):
    """Parse a file-number selection string.

    Accepts:
      "5"       → [5]
      "1,3,5"   → [1, 3, 5]
      "1-5"     → [1, 2, 3, 4, 5]
      "1-3,7"   → [1, 2, 3, 7]

    Returns a sorted, deduplicated list of ints in [lo, hi], or None if
    the input is not parseable as a numeric selection.
    """
    text = text.strip()
    if not text:
        return None
    nums = set()
    try:
        for part in text.split(','):
            part = part.strip()
            if '-' in part:
                a, b = part.split('-', 1)
                for n in range(int(a.strip()), int(b.strip()) + 1):
                    nums.add(n)
            else:
                nums.add(int(part))
    except (ValueError, TypeError):
        return None
    valid = sorted(n for n in nums if lo <= n <= hi)
    return valid if valid else None


# ---------------------------------------------------------------------------
# Top-level BBS menu
# ---------------------------------------------------------------------------

class BBSMenuUI:
    """Reusable menu helpers attached to a BBSSession."""

    def __init__(self, session):
        self.session = session

    async def show_main(self):
        """Top-level 'Main BBS' menu — wired into BBSSession.show_main_menu()."""
        while True:
            menu = (
                "\r\n"
                "╔══════════════════════════════════════════╗\r\n"
                "║              Main BBS Menu               ║\r\n"
                "╠══════════════════════════════════════════╣\r\n"
                "║  M. Message Boards                       ║\r\n"
                "║  B. Bulletins                            ║\r\n"
                "║  P. Private Messages                     ║\r\n"
                "║  E. Echomail                             ║\r\n"
                "║  F. File Library                         ║\r\n"
                "║  R. RSS News Reader                      ║\r\n"
                "║  U. Who's Online                         ║\r\n"
                "║  Y. Your Profile                         ║\r\n"
                "║  Q. Return                               ║\r\n"
                "╚══════════════════════════════════════════╝\r\n"
                "\r\n"
                "Choice: "
            )
            choice = (await self.session.read_line(menu) or '').strip().upper()
            if choice == 'Q':
                return
            elif choice == 'M':
                await self.list_boards()
            elif choice == 'B':
                await self.list_bulletins()
            elif choice == 'P':
                await self.list_pm_inbox()
            elif choice == 'E':
                await self.list_echo_areas()
            elif choice == 'F':
                await self.list_files()
            elif choice == 'R':
                await self.show_rss()
            elif choice == 'U':
                await self.show_online()
            elif choice == 'Y':
                await self.show_profile()

    # ------------------------------------------------------------------
    # Online users
    # ------------------------------------------------------------------

    async def show_online(self):
        from anetbbs.models import UserSession, User
        with _app().app_context():
            five_min_ago = datetime.utcnow() - timedelta(minutes=5)
            sessions = (UserSession.query
                        .filter(UserSession.last_seen >= five_min_ago)
                        .order_by(UserSession.last_seen.desc())
                        .all())
            rows = []
            for s in sessions:
                u = User.query.get(s.user_id)
                if not u:
                    continue
                # page field is "[telnet] chat" or "/boards/" etc — encodes protocol
                page = (s.page or '').strip()
                if page.startswith('['):
                    proto, _, where = page.partition(']')
                    proto = proto.lstrip('[')
                    where = (where or '').strip() or '-'
                else:
                    proto = 'web'
                    where = page or '-'
                rows.append((u.username, proto, where, s.last_seen))

        from .ansi_ui import banner, footer, FG, RESET, BOLD
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner("Who's Online"))
        if not rows:
            await self.session.write(
                f"  {FG['gry']}(no one is online right now){RESET}\r\n")
        else:
            await self.session.write(
                f"  {FG['cyan']}{BOLD}{'User':<16} {'Proto':<8} "
                f"{'Where':<22} {'Last seen':<12}{RESET}\r\n"
                f"  {FG['gry']}{'─' * 60}{RESET}\r\n")
            for u, proto, where, ts in rows:
                tstr = ts.strftime('%H:%M:%S') if ts else '?'
                proto_color = (FG['grn'] if proto == 'telnet'
                               else FG['mag'] if proto == 'ssh'
                               else FG['yel'] if proto == 'web'
                               else FG['cyan'])
                await self.session.write(
                    f"  {FG['wht']}{u[:16]:<16}{RESET} "
                    f"{proto_color}{proto[:8]:<8}{RESET} "
                    f"{FG['dim']}{where[:22]:<22}{RESET} "
                    f"{FG['cyan']}{tstr:<12}{RESET}\r\n")
        await self.session.write('\r\n' + footer() + '\r\n')
        await self.session.read_line(
            f"{FG['cyan']}Press Enter to continue...{RESET}")

    # ------------------------------------------------------------------
    # Message boards
    # ------------------------------------------------------------------

    async def list_boards(self):
        from anetbbs.models import Board
        from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD
        with _app().app_context():
            _user_level = int((self.session.user or {}).get('access_level', 10))
            _is_admin = bool((self.session.user or {}).get('is_admin'))
            _bq = Board.query.filter_by(is_active=True)
            if not _is_admin:
                _bq = _bq.filter(Board.min_access_level <= _user_level)
            boards = _bq.order_by(Board.order, Board.name).all()
            board_list = [(b.id, b.name, b.description or '', b.posts.count()) for b in boards]

        if not board_list:
            await self.session.write("\r\nNo boards configured yet.\r\n")
            await self.session.read_line("\r\nPress Enter...")
            return

        while True:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Message Boards'))
            for i, (_, name, desc, count) in enumerate(board_list, 1):
                await self.session.write(
                    f"  {FG['yel']}{BOLD}{i:2d}{RESET}{FG['gry']}.{RESET} "
                    f"{FG['grn']}{name:<28}{RESET} "
                    f"{FG['cyan']}({count:4d} threads){RESET}\r\n")
                if desc:
                    await self.session.write(
                        f"      {FG['dim']}{desc[:62]}{RESET}\r\n")
            await self.session.write(
                f"\r\n  {FG['yel']}{BOLD}Q{RESET}{FG['gry']}.{RESET} "
                f"{FG['red']}Return{RESET}\r\n")
            await self.session.write(footer() + '\r\n')
            choice = (await self.session.read_line(
                _prompt('Pick board (number / Q): ')) or '').strip()
            if choice.upper() == 'Q' or not choice:
                return
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(board_list):
                    await self.list_threads(board_list[idx][0], board_list[idx][1])
            except ValueError:
                pass

    async def list_threads(self, board_id, board_name):
        from anetbbs.models import Post, User
        with _app().app_context():
            threads = (Post.query
                       .filter_by(board_id=board_id, parent_id=None)
                       .order_by(Post.created_at.desc())
                       .limit(50).all())
            t_list = []
            for t in threads:
                author = User.query.get(t.author_id)
                t_list.append((t.id, t.subject, author.username if author else '?',
                               t.created_at, t.replies.count()))

        if not t_list:
            await self.session.write(f"\r\n{board_name}: no threads yet.\r\n")
            await self.session.read_line("\r\nPress Enter...")
            return

        while True:
            await self.session.write(f"\r\n=== {board_name} (latest 50 threads) ===\r\n\r\n")
            for i, (_, subj, who, when, n_replies) in enumerate(t_list, 1):
                ts = when.strftime('%m-%d %H:%M') if when else '?'
                line = f"  {i:2d}. [{n_replies:2d}] {subj[:35]:<35} by {who[:12]:<12} {ts}"
                await self.session.write(line + "\r\n")
            choice = (await self.session.read_line("\r\nPick thread (number) or Q: ") or '').strip()
            if choice.upper() == 'Q' or not choice:
                return
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(t_list):
                    await self.read_thread(t_list[idx][0])
            except ValueError:
                pass

    async def read_thread(self, post_id):
        from anetbbs.models import Post, User
        with _app().app_context():
            root = Post.query.get(post_id)
            if not root:
                return
            posts = [root] + list(root.replies.order_by(Post.created_at).all())
            rendered = []
            for p in posts:
                author = User.query.get(p.author_id)
                rendered.append({
                    'subject': p.subject,
                    'author': author.username if author else '?',
                    'when': p.created_at,
                    'content': p.content,
                })

        await self.session.write("\r\n" + "═" * 64 + "\r\n")
        for i, p in enumerate(rendered):
            ts = p['when'].strftime('%Y-%m-%d %H:%M') if p['when'] else '?'
            tag = '[OP]' if i == 0 else f'[Reply {i}]'
            await self.session.write(f"\r\n{tag} {p['subject']}\r\n")
            await self.session.write(f"From: {p['author']}    Date: {ts}\r\n")
            await self.session.write("─" * 64 + "\r\n")
            for line in (p['content'] or '').splitlines():
                await self.session.write(line[:78] + "\r\n")
            await self.session.write("\r\n")
        await self.session.read_line("Press Enter...")

    # ------------------------------------------------------------------
    # Bulletins
    # ------------------------------------------------------------------

    async def list_bulletins(self):
        from anetbbs.models import Message as Bulletin
        from anetbbs.models import User
        with _app().app_context():
            now = datetime.utcnow()
            bulletins = (Bulletin.query
                         .filter((Bulletin.expires_at == None) | (Bulletin.expires_at > now))
                         .order_by(Bulletin.is_pinned.desc(), Bulletin.created_at.desc())
                         .limit(20).all())
            b_list = []
            for b in bulletins:
                author = User.query.get(b.author_id)
                b_list.append((b.id, b.title, author.username if author else '?',
                               b.created_at, b.is_pinned, b.content))

        if not b_list:
            await self.session.write("\r\nNo bulletins.\r\n")
            await self.session.read_line("\r\nPress Enter...")
            return

        from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD
        while True:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Bulletins'))
            for i, (_, title, who, when, pinned, _) in enumerate(b_list, 1):
                ts = when.strftime('%m-%d') if when else '?'
                pin = (FG['yel'] + '*' + RESET) if pinned else ' '
                await self.session.write(
                    f"  {FG['yel']}{BOLD}{i:2d}{RESET}{FG['gry']}.{RESET}{pin} "
                    f"{FG['wht']}{title[:40]:<40}{RESET} "
                    f"{FG['gry']}by{RESET} {FG['grn']}{who[:12]:<12}{RESET} "
                    f"{FG['cyan']}{ts}{RESET}\r\n")
            await self.session.write('\r\n' + footer() + '\r\n')
            choice = (await self.session.read_line(
                _prompt('Pick bulletin (number / Q): ')) or '').strip()
            if choice.upper() == 'Q' or not choice:
                return
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(b_list):
                    _, title, who, when, _, content = b_list[idx]
                    ts = when.strftime('%Y-%m-%d %H:%M') if when else '?'
                    await self._page_text(content or '',
                                          title=title,
                                          subtitle=f'by {who} - {ts}')
            except ValueError:
                pass

    async def _page_text(self, body, title='', subtitle='', page_size=22):
        """Page through `body` one screenful at a time with a [MORE] prompt.

        Honors raw ANSI escape codes (so an ANSI bulletin renders) but
        breaks on every 22 visible-text lines so users can read at their
        own pace. After the last page, waits for a single keypress.
        """
        CYAN = '\x1b[96m'; YEL = '\x1b[93m'; WHT = '\x1b[97m'
        DIM = '\x1b[37m'; BOLD = '\x1b[1m'; RESET = '\x1b[0m'

        # Header
        await self.session.write(
            f'\r\n{BOLD}{CYAN}╔{"═" * 76}╗{RESET}\r\n')
        if title:
            await self.session.write(
                f'{BOLD}{CYAN}║ {WHT}{title[:74]:<74} {CYAN}║{RESET}\r\n')
        if subtitle:
            await self.session.write(
                f'{BOLD}{CYAN}║ {DIM}{subtitle[:74]:<74} {CYAN}║{RESET}\r\n')
        await self.session.write(
            f'{BOLD}{CYAN}╚{"═" * 76}╝{RESET}\r\n')

        # Word-wrap plain-text lines to the terminal width; pass ANSI lines
        # through unchanged (their visible width is hard to compute).
        col_w = max(40, (getattr(self.session, 'window_size', (80, 24))[0] or 80) - 2)
        raw_lines = (body or '').splitlines() or ['(empty)']
        lines = []
        for _ln in raw_lines:
            if '\x1b' in _ln:
                lines.append(_ln)
            else:
                lines.extend(self._wrap_text(_ln, col_w) or [''])
        total = len(lines)
        page = 0
        while page * page_size < total:
            chunk = lines[page * page_size:(page + 1) * page_size]
            for line in chunk:
                await self.session.write(line + '\r\n')
            page += 1
            if page * page_size >= total:
                # End of bulletin
                await self.session.read_line(
                    f'{YEL}--- end ---{RESET}  Press Enter to continue: ')
                return
            # Show pager prompt
            shown = min(page * page_size, total)
            prompt = (f'{YEL}--MORE--{RESET}  '
                      f'({shown}/{total} lines)  '
                      f'{DIM}[Enter]=more  [Q]=quit:{RESET} ')
            ans = (await self.session.read_line(prompt) or '').strip().upper()
            if ans == 'Q':
                return

    # ------------------------------------------------------------------
    # Private messages
    # ------------------------------------------------------------------

    async def list_pm_inbox(self):
        from anetbbs.models import db, PrivateMessage, User
        my_id = self.session.user['id']

        with _app().app_context():
            inbox = (PrivateMessage.query
                     .filter_by(recipient_id=my_id, is_deleted_recipient=False)
                     .order_by(PrivateMessage.created_at.desc())
                     .limit(50).all())
            i_list = []
            for m in inbox:
                sender = User.query.get(m.sender_id)
                i_list.append((m.id, m.subject, sender.username if sender else '?',
                               m.created_at, m.read_at is not None, m.body))

        if not i_list:
            from .ansi_ui import banner, FG, RESET
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('PM Inbox'))
            await self.session.write(
                f"  {FG['gry']}Your inbox is empty.{RESET}\r\n")
            await self.session.read_line("\r\nPress Enter...")
            return

        while True:
            from .ansi_ui import banner as _bnr
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(_bnr('PM Inbox'))
            for i, (_, subj, who, when, was_read, _) in enumerate(i_list, 1):
                ts = when.strftime('%m-%d %H:%M') if when else '?'
                mark = ' ' if was_read else '*'
                await self.session.write(f"  {i:2d}.{mark} {subj[:38]:<38} from {who[:12]:<12} {ts}\r\n")
            choice = (await self.session.read_line("\r\nPick message (number) or Q: ") or '').strip()
            if choice.upper() == 'Q' or not choice:
                return
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(i_list):
                    pm_id, subj, who, when, _, body = i_list[idx]
                    ts = when.strftime('%Y-%m-%d %H:%M') if when else '?'
                    await self.session.write("\r\n" + "═" * 64 + "\r\n")
                    await self.session.write(f"  Subject: {subj}\r\n  From: {who}\r\n  Date: {ts}\r\n")
                    await self.session.write("─" * 64 + "\r\n")
                    for line in (body or '').splitlines():
                        await self.session.write(line[:78] + "\r\n")
                    # Mark as read
                    with _app().app_context():
                        pm = PrivateMessage.query.get(pm_id)
                        if pm and pm.read_at is None:
                            pm.read_at = datetime.utcnow()
                            db.session.commit()
                    await self.session.read_line("\r\nPress Enter...")
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Inter-BBS Instant Messages (RFC 1312 / MSP)
    # ------------------------------------------------------------------

    async def list_imsg_inbox(self):
        """Read / reply / delete InterBBS instant messages from the terminal."""
        from anetbbs.models import db, InstantMessage
        from anetbbs.msp.client import send_msp
        from anetbbs.msp.protocol import MSP_DEFAULT_PORT
        my_id = self.session.user['id']

        with _app().app_context():
            ims = (InstantMessage.query
                   .filter_by(recipient_id=my_id)
                   .order_by(InstantMessage.received_at.desc())
                   .limit(50).all())
            rows = [(m.id, m.sender_label or '?', m.sender_host or '?',
                     m.received_at, m.is_read, m.body or '') for m in ims]

        if not rows:
            from .ansi_ui import banner as _bnr, FG as _F, RESET as _R
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(_bnr('Inter-BBS Instant Messages'))
            await self.session.write(
                f"  {_F['gry']}No InterBBS instant messages.{_R}\r\n")
            await self.session.read_line("\r\nPress Enter...")
            return

        while True:
            from .ansi_ui import banner as _bnr2
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(_bnr2('Inter-BBS Instant Messages'))
            for i, (_, who, host, when, was_read, body) in enumerate(rows, 1):
                ts = when.strftime('%m-%d %H:%M') if when else '?'
                mark = ' ' if was_read else '*'
                preview = body.replace('\r', ' ').replace('\n', ' ')[:30]
                await self.session.write(
                    f"  {i:2d}.{mark} {who[:18]:<18} {ts}  {preview}\r\n")
            choice = (await self.session.read_line(
                "\r\nPick # to read, R# to reply, D# to delete, Q to quit: ")
                      or '').strip().upper()
            if not choice or choice == 'Q':
                return
            cmd, num = ('R', choice[1:]) if choice.startswith('R') else \
                       ('D', choice[1:]) if choice.startswith('D') else \
                       ('V', choice)
            try:
                idx = int(num) - 1
                if not (0 <= idx < len(rows)):
                    continue
            except ValueError:
                continue
            mid, who, host, when, _, body = rows[idx]

            if cmd == 'V':
                await self.session.write("\r\n" + "═" * 64 + "\r\n")
                ts = when.strftime('%Y-%m-%d %H:%M') if when else '?'
                await self.session.write(f"  From: {who}\r\n  Host: {host}\r\n  Date: {ts}\r\n")
                await self.session.write("─" * 64 + "\r\n")
                for line in body.splitlines():
                    await self.session.write(line[:78] + "\r\n")
                with _app().app_context():
                    im = InstantMessage.query.get(mid)
                    if im and not im.is_read:
                        im.is_read = True
                        db.session.commit()
                await self.session.read_line("\r\nPress Enter...")

            elif cmd == 'D':
                with _app().app_context():
                    im = InstantMessage.query.get(mid)
                    if im:
                        db.session.delete(im)
                        db.session.commit()
                rows.pop(idx)
                await self.session.write("\r\nDeleted.\r\n")
                if not rows:
                    return

            elif cmd == 'R':
                # Reply: who is "user@bbsname" or just "user"; strip any @ part.
                target_user = who.split('@', 1)[0]
                target_host = host
                await self.session.write(
                    f"\r\nReply to {target_user}@{target_host}\r\n"
                    "Type your message (single line, blank to abort):\r\n")
                msg = (await self.session.read_line("> ") or '').strip()
                if not msg:
                    continue
                me = self.session.user
                bbs_name = _app().config.get('BBS_NAME', '')
                ok = send_msp(
                    host=target_host,
                    port=MSP_DEFAULT_PORT,
                    recipient=target_user,
                    message=msg,
                    sender=me.get('username', 'sysop'),
                    sender_real_name=me.get('display_name') or me.get('username', ''),
                    sender_system=bbs_name,
                )
                await self.session.write(
                    "\r\nSent.\r\n" if ok else "\r\nDelivery failed.\r\n")

    async def send_imsg(self):
        """Compose a fresh InterBBS IM from the terminal."""
        from anetbbs.msp.client import send_msp
        from anetbbs.msp.protocol import MSP_DEFAULT_PORT
        from .ansi_ui import banner, FG, RESET
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner('Send Inter-BBS Instant Message'))
        dest = (await self.session.read_line(
            f"  {FG['cyan']}Destination (user@host):{RESET} ") or '').strip()
        if '@' not in dest:
            await self.session.write(
                f"\r\n{FG['red']}Need user@host format.{RESET}\r\n")
            return
        target_user, target_host = dest.rsplit('@', 1)
        msg = (await self.session.read_line(
            f"  {FG['cyan']}Message:{RESET} ") or '').strip()
        if not msg:
            return
        me = self.session.user
        bbs_name = _app().config.get('BBS_NAME', '')
        ok = send_msp(
            host=target_host,
            port=MSP_DEFAULT_PORT,
            recipient=target_user,
            message=msg,
            sender=me.get('username', 'sysop'),
            sender_real_name=me.get('display_name') or me.get('username', ''),
            sender_system=bbs_name,
        )
        await self.session.write(
            f"\r\n{'Delivered' if ok else 'Delivery failed'}.\r\n")
        await self.session.read_line("\r\nPress Enter...")

    # ------------------------------------------------------------------
    # Echomail areas
    # ------------------------------------------------------------------

    async def list_echo_areas(self):
        from anetbbs.models import EchoArea, EchomailNetwork, EchomailMessage
        with _app().app_context():
            _user_level = int((self.session.user or {}).get('access_level', 10))
            _is_admin = bool((self.session.user or {}).get('is_admin'))
            _eq = EchoArea.query.join(EchomailNetwork)
            if not _is_admin:
                _eq = _eq.filter(
                    EchoArea.is_sysop_only == False,
                    EchoArea.min_access_level <= _user_level,
                )
            areas = _eq.order_by(EchomailNetwork.name, EchoArea.name).all()
            a_list = [(a.id, a.tag, a.name, a.network.name,
                       EchomailMessage.query.filter_by(area_id=a.id).count()) for a in areas]

        if not a_list:
            await self.session.write("\r\nNo echomail areas configured.\r\n")
            await self.session.read_line("\r\nPress Enter...")
            return

        from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD
        # Page so the area list doesn't scroll past on a 24-row terminal —
        # mirrors the paging used by compose_echomail (C).
        PAGE = 18
        while True:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Echomail Areas'))
            await self.session.write(
                f"  {FG['cyan']}{BOLD}{'#':>2}  {'Tag':<18} "
                f"{'Name':<25} {'Net':<10} {'Msgs':>6}{RESET}\r\n"
                f"  {FG['gry']}{'─' * 64}{RESET}\r\n")
            for i, (_, tag, name, net, n) in enumerate(a_list, 1):
                await self.session.write(
                    f"  {FG['yel']}{BOLD}{i:2d}{RESET}  "
                    f"{FG['grn']}{tag[:18]:<18}{RESET} "
                    f"{FG['wht']}{name[:25]:<25}{RESET} "
                    f"{FG['mag']}{net[:10]:<10}{RESET} "
                    f"{FG['cyan']}{n:6d}{RESET}\r\n")
                if i % PAGE == 0 and i < len(a_list):
                    ans = (await self.session.read_line(
                        f"  {FG['cyan']}-- more (Enter, "
                        f"Q=stop listing) --{RESET}") or '').strip().upper()
                    if ans == 'Q':
                        # User saw what they wanted — skip the rest of
                        # the listing and go straight to the picker.
                        await self.session.write('\r\n')
                        break
            await self.session.write('\r\n' + footer() + '\r\n')
            choice = (await self.session.read_line(
                _prompt('Pick area (number / Q): ')) or '').strip()
            if choice.upper() == 'Q' or not choice:
                return
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(a_list):
                    await self.read_echo_area(a_list[idx][0], a_list[idx][1])
            except ValueError:
                pass

    async def read_echo_area(self, area_id, tag):
        from anetbbs.models import EchomailMessage, EchoArea
        with _app().app_context():
            msgs = (EchomailMessage.query
                    .filter_by(area_id=area_id)
                    .order_by(EchomailMessage.created_at.desc())
                    .limit(30).all())
            m_list = [(m.id, m.subject, m.from_name, m.to_name,
                       m.created_at, m.body) for m in msgs]
            aobj     = EchoArea.query.get(area_id)
            net_id   = aobj.network_id if aobj else None
            net_addr = (aobj.network.our_address or '1:1/1') if (aobj and aobj.network) else '1:1/1'

        from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD

        if not m_list:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner(tag))
            await self.session.write(
                f"  {FG['gry']}(no messages in this area yet){RESET}\r\n")
            await self.session.write('\r\n' + footer() + '\r\n')
            await self.session.read_line(
                f"{FG['cyan']}Press Enter...{RESET}")
            return

        LIST_PAGE = 18    # rows before --more-- on the index
        while True:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner(f'{tag} - latest 30'))
            await self.session.write(
                f"  {FG['cyan']}{BOLD}{'#':>2}  {'Subject':<38} "
                f"{'From':<12} {'Date':<6}{RESET}\r\n"
                f"  {FG['gry']}{'─' * 64}{RESET}\r\n")
            for i, (_, subj, who, _, when, _) in enumerate(m_list, 1):
                ts = when.strftime('%m-%d') if when else '?'
                await self.session.write(
                    f"  {FG['yel']}{BOLD}{i:2d}{RESET}  "
                    f"{FG['wht']}{(subj or '(no subject)')[:38]:<38}{RESET} "
                    f"{FG['grn']}{(who or '?')[:12]:<12}{RESET} "
                    f"{FG['cyan']}{ts:<6}{RESET}\r\n")
                if i % LIST_PAGE == 0 and i < len(m_list):
                    ans = (await self.session.read_line(
                        f"  {FG['cyan']}-- more (Enter, "
                        f"Q=stop listing) --{RESET}") or '').strip().upper()
                    if ans == 'Q':
                        await self.session.write('\r\n')
                        break
            await self.session.write('\r\n' + footer() + '\r\n')
            choice = (await self.session.read_line(
                _prompt('Pick message (number / Q): ')) or '').strip()
            if choice.upper() == 'Q' or not choice:
                return
            try:
                idx = int(choice) - 1
            except ValueError:
                continue
            if not (0 <= idx < len(m_list)):
                continue
            _, subj, frm, to, when, body = m_list[idx]
            ts = when.strftime('%Y-%m-%d %H:%M') if when else '?'
            # Strip SAUCE record: 0x1A (Ctrl+Z) marks end of art content;
            # everything from 0x1A onward is binary metadata that must not
            # be rendered (it looks like random ANSI sequences to renderers).
            body = (body or '')
            _sa = body.find('\x1a')
            if _sa >= 0:
                body = body[:_sa]
            # Fix QWK 0xE3 separators that may have split CSI sequences.
            body_fixed = re.sub(r'\x1b\n?\[[0-9;?\n]*[@-~]',
                                lambda m: m.group(0).replace('\n', ''),
                                body or '')
            from .anedit import launch_aneview, launch_anedit
            # Route all messages through ANView (VT renderer inside).
            # CP437, pipe codes, cursor-pos/block-art \n handling are all
            # done in launch_aneview via to_ansi_lines().
            view_result = await launch_aneview(
                self.session, body_fixed,
                subject=subj or '(no subject)',
                from_name=frm or '?',
                to_name=to or '?',
                date_str=ts,
            )
            if view_result in ('reply', 'new'):
                if view_result == 'reply':
                    compose_to   = frm or 'All'
                    compose_subj = ('Re: ' + subj) if subj else 'Re: (no subject)'
                    quote        = body_fixed
                else:
                    compose_to   = 'All'
                    compose_subj = ''
                    quote        = ''
                if not compose_subj:
                    await self.session.write('\x1b[2J\x1b[H')
                    await self.session.write(banner('Compose in ' + tag))
                    compose_to = (await self.session.read_line(
                        f"  {FG['cyan']}To:{RESET} ") or 'All').strip()
                    compose_subj = (await self.session.read_line(
                        f"  {FG['cyan']}Subject:{RESET} ") or '').strip()
                    if not compose_subj:
                        continue
                username = self.session.user.get('username', 'guest')
                body_out = await launch_anedit(
                    self.session, quote=quote,
                    subject=compose_subj, username=username)
                if body_out and net_id:
                    with _app().app_context():
                        from anetbbs.models import db as _db2, EchomailMessage as _EM
                        em = _EM(
                            area_id=area_id,
                            network_id=net_id,
                            from_name=username[:100],
                            from_address=net_addr,
                            to_name=compose_to[:100],
                            subject=compose_subj[:200],
                            body=body_out,
                            direction='outbound',
                        )
                        _db2.session.add(em)
                        _db2.session.commit()
                    await self.session.write(
                        f"\r\n  {FG['grn']}{BOLD}[OK] Message queued for next poll.{RESET}\r\n")
                    await self.session.read_line(
                        f"\r\n{FG['cyan']}Press Enter...{RESET}")

    # ------------------------------------------------------------------
    # File library (list-only for now)
    # ------------------------------------------------------------------

    async def list_files(self):
        """Browse file areas, download via ZMODEM/YMODEM/XMODEM, upload via ZMODEM."""
        from anetbbs.models import FileUpload, FileArea, TicFile
        from .xfer import available_protocols
        from .ansi_ui import FG, RESET, BOLD
        app = _app()

        while True:
            with app.app_context():
                is_sysop = bool(self.session.user.get('is_admin'))
                # Sysop sees ALL areas (active + inactive, public + sysop-only).
                # Regular users only see active non-sysop areas.
                user_level = int((self.session.user or {}).get('access_level', 10))
                if is_sysop:
                    areas = (FileArea.query
                             .order_by(FileArea.name).all())
                else:
                    areas = (FileArea.query
                             .filter_by(is_active=True, is_sysop_only=False)
                             .filter(FileArea.min_access_level <= user_level)
                             .order_by(FileArea.name).all())

                from anetbbs.web.file_areas import _scan_area as _disk_scan

                area_rows = []
                for a in areas:
                    if a.storage_path and os.path.isdir(a.storage_path):
                        # Count directly from disk (matches web view)
                        try:
                            cnt = sum(
                                1 for f in os.listdir(a.storage_path)
                                if not f.startswith('.')
                                and os.path.isfile(
                                    os.path.join(a.storage_path, f)))
                        except OSError:
                            cnt = 0
                    else:
                        up_cnt = FileUpload.query.filter(
                            FileUpload.file_area_id == a.id,
                            FileUpload.is_public.isnot(False)).count()
                        tic_cnt = TicFile.query.filter_by(
                            file_area_id=a.id, status='filed').count()
                        cnt = up_cnt + tic_cnt
                    perm = (a.upload_permission or 'users').lower()
                    write_lvl = a.min_write_level if a.min_write_level is not None else a.min_access_level
                    can_upload = ((perm == 'users' or (perm == 'sysop' and is_sysop))
                                  and (is_sysop or user_level >= write_lvl))
                    inactive = not a.is_active
                    sysop_flag = a.is_sysop_only
                    # Strip FidoNet network-prefix artifacts like "0 ! " or "1 !"
                    import re as _re
                    raw_name = (a.name or a.tag or '?').strip()
                    disp_name = _re.sub(r'^\d+\s*!\s*', '', raw_name).strip() or raw_name
                    area_rows.append({
                        'id': a.id, 'name': disp_name, 'tag': a.tag or '',
                        'desc': (a.description or '').strip(),
                        'can_upload': can_upload, 'cnt': cnt,
                        'inactive': inactive, 'sysop_only': sysop_flag,
                        'storage_path': a.storage_path or '',
                    })

                top_cnt = (FileUpload.query.filter(
                    FileUpload.file_area_id.is_(None),
                    FileUpload.is_public.isnot(False)).count() +
                    TicFile.query.filter_by(
                    file_area_id=None, status='filed').count())

                domain = app.config.get('BBS_DOMAIN', '')
                port = app.config.get('WEB_PORT', 5000)
                web_base = (f"https://{domain}" if domain
                            else f"http://localhost:{port}")
                uploads_dir = app.config.get(
                    'UPLOADS_DIR',
                    os.path.join(app.config['DATA_DIR'], 'uploads'))

            protos = available_protocols()

            # ---- Draw area list ----
            await self.session.write('\x1b[2J\x1b[H')
            hdr = (f"{FG['cyan']}{BOLD}"
                   f"{'─'*44}\r\n"
                   f" File Library - Areas\r\n"
                   f"{'─'*44}{RESET}\r\n\r\n")
            await self.session.write(hdr)

            if top_cnt or is_sysop:
                await self.session.write(
                    f"  {FG['yel']} 0.{RESET} "
                    f"{FG['wht']}{'General / Top-level':<38}{RESET}"
                    f"  {FG['gry']}({top_cnt} files){RESET}\r\n")

            for i, ar in enumerate(area_rows, 1):
                cnt_str = f"({ar['cnt']} files)"
                flags = ''
                if ar['inactive']:
                    flags += f" {FG['red']}[inactive]{RESET}"
                if ar['sysop_only']:
                    flags += f" {FG['mag']}[sysop]{RESET}"
                await self.session.write(
                    f"  {FG['yel']}{i:2d}.{RESET} "
                    f"{FG['wht']}{ar['name'][:38]:<38}{RESET}"
                    f"  {FG['gry']}{cnt_str}{RESET}"
                    f"{flags}\r\n")

            if not area_rows and not top_cnt:
                await self.session.write(
                    f"  {FG['gry']}(no file areas configured){RESET}\r\n")

            if protos:
                proto_note = ' / '.join(p.upper() for p in protos)
                await self.session.write(
                    f"\r\n  {FG['gry']}Transfer protocols: {proto_note}{RESET}\r\n")
            else:
                await self.session.write(
                    f"\r\n  {FG['red']}lrzsz not installed - "
                    f"downloads will show web URL only{RESET}\r\n")

            choice = (await self.session.read_line(
                f"\r\n{FG['cyan']}Enter area #, A=All, Q=Back:{RESET} ")
                      or '').strip().upper()

            if choice == 'Q' or not choice:
                return
            if choice == 'A':
                area_filter, area_name, area_id, can_up, stor = \
                    'all', 'All Files', None, False, ''
            elif choice == '0' and (top_cnt or is_sysop):
                area_filter, area_name, area_id, can_up, stor = \
                    'top', 'General / Top-level', None, True, ''
            else:
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(area_rows):
                        ar = area_rows[idx]
                        area_id     = ar['id']
                        area_name   = ar['name']
                        can_up      = ar['can_upload']
                        stor        = ar['storage_path']
                        area_filter = 'area'
                    else:
                        await self.session.write(
                            f"{FG['red']}Invalid area number.{RESET}\r\n")
                        continue
                except ValueError:
                    await self.session.write(
                        f"{FG['red']}Enter a number, A, or Q.{RESET}\r\n")
                    continue

            await self._file_area_browse(
                area_id, area_name, area_filter,
                can_up, uploads_dir, web_base, protos, stor)

    async def _file_area_browse(self, area_id, area_name, area_filter,
                                can_upload, uploads_dir, web_base, protos,
                                storage_path=''):
        """Paginated file browser. Reads disk (storage_path) when available,
        falls back to FileUpload+TicFile DB tables otherwise."""
        from anetbbs.models import FileUpload, TicFile, User, FileArea
        from .ansi_ui import FG, RESET, BOLD
        app = _app()
        PAGE = 9   # 9 × 2-line entries + 4 header + 2 nav = 24 lines, fits 80×25
        page = 0

        while True:
            with app.app_context():
                f_list = []

                if area_filter == 'area' and storage_path and os.path.isdir(storage_path):
                    # Primary path: read files from disk (matches web view)
                    from anetbbs.web.file_areas import _scan_area as _disk_scan
                    area_obj = FileArea.query.get(area_id)
                    if area_obj:
                        disk_files = _disk_scan(area_obj)
                        for df in disk_files:
                            full_desc = (df.get('description') or '').strip()
                            f_list.append({
                                'upload_id': None,
                                'tic_id':    None,
                                'name': df['name'],
                                'size': df['size'],
                                'who':  'FidoNet' if full_desc else 'sysop',
                                'desc': full_desc.split('\n')[0][:60],
                                'desc_full': full_desc,
                                'path': os.path.join(storage_path, df['name']),
                                'date': df.get('mtime'),
                                'area_id_for_web': area_id,
                            })
                        # Sort newest-modified first
                        f_list.sort(
                            key=lambda x: x['date'] or datetime.min, reverse=True)
                else:
                    # Fallback: DB-based (FileUpload + TicFile)
                    uq = FileUpload.query.filter(FileUpload.is_public.isnot(False))
                    if area_filter == 'area':
                        uq = uq.filter(FileUpload.file_area_id == area_id)
                    elif area_filter == 'top':
                        uq = uq.filter(FileUpload.file_area_id.is_(None))
                    uploads = uq.order_by(FileUpload.id.desc()).all()

                    tq = TicFile.query.filter_by(status='filed')
                    if area_filter == 'area':
                        tq = tq.filter_by(file_area_id=area_id)
                    elif area_filter == 'top':
                        tq = tq.filter_by(file_area_id=None)
                    tics = tq.order_by(TicFile.received_at.desc()).all()

                    for f in uploads:
                        u = User.query.get(f.uploader_id or 0)
                        full_d = (f.description or '').strip()
                        f_list.append({
                            'upload_id': f.id,
                            'tic_id':    None,
                            'name': (f.original_filename or f.filename or '?'),
                            'size': f.file_size or 0,
                            'who':  u.username if u else '?',
                            'desc': full_d.split('\n')[0][:60],
                            'desc_full': full_d,
                            'path': (f.file_path or
                                     os.path.join(uploads_dir, f.filename)),
                            'date': f.created_at,
                            'area_id_for_web': None,
                        })
                    for t in tics:
                        full_d = (t.description or '').strip()
                        f_list.append({
                            'upload_id': t.file_upload_id,
                            'tic_id':    t.id,
                            'name': t.filename or '?',
                            'size': t.size_bytes or 0,
                            'who':  t.origin or t.from_address or 'FidoNet',
                            'desc': full_d.split('\n')[0][:60],
                            'desc_full': full_d,
                            'path': t.stored_path or '',
                            'date': t.received_at,
                            'area_id_for_web': None,
                        })
                    f_list.sort(
                        key=lambda x: x['date'] or datetime.min, reverse=True)

                total = len(f_list)

            page_files = f_list[page * PAGE: (page + 1) * PAGE]
            pages = max(1, (total + PAGE - 1) // PAGE)

            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(
                f"{FG['cyan']}{BOLD}"
                f"{'─'*44}\r\n"
                f" {area_name}  "
                f"{FG['gry']}(page {page+1}/{pages}, {total} files){RESET}"
                f"{FG['cyan']}{BOLD}\r\n"
                f"{'─'*44}{RESET}\r\n\r\n")

            if not page_files:
                await self.session.write(
                    f"  {FG['gry']}(no files here yet){RESET}\r\n")

            for i, f in enumerate(page_files, page * PAGE + 1):
                sz = (f"{f['size']:>10,}" if isinstance(f['size'], int)
                      else f"{str(f['size']):>10}")
                src = ('FTN' if f['tic_id'] else f['who'][:10])
                await self.session.write(
                    f"  {FG['yel']}{i:3d}.{RESET} "
                    f"{FG['wht']}{f['name'][:32]:<32}{RESET} "
                    f"{FG['gry']}{sz}  {src:<12}{RESET}\r\n")
                if f['desc']:
                    await self.session.write(
                        f"        {FG['dim']}{f['desc'][:60]}{RESET}\r\n")

            nav = []
            if page > 0:     nav.append("P=Prev")
            if (page+1) < pages: nav.append("N=Next")
            nav.append("#=DL  #,#=Batch  V#=Info")
            if can_upload and protos: nav.append("U=Upload")
            nav.append("Q=Back")

            choice = (await self.session.read_line(
                f"\r\n{FG['cyan']}[{'  '.join(nav)}]:{RESET} ")
                      or '').strip()

            cu = choice.upper()
            if cu == 'Q' or not choice:  return
            if cu == 'N' and (page+1) < pages:
                page += 1; continue
            if cu == 'P' and page > 0:
                page -= 1; continue
            if cu == 'U' and can_upload and protos:
                await self._upload_terminal_file(area_id, uploads_dir, protos)
                continue

            # V# — view extended description
            if cu.startswith('V') and len(cu) > 1:
                try:
                    num = int(cu[1:].strip())
                    local_idx = num - page * PAGE - 1
                    if 0 <= local_idx < len(page_files):
                        await self._view_file_desc(page_files[local_idx])
                    else:
                        await self.session.write(
                            f"{FG['red']}Number not on this page.{RESET}\r\n")
                except ValueError:
                    await self.session.write(
                        f"{FG['red']}Enter V followed by a file number.{RESET}\r\n")
                continue

            # Parse batch (1,3,5 or 1-5) or single number
            nums = _parse_file_selection(choice, page * PAGE + 1,
                                         page * PAGE + len(page_files))
            if nums is None:
                await self.session.write(
                    f"{FG['red']}Enter a number, range (1-5), list (1,3,5), or command.{RESET}\r\n")
                continue

            if len(nums) == 1:
                local_idx = nums[0] - page * PAGE - 1
                if 0 <= local_idx < len(page_files):
                    await self._download_file(page_files[local_idx], web_base, protos)
                else:
                    await self.session.write(
                        f"{FG['red']}Number not on this page.{RESET}\r\n")
            else:
                # Batch download
                batch = []
                for n in nums:
                    li = n - page * PAGE - 1
                    if 0 <= li < len(page_files):
                        batch.append(page_files[li])
                if batch:
                    await self._batch_download(batch, web_base, protos)
                else:
                    await self.session.write(
                        f"{FG['red']}No valid files in that selection.{RESET}\r\n")

    async def _view_file_desc(self, f):
        """Show extended description for a file, with word-wrap."""
        from .ansi_ui import FG, RESET, BOLD
        name  = f['name']
        size  = f['size']
        desc  = f.get('desc_full') or f.get('desc') or ''
        date  = f.get('date')

        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(
            f"{FG['cyan']}{BOLD}"
            f"{'─'*44}\r\n"
            f" {name[:40]}\r\n"
            f"{'─'*44}{RESET}\r\n\r\n")

        sz_str = f"{size:,}" if isinstance(size, int) else str(size)
        await self.session.write(
            f"  {FG['yel']}Size:{RESET} {FG['wht']}{sz_str} bytes{RESET}\r\n")
        if date and hasattr(date, 'strftime'):
            await self.session.write(
                f"  {FG['yel']}Date:{RESET} "
                f"{FG['wht']}{date.strftime('%Y-%m-%d')}{RESET}\r\n")

        if desc:
            await self.session.write('\r\n')
            for line in desc.split('\n'):
                # Lines with ESC sequences are ANSI art — pass through as-is
                if '\x1b' in line:
                    await self.session.write(f"  {line}\r\n")
                    continue
                line = line.rstrip()
                if not line:
                    await self.session.write('\r\n')
                    continue
                # Word-wrap plain text at 76 chars
                while len(line) > 76:
                    split = line.rfind(' ', 0, 76)
                    if split == -1:
                        split = 76
                    await self.session.write(f"  {line[:split]}\r\n")
                    line = line[split:].lstrip()
                if line:
                    await self.session.write(f"  {line}\r\n")
        else:
            await self.session.write(
                f"\r\n  {FG['gry']}(no description available){RESET}\r\n")

        await self.session.read_line(f"\r\n{FG['cyan']}Press Enter...{RESET}")

    async def _batch_download(self, files, web_base, protos):
        """Prompt for protocol once, then sequentially send multiple files."""
        from .xfer import send_file
        from .ansi_ui import FG, RESET, BOLD

        if not protos:
            await self.session.write(
                f"\r\n{FG['red']}lrzsz not installed - "
                f"cannot batch download.{RESET}\r\n")
            await self.session.read_line("Press Enter...")
            return

        labels = {'zmodem': 'Z=ZMODEM', 'ymodem': 'Y=YMODEM', 'xmodem': 'X=XMODEM'}
        opts   = [labels[p] for p in protos] + ['Q=Cancel']

        names_str = ', '.join(f['name'] for f in files[:5])
        if len(files) > 5:
            names_str += f' (+{len(files)-5} more)'

        await self.session.write(
            f"\r\n{FG['cyan']}Batch download: {FG['wht']}{names_str}{RESET}\r\n")

        proto_choice = (await self.session.read_line(
            f"{FG['cyan']}Protocol [{' '.join(opts)}]:{RESET} ")
                        or '').strip().upper()

        proto_map = {'Z': 'zmodem', 'Y': 'ymodem', 'X': 'xmodem'}
        if proto_choice == 'Q' or not proto_choice:
            return
        protocol = proto_map.get(proto_choice)
        if not protocol or protocol not in protos:
            await self.session.write(f"{FG['red']}Unknown protocol.{RESET}\r\n")
            return

        for idx, f in enumerate(files, 1):
            fpath = f['path']
            name  = f['name']
            if not fpath or not os.path.isfile(fpath):
                await self.session.write(
                    f"  {FG['red']}[{idx}/{len(files)}] {name}: "
                    f"file not on disk - skipped{RESET}\r\n")
                continue

            await self.session.write(
                f"\r\n{FG['grn']}[{idx}/{len(files)}] Sending {name} ...{RESET}\r\n"
                f"Begin your terminal's {FG['wht']}receive{RESET} now.\r\n\r\n")
            await asyncio.sleep(1)
            try:
                while True:
                    chunk = await asyncio.wait_for(
                        self.session.reader.read(4096), timeout=0.2)
                    if not chunk:
                        break
            except (asyncio.TimeoutError, Exception):
                pass

            try:
                ok = await send_file(self.session, fpath, protocol)
            except Exception as exc:
                logger.exception('batch send_file failed for %s: %s', fpath, exc)
                ok = False

            if ok:
                await self.session.write(
                    f"\r\n{FG['grn']}[OK] {name}{RESET}\r\n")
            else:
                await self.session.write(
                    f"\r\n{FG['red']}[FAILED] {name}{RESET}\r\n")

        # Drain any residual binary from the terminal
        try:
            while True:
                chunk = await asyncio.wait_for(
                    self.session.reader.read(4096), timeout=0.3)
                if not chunk:
                    break
        except (asyncio.TimeoutError, Exception):
            pass
        await self.session.read_line(f"\r\n{FG['cyan']}Batch complete. Press Enter...{RESET}")

    async def _download_file(self, f, web_base, protos):
        """Offer ZMODEM/YMODEM/XMODEM or web URL for one file."""
        from .xfer import send_file
        from .ansi_ui import FG, RESET, BOLD
        name  = f['name']
        size  = f['size']
        fpath = f['path']
        web_id = f.get('upload_id')
        web_area_id = f.get('area_id_for_web')

        # Build web URL: storage_path files served at /file-areas/<id>/<name>,
        # FileUpload records served at /files/download/<id>
        if web_area_id:
            web_url = f"{web_base}/file-areas/{web_area_id}/{name}"
        elif web_id:
            web_url = f"{web_base}/files/download/{web_id}"
        else:
            web_url = None

        sz_str = f"{size:,}" if isinstance(size, int) else str(size)
        await self.session.write(
            f"\r\n{FG['wht']}{BOLD}{name}{RESET}  "
            f"{FG['gry']}({sz_str} bytes){RESET}\r\n")

        # No lrzsz - web URL only
        if not protos:
            if web_url:
                await self.session.write(
                    f"\r\n{FG['yel']}lrzsz not installed. Web URL:{RESET}\r\n"
                    f"  {web_url}\r\n")
            else:
                await self.session.write(
                    f"\r\n{FG['red']}No transfer protocols "
                    f"(install lrzsz) and no web URL for this file.{RESET}\r\n")
            await self.session.read_line("\r\nPress Enter...")
            return

        # File not on disk - fall back gracefully
        if not fpath or not os.path.isfile(fpath):
            if web_url:
                await self.session.write(
                    f"\r\n{FG['yel']}File not on disk. Web URL:{RESET}\r\n"
                    f"  {web_url}\r\n")
            else:
                await self.session.write(
                    f"\r\n{FG['red']}File not found on server disk.{RESET}\r\n")
            await self.session.read_line("\r\nPress Enter...")
            return

        labels = {'zmodem': 'Z=ZMODEM', 'ymodem': 'Y=YMODEM', 'xmodem': 'X=XMODEM'}
        opts = [labels[p] for p in protos]
        if web_url:
            opts.append("W=Web URL")
        opts.append("Q=Cancel")

        proto_choice = (await self.session.read_line(
            f"{FG['cyan']}Protocol [{' '.join(opts)}]:{RESET} ")
                        or '').strip().upper()

        proto_map = {'Z': 'zmodem', 'Y': 'ymodem', 'X': 'xmodem'}
        if proto_choice == 'Q' or not proto_choice:
            return
        if proto_choice == 'W' and web_url:
            await self.session.write(
                f"\r\n{FG['yel']}Web URL:{RESET}\r\n  {web_url}\r\n")
            await self.session.read_line("\r\nPress Enter...")
            return

        protocol = proto_map.get(proto_choice)
        if not protocol or protocol not in protos:
            await self.session.write(
                f"{FG['red']}Unknown protocol choice.{RESET}\r\n")
            return

        await self.session.write(
            f"\r\n{FG['grn']}Starting {protocol.upper()} send of {name} ...{RESET}\r\n"
            f"Begin your terminal's {FG['wht']}receive{RESET} now.\r\n\r\n")
        await asyncio.sleep(1)

        # Drain any buffered input (leftover keystrokes, ANSI sequences)
        # before handing stdin to sz.  Stale bytes fed into sz at startup
        # corrupt the initial ZRQINIT/ZRINIT handshake and cause sz to exit
        # non-zero, requiring the user to retry the transfer.
        try:
            while True:
                chunk = await asyncio.wait_for(
                    self.session.reader.read(4096), timeout=0.2)
                if not chunk:
                    break
        except (asyncio.TimeoutError, Exception):
            pass

        try:
            ok = await send_file(self.session, fpath, protocol)
        except Exception as exc:
            logger.exception('send_file failed for %s: %s', fpath, exc)
            ok = False

        if ok:
            await self.session.write(
                f"\r\n\r\n{FG['grn']}[Transfer complete: {name}]{RESET}\r\n")
        else:
            await self.session.write(
                f"\r\n\r\n{FG['red']}[Transfer failed or cancelled.]{RESET}\r\n")
        # Drain any binary garbage the terminal sent during the failed
        # transfer before returning to the menu loop.
        try:
            while True:
                chunk = await asyncio.wait_for(
                    self.session.reader.read(4096), timeout=0.3)
                if not chunk:
                    break
        except (asyncio.TimeoutError, Exception):
            pass
        await self.session.read_line("Press Enter...")

    async def _upload_terminal_file(self, area_id, uploads_dir, protos):
        """Receive a file upload from the user via ZMODEM/YMODEM/XMODEM."""
        from anetbbs.models import db, FileUpload
        from .xfer import recv_file
        import uuid, mimetypes, shutil as _shutil
        from ..features.archive_meta import extract_archive_description

        opts = []
        labels = {'zmodem': 'Z=ZMODEM', 'ymodem': 'Y=YMODEM', 'xmodem': 'X=XMODEM'}
        for p in protos:
            opts.append(labels[p])
        opts.append("Q=Cancel")

        proto_choice = (await self.session.read_line(
            f"\r\nUpload protocol [{' '.join(opts)}]: ") or '').strip().upper()
        if proto_choice == 'Q' or not proto_choice:
            return

        proto_map = {'Z': 'zmodem', 'Y': 'ymodem', 'X': 'xmodem'}
        protocol = proto_map.get(proto_choice)
        if not protocol or protocol not in protos:
            await self.session.write("\r\nUnknown protocol.\r\n")
            return

        desc = (await self.session.read_line(
            "Description (optional, blank to skip): ") or '').strip()

        await self.session.write(
            f"\r\nReady for {protocol.upper()} receive.\r\n"
            f"Begin your terminal's send now.\r\n\r\n")
        await asyncio.sleep(1)

        received = await recv_file(self.session, protocol)

        if not received:
            await self.session.write("\r\n[Upload failed or no file received.]\r\n")
            await self.session.read_line("Press Enter...")
            return

        os.makedirs(uploads_dir, exist_ok=True)

        app = _app()
        uid = self.session.user.get('id')
        saved = []

        for orig_name, tmp_path in received:
            try:
                ext = orig_name.rsplit('.', 1)[-1].lower() \
                      if '.' in orig_name else ''
                stored = f"{uuid.uuid4().hex}.{ext}" if ext \
                         else uuid.uuid4().hex
                dest = os.path.join(uploads_dir, stored)
                _shutil.move(tmp_path, dest)
                size = os.path.getsize(dest)
                mime = mimetypes.guess_type(orig_name)[0] \
                       or 'application/octet-stream'

                # Auto-extract FILE_ID.DIZ if user left desc blank
                file_desc = desc
                if not file_desc:
                    try:
                        file_desc = extract_archive_description(dest) or ''
                    except Exception:
                        pass

                with app.app_context():
                    fu = FileUpload(
                        uploader_id=uid,
                        filename=stored,
                        original_filename=orig_name,
                        file_path=dest,
                        file_size=size,
                        mime_type=mime,
                        description=file_desc,
                        file_area_id=area_id,
                        is_public=True,
                    )
                    db.session.add(fu)
                    db.session.commit()
                saved.append(orig_name)
            except Exception as exc:
                await self.session.write(f"\r\nFailed to save {orig_name}: {exc}\r\n")
            finally:
                try:
                    _shutil.rmtree(os.path.dirname(tmp_path),
                                   ignore_errors=True)
                except Exception:
                    pass

        if saved:
            await self.session.write(
                f"\r\n[Uploaded: {', '.join(saved)}]\r\n")
        await self.session.read_line("Press Enter...")

    # ------------------------------------------------------------------
    # User profile
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # RSS News Reader
    # ------------------------------------------------------------------

    async def show_rss(self):
        """Browse RSS feeds + items the sysop has subscribed."""
        from anetbbs.models import RssFeed, RssItem, RssReadStatus
        from .ansi_ui import banner, footer, FG, RESET

        uid = self.session.user.get('id') if isinstance(self.session.user, dict) \
              else getattr(self.session.user, 'id', None)

        while True:
            with _app().app_context():
                _user_level_rss = int((self.session.user or {}).get('access_level', 10))
                _is_admin_rss = bool((self.session.user or {}).get('is_admin'))
                _rq = RssFeed.query.filter_by(is_active=True)
                if not _is_admin_rss:
                    _rq = _rq.filter(RssFeed.min_access_level <= _user_level_rss)
                feeds = _rq.order_by(RssFeed.sort_order, RssFeed.name).all()
                # Snapshot the data we need so the rest of the loop can
                # touch session.write/read_line without a stale session.
                feed_rows = []
                for f in feeds:
                    total = RssItem.query.filter_by(feed_id=f.id).count()
                    if uid:
                        read = (RssReadStatus.query
                                .join(RssItem,
                                      RssReadStatus.item_id == RssItem.id)
                                .filter(RssItem.feed_id == f.id,
                                        RssReadStatus.user_id == uid).count())
                    else:
                        read = 0
                    feed_rows.append((f.id, f.name, total, total - read))
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('RSS News Reader'))
            if not feed_rows:
                await self.session.write(
                    f"  {FG['gry']}No RSS feeds configured. Ask the sysop "
                    f"to add some at /admin/rss/.{RESET}\r\n")
                await self.session.write('\r\n' + footer() + '\r\n')
                await self.session.read_line("Press Enter... ")
                return
            await self.session.write(
                f"  {FG['cyan']}#  Feed                                 "
                f"Items  Unread{RESET}\r\n"
                f"  {FG['gry']}{'─' * 60}{RESET}\r\n")
            for i, (_, name, total, unread) in enumerate(feed_rows, 1):
                unread_marker = (f"{FG['yel']}{unread:>4}{RESET}"
                                  if unread else f"{FG['gry']}{'-':>4}{RESET}")
                await self.session.write(
                    f"  {FG['grn']}{i:<3}{RESET}{name[:35]:<37} "
                    f"{total:>5}  {unread_marker}\r\n")
            await self.session.write('\r\n' + footer() + '\r\n')
            await self.session.write(
                f"  {FG['cyan']}A{RESET}=All Feeds (river)   "
                f"{FG['cyan']}#{RESET}=View feed   "
                f"{FG['cyan']}Q{RESET}=Back\r\n")
            choice = (await self.session.read_line('\r\nChoice: ') or '').strip().upper()
            if choice == 'Q' or choice == '':
                return
            if choice == 'A':
                await self._rss_river()
                continue
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(feed_rows):
                    await self._rss_feed_items(feed_rows[idx][0],
                                                feed_rows[idx][1])
            except ValueError:
                pass

    async def _rss_feed_items(self, feed_id, feed_name):
        """List items in one feed, paginated."""
        from anetbbs.models import RssItem, RssReadStatus
        from .ansi_ui import banner, footer, FG, RESET

        uid = self.session.user.get('id') if isinstance(self.session.user, dict) \
              else getattr(self.session.user, 'id', None)
        page = 0
        per_page = 15

        while True:
            with _app().app_context():
                q = (RssItem.query.filter_by(feed_id=feed_id)
                     .order_by(RssItem.published_at.desc().nullslast()))
                total_count = q.count()
                items = q.offset(page * per_page).limit(per_page).all()
                rows = []
                read_ids = set()
                if uid and items:
                    item_ids = [i.id for i in items]
                    read_ids = set(
                        r[0] for r in RssReadStatus.query
                        .with_entities(RssReadStatus.item_id)
                        .filter(RssReadStatus.user_id == uid,
                                RssReadStatus.item_id.in_(item_ids)).all())
                for i in items:
                    rows.append((i.id, i.title or '(no title)',
                                  i.published_at, i.id in read_ids))
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner(f'{feed_name}'))
            if not rows:
                await self.session.write(
                    f"  {FG['gry']}No items yet. The poller fetches every "
                    f"30 minutes; if this is fresh, give it a moment.{RESET}\r\n")
                await self.session.write('\r\n' + footer() + '\r\n')
                await self.session.read_line("Press Enter... ")
                return
            await self.session.write(
                f"  {FG['cyan']}#   Date    Title{RESET}\r\n"
                f"  {FG['gry']}{'─' * 70}{RESET}\r\n")
            for i, (_, title, ts, was_read) in enumerate(rows, 1):
                ts_str = ts.strftime('%m-%d') if ts else '  ?  '
                title_color = FG['gry'] if was_read else FG['wht']
                marker = ' ' if was_read else '*'
                await self.session.write(
                    f"  {FG['grn']}{i:<3}{RESET}{marker}{ts_str}  "
                    f"{title_color}{title[:60]:<60}{RESET}\r\n")
            total_pages = (total_count + per_page - 1) // per_page
            await self.session.write('\r\n' + footer() + '\r\n')
            await self.session.write(
                f"  Page {page + 1}/{total_pages or 1}   "
                f"{FG['cyan']}#{RESET}=read item   "
                f"{FG['cyan']}N{RESET}=next page   "
                f"{FG['cyan']}P{RESET}=prev   "
                f"{FG['cyan']}Q{RESET}=back\r\n")
            choice = (await self.session.read_line('\r\nChoice: ') or '').strip().upper()
            if choice == 'Q' or choice == '':
                return
            if choice == 'N':
                if (page + 1) * per_page < total_count:
                    page += 1
                continue
            if choice == 'P':
                if page > 0:
                    page -= 1
                continue
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(rows):
                    await self._rss_view_item(rows[idx][0])
            except ValueError:
                pass

    async def _rss_view_item(self, item_id):
        """Display a single RSS item full-screen and mark as read."""
        from anetbbs.models import db, RssItem, RssReadStatus
        from .ansi_ui import banner, footer, FG, RESET

        uid = self.session.user.get('id') if isinstance(self.session.user, dict) \
              else getattr(self.session.user, 'id', None)

        with _app().app_context():
            app = _app()
            item = RssItem.query.get(item_id)
            if not item:
                return
            title = item.title or '(no title)'
            link = item.link or ''
            author = item.author or ''
            published = item.published_at.strftime('%Y-%m-%d %H:%M UTC') \
                        if item.published_at else 'unknown'
            summary = item.summary or '(no summary)'
            feed_name = item.feed.name
            # Build short URL for terminal display
            domain = app.config.get('BBS_DOMAIN', '')
            port = app.config.get('WEB_PORT', 5000)
            web_base = (f"https://{domain}" if domain
                        else f"http://localhost:{port}")
            short_url = f"{web_base}/r/{item.id}" if link else ''
            # Mark as read
            if uid and not RssReadStatus.query.filter_by(
                    user_id=uid, item_id=item.id).first():
                try:
                    db.session.add(RssReadStatus(user_id=uid, item_id=item.id))
                    db.session.commit()
                except Exception:
                    db.session.rollback()
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner(feed_name[:40]))
        for title_line in self._wrap_text(title, 74):
            await self.session.write(f"  {FG['cyan']}{title_line}{RESET}\r\n")
        await self.session.write(
            f"  {FG['gry']}{'─' * 74}{RESET}\r\n"
            f"  {FG['gry']}Date:{RESET} {published}\r\n")
        if author:
            await self.session.write(
                f"  {FG['gry']}Author:{RESET} {author}\r\n")
        if short_url:
            await self.session.write(
                f"  {FG['gry']}Link:{RESET} {short_url}\r\n")
        await self.session.write(f"  {FG['gry']}{'─' * 74}{RESET}\r\n\r\n")
        # Word-wrap the summary to ~76 cols for readability on 80-col
        # terminals.
        for line in self._wrap_text(summary, 76):
            await self.session.write('  ' + line + '\r\n')
        await self.session.write('\r\n' + footer() + '\r\n')
        await self.session.read_line('\r\nPress Enter to return... ')

    @staticmethod
    def _wrap_text(text, width):
        """Simple word-wrap. Preserves paragraph breaks."""
        out = []
        for para in text.split('\n'):
            if not para.strip():
                out.append('')
                continue
            words = para.split()
            line = ''
            for w in words:
                if line and len(line) + 1 + len(w) > width:
                    out.append(line)
                    line = w
                else:
                    line = (line + ' ' + w) if line else w
            if line:
                out.append(line)
        return out

    async def _rss_river(self):
        """Combined river of newest items from all feeds, paginated."""
        from anetbbs.models import RssItem, RssFeed, RssReadStatus
        from .ansi_ui import banner, footer, FG, RESET

        uid = self.session.user.get('id') if isinstance(self.session.user, dict) \
              else getattr(self.session.user, 'id', None)
        page = 0
        per_page = 15

        while True:
            with _app().app_context():
                q = (RssItem.query.join(RssFeed)
                     .filter(RssFeed.is_active.is_(True))
                     .order_by(RssItem.published_at.desc().nullslast()))
                total_count = q.count()
                items = q.offset(page * per_page).limit(per_page).all()
                rows = []
                read_ids = set()
                if uid and items:
                    item_ids = [i.id for i in items]
                    read_ids = set(
                        r[0] for r in RssReadStatus.query
                        .with_entities(RssReadStatus.item_id)
                        .filter(RssReadStatus.user_id == uid,
                                RssReadStatus.item_id.in_(item_ids)).all())
                for i in items:
                    rows.append((i.id, i.title or '(no title)',
                                  i.feed.name, i.published_at,
                                  i.id in read_ids))
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('RSS - All Feeds'))
            if not rows:
                await self.session.write(
                    f"  {FG['gry']}No items across any feed yet.{RESET}\r\n")
                await self.session.write('\r\n' + footer() + '\r\n')
                await self.session.read_line("Press Enter... ")
                return
            await self.session.write(
                f"  {FG['cyan']}#   Date   Feed             Title{RESET}\r\n"
                f"  {FG['gry']}{'─' * 74}{RESET}\r\n")
            for i, (_, title, feed_name, ts, was_read) in enumerate(rows, 1):
                ts_str = ts.strftime('%m-%d') if ts else '  ?  '
                title_color = FG['gry'] if was_read else FG['wht']
                marker = ' ' if was_read else '*'
                await self.session.write(
                    f"  {FG['grn']}{i:<3}{RESET}{marker}{ts_str}  "
                    f"{FG['cyan']}{feed_name[:14]:<16}{RESET}"
                    f"{title_color}{title[:42]:<42}{RESET}\r\n")
            total_pages = (total_count + per_page - 1) // per_page
            await self.session.write('\r\n' + footer() + '\r\n')
            await self.session.write(
                f"  Page {page + 1}/{total_pages or 1}   "
                f"{FG['cyan']}#{RESET}=read   {FG['cyan']}N{RESET}=next   "
                f"{FG['cyan']}P{RESET}=prev   {FG['cyan']}Q{RESET}=back\r\n")
            choice = (await self.session.read_line('\r\nChoice: ') or '').strip().upper()
            if choice == 'Q' or choice == '':
                return
            if choice == 'N':
                if (page + 1) * per_page < total_count:
                    page += 1
                continue
            if choice == 'P':
                if page > 0:
                    page -= 1
                continue
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(rows):
                    await self._rss_view_item(rows[idx][0])
            except ValueError:
                pass

    async def show_profile(self):
        from anetbbs.models import User
        while True:
            with _app().app_context():
                u = User.query.get(self.session.user['id'])
                if not u:
                    await self.session.write("\r\nProfile not found.\r\n")
                    await self.session.read_line("\r\nPress Enter...")
                    return

            from .ansi_ui import banner, footer, FG, RESET
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Your Profile'))
            for label, value in [
                ('Username', u.username),
                ('Display name', u.display_name or '-'),
                ('Email', u.email or '-'),
                ('Joined', u.created_at.strftime('%Y-%m-%d') if u.created_at else '?'),
                ('Last login', u.last_login.strftime('%Y-%m-%d %H:%M') if u.last_login else 'never'),
                ('Login count', str(u.login_count or 0)),
                ('Admin', 'yes' if u.is_admin else 'no'),
                ('Location', u.location or '-'),
                ('Bio', (u.bio or '-')[:60]),
            ]:
                await self.session.write(
                    f"  {FG['cyan']}{label:<14}{RESET} "
                    f"{FG['gry']}:{RESET} "
                    f"{FG['grn']}{value}{RESET}\r\n")
            await self.session.write('\r\n' + footer() + '\r\n')
            choice = (await self.session.read_line(
                f"\r\n{FG['cyan']}E{RESET}=Edit Profile  "
                f"{FG['cyan']}W{RESET}=Change Password  "
                f"{FG['cyan']}Q{RESET}=Back  > ") or '').strip().upper()
            if choice == 'Q' or not choice:
                return
            elif choice == 'E':
                await self.edit_profile()
            elif choice == 'W':
                await self.change_password()


# ---------------------------------------------------------------------------
# Phase 3: Compose / Post / Reply / Edit
# ---------------------------------------------------------------------------

class _ComposeMixin:
    """Shared helpers for multi-line input (subject + body) used by post/PM/echomail compose."""

    async def _read_body(self, prompt='Enter message. End with a single "." on its own line, or /A to abort:'):
        """Read a multi-line body. Lines starting with '/' are commands:
              /S   save and return text
              /A   abort and return None
              .    (line of just a dot) save and return text
        Returns the text on save, None on abort.
        """
        await self.session.write("\r\n" + prompt + "\r\n")
        lines = []
        while True:
            line = await self.session.read_line('')
            if line is None:
                return None
            stripped = line.strip()
            if stripped in ('.',) or stripped.upper() == '/S':
                return '\n'.join(lines)
            if stripped.upper() == '/A':
                return None
            lines.append(line)


# Re-open BBSMenuUI to append more methods; Python lets us assign new methods
# to the class. (Or we could subclass; this is shorter.)

async def _post_compose(self, board_id, board_name, parent_id=None):
    from anetbbs.models import db, Post
    from .ansi_ui import banner, FG, RESET, BOLD
    from .anedit import launch_anedit

    # Gather quote text when replying
    quote = ""
    subject_default = ""
    if parent_id:
        with _app().app_context():
            parent = Post.query.get(parent_id)
            if parent:
                quote = parent.content or ""
                subject_default = parent.subject or ""
                if not subject_default.lower().startswith('re:'):
                    subject_default = f"Re: {subject_default}"

    await self.session.write('\x1b[2J\x1b[H')
    if parent_id:
        await self.session.write(banner(f'Reply — {board_name}'))
    else:
        await self.session.write(banner(f'New Thread — {board_name}'))

    prompt_subj = (f"  {FG['cyan']}Subject{RESET}"
                   + (f" [{subject_default[:40]}]" if subject_default else "")
                   + f"{FG['cyan']}:{RESET} ")
    subject = (await self.session.read_line(prompt_subj) or '').strip()
    if not subject and subject_default:
        subject = subject_default
    if not subject:
        await self.session.write(f"  {FG['gry']}Cancelled (empty subject).{RESET}\r\n")
        await self.session.read_line("  Press Enter...")
        return

    username = self.session.user.get('username', 'guest')
    body = await launch_anedit(self.session, quote=quote,
                               subject=subject, username=username)
    if body is None:
        await self.session.write(f"\r\n  {FG['gry']}Aborted.{RESET}\r\n")
        await self.session.read_line("  Press Enter...")
        return

    with _app().app_context():
        p = Post(board_id=board_id, author_id=self.session.user['id'],
                 parent_id=parent_id, subject=subject[:200], content=body)
        db.session.add(p)
        db.session.commit()
    await self.session.write(
        f"\r\n  {FG['grn']}{BOLD}[OK]{RESET} Posted (#{p.id}).\r\n")
    await self.session.read_line("\r\n  Press Enter...")
BBSMenuUI._post_compose = _post_compose


async def _send_pm(self):
    from anetbbs.models import db, User, PrivateMessage
    from .ansi_ui import banner, FG, RESET
    await self.session.write('\x1b[2J\x1b[H')
    await self.session.write(banner('Send Private Message'))
    to_username = (await self.session.read_line(
        f"  {FG['cyan']}To (username):{RESET} ") or '').strip()
    if not to_username:
        return
    with _app().app_context():
        recipient = User.query.filter_by(username=to_username).first()
        if not recipient:
            await self.session.write(f"\r\nNo such user '{to_username}'.\r\n")
            await self.session.read_line("Press Enter...")
            return
        recipient_id = recipient.id
    subject = (await self.session.read_line(f"  {FG['cyan']}Subject:{RESET} ") or '').strip()
    if not subject:
        return
    from .anedit import launch_anedit
    username = self.session.user.get('username', 'guest')
    body = await launch_anedit(self.session, subject=subject, username=username)
    if body is None:
        await self.session.write(f"\r\n  {FG['gry']}Aborted.{RESET}\r\n")
        await self.session.read_line("  Press Enter...")
        return
    with _app().app_context():
        pm = PrivateMessage(sender_id=self.session.user['id'], recipient_id=recipient_id,
                            subject=subject[:200], body=body)
        db.session.add(pm)
        db.session.commit()
    await self.session.write(f"\r\n  {FG['grn']}{BOLD}[OK]{RESET} Sent to {to_username}.\r\n")
    await self.session.read_line("  Press Enter...")
BBSMenuUI.send_pm = _send_pm


async def _compose_echomail(self):
    from anetbbs.models import db, EchoArea, EchomailMessage, EchomailNetwork
    from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD
    with _app().app_context():
        areas = EchoArea.query.join(EchomailNetwork).order_by(EchomailNetwork.name, EchoArea.name).all()
        a_list = [(a.id, a.tag, a.name, a.network.name, a.network.our_address or '1:1/1') for a in areas]
    if not a_list:
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner('Compose Echomail'))
        await self.session.write(
            f"  {FG['gry']}(no echomail areas configured){RESET}\r\n")
        await self.session.write('\r\n' + footer() + '\r\n')
        await self.session.read_line(
            f"{FG['cyan']}Press Enter...{RESET}")
        return
    await self.session.write('\x1b[2J\x1b[H')
    await self.session.write(banner('Compose Echomail'))
    # Page through long area lists so the user can see them all on a 24-row term.
    PAGE = 18
    for i, (_, tag, name, net, _) in enumerate(a_list, 1):
        await self.session.write(
            f"  {FG['yel']}{BOLD}{i:3d}{RESET}{FG['gry']}.{RESET} "
            f"{FG['grn']}{tag[:18]:<18}{RESET} "
            f"{FG['wht']}{name[:25]:<25}{RESET} "
            f"{FG['mag']}({net}){RESET}\r\n")
        if i % PAGE == 0 and i < len(a_list):
            ans = (await self.session.read_line(
                f"  {FG['cyan']}-- more (Enter, "
                f"Q=stop listing) --{RESET}") or '').strip().upper()
            if ans == 'Q':
                await self.session.write('\r\n')
                break
    await self.session.write('\r\n' + footer() + '\r\n')
    pick = (await self.session.read_line(
        _prompt('Pick area (number / Q): ')) or '').strip()
    if pick.upper() == 'Q':
        return
    try:
        idx = int(pick) - 1
        area = a_list[idx]
    except (ValueError, IndexError):
        return
    to_name = (await self.session.read_line(
        f"  {FG['cyan']}To (name, e.g. 'All'):{RESET} ") or 'All').strip()
    subject = (await self.session.read_line(
        f"  {FG['cyan']}Subject:{RESET} ") or '').strip()
    if not subject:
        return
    from .anedit import launch_anedit
    username = self.session.user.get('username', 'guest')
    body = await launch_anedit(self.session, subject=subject, username=username)
    if body is None:
        await self.session.write(
            f"\r\n  {FG['gry']}Aborted.{RESET}\r\n")
        await self.session.read_line(f"\r\n{FG['cyan']}Press Enter...{RESET}")
        return
    with _app().app_context():
        em = EchomailMessage(
            area_id=area[0],
            network_id=EchomailNetwork.query.filter_by(name=area[3]).first().id,
            from_name=username[:100],
            from_address=area[4],
            to_name=to_name[:100],
            subject=subject[:200],
            body=body,
            direction='outbound',
        )
        db.session.add(em)
        db.session.commit()
    await self.session.write(
        f"\r\n  {FG['grn']}{BOLD}[OK] Queued for next BinkP poll.{RESET}"
        f"  {FG['gry']}(msg id={em.id}){RESET}\r\n")
    await self.session.read_line(
        f"\r\n{FG['cyan']}Press Enter...{RESET}")
BBSMenuUI.compose_echomail = _compose_echomail


async def _edit_profile(self):
    from anetbbs.models import db, User
    fields = [
        ('display_name', 'Display name', 100),
        ('email', 'Email', 120),
        ('location', 'Location', 100),
        ('website', 'Website', 255),
        ('bio', 'Bio (one line)', 500),
        ('signature', 'Signature (one line)', 500),
    ]
    with _app().app_context():
        u = User.query.get(self.session.user['id'])
        if not u:
            return
        await self.session.write("\r\n=== Edit Profile (blank = keep) ===\r\n\r\n")
        for attr, label, maxlen in fields:
            current = getattr(u, attr, '') or ''
            new = await self.session.read_line(f"{label} [{current[:30]}]: ")
            if new and new.strip():
                setattr(u, attr, new.strip()[:maxlen])
        db.session.commit()
    await self.session.write("\r\nProfile saved.\r\n")
    await self.session.read_line("Press Enter...")
BBSMenuUI.edit_profile = _edit_profile


async def _change_password(self):
    from anetbbs.models import db, User
    from werkzeug.security import check_password_hash, generate_password_hash
    cur = await self.session.read_password("\r\nCurrent password: ")
    new1 = await self.session.read_password("New password: ")
    new2 = await self.session.read_password("Confirm new: ")
    if not new1 or new1 != new2:
        await self.session.write("\r\nPasswords don't match (or empty). Cancelled.\r\n")
        await self.session.read_line("Press Enter...")
        return
    with _app().app_context():
        u = User.query.get(self.session.user['id'])
        if not u or not check_password_hash(u.password_hash, cur or ''):
            await self.session.write("\r\nCurrent password incorrect.\r\n")
            await self.session.read_line("Press Enter...")
            return
        u.password_hash = generate_password_hash(new1)
        db.session.commit()
    await self.session.write("\r\nPassword changed.\r\n")
    await self.session.read_line("Press Enter...")
BBSMenuUI.change_password = _change_password


# Override list_threads to add 'N' for new thread + 'R' from inside read_thread
async def _list_threads_v2(self, board_id, board_name):
    from anetbbs.models import Post, User
    from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD
    while True:
        with _app().app_context():
            threads = (Post.query
                       .filter_by(board_id=board_id, parent_id=None)
                       .order_by(Post.created_at.desc())
                       .limit(50).all())
            t_list = []
            for t in threads:
                author = User.query.get(t.author_id)
                t_list.append((t.id, t.subject, author.username if author else '?',
                               t.created_at, t.replies.count()))

        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner(board_name))
        if not t_list:
            await self.session.write(f"  {FG['gry']}(no threads yet){RESET}\r\n")
        for i, (_, subj, who, when, n_replies) in enumerate(t_list, 1):
            ts = when.strftime('%m-%d %H:%M') if when else '?'
            rep = f"[{n_replies}]" if n_replies else "   "
            await self.session.write(
                f"  {FG['yel']}{BOLD}{i:2d}{RESET}"
                f"{FG['gry']}.{RESET} "
                f"{FG['cyan']}{rep:<5}{RESET}"
                f"{FG['wht']}{subj[:35]:<35}{RESET}  "
                f"{FG['grn']}{who[:14]:<14}{RESET}"
                f"{FG['gry']}{ts}{RESET}\r\n")
        await self.session.write('\r\n' + footer() + '\r\n')
        choice = (await self.session.read_line(
            _prompt('Number / N=new / Q=back: ')) or '').strip()
        u = choice.upper()
        if u == 'Q' or not choice:
            return
        if u == 'N':
            await self._post_compose(board_id, board_name)
            continue
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(t_list):
                await self.read_thread_v2(t_list[idx][0], board_id, board_name)
        except ValueError:
            pass
BBSMenuUI.list_threads = _list_threads_v2


async def _read_thread_v2(self, post_id, board_id, board_name):
    from anetbbs.models import Post, User
    from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD
    with _app().app_context():
        root = Post.query.get(post_id)
        if not root:
            return
        posts = [root] + list(root.replies.order_by(Post.created_at).all())
        rendered = []
        for p in posts:
            author = User.query.get(p.author_id)
            rendered.append({
                'subject': p.subject, 'author': author.username if author else '?',
                'when': p.created_at, 'content': p.content,
                'pid': p.id,
            })
    await self.session.write('\x1b[2J\x1b[H')
    await self.session.write(banner(f'{board_name} — {rendered[0]["subject"][:40]}'))
    for i, p in enumerate(rendered):
        ts = p['when'].strftime('%Y-%m-%d %H:%M') if p['when'] else '?'
        tag = f"{FG['yel']}{BOLD}[OP]{RESET}" if i == 0 else f"{FG['gry']}[Reply {i}]{RESET}"
        await self.session.write(
            f"\r\n{tag}  "
            f"{FG['cyan']}{BOLD}{p['subject']}{RESET}\r\n"
            f"  {FG['grn']}From:{RESET} {p['author']:<16}"
            f"  {FG['gry']}Date:{RESET} {ts}\r\n"
            f"  {FG['gry']}{'─' * 60}{RESET}\r\n")
        for line in (p['content'] or '').splitlines():
            if line.lstrip().startswith('>'):
                await self.session.write(f"  {FG['gry']}{line[:76]}{RESET}\r\n")
            else:
                await self.session.write(f"  {line[:76]}\r\n")
    await self.session.write('\r\n' + footer() + '\r\n')
    choice = (await self.session.read_line(
        _prompt('R=reply  Enter=back: ')) or '').strip().upper()
    if choice == 'R':
        await self._post_compose(board_id, board_name, parent_id=post_id)
BBSMenuUI.read_thread_v2 = _read_thread_v2


# ---------------------------------------------------------------------------
# Phase 4: Sysop tools (admin users only)
# ---------------------------------------------------------------------------

async def _sysop_menu(self):
    """Top-level sysop menu — only shown to is_admin users."""
    if not self.session.user.get('is_admin'):
        await self.session.write("\r\nSysop access required.\r\n")
        await self.session.read_line("Press Enter...")
        return
    from .ansi_ui import (load_menu_ansi, banner, menu_item, footer,
                          prompt as _prompt, FG, RESET)
    while True:
        ansi = load_menu_ansi('sysop_menu')
        if ansi:
            self.session.writer.write(b'\x1b[2J\x1b[H' + ansi)
            await self.session.writer.drain()
        else:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Sysop Tools'))
            for hk, lbl in (('U', 'Manage users'),
                            ('B', 'Manage boards'),
                            ('S', 'Server status'),
                            ('Q', 'Return')):
                await self.session.write(menu_item(hk, lbl) + '\r\n')
            await self.session.write('\r\n' + footer() + '\r\n')
        choice = (await self.session.read_line(_prompt('Choice: ')) or '').upper()
        if choice == 'Q' or not choice:
            return
        elif choice == 'U':
            await self.sysop_users()
        elif choice == 'B':
            await self.sysop_boards()
        elif choice == 'S':
            await self.sysop_status()
BBSMenuUI.sysop_menu = _sysop_menu


async def _sysop_users(self):
    from anetbbs.models import User
    from .ansi_ui import load_menu_ansi, banner, footer, FG, RESET, BOLD, prompt as _p
    while True:
        with _app().app_context():
            users = User.query.order_by(User.id).all()
            u_list = [(u.id, u.username, u.email or '-', u.is_active, u.is_admin,
                       u.last_login.strftime('%Y-%m-%d') if u.last_login else 'never',
                       u.login_count or 0) for u in users]
        ansi = load_menu_ansi('sysop_users')
        if ansi:
            self.session.writer.write(b'\x1b[2J\x1b[H' + ansi)
            await self.session.writer.drain()
        else:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Manage Users'))
        await self.session.write(
            f"  {FG['cyan']}{BOLD}{'ID':<4}{'Username':<18}{'Active':<8}"
            f"{'Admin':<8}{'Last login':<14}Logins{RESET}\r\n"
            f"  {FG['gry']}{'─' * 60}{RESET}\r\n")
        for uid, name, _, active, admin, lastl, n in u_list:
            active_s = f"{FG['grn']}yes{RESET}" if active else f"{FG['red']} no{RESET}"
            admin_s  = f"{FG['yel']}yes{RESET}" if admin  else f"{FG['dim']} no{RESET}"
            await self.session.write(
                f"  {FG['wht']}{uid:<4}{RESET}"
                f"{FG['grn']}{name[:18]:<18}{RESET}"
                f"{active_s:<20}{admin_s:<20}"
                f"{FG['dim']}{lastl:<14}{RESET}"
                f"{FG['cyan']}{n}{RESET}\r\n")
        await self.session.write('\r\n' + footer() + '\r\n')
        choice = (await self.session.read_line(_p('User ID to edit (or Q): ')) or '').strip()
        if choice.upper() == 'Q' or not choice:
            return
        try:
            uid = int(choice)
        except ValueError:
            continue
        await self._sysop_edit_user(uid)
BBSMenuUI.sysop_users = _sysop_users


async def _sysop_edit_user(self, uid):
    from anetbbs.models import db, User
    from werkzeug.security import generate_password_hash
    from .ansi_ui import banner, menu_item, footer, FG, RESET, prompt as _p
    with _app().app_context():
        u = User.query.get(uid)
        if not u:
            await self.session.write(f"\r\n{FG['red']}No such user.{RESET}\r\n")
            await self.session.read_line("Press Enter...")
            return
        info = (u.username, u.email, u.is_active, u.is_admin)
    while True:
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner(f'Edit User #{uid}'))
        active_s = f"{FG['grn']}yes{RESET}" if info[2] else f"{FG['red']}no{RESET}"
        admin_s  = f"{FG['yel']}yes{RESET}" if info[3] else f"{FG['dim']}no{RESET}"
        await self.session.write(
            f"  {FG['cyan']}User:{RESET}   {FG['wht']}{info[0]}{RESET}\r\n"
            f"  {FG['cyan']}Email:{RESET}  {FG['dim']}{info[1]}{RESET}\r\n"
            f"  {FG['cyan']}Active:{RESET} {active_s}    "
            f"{FG['cyan']}Admin:{RESET} {admin_s}\r\n\r\n")
        for hk, lbl in (('T', 'Toggle active'),
                        ('A', 'Toggle admin'),
                        ('P', 'Reset password'),
                        ('D', 'Delete user (PERMANENT)'),
                        ('Q', 'Back')):
            await self.session.write(menu_item(hk, lbl) + '\r\n')
        await self.session.write('\r\n' + footer() + '\r\n')
        choice = (await self.session.read_line(_p('Choice: ')) or '').strip().upper()
        if choice == 'Q' or not choice:
            return
        with _app().app_context():
            u = User.query.get(uid)
            if not u:
                return
            if choice == 'T':
                u.is_active = not u.is_active
                info = (u.username, u.email, u.is_active, u.is_admin)
                await self.session.write(
                    f"\r\n{FG['grn']}Active toggled to {u.is_active}.{RESET}\r\n")
            elif choice == 'A':
                u.is_admin = not u.is_admin
                info = (u.username, u.email, u.is_active, u.is_admin)
                await self.session.write(
                    f"\r\n{FG['grn']}Admin toggled to {u.is_admin}.{RESET}\r\n")
            elif choice == 'P':
                new = await self.session.read_line("New password: ")
                if new:
                    u.password_hash = generate_password_hash(new)
                    await self.session.write(f"\r\n{FG['grn']}Password updated.{RESET}\r\n")
            elif choice == 'D':
                confirm = await self.session.read_line(
                    f"\r\n{FG['red']}Type DELETE to confirm: {RESET}")
                if confirm == 'DELETE':
                    db.session.delete(u)
                    db.session.commit()
                    await self.session.write(f"\r\n{FG['red']}User deleted.{RESET}\r\n")
                    await self.session.read_line("Press Enter...")
                    return
            db.session.commit()
BBSMenuUI._sysop_edit_user = _sysop_edit_user


async def _sysop_boards(self):
    from anetbbs.models import db, Board
    from .ansi_ui import load_menu_ansi, banner, menu_item, footer, FG, RESET, BOLD, prompt as _p
    while True:
        with _app().app_context():
            boards = Board.query.order_by(Board.order, Board.name).all()
            b_list = [(b.id, b.name, b.description or '', b.is_active, b.posts.count()) for b in boards]
        ansi = load_menu_ansi('sysop_boards')
        if ansi:
            self.session.writer.write(b'\x1b[2J\x1b[H' + ansi)
            await self.session.writer.drain()
        else:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Manage Boards'))
        for bid, name, desc, active, count in b_list:
            mark = f"{FG['grn']}*{RESET}" if active else f"{FG['red']}X{RESET}"
            await self.session.write(
                f"  [{mark}] {FG['wht']}{bid:<3}{RESET} "
                f"{FG['grn']}{name[:25]:<25}{RESET} "
                f"{FG['cyan']}({count:4d} threads){RESET}\r\n")
            if desc:
                await self.session.write(f"          {FG['dim']}{desc[:60]}{RESET}\r\n")
        await self.session.write('\r\n')
        await self.session.write(menu_item('N', 'New board') + '\r\n')
        await self.session.write(menu_item('Q', 'Back') + '\r\n')
        await self.session.write(f"  {FG['dim']}Or enter a board ID to edit{RESET}\r\n")
        await self.session.write('\r\n' + footer() + '\r\n')
        choice = (await self.session.read_line(_p('Choice: ')) or '').strip()
        u = choice.upper()
        if u == 'Q' or not choice:
            return
        if u == 'N':
            name = (await self.session.read_line("Board name: ") or '').strip()
            if not name:
                continue
            desc = (await self.session.read_line("Description: ") or '').strip()
            with _app().app_context():
                db.session.add(Board(name=name[:100], description=desc, is_active=True))
                db.session.commit()
            await self.session.write(f"\r\n{FG['grn']}Board created.{RESET}\r\n")
            continue
        try:
            bid = int(choice)
        except ValueError:
            continue
        with _app().app_context():
            b = Board.query.get(bid)
            if not b:
                await self.session.write(f"\r\n{FG['red']}Board not found.{RESET}\r\n")
                continue
            await self.session.write(
                f"\r\n{FG['cyan']}Editing board #{bid}: {FG['wht']}{b.name}{RESET}\r\n"
                f"  {FG['yel']}[T]{RESET} toggle active  "
                f"{FG['yel']}[R]{RESET} rename  "
                f"{FG['yel']}[X]{RESET} delete  "
                f"{FG['yel']}[Q]{RESET} back\r\n")
            sub = (await self.session.read_line(_p('Choice: ')) or '').strip().upper()
            if sub == 'T':
                b.is_active = not b.is_active
                await self.session.write(f"\r\n{FG['grn']}Active toggled.{RESET}\r\n")
            elif sub == 'R':
                new = (await self.session.read_line("New name: ") or '').strip()
                if new:
                    b.name = new[:100]
                    await self.session.write(f"\r\n{FG['grn']}Renamed.{RESET}\r\n")
            elif sub == 'X':
                confirm = await self.session.read_line(
                    f"\r\n{FG['red']}Type DELETE to confirm: {RESET}")
                if confirm == 'DELETE':
                    db.session.delete(b)
                    await self.session.write(f"\r\n{FG['red']}Deleted.{RESET}\r\n")
            db.session.commit()
BBSMenuUI.sysop_boards = _sysop_boards


async def _sysop_status(self):
    """Quick server-status snapshot for sysops (counts + recent activity)."""
    from anetbbs.models import (User, UserSession, Post, Message as Bulletin,
                                PrivateMessage, EchomailMessage)
    from .ansi_ui import load_menu_ansi, banner, footer, FG, RESET, BOLD, prompt as _p
    with _app().app_context():
        five = datetime.utcnow() - timedelta(minutes=5)
        stats = {
            'Users total':      User.query.count(),
            'Active (30d)':     User.query.filter(User.last_login >= datetime.utcnow() - timedelta(days=30)).count(),
            'Online now':       UserSession.query.filter(UserSession.last_seen >= five).count(),
            'Posts':            Post.query.count(),
            'Private msgs':     PrivateMessage.query.count(),
            'Bulletins':        Bulletin.query.count(),
            'Echo inbound':     EchomailMessage.query.filter_by(direction='inbound').count(),
            'Echo out (queue)': EchomailMessage.query.filter_by(direction='outbound', sent_at=None).count(),
            'Echo out (sent)':  EchomailMessage.query.filter(EchomailMessage.sent_at != None).count(),
        }
    ansi = load_menu_ansi('sysop_status')
    if ansi:
        self.session.writer.write(b'\x1b[2J\x1b[H' + ansi)
        await self.session.writer.drain()
    else:
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner('Server Status'))
    for k, v in stats.items():
        await self.session.write(
            f"  {FG['cyan']}{k:<18}{RESET} "
            f"{FG['wht']}{BOLD}{v}{RESET}\r\n")
    await self.session.write('\r\n' + footer() + '\r\n')
    await self.session.read_line(_p('Press Enter...'))
BBSMenuUI.sysop_status = _sysop_status


# ---------------------------------------------------------------------------
# Re-wire the main BBS menu to add Send PM, Compose Echomail, Edit profile,
# Change password, and Sysop menu (admin-gated).
# ---------------------------------------------------------------------------

async def _show_main_v2(self):
    while True:
        is_sysop = self.session.user.get('is_admin')
        sysop_line = "║  S. Sysop tools                          ║\r\n" if is_sysop else ""
        menu = (
            "\r\n"
            "╔══════════════════════════════════════════╗\r\n"
            "║              Main BBS Menu               ║\r\n"
            "╠══════════════════════════════════════════╣\r\n"
            "║  M. Message Boards                       ║\r\n"
            "║  N. New PM (send to user)                ║\r\n"
            "║  P. PM Inbox                             ║\r\n"
            "║  B. Bulletins                            ║\r\n"
            "║  E. Echomail (read)                      ║\r\n"
            "║  C. Compose Echomail                     ║\r\n"
            "║  F. File Library                         ║\r\n"
            "║  U. Who's Online                         ║\r\n"
            "║  Y. Your Profile                         ║\r\n"
            "║  X. Edit Profile                         ║\r\n"
            "║  W. Change Password                      ║\r\n"
            f"{sysop_line}"
            "║  Q. Return                               ║\r\n"
            "╚══════════════════════════════════════════╝\r\n"
            "\r\n"
            "Choice: "
        )
        choice = (await self.session.read_line(menu) or '').strip().upper()
        if choice == 'Q':
            return
        elif choice == 'M':
            await self.list_boards()
        elif choice == 'B':
            await self.list_bulletins()
        elif choice == 'P':
            await self.list_pm_inbox()
        elif choice == 'N':
            await self.send_pm()
        elif choice == 'E':
            await self.list_echo_areas()
        elif choice == 'C':
            await self.compose_echomail()
        elif choice == 'F':
            await self.list_files()
        elif choice == 'U':
            await self.show_online()
        elif choice == 'Y':
            await self.show_profile()
        elif choice == 'X':
            await self.edit_profile()
        elif choice == 'W':
            await self.change_password()
        elif choice == 'S' and is_sysop:
            await self.sysop_menu()
BBSMenuUI.show_main = _show_main_v2
