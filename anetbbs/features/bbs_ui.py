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
        """Network chooser → per-network area list → read area."""
        from anetbbs.models import EchoArea, EchomailNetwork, EchomailMessage
        from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD

        _user_level = int((self.session.user or {}).get('access_level', 10))
        _is_admin   = bool((self.session.user or {}).get('is_admin'))

        with _app().app_context():
            nets = EchomailNetwork.query.filter_by(is_active=True)\
                .order_by(EchomailNetwork.name).all()
            net_rows = []
            for net in nets:
                _eq = EchoArea.query.filter_by(
                    network_id=net.id, is_active=True, is_subscribed=True)
                if not _is_admin:
                    _eq = _eq.filter(
                        EchoArea.is_sysop_only == False,
                        EchoArea.min_access_level <= _user_level,
                    )
                count = _eq.count()
                if count:
                    net_rows.append((net.id, net.name, net.network_type, count))

        if not net_rows:
            await self.session.write("\r\nNo echomail areas configured.\r\n")
            await self.session.read_line("\r\nPress Enter...")
            return

        while True:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Echomail Networks'))
            await self.session.write(
                f"  {FG['cyan']}{BOLD}{'#':>2}  {'Network':<30} "
                f"{'Type':<6} {'Areas':>5}{RESET}\r\n"
                f"  {FG['gry']}{'─' * 50}{RESET}\r\n")
            for i, (_, name, ntype, count) in enumerate(net_rows, 1):
                type_col = FG['cyan'] if ntype == 'binkp' else FG['yel']
                await self.session.write(
                    f"  {FG['yel']}{BOLD}{i:2d}{RESET}  "
                    f"{FG['wht']}{name[:30]:<30}{RESET}  "
                    f"{type_col}{ntype[:5]:<5}{RESET}  "
                    f"{FG['grn']}{count:5d}{RESET}\r\n")
            await self.session.write(
                f"\r\n  {FG['gry']}A = Apply for ANotherNetwork QWK node{RESET}\r\n")
            await self.session.write('\r\n' + footer() + '\r\n')
            choice = (await self.session.read_line(
                _prompt('Choose network (number / A=apply / Q): ')) or '').strip().upper()
            if choice == 'Q' or not choice:
                return
            if choice == 'A':
                await self._apply_qwk_node()
                continue
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(net_rows):
                    await self._list_network_areas(
                        net_rows[idx][0], net_rows[idx][1],
                        _user_level, _is_admin)
            except ValueError:
                pass

    async def _list_network_areas(self, network_id, net_name,
                                   user_level, is_admin):
        """Show areas for one network as a scrollable lightbar."""
        from anetbbs.models import EchoArea, EchomailMessage
        from .ansi_ui import banner, FG, RESET, BOLD

        with _app().app_context():
            _eq = (EchoArea.query
                   .filter_by(network_id=network_id,
                               is_active=True, is_subscribed=True))
            if not is_admin:
                _eq = _eq.filter(
                    EchoArea.is_sysop_only == False,
                    EchoArea.min_access_level <= user_level,
                )
            areas = _eq.order_by(EchoArea.category, EchoArea.order,
                                  EchoArea.name).all()
            a_list = []
            for area in areas:
                cat = area.category or 'General'
                msg_count = EchomailMessage.query.filter_by(
                    area_id=area.id).count()
                a_list.append((area.id, area.tag, area.name, msg_count, cat))

        if not a_list:
            await self.session.write(
                f"\r\n  {FG['gry']}No areas in this network.{RESET}\r\n")
            await self.session.read_line(
                f"  {FG['cyan']}Press Enter...{RESET}")
            return

        COL_TAG  = 16
        COL_NAME = 24
        COL_CAT  = 10

        async def render_header_areas():
            await self.session.write(banner(net_name))
            await self.session.write(
                f"  {FG['cyan']}{BOLD}"
                f"{'#':>3}  {'Tag':<{COL_TAG}} {'Name':<{COL_NAME}} "
                f"{'Category':<{COL_CAT}} {'Msgs':>5}{RESET}\r\n"
                f"  {FG['gry']}{'─' * 72}{RESET}\r\n")

        def render_row_area(idx, row, selected):
            _, tag, name, n, cat = row
            n_col = FG['wht'] if not selected else ''
            return (f"  {FG['yel']}{idx+1:>3}{RESET}  "
                    f"{n_col}{tag[:COL_TAG]:<{COL_TAG}}{RESET} "
                    f"{FG['wht']}{name[:COL_NAME]:<{COL_NAME}}{RESET} "
                    f"{FG['gry']}{cat[:COL_CAT]:<{COL_CAT}}{RESET} "
                    f"{FG['grn']}{n:>5}{RESET}")

        def render_hint_area(sel, total):
            return (f"  {FG['cyan']}{sel+1}/{total}{RESET}  "
                    f"{FG['cyan']}Up/Dn PgUp/PgDn{RESET}=scroll  "
                    f"{FG['cyan']}Enter{RESET}=open  "
                    f"{FG['cyan']}Q{RESET}=back")

        last_sel = 0
        while True:
            result = await self._rss_lightbar(
                a_list, render_header_areas, render_row_area, render_hint_area,
                initial_sel=last_sel)
            if result[0] == 'quit':
                return
            elif result[0] == 'enter':
                last_sel = result[1]
                aid, tag, _, _, _ = a_list[last_sel]
                await self.read_echo_area(aid, tag)

    # ------------------------------------------------------------------
    # ANotherNetwork QWK node self-registration
    # ------------------------------------------------------------------

    async def _apply_qwk_node(self):
        """Walk the sysop through a QWK node application wizard."""
        import re, secrets, string as _str
        from anetbbs.models import db, QWKNode, QWKNodeRequest
        from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD

        user     = self.session.user or {}
        user_id  = user.get('id')
        username = user.get('username', 'Guest')

        # Check for an existing request from this user.
        with _app().app_context():
            existing = None
            if user_id:
                existing = (QWKNodeRequest.query
                            .filter_by(applied_by_user_id=user_id)
                            .order_by(QWKNodeRequest.created_at.desc())
                            .first())
        if existing:
            await self._show_qwk_request_status(existing)
            return

        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner('ANotherNetwork — QWK Node Application'))
        await self.session.write(
            f"\r\n"
            f"  {FG['wht']}ANotherNetwork (Zone 1200) connects BBS systems via QWK echomail.{RESET}\r\n"
            f"  {FG['wht']}Register as a QWK node to exchange messages with other systems.{RESET}\r\n"
            f"\r\n"
            f"  {FG['cyan']}Hub  : bbs.a-net.fyi  (FTP port 21){RESET}\r\n"
            f"  {FG['cyan']}Poll : download ANET.qwk  /  upload <YOURID>.rep{RESET}\r\n"
            f"\r\n"
            f"  {FG['gry']}Fill in the details below. The hub sysop will review your{RESET}\r\n"
            f"  {FG['gry']}application and post your credentials here when approved.{RESET}\r\n"
            f"\r\n"
        )
        go = (await self.session.read_line(
            _prompt('Press Enter to continue or Q to cancel: ')
        ) or '').strip().upper()
        if go == 'Q':
            return

        # ── collect fields ────────────────────────────────────────────
        bbs_name = ''
        while not bbs_name:
            bbs_name = (await self.session.read_line(
                f"  {FG['yel']}BBS Name: {RESET}") or '').strip()[:100]
            if not bbs_name:
                await self.session.write(
                    f"  {FG['red']}BBS name is required.{RESET}\r\n")

        packet_id = ''
        while not packet_id:
            pid = (await self.session.read_line(
                f"  {FG['yel']}Desired Packet ID (2-8 chars, A-Z / 0-9): {RESET}"
            ) or '').strip().upper()
            if not pid:
                await self.session.write(
                    f"  {FG['red']}Packet ID is required.{RESET}\r\n")
                continue
            if not re.match(r'^[A-Z0-9]{2,8}$', pid):
                await self.session.write(
                    f"  {FG['red']}Use 2-8 characters: A-Z and 0-9 only.{RESET}\r\n")
                continue
            with _app().app_context():
                taken = (QWKNode.query.filter_by(packet_id=pid).first() or
                         QWKNodeRequest.query.filter_by(
                             packet_id=pid, status='pending').first())
            if taken:
                await self.session.write(
                    f"  {FG['red']}That packet ID is taken — please choose another.{RESET}\r\n")
                continue
            packet_id = pid

        sysop_in = (await self.session.read_line(
            f"  {FG['yel']}Your name [{username}]: {RESET}") or '').strip()
        sysop_name = (sysop_in or username)[:100]

        email = (await self.session.read_line(
            f"  {FG['yel']}Contact email (Enter to skip): {RESET}") or '').strip()[:200] or None

        bbs_addr = (await self.session.read_line(
            f"  {FG['yel']}BBS address (e.g. mybbs.net:23, or Enter to skip): {RESET}"
        ) or '').strip()[:200] or None

        notes = (await self.session.read_line(
            f"  {FG['yel']}Notes / BBS software (Enter to skip): {RESET}"
        ) or '').strip()[:500] or None

        # ── review & confirm ──────────────────────────────────────────
        await self.session.write(
            f"\r\n{FG['cyan']}{BOLD}  ── Application Summary ──{RESET}\r\n"
            f"  BBS Name  : {FG['wht']}{bbs_name}{RESET}\r\n"
            f"  Packet ID : {FG['grn']}{BOLD}{packet_id}{RESET}\r\n"
            f"  Sysop     : {FG['wht']}{sysop_name}{RESET}\r\n"
            f"  Email     : {FG['wht']}{email or '(not provided)'}{RESET}\r\n"
            f"  BBS Addr  : {FG['wht']}{bbs_addr or '(not provided)'}{RESET}\r\n"
            f"\r\n"
        )
        confirm = (await self.session.read_line(
            _prompt('Submit? (Y / N): ')
        ) or '').strip().upper()
        if confirm != 'Y':
            await self.session.write(
                f"  {FG['gry']}Application cancelled.{RESET}\r\n")
            await self.session.read_line(
                _prompt('Press Enter to continue...'))
            return

        with _app().app_context():
            req = QWKNodeRequest(
                bbs_name=bbs_name,
                packet_id=packet_id,
                sysop_name=sysop_name,
                email=email,
                bbs_address=bbs_addr,
                notes=notes,
                applied_via='terminal',
                applied_by_user_id=user_id,
                applied_by_username=username,
            )
            db.session.add(req)
            db.session.commit()

        await self.session.write(
            f"\r\n  {FG['grn']}{BOLD}Application submitted!{RESET}\r\n"
            f"  {FG['wht']}The hub sysop will review your request.{RESET}\r\n"
            f"  {FG['wht']}Return to this menu to check your status.{RESET}\r\n\r\n"
        )
        await self.session.read_line(_prompt('Press Enter to continue...'))

    async def _show_qwk_request_status(self, req):
        """Show the applicant their application status (pending/approved/denied)."""
        from anetbbs.models import db, QWKNodeRequest as _QNR
        from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD

        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner('ANotherNetwork — Node Application Status'))

        date_str = req.created_at.strftime('%Y-%m-%d') if req.created_at else '?'

        if req.status == 'pending':
            await self.session.write(
                f"\r\n  {FG['yel']}{BOLD}Status : PENDING REVIEW{RESET}\r\n"
                f"\r\n"
                f"  {FG['wht']}Packet ID requested : {FG['grn']}{req.packet_id}{RESET}\r\n"
                f"  {FG['wht']}BBS Name            : {FG['wht']}{req.bbs_name}{RESET}\r\n"
                f"  {FG['wht']}Applied             : {FG['gry']}{date_str}{RESET}\r\n"
                f"\r\n"
                f"  {FG['gry']}Your application is under review. Check back soon.{RESET}\r\n"
            )

        elif req.status == 'approved':
            await self.session.write(
                f"\r\n  {FG['grn']}{BOLD}Status : APPROVED{RESET}\r\n"
                f"\r\n"
                f"  {FG['wht']}Configure your BBS with these credentials:{RESET}\r\n"
                f"\r\n"
                f"  {FG['cyan']}Packet ID : {FG['grn']}{BOLD}{req.packet_id}{RESET}\r\n"
                f"  {FG['cyan']}Password  : {FG['grn']}{BOLD}{req.generated_password or '(contact hub sysop)'}{RESET}\r\n"
                f"  {FG['cyan']}Hub FTP   : {FG['wht']}bbs.a-net.fyi  port 21{RESET}\r\n"
                f"  {FG['cyan']}Download  : {FG['wht']}ANET.qwk{RESET}\r\n"
                f"  {FG['cyan']}Upload    : {FG['wht']}{req.packet_id}.rep{RESET}\r\n"
                f"\r\n"
                f"  {FG['gry']}Login to FTP with your Packet ID as the username.{RESET}\r\n"
            )
            if not req.seen_by_applicant:
                with _app().app_context():
                    r = _QNR.query.get(req.id)
                    if r:
                        r.seen_by_applicant = True
                        db.session.commit()

        elif req.status == 'denied':
            await self.session.write(
                f"\r\n  {FG['red']}{BOLD}Status : NOT APPROVED{RESET}\r\n"
                f"\r\n"
                f"  {FG['wht']}Your application for {FG['grn']}{req.packet_id}{FG['wht']} was not approved.{RESET}\r\n"
            )
            if req.deny_reason:
                await self.session.write(
                    f"  {FG['yel']}Reason : {FG['wht']}{req.deny_reason}{RESET}\r\n"
                )
            await self.session.write(
                f"\r\n  {FG['gry']}You may apply again with a different packet ID.{RESET}\r\n"
            )

        await self.session.read_line(_prompt('\r\nPress Enter to continue...'))

    async def read_echo_area(self, area_id, tag):
        from anetbbs.models import EchomailMessage, EchoArea
        from .ansi_ui import banner, FG, RESET, BOLD

        with _app().app_context():
            msgs = (EchomailMessage.query
                    .filter_by(area_id=area_id)
                    .order_by(EchomailMessage.created_at.desc())
                    .limit(300).all())
            m_list = [(m.id, m.subject, m.from_name, m.to_name,
                       m.created_at, m.body) for m in msgs]
            aobj     = EchoArea.query.get(area_id)
            net_id   = aobj.network_id if aobj else None
            net_addr = (aobj.network.our_address or '1:1/1') if (aobj and aobj.network) else '1:1/1'

        if not m_list:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner(tag))
            await self.session.write(
                f"  {FG['gry']}(no messages in this area yet){RESET}\r\n")
            await self.session.read_line(f"  {FG['cyan']}Press Enter...{RESET}")
            return

        COL_SUBJ = 36
        COL_FROM = 14

        async def render_header_msgs():
            await self.session.write(banner(tag))
            await self.session.write(
                f"  {FG['cyan']}{BOLD}"
                f"{'#':>3}  {'Subject':<{COL_SUBJ}} "
                f"{'From':<{COL_FROM}} {'Date':<5}{RESET}\r\n"
                f"  {FG['gry']}{'─' * 74}{RESET}\r\n")

        def render_row_msg(idx, row, selected):
            _, subj, who, _, when, _ = row
            ts = when.strftime('%m-%d') if when else '  ?  '
            s_col = FG['wht'] if not selected else ''
            return (f"  {FG['yel']}{idx+1:>3}{RESET}  "
                    f"{s_col}{(subj or '(no subject)')[:COL_SUBJ]:<{COL_SUBJ}}{RESET} "
                    f"{FG['grn']}{(who or '?')[:COL_FROM]:<{COL_FROM}}{RESET} "
                    f"{FG['cyan']}{ts:<5}{RESET}")

        def render_hint_msgs(sel, total):
            return (f"  {FG['cyan']}{sel+1}/{total}{RESET}  "
                    f"{FG['cyan']}Up/Dn PgUp/PgDn{RESET}=scroll  "
                    f"{FG['cyan']}Enter{RESET}=read  "
                    f"{FG['cyan']}Q{RESET}=back")

        last_sel = 0
        while True:
            result = await self._rss_lightbar(
                m_list, render_header_msgs, render_row_msg, render_hint_msgs,
                initial_sel=last_sel)

            if result[0] == 'quit':
                return
            elif result[0] == 'enter':
                last_sel = result[1]
                _, subj, frm, to, when, body = m_list[last_sel]
                ts = when.strftime('%Y-%m-%d %H:%M') if when else '?'
                # Strip SAUCE record: 0x1A marks end of ANSI art content.
                body = (body or '')
                _sa = body.find('\x1a')
                if _sa >= 0:
                    body = body[:_sa]
                # Fix QWK 0xE3 separators that may have split CSI sequences.
                body_fixed = re.sub(r'\x1b\n?\[[0-9;?\n]*[@-~]',
                                    lambda m: m.group(0).replace('\n', ''),
                                    body or '')
                from .anedit import launch_aneview, launch_anedit
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

    # ── RSS lightbar helper ──────────────────────────────────────────
    # Layout on a 24-row × 80-col terminal:
    #   Row 1     : blank (before banner)
    #   Rows 2-4  : banner (3 rows)
    #   Row 5     : column header
    #   Row 6     : separator
    #   Rows 7-21 : up to 15 list items (lightbar scrolls inside this window)
    #   Row 22    : separator
    #   Row 23    : status + key hints
    #   Row 24    : cursor (kept blank)
    _LB_START   = 7    # first item row
    _LB_VISIBLE = 15   # item rows
    _LB_SEP_ROW = 22
    _LB_HINT_ROW= 23

    async def _rss_lightbar(self, rows, render_header, render_row, render_hint,
                             initial_sel=0):
        """Generic arrow-key lightbar selector for RSS lists.

        render_header()          — async: writes banner + column-header lines
                                   (cursor ends at row _LB_START after call)
        render_row(idx, row, sel)— sync: returns a ≤78-char formatted string
        render_hint(sel, total)  — sync: returns the status+hints string
        Returns ('enter', idx) | ('key', char) | ('quit',)
        """
        from .ansi_ui import FG, RESET, BOLD, footer

        if not rows:
            return ('quit',)

        SEL   = '\x1b[7m\x1b[1m'   # reverse + bold
        NORM  = '\x1b[0m'
        EOL   = '\x1b[K'

        def _at(r):
            return f'\x1b[{r};1H'

        def _render(idx, row, selected):
            base = render_row(idx, row, selected)
            return (f'{SEL}{base}{NORM}{EOL}' if selected
                    else f'{NORM}{base}{EOL}')

        sel    = min(initial_sel, len(rows) - 1)
        scroll = max(0, sel - self._LB_VISIBLE + 1)

        async def _draw_full():
            await self.session.write('\x1b[2J\x1b[H')
            await render_header()
            # Items
            for i in range(self._LB_VISIBLE):
                idx = scroll + i
                row_txt = (_render(idx, rows[idx], idx == sel)
                           if idx < len(rows) else EOL)
                await self.session.write(f'{_at(self._LB_START + i)}{row_txt}')
            # Separator + hint
            await self.session.write(
                f'{_at(self._LB_SEP_ROW)}{FG["gry"]}{"─" * 76}{NORM}{EOL}')
            hint = render_hint(sel, len(rows))
            await self.session.write(f'{_at(self._LB_HINT_ROW)}{hint}{EOL}')
            # Hide cursor at a safe spot
            await self.session.write(f'\x1b[24;1H')

        async def _draw_row(idx):
            if scroll <= idx < scroll + self._LB_VISIBLE:
                row_num = self._LB_START + (idx - scroll)
                await self.session.write(
                    f'{_at(row_num)}{_render(idx, rows[idx], idx == sel)}')

        async def _refresh_hint():
            hint = render_hint(sel, len(rows))
            await self.session.write(
                f'{_at(self._LB_HINT_ROW)}{hint}{EOL}\x1b[24;1H')

        await _draw_full()

        while True:
            key = await self.session.read_key_arrow()

            if key == 'UP':
                if sel > 0:
                    old, sel = sel, sel - 1
                    if sel < scroll:
                        scroll = sel
                        await _draw_full()
                    else:
                        await _draw_row(old)
                        await _draw_row(sel)
                        await _refresh_hint()

            elif key == 'DOWN':
                if sel < len(rows) - 1:
                    old, sel = sel, sel + 1
                    if sel >= scroll + self._LB_VISIBLE:
                        scroll = sel - self._LB_VISIBLE + 1
                        await _draw_full()
                    else:
                        await _draw_row(old)
                        await _draw_row(sel)
                        await _refresh_hint()

            elif key == 'PGUP':
                _max_scroll = max(0, len(rows) - self._LB_VISIBLE)
                if scroll > 0:
                    scroll = max(0, scroll - self._LB_VISIBLE)
                    sel = scroll
                    await _draw_full()

            elif key == 'PGDN':
                _max_scroll = max(0, len(rows) - self._LB_VISIBLE)
                if scroll < _max_scroll:
                    scroll = min(_max_scroll, scroll + self._LB_VISIBLE)
                    sel = scroll
                    await _draw_full()

            elif key == 'HOME':
                if sel != 0:
                    sel, scroll = 0, 0
                    await _draw_full()

            elif key == 'END':
                if sel != len(rows) - 1:
                    sel = len(rows) - 1
                    scroll = max(0, len(rows) - self._LB_VISIBLE)
                    await _draw_full()

            elif key == 'ENTER':
                return ('enter', sel)

            elif key in ('Q', 'ESC', 'CTRL_C'):
                return ('quit',)

            else:
                return ('key', key)

    # ── RSS article pager ────────────────────────────────────────────
    async def _rss_pager(self, lines, header_lines, hint_str):
        """Scrollable text pager. header_lines are fixed; lines scrolls.

        header_lines : list of already-formatted strings for the fixed header
        lines        : list of body text strings (plain, pre-wrapped)
        hint_str     : key-hint footer string
        """
        from .ansi_ui import FG, RESET, BOLD, footer

        EOL      = '\x1b[K'
        NORM     = '\x1b[0m'
        hdr_rows = len(header_lines)          # rows consumed by header
        # how many terminal rows remain for body:
        # 24 total − 1 blank − hdr_rows − 1 sep − 1 hint = 21 − hdr_rows
        body_visible = max(4, 21 - hdr_rows)
        body_start   = 2 + hdr_rows           # row 2 = first content row
        body_end_row = body_start + body_visible - 1
        sep_row      = body_end_row + 1
        hint_row     = sep_row + 1

        offset = 0   # index of first visible body line

        async def _draw():
            await self.session.write('\x1b[2J\x1b[H')
            # Fixed header
            for r, hl in enumerate(header_lines, 2):
                await self.session.write(f'\x1b[{r};1H{hl}{EOL}')
            # Body lines
            for i in range(body_visible):
                li = offset + i
                txt = lines[li] if li < len(lines) else ''
                await self.session.write(f'\x1b[{body_start + i};1H  {txt}{EOL}')
            # Separator + hint
            pct = ''
            if lines:
                pct_val = min(100, int((offset + body_visible) * 100 / len(lines)))
                pct = f'{FG["gry"]} {pct_val}%{NORM}'
            await self.session.write(
                f'\x1b[{sep_row};1H{FG["gry"]}{"─" * 76}{NORM}{EOL}')
            await self.session.write(
                f'\x1b[{hint_row};1H{hint_str}{pct}{EOL}')
            await self.session.write(f'\x1b[24;1H')

        await _draw()

        while True:
            key = await self.session.read_key_arrow()
            max_offset = max(0, len(lines) - body_visible)

            if key == 'UP':
                if offset > 0:
                    offset -= 1
                    await _draw()
            elif key == 'DOWN':
                if offset < max_offset:
                    offset += 1
                    await _draw()
            elif key == 'PGUP':
                if offset > 0:
                    offset = max(0, offset - body_visible)
                    await _draw()
            elif key == 'PGDN':
                if offset < max_offset:
                    offset = min(max_offset, offset + body_visible)
                    await _draw()
            elif key == 'HOME':
                if offset != 0:
                    offset = 0
                    await _draw()
            elif key == 'END':
                if offset != max_offset:
                    offset = max_offset
                    await _draw()
            elif key in ('Q', 'ENTER', 'CTRL_C'):
                return

    # ── Sixel image rendering ────────────────────────────────────────
    async def _rss_detect_sixel(self):
        """Detect sixel support via DA1 primary device attributes. Cached.

        Sends ESC [ 0 c and reads the response, which looks like:
          ESC [ ? 65 ; 1 ; 2 ; 4 ; 9 c
        The flag 4 in the parameter list means sixel is supported.
        We also need img2sixel on PATH — if it's missing, report False."""
        if hasattr(self.session, '_sixel_ok'):
            return self.session._sixel_ok
        import asyncio as _aio
        import re as _re
        import shutil as _sh
        # Cheap fast-path: if the tool isn't installed, skip the DA1 round-trip.
        if not _sh.which('img2sixel'):
            self.session._sixel_ok = False
            return False
        # Send DA1 query and read until 'c' or timeout.
        await self.session.write('\x1b[0c')
        buf = b''
        try:
            # Allow up to 1.5 s — covers high-latency links
            deadline = _aio.get_event_loop().time() + 1.5
            while _aio.get_event_loop().time() < deadline:
                remaining = deadline - _aio.get_event_loop().time()
                ch = await _aio.wait_for(
                    self.session.reader.read(1), timeout=min(0.3, remaining))
                if not ch:
                    break
                buf += ch
                if ch == b'c':   # final byte of DA1 response
                    break
        except Exception:
            pass
        resp = buf.decode('latin-1', errors='replace')
        # Flag 4 = sixel: appears as ;4; or ;4c in the CSI response
        self.session._sixel_ok = bool(_re.search(r'[;?]4[;c]', resp))
        return self.session._sixel_ok

    async def _rss_render_sixel(self, image_url, width_px=280):
        """Download image_url and emit sixel to terminal. Returns True if OK."""
        import shutil, subprocess, tempfile, os, urllib.request as _ur
        if not shutil.which('img2sixel'):
            return False
        tmp = None
        try:
            ext = image_url.split('?')[0].rsplit('.', 1)[-1].lower()
            if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'):
                ext = 'jpg'
            fd, tmp = tempfile.mkstemp(suffix=f'.{ext}')
            os.close(fd)
            req = _ur.Request(image_url, headers={'User-Agent': 'ANetBBS/1.0'})
            with _ur.urlopen(req, timeout=6) as resp:
                with open(tmp, 'wb') as f:
                    f.write(resp.read(1024 * 512))
            result = subprocess.run(
                ['img2sixel', '-w', str(width_px), tmp],
                capture_output=True, timeout=10)
            if result.returncode == 0:
                await self.session.write(
                    result.stdout.decode('latin-1', errors='replace'))
                return True
        except Exception:
            pass
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
        return False

    # ── HTML → plain text for terminal ──────────────────────────────
    @staticmethod
    def _html_to_text(html):
        """Convert HTML to readable plain text (for terminal pager)."""
        from html.parser import HTMLParser
        from html import unescape as _ue
        import re as _re

        BLOCK = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                  'blockquote', 'li', 'tr', 'br', 'hr', 'pre', 'article',
                  'section', 'header', 'footer', 'figure', 'figcaption'}
        SKIP  = {'script', 'style', 'nav', 'head', 'noscript', 'svg',
                  'iframe', 'form', 'button', 'input'}

        class _P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.parts = []
                self._skip = 0
            def handle_starttag(self, tag, attrs):
                if tag in SKIP:
                    self._skip += 1
                elif not self._skip and tag in BLOCK:
                    self.parts.append('\n')
            def handle_endtag(self, tag):
                if tag in SKIP:
                    self._skip = max(0, self._skip - 1)
                elif not self._skip and tag in BLOCK:
                    self.parts.append('\n')
            def handle_data(self, data):
                if not self._skip:
                    self.parts.append(data)

        p = _P()
        p.feed(html)
        text = ''.join(p.parts)
        text = _re.sub(r'\n{3,}', '\n\n', text).strip()
        return text

    # ── RSS main screens ─────────────────────────────────────────────
    async def show_rss(self):
        """RSS feed list with arrow-key lightbar."""
        from anetbbs.models import RssFeed, RssItem, RssReadStatus, db
        from .ansi_ui import banner, footer, FG, RESET, BOLD
        from sqlalchemy import func

        uid        = (self.session.user or {}).get('id')
        user_level = int((self.session.user or {}).get('access_level', 10))
        is_admin   = bool((self.session.user or {}).get('is_admin'))

        # Ask once per session whether sixel is supported.
        # Skips auto-detection entirely — no DA1 round-trip needed.
        if not hasattr(self.session, '_sixel_ok'):
            import shutil as _sh
            await self.session.write(
                '\x1b[2J\x1b[H'
                f"\r\n  {FG['cyan']}ANetBBS RSS Reader{RESET}\r\n\r\n"
                f"  Does your terminal support sixel graphics? [{FG['wht']}Y{RESET}/{FG['wht']}N{RESET}] "
            )
            ans = await self.session.read_line()
            self.session._sixel_ok = (
                ans.strip().upper() == 'Y' and bool(_sh.which('img2sixel'))
            )

        last_sel = 0
        while True:
            with _app().app_context():
                rq = RssFeed.query.filter_by(is_active=True)
                if not is_admin:
                    rq = rq.filter(RssFeed.min_access_level <= user_level)
                feeds = rq.order_by(RssFeed.sort_order, RssFeed.name).all()

                # Aggregated unread counts — two queries instead of N+1
                total_by = dict(db.session.query(
                    RssItem.feed_id, func.count(RssItem.id)
                ).group_by(RssItem.feed_id).all())
                read_by = {}
                if uid:
                    read_by = dict(db.session.query(
                        RssItem.feed_id, func.count(RssReadStatus.id)
                    ).join(RssReadStatus, RssReadStatus.item_id == RssItem.id)
                     .filter(RssReadStatus.user_id == uid)
                     .group_by(RssItem.feed_id).all())

                feed_rows = []
                for f in feeds:
                    tot = total_by.get(f.id, 0)
                    unread = tot - read_by.get(f.id, 0)
                    feed_rows.append((f.id, f.name, f.category or 'general',
                                      tot, unread))

            if not feed_rows:
                await self.session.write('\x1b[2J\x1b[H')
                await self.session.write(banner('RSS Reader'))
                await self.session.write(
                    f"  {FG['gry']}No feeds configured. Add one at /admin/rss/.{RESET}\r\n")
                await self.session.read_line(f"  {FG['cyan']}Press Enter...{RESET}")
                return

            COL_W = 32  # feed name truncation

            async def render_header_rss():
                await self.session.write(banner('RSS Reader'))
                await self.session.write(
                    f"  {FG['cyan']}{BOLD}"
                    f"{'#':>2}  {'Feed':<{COL_W}}  {'Category':<10}  "
                    f"{'Items':>5}  {'Unread':>6}{RESET}\r\n"
                    f"  {FG['gry']}{'─' * 72}{RESET}\r\n")

            def render_row_rss(idx, row, selected):
                fid, name, cat, tot, unread = row
                u_str = (f"{FG['yel']}{BOLD}{unread:>6}{RESET}"
                         if unread else f"{FG['gry']}{'─':>6}{RESET}")
                n_col = FG['wht'] if not selected else ''
                return (f"  {FG['grn']}{idx+1:>2}{RESET}  "
                        f"{n_col}{name[:COL_W]:<{COL_W}}{RESET}  "
                        f"{FG['gry']}{cat[:10]:<10}{RESET}  "
                        f"{tot:>5}  {u_str}")

            def render_hint_rss(sel, total):
                row = feed_rows[sel]
                n = f"{FG['cyan']}{sel+1}/{total}{RESET} "
                return (f"  {n}"
                        f"{FG['cyan']}Up/Dn{RESET}=move  "
                        f"{FG['cyan']}Enter{RESET}=open  "
                        f"{FG['cyan']}A{RESET}=River  "
                        f"{FG['cyan']}M{RESET}=mark all read  "
                        f"{FG['cyan']}Q{RESET}=back")

            result = await self._rss_lightbar(
                feed_rows, render_header_rss, render_row_rss, render_hint_rss,
                initial_sel=last_sel)

            if result[0] == 'quit':
                return
            elif result[0] == 'enter':
                last_sel = result[1]
                fid, fname = feed_rows[last_sel][0], feed_rows[last_sel][1]
                await self._rss_feed_items(fid, fname)
            elif result[0] == 'key':
                k = result[1]
                if k == 'A':
                    await self._rss_river()
                elif k == 'M':
                    with _app().app_context():
                        unread_ids = [
                            r[0] for r in
                            db.session.query(RssItem.id)
                            .outerjoin(RssReadStatus,
                                       (RssReadStatus.item_id == RssItem.id) &
                                       (RssReadStatus.user_id == uid))
                            .filter(RssReadStatus.id.is_(None)).all()
                        ] if uid else []
                        for iid in unread_ids:
                            db.session.add(
                                RssReadStatus(user_id=uid, item_id=iid))
                        if unread_ids:
                            db.session.commit()
                # loop to refresh counts

    async def _rss_feed_items(self, feed_id, feed_name):
        """Item list for one feed — arrow-key lightbar."""
        from anetbbs.models import RssItem, RssReadStatus, db
        from .ansi_ui import banner, FG, RESET, BOLD

        uid = (self.session.user or {}).get('id')

        last_sel = 0
        while True:
            with _app().app_context():
                q = (RssItem.query.filter_by(feed_id=feed_id)
                     .order_by(RssItem.published_at.desc().nullslast()))
                all_items = q.limit(300).all()
                if not all_items:
                    await self.session.write('\x1b[2J\x1b[H')
                    await self.session.write(banner(feed_name[:36]))
                    await self.session.write(
                        f"  {FG['gry']}No items yet. Poller runs every ~30 min.{RESET}\r\n")
                    await self.session.read_line(f"  {FG['cyan']}Press Enter...{RESET}")
                    return
                read_ids = set()
                if uid and all_items:
                    iids = [i.id for i in all_items]
                    read_ids = set(
                        r[0] for r in db.session.query(RssReadStatus.item_id)
                        .filter(RssReadStatus.user_id == uid,
                                RssReadStatus.item_id.in_(iids)).all())
                rows = [(i.id, i.title or '(no title)',
                         i.published_at, i.id not in read_ids)
                        for i in all_items]

            async def render_header_items():
                await self.session.write(banner(feed_name[:36]))
                await self.session.write(
                    f"  {FG['cyan']}{BOLD}"
                    f"{'':>4}{'Date':>5}  {'Title':<62}{RESET}\r\n"
                    f"  {FG['gry']}{'─' * 74}{RESET}\r\n")

            def render_row_items(idx, row, selected):
                iid, title, ts, is_unread = row
                ts_s  = ts.strftime('%m-%d') if ts else '  ?  '
                mark  = f"{FG['yel']}*{RESET}" if is_unread else ' '
                tcol  = FG['wht'] if is_unread else FG['gry']
                n_col = f"{FG['grn']}{idx+1:>3}{RESET}"
                t_safe = BBSMenuUI._sanitize_cp437(title)
                return (f"  {n_col} {mark}{ts_s}  "
                        f"{tcol}{t_safe[:62]:<62}{RESET}")

            def render_hint_items(sel, total):
                _, _, _, is_unread = rows[sel]
                n = f"{FG['cyan']}{sel+1}/{total}{RESET} "
                new_lbl = (f" {FG['yel']}[NEW]{RESET}" if is_unread else '')
                return (f"  {n}"
                        f"{FG['cyan']}Up/Dn{RESET}=move  "
                        f"{FG['cyan']}Enter{RESET}=read{new_lbl}  "
                        f"{FG['cyan']}M{RESET}=mark feed read  "
                        f"{FG['cyan']}Q{RESET}=back")

            result = await self._rss_lightbar(
                rows, render_header_items, render_row_items, render_hint_items,
                initial_sel=last_sel)

            if result[0] == 'quit':
                return
            elif result[0] == 'enter':
                last_sel = result[1]
                await self._rss_view_item(rows[last_sel][0])
                # Refresh read state after returning
                with _app().app_context():
                    if uid:
                        iids = [r[0] for r in rows]
                        read_ids = set(
                            r[0] for r in db.session.query(RssReadStatus.item_id)
                            .filter(RssReadStatus.user_id == uid,
                                    RssReadStatus.item_id.in_(iids)).all())
                        rows = [(iid, t, ts, iid not in read_ids)
                                for iid, t, ts, _ in rows]
            elif result[0] == 'key' and result[1] == 'M':
                with _app().app_context():
                    if uid:
                        unread_ids = [r[0] for r in rows if r[3]]
                        for iid in unread_ids:
                            try:
                                db.session.add(
                                    RssReadStatus(user_id=uid, item_id=iid))
                            except Exception:
                                pass
                        if unread_ids:
                            db.session.commit()
                rows = [(iid, t, ts, False) for iid, t, ts, _ in rows]

    async def _rss_view_item(self, item_id):
        """Display a single article in a scrollable pager. Marks as read."""
        from anetbbs.models import db, RssItem, RssReadStatus
        from .ansi_ui import banner, FG, RESET, BOLD

        uid = (self.session.user or {}).get('id')

        with _app().app_context():
            app = _app()
            item = RssItem.query.get(item_id)
            if not item:
                return
            title     = self._sanitize_cp437(item.title or '(no title)')
            link      = item.link or ''
            author    = self._sanitize_cp437(item.author or '')
            pub_str   = (item.published_at.strftime('%Y-%m-%d %H:%M UTC')
                         if item.published_at else '')
            image_url = item.image_url or ''
            feed_name = self._sanitize_cp437(item.feed.name)
            domain    = app.config.get('BBS_DOMAIN', '')
            port      = app.config.get('WEB_PORT', 5000)
            web_base  = (f"https://{domain}" if domain
                         else f"http://localhost:{port}")
            short_url = f"{web_base}/r/{item.id}" if link else ''

            # Build body text from HTML (preferred) or plain summary
            if item.content_html:
                raw_body = self._sanitize_cp437(self._html_to_text(item.content_html))
            elif item.summary:
                raw_body = self._sanitize_cp437(item.summary)
            else:
                raw_body = '(no content — open the link to read on the source site)'

            # Mark as read
            if uid and not RssReadStatus.query.filter_by(
                    user_id=uid, item_id=item.id).first():
                try:
                    db.session.add(RssReadStatus(user_id=uid, item_id=item.id))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

        # Detect sixel support once per session
        sixel_ok = await self._rss_detect_sixel() if image_url else False

        # Build fixed header lines for the pager
        hdr = []
        hdr.append(f"\x1b[2J\x1b[H")  # written by pager via draw(), not here
        # We build them as plain strings; _rss_pager starts at row 2.
        hdr_lines = []
        hdr_lines.append(
            f"  {FG['gry']}{feed_name[:40]}{RESET}"
            f"{'  ' + pub_str if pub_str else ''}")
        for tl in self._wrap_text(title, 72):
            hdr_lines.append(f"  {FG['cyan']}{BOLD}{tl}{RESET}")
        if author:
            hdr_lines.append(f"  {FG['gry']}by {author}{RESET}")
        if short_url:
            hdr_lines.append(f"  {FG['gry']}web:{RESET} {short_url}")
        hdr_lines.append(f"  {FG['gry']}{'─' * 72}{RESET}")

        # If sixel capable, show image in its own "pane" first
        if sixel_ok and image_url:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner(feed_name[:36]))
            for hl in hdr_lines[:-1]:   # all but the separator
                await self.session.write(hl + '\r\n')
            await self.session.write(f"  {FG['gry']}(loading image…){RESET}\r\n")
            shown = await self._rss_render_sixel(image_url, width_px=300)
            if not shown:
                await self.session.write(f"\r\n  {FG['gry']}(image unavailable){RESET}\r\n")
            await self.session.read_line(
                f"\r\n  {FG['cyan']}Press Enter for article…{RESET}")

        # Word-wrap body into lines
        body_lines = []
        for para in raw_body.split('\n'):
            if not para.strip():
                body_lines.append('')
            else:
                body_lines.extend(self._wrap_text(para, 74))

        hint_str = (f"  {FG['cyan']}Up/Dn PgUp/PgDn{RESET}=scroll  "
                    f"{FG['cyan']}Home/End{RESET}=jump  "
                    f"{FG['cyan']}Q/Enter{RESET}=back")

        # Build the pager header (banner + article header)
        async def _show_pager():
            # The pager renders a banner itself as its first header rows.
            # We inline the banner into hdr_lines by prepending it.
            from .ansi_ui import banner as _bn, BG
            full_hdr = []
            # banner outputs rows 2-4; row 1 is blank (before banner \r\n)
            # We pass the banner rows as the first header lines.
            full_hdr.append(f"  {FG['gry']}{feed_name[:40]}{RESET}"
                             f"{'  ' + pub_str if pub_str else ''}")
            for tl in self._wrap_text(title, 72):
                full_hdr.append(f"  {FG['cyan']}{BOLD}{tl}{RESET}")
            if author:
                full_hdr.append(f"  {FG['gry']}by {author}{RESET}")
            if short_url:
                full_hdr.append(f"  {FG['gry']}web:{RESET} {short_url}")
            full_hdr.append(f"  {FG['gry']}{'─' * 72}{RESET}")
            await self._rss_pager(body_lines, full_hdr, hint_str)

        await _show_pager()

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

    @staticmethod
    def _sanitize_cp437(text):
        """Replace common Unicode chars with CP437-safe equivalents."""
        _sub = {
            '–': '-', '—': '-', '―': '-',
            '‘': "'", '’': "'", '‚': "'",
            '“': '"', '”': '"', '„': '"',
            '…': '...',
            '•': '*', '‣': '>', '⁃': '-',
            ' ': ' ', '­': '', '​': '', '‌': '',
            '·': '.', '∙': '.',
            '©': '(c)', '®': '(R)', '™': '(tm)',
            '×': 'x', '÷': '/',
            '←': '<-', '→': '->', '↑': '^', '↓': 'v',
            '≤': '<=', '≥': '>=', '≠': '!=',
            '«': '<<', '»': '>>',
        }
        out = []
        for ch in text:
            repl = _sub.get(ch, ch)
            for c in repl:
                try:
                    c.encode('cp437')
                    out.append(c)
                except (UnicodeEncodeError, LookupError):
                    out.append('?')
        return ''.join(out)

    async def _rss_river(self):
        """Combined all-feeds river — arrow-key lightbar."""
        from anetbbs.models import RssItem, RssFeed, RssReadStatus, db
        from .ansi_ui import banner, FG, RESET, BOLD

        uid        = (self.session.user or {}).get('id')
        user_level = int((self.session.user or {}).get('access_level', 10))
        is_admin   = bool((self.session.user or {}).get('is_admin'))

        last_sel = 0
        while True:
            with _app().app_context():
                q = (RssItem.query.join(RssFeed)
                     .filter(RssFeed.is_active.is_(True))
                     .filter(RssFeed.min_access_level <= user_level
                             if not is_admin else True)
                     .order_by(RssItem.published_at.desc().nullslast()))
                all_items = q.limit(300).all()
                if not all_items:
                    await self.session.write('\x1b[2J\x1b[H')
                    await self.session.write(banner('RSS River'))
                    await self.session.write(
                        f"  {FG['gry']}No items across any feed yet.{RESET}\r\n")
                    await self.session.read_line(f"  {FG['cyan']}Press Enter...{RESET}")
                    return
                read_ids = set()
                if uid and all_items:
                    iids = [i.id for i in all_items]
                    read_ids = set(
                        r[0] for r in db.session.query(RssReadStatus.item_id)
                        .filter(RssReadStatus.user_id == uid,
                                RssReadStatus.item_id.in_(iids)).all())
                rows = [(i.id, i.title or '(no title)',
                         i.feed.name, i.published_at,
                         i.id not in read_ids)
                        for i in all_items]

            async def render_header_river():
                await self.session.write(banner('RSS River — All Feeds'))
                await self.session.write(
                    f"  {FG['cyan']}{BOLD}"
                    f"{'':>4}{'Date':>5}  {'Feed':<14}  {'Title':<46}{RESET}\r\n"
                    f"  {FG['gry']}{'─' * 74}{RESET}\r\n")

            def render_row_river(idx, row, selected):
                iid, title, feed_name, ts, is_unread = row
                ts_s  = ts.strftime('%m-%d') if ts else '  ?  '
                mark  = f"{FG['yel']}*{RESET}" if is_unread else ' '
                tcol  = FG['wht'] if is_unread else FG['gry']
                n_col = f"{FG['grn']}{idx+1:>3}{RESET}"
                t_safe = BBSMenuUI._sanitize_cp437(title)
                return (f"  {n_col} {mark}{ts_s}  "
                        f"{FG['cyan']}{feed_name[:14]:<14}{RESET}  "
                        f"{tcol}{t_safe[:46]:<46}{RESET}")

            def render_hint_river(sel, total):
                _, _, _, _, is_unread = rows[sel]
                n = f"{FG['cyan']}{sel+1}/{total}{RESET} "
                new_lbl = (f" {FG['yel']}[NEW]{RESET}" if is_unread else '')
                return (f"  {n}"
                        f"{FG['cyan']}Up/Dn{RESET}=move  "
                        f"{FG['cyan']}Enter{RESET}=read{new_lbl}  "
                        f"{FG['cyan']}M{RESET}=mark all read  "
                        f"{FG['cyan']}Q{RESET}=back")

            result = await self._rss_lightbar(
                rows, render_header_river, render_row_river, render_hint_river,
                initial_sel=last_sel)

            if result[0] == 'quit':
                return
            elif result[0] == 'enter':
                last_sel = result[1]
                await self._rss_view_item(rows[last_sel][0])
                # Refresh read state
                with _app().app_context():
                    if uid:
                        iids = [r[0] for r in rows]
                        read_ids = set(
                            r[0] for r in db.session.query(RssReadStatus.item_id)
                            .filter(RssReadStatus.user_id == uid,
                                    RssReadStatus.item_id.in_(iids)).all())
                        rows = [(iid, t, fn, ts, iid not in read_ids)
                                for iid, t, fn, ts, _ in rows]
            elif result[0] == 'key' and result[1] == 'M':
                with _app().app_context():
                    if uid:
                        unread_ids = [r[0] for r in rows if r[4]]
                        for iid in unread_ids:
                            try:
                                db.session.add(
                                    RssReadStatus(user_id=uid, item_id=iid))
                            except Exception:
                                pass
                        if unread_ids:
                            db.session.commit()
                rows = [(iid, t, fn, ts, False) for iid, t, fn, ts, _ in rows]

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
    """Compose an echomail — network-first, then area, then message."""
    from anetbbs.models import db, EchoArea, EchomailMessage, EchomailNetwork
    from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD
    from collections import OrderedDict

    user_level = int((self.session.user or {}).get('access_level', 10))
    is_admin   = bool((self.session.user or {}).get('is_admin'))
    username   = (self.session.user or {}).get('username', 'guest')

    # ── Step 1: choose network ────────────────────────────────────────
    with _app().app_context():
        nets = EchomailNetwork.query.filter_by(is_active=True)\
            .order_by(EchomailNetwork.name).all()
        net_rows = []
        for net in nets:
            q = EchoArea.query.filter_by(
                network_id=net.id, is_active=True, is_subscribed=True)
            if not is_admin:
                q = q.filter(EchoArea.is_sysop_only == False,
                             EchoArea.min_access_level <= user_level)
            cnt = q.count()
            if cnt:
                net_rows.append((net.id, net.name, net.network_type, cnt))

    if not net_rows:
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner('Compose Echomail'))
        await self.session.write(
            f"  {FG['gry']}No echomail areas configured.{RESET}\r\n")
        await self.session.write('\r\n' + footer() + '\r\n')
        await self.session.read_line(f"  {FG['cyan']}Press Enter...{RESET}")
        return

    selected_area = None
    while selected_area is None:
        # Network chooser
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner('Compose Echomail — Choose Network'))
        await self.session.write(
            f"  {FG['cyan']}{BOLD}{'#':>2}  {'Network':<30} {'Type':<6} {'Areas':>5}{RESET}\r\n"
            f"  {FG['gry']}{'─' * 50}{RESET}\r\n")
        for i, (_, name, ntype, cnt) in enumerate(net_rows, 1):
            type_col = FG['cyan'] if ntype == 'binkp' else FG['yel']
            await self.session.write(
                f"  {FG['yel']}{BOLD}{i:2d}{RESET}  "
                f"{FG['wht']}{name[:30]:<30}{RESET}  "
                f"{type_col}{ntype[:5]:<5}{RESET}  "
                f"{FG['grn']}{cnt:5d}{RESET}\r\n")
        await self.session.write('\r\n' + footer() + '\r\n')
        net_choice = (await self.session.read_line(
            _prompt('Choose network (number / Q): ')) or '').strip().upper()
        if net_choice == 'Q' or not net_choice:
            return
        try:
            nidx = int(net_choice) - 1
            if not (0 <= nidx < len(net_rows)):
                continue
        except ValueError:
            continue

        net_id, net_name, _, _ = net_rows[nidx]

        # ── Step 2: choose area within network ────────────────────────
        with _app().app_context():
            q = EchoArea.query.filter_by(
                network_id=net_id, is_active=True, is_subscribed=True)
            if not is_admin:
                q = q.filter(EchoArea.is_sysop_only == False,
                             EchoArea.min_access_level <= user_level)
            areas = q.order_by(EchoArea.category, EchoArea.order,
                               EchoArea.name).all()
            # Build grouped list
            a_list   = []
            cat_rows = []
            cur_cat  = None
            for area in areas:
                cat = area.category or 'General'
                if cat != cur_cat:
                    cat_rows.append((len(a_list), cat))
                    cur_cat = cat
                a_list.append((area.id, area.tag, area.name,
                               area.network_id,
                               area.network.our_address or '1:1/1'))

        while True:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner(f'Compose — {net_name}'))

            cat_idx, next_cat_at, next_cat_lbl = 0, (cat_rows[0][0] if cat_rows else len(a_list)), (cat_rows[0][1] if cat_rows else '')
            row_count = 0
            PAGE = 17
            for i, (_, tag, name, _, _) in enumerate(a_list):
                if i == next_cat_at:
                    await self.session.write(
                        f"  {FG['grn']}{BOLD}── {next_cat_lbl} ──{RESET}\r\n")
                    cat_idx += 1
                    if cat_idx < len(cat_rows):
                        next_cat_at  = cat_rows[cat_idx][0]
                        next_cat_lbl = cat_rows[cat_idx][1]
                    row_count += 1
                await self.session.write(
                    f"  {FG['yel']}{BOLD}{i+1:2d}{RESET}  "
                    f"{FG['cyan']}{tag[:18]:<18}{RESET} "
                    f"{FG['wht']}{name[:28]:<28}{RESET}\r\n")
                row_count += 1
                if row_count % PAGE == 0 and (i + 1) < len(a_list):
                    ans = (await self.session.read_line(
                        f"  {FG['cyan']}-- more (Enter / Q) --{RESET}"
                    ) or '').strip().upper()
                    if ans == 'Q':
                        break

            await self.session.write('\r\n' + footer() + '\r\n')
            pick = (await self.session.read_line(
                _prompt('Pick area (number / B=back / Q): ')
            ) or '').strip().upper()
            if pick == 'Q':
                return
            if pick == 'B' or not pick:
                break   # back to network chooser
            try:
                aidx = int(pick) - 1
                if 0 <= aidx < len(a_list):
                    selected_area = a_list[aidx]
                    break
            except ValueError:
                pass

    if selected_area is None:
        return

    area_id, area_tag, area_name, network_id, our_addr = selected_area

    # ── Step 3: compose the message ──────────────────────────────────
    await self.session.write('\x1b[2J\x1b[H')
    await self.session.write(banner(f'Compose — {area_tag}'))
    await self.session.write(
        f"  {FG['gry']}Area: {FG['wht']}{area_name}{RESET}\r\n\r\n")

    to_name = (await self.session.read_line(
        f"  {FG['cyan']}To{RESET} (e.g. All): ") or 'All').strip()
    subject = (await self.session.read_line(
        f"  {FG['cyan']}Subject{RESET}: ") or '').strip()
    if not subject:
        await self.session.write(
            f"  {FG['gry']}No subject — cancelled.{RESET}\r\n")
        await self.session.read_line(f"\r\n{FG['cyan']}Press Enter...{RESET}")
        return

    from .anedit import launch_anedit
    body = await launch_anedit(self.session, subject=subject, username=username)
    if body is None:
        await self.session.write(f"\r\n  {FG['gry']}Aborted.{RESET}\r\n")
        await self.session.read_line(f"\r\n{FG['cyan']}Press Enter...{RESET}")
        return

    with _app().app_context():
        em = EchomailMessage(
            area_id=area_id,
            network_id=network_id,
            from_name=username[:100],
            from_address=our_addr,
            to_name=to_name[:100],
            subject=subject[:200],
            body=body,
            direction='outbound',
        )
        db.session.add(em)
        db.session.commit()
        saved_id = em.id

    await self.session.write(
        f"\r\n  {FG['grn']}{BOLD}Message queued (#{saved_id}).{RESET}"
        f"  {FG['gry']}Goes out on next poll.{RESET}\r\n")
    await self.session.read_line(f"\r\n{FG['cyan']}Press Enter...{RESET}")

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
            "║  R. RSS News Reader                      ║\r\n"
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
        elif choice == 'R':
            await self.show_rss()
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
