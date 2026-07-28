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
from ..core.tz import fmt_eastern

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
            try:
                from .notify import check_new_notifications
                await check_new_notifications(self.session)
            except Exception:
                pass
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

        from .ansi_ui import banner, footer, FG, RESET, BOLD, ui_width
        _w = ui_width(self.session)
        # Fixed columns: indent(2)+user(16)+sp(1)+proto(8)+sp(1)+last(12)+margins(6) = 46
        _where_w = max(22, _w - 46)
        _sep_w   = max(60, _w - 4)
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner("Who's Online", _w))
        if not rows:
            await self.session.write(
                f"  {FG['gry']}(no one is online right now){RESET}\r\n")
        else:
            await self.session.write(
                f"  {FG['cyan']}{BOLD}{'User':<16} {'Proto':<8} "
                f"{'Where':<{_where_w}} {'Last seen':<12}{RESET}\r\n"
                f"  {FG['gry']}{'─' * _sep_w}{RESET}\r\n")
            for u, proto, where, ts in rows:
                tstr = fmt_eastern(ts, '%H:%M:%S', '?')
                proto_color = (FG['grn'] if proto == 'telnet'
                               else FG['mag'] if proto == 'ssh'
                               else FG['yel'] if proto == 'web'
                               else FG['cyan'])
                await self.session.write(
                    f"  {FG['wht']}{u[:16]:<16}{RESET} "
                    f"{proto_color}{proto[:8]:<8}{RESET} "
                    f"{FG['dim']}{where[:_where_w]:<{_where_w}}{RESET} "
                    f"{FG['cyan']}{tstr:<12}{RESET}\r\n")
        await self.session.write('\r\n' + footer(_w) + '\r\n')
        await self.session.read_line(
            f"{FG['cyan']}Press Enter to continue...{RESET}")

    # ------------------------------------------------------------------
    # Message boards
    # ------------------------------------------------------------------

    async def list_boards(self):
        from anetbbs.models import Board
        from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD, ui_width
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
            _w = ui_width(self.session)
            # indent(2)+num(3)+sp(1) = 6 prefix; threads col = 16; spacing = 4
            _name_w = max(28, _w - 6 - 16 - 4)
            _desc_w = max(62, _w - 8)
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Message Boards', _w))
            for i, (_, name, desc, count) in enumerate(board_list, 1):
                await self.session.write(
                    f"  {FG['yel']}{BOLD}{i:2d}{RESET}{FG['gry']}.{RESET} "
                    f"{FG['grn']}{name:<{_name_w}}{RESET} "
                    f"{FG['cyan']}({count:4d} threads){RESET}\r\n")
                if desc:
                    await self.session.write(
                        f"      {FG['dim']}{desc[:_desc_w]}{RESET}\r\n")
            await self.session.write(
                f"\r\n  {FG['yel']}{BOLD}Q{RESET}{FG['gry']}.{RESET} "
                f"{FG['red']}Return{RESET}\r\n")
            await self.session.write(footer(_w) + '\r\n')
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

    # list_threads/read_thread used to live here as class-body methods,
    # but both are dead code: `BBSMenuUI.list_threads = _list_threads_v2`
    # (bottom of this file) shadows the class-body list_threads at every
    # call site, so read_thread (only ever called from the shadowed
    # method) was never reachable either. The real, reachable
    # implementations are _list_threads_v2/_read_thread_v2 further down.
    # See feedback_bbs_ui_monkeypatch memory -- this is that exact trap.

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

        from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD, ui_width
        while True:
            _w = ui_width(self.session)
            _title_w = max(40, _w - 34)
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Bulletins', _w))
            for i, (_, title, who, when, pinned, _) in enumerate(b_list, 1):
                ts = fmt_eastern(when, '%m-%d', '?')
                pin = (FG['yel'] + '*' + RESET) if pinned else ' '
                await self.session.write(
                    f"  {FG['yel']}{BOLD}{i:2d}{RESET}{FG['gry']}.{RESET}{pin} "
                    f"{FG['wht']}{title[:_title_w]:<{_title_w}}{RESET} "
                    f"{FG['gry']}by{RESET} {FG['grn']}{who[:12]:<12}{RESET} "
                    f"{FG['cyan']}{ts}{RESET}\r\n")
            await self.session.write('\r\n' + footer(_w) + '\r\n')
            choice = (await self.session.read_line(
                _prompt('Pick bulletin (number / Q): ')) or '').strip()
            if choice.upper() == 'Q' or not choice:
                return
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(b_list):
                    _, title, who, when, _, content = b_list[idx]
                    ts = fmt_eastern(when, '%Y-%m-%d %H:%M', '?')
                    await self._view_bulletin(title, who, ts, content or '')
            except ValueError:
                pass

    async def _view_bulletin(self, title, who, ts, content):
        """Scrollable ANView reader instead of the old page-break [MORE]
        pager -- matches how echo/private messages are already read.

        Bulletins are authored via the web admin's plain-textarea form
        (anetbbs/web/admin.py's BulletinForm), NOT typed at a terminal
        -- unlike echo/private messages, which launch_aneview() assumes
        are stored as CP437-byte-as-latin1 mojibake (the real wire
        convention for FidoNet/terminal-composed text). Round-tripping
        plain Unicode bulletin text through that CP437 decode would
        silently corrupt any non-ASCII character (curly quotes, em
        dashes, accented letters) into an unrelated CP437 glyph, so
        this builds ANView lines directly with the same word-wrap +
        raw-ANSI-passthrough logic _page_text used, skipping the
        CP437/pipe-code pipeline entirely. Raw ANSI escapes in a
        bulletin (sysop-authored colored announcements) still render.
        """
        from .anedit import ANView
        col_w = max(40, (getattr(self.session, 'window_size', (80, 24))[0] or 80) - 2)
        lines = [
            f'\x1b[36mBy:  \x1b[0m{who or "?"}',
            f'\x1b[36mDate:\x1b[0m{ts}',
            '\x1b[36m' + '─' * col_w + '\x1b[0m',
            '',
        ]
        raw_lines = content.splitlines() or ['(empty)']
        for _ln in raw_lines:
            if '\x1b' in _ln:
                lines.append(_ln)
            else:
                lines.extend(self._wrap_text(_ln, col_w) or [''])
        viewer = ANView(self.session, lines, subject=title or '(no subject)')
        await viewer.run()

    async def _page_text(self, body, title='', subtitle='', page_size=22):
        """Page through `body` one screenful at a time with a [MORE] prompt.

        Honors raw ANSI escape codes (so an ANSI bulletin renders) but
        breaks on every 22 visible-text lines so users can read at their
        own pace. After the last page, waits for a single keypress.
        """
        # Base 30-37 -- BOLD is already combined alongside every use of
        # these below, so bare aixterm 90-97 (unrecognized by MagiTerm/
        # NetRunner/PuTTY) isn't needed here.
        CYAN = '\x1b[36m'; WHT = '\x1b[37m'
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
        await self._page_lines(lines, page_size=page_size)

    async def _page_lines(self, lines, page_size=22, end_message='--- end ---'):
        """Write a pre-built list of terminal lines, pausing with a
        [MORE] prompt every `page_size` lines. Shared by any reader that
        needs to paginate wrapped text (bulletins, threads, PMs, IMs).
        """
        # Bold+base combined (1;33) -- no separate BOLD var in this
        # function, and bare aixterm 93 isn't recognized by MagiTerm/
        # NetRunner/PuTTY.
        YEL = '\x1b[1;33m'; DIM = '\x1b[37m'; RESET = '\x1b[0m'
        total = len(lines)
        if not total:
            return
        page = 0
        while page * page_size < total:
            chunk = lines[page * page_size:(page + 1) * page_size]
            for line in chunk:
                await self.session.write(line + '\r\n')
            page += 1
            if page * page_size >= total:
                await self.session.read_line(
                    f'{YEL}{end_message}{RESET}  Press Enter to continue: ')
                return
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
            from .ansi_ui import banner, FG, RESET, ui_width
            _w = ui_width(self.session)
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('PM Inbox', _w))
            await self.session.write(
                f"  {FG['gry']}Your inbox is empty.{RESET}\r\n")
            await self.session.read_line("\r\nPress Enter...")
            return

        while True:
            from .ansi_ui import banner as _bnr, ui_width as _uw, FG, RESET
            _w = _uw(self.session)
            _subj_w = max(38, _w - 36)
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(_bnr('PM Inbox', _w))
            for i, (_, subj, who, when, was_read, _) in enumerate(i_list, 1):
                ts = fmt_eastern(when, '%m-%d %H:%M', '?')
                mark = ' ' if was_read else '*'
                await self.session.write(f"  {i:2d}.{mark} {subj[:_subj_w]:<{_subj_w}} from {who[:12]:<12} {ts}\r\n")
            choice = (await self.session.read_line("\r\nPick message (number) or Q: ") or '').strip()
            if choice.upper() == 'Q' or not choice:
                return
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(i_list):
                    pm_id, subj, who, when, _, body = i_list[idx]
                    ts = fmt_eastern(when, '%Y-%m-%d %H:%M', '?')
                    await self.session.write("\r\n" + "═" * _w + "\r\n")
                    await self.session.write(f"  Subject: {subj}\r\n  From: {who}\r\n  Date: {ts}\r\n")
                    await self.session.write("─" * _w + "\r\n")
                    await self._page_lines(
                        [self._linkify_url_line(l) for l in self._wrap_text(body or '', _w)])
                    # Mark as read
                    with _app().app_context():
                        pm = PrivateMessage.query.get(pm_id)
                        if pm and pm.read_at is None:
                            pm.read_at = datetime.utcnow()
                            db.session.commit()
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

        from .ansi_ui import banner as _bnr, FG as _F, RESET as _R, ui_width as _uw
        _w = _uw(self.session)
        _prev_w = max(30, _w - 36)
        if not rows:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(_bnr('Inter-BBS Instant Messages', _w))
            await self.session.write(
                f"  {_F['gry']}No InterBBS instant messages.{_R}\r\n")
            await self.session.read_line("\r\nPress Enter...")
            return

        while True:
            _w = _uw(self.session)
            _prev_w = max(30, _w - 36)
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(_bnr('Inter-BBS Instant Messages', _w))
            for i, (_, who, host, when, was_read, body) in enumerate(rows, 1):
                ts = fmt_eastern(when, '%m-%d %H:%M', '?')
                mark = ' ' if was_read else '*'
                preview = body.replace('\r', ' ').replace('\n', ' ')[:_prev_w]
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
                await self.session.write("\r\n" + "═" * _w + "\r\n")
                ts = fmt_eastern(when, '%Y-%m-%d %H:%M', '?')
                await self.session.write(f"  From: {who}\r\n  Host: {host}\r\n  Date: {ts}\r\n")
                await self.session.write("─" * _w + "\r\n")
                await self._page_lines(
                    [self._linkify_url_line(l) for l in self._wrap_text(body or '', _w)])
                with _app().app_context():
                    im = InstantMessage.query.get(mid)
                    if im and not im.is_read:
                        im.is_read = True
                        db.session.commit()

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

    async def _msp_pick_directory_bbs(self):
        """BBS directory lightbar picker. Returns the picked dict (with
        hostname/name/sysop/location/systat_port keys), or None on an
        empty directory or the user backing out -- both are meant to
        fall through to manual user@host entry, not dead-end."""
        from anetbbs.models import BbsDirectoryEntry
        from .ansi_ui import banner, FG, RESET, BOLD, ui_width

        with _app().app_context():
            entries = BbsDirectoryEntry.query.order_by(BbsDirectoryEntry.name).all()
            rows = [{'hostname': e.hostname, 'name': e.name or e.hostname,
                     'sysop': e.sysop or '', 'location': e.location or '',
                     'systat_port': e.systat_port or 11} for e in entries]
        if not rows:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Pick a BBS', ui_width(self.session)))
            await self.session.write(
                f"  {FG['gry']}No BBSes in the directory yet.{RESET}\r\n")
            await self.session.read_line(f"  {FG['cyan']}Press Enter...{RESET}")
            return None

        async def render_header():
            _w = ui_width(self.session)
            await self.session.write(banner('Pick a BBS', _w))
            await self.session.write(
                f"  {FG['cyan']}{BOLD}{'#':>2}  {'Name':<24}  {'Sysop':<18}  {'Location':<16}{RESET}\r\n"
                f"  {FG['gry']}{'─' * max(72, _w - 4)}{RESET}\r\n")

        def render_row(idx, row, selected):
            return (f"  {FG['grn']}{idx+1:>2}{RESET}  {row['name'][:24]:<24}  "
                    f"{FG['dim']}{row['sysop'][:18]:<18}{RESET}  {row['location'][:16]:<16}")

        def render_hint(sel, total):
            return (f"  {FG['cyan']}{sel+1}/{total}{RESET} "
                    f"{FG['cyan']}Up/Dn{RESET}=move  {FG['cyan']}Enter{RESET}=pick  "
                    f"{FG['cyan']}Q{RESET}=type user@host manually")

        result = await self._rss_lightbar(rows, render_header, render_row, render_hint)
        if result[0] == 'enter':
            return rows[result[1]]
        return None

    async def _msp_pick_online_user(self, bbs_row):
        """Live-SYSTAT-probe bbs_row and let the sysop/user pick a
        currently-online user there. Returns the picked username, or
        None to fall back to manual entry (probe failed/empty/timed
        out, or the user picked the manual-entry row, or backed out)."""
        from anetbbs.msp.systat import query_systat, parse_systat_response
        from .ansi_ui import banner, FG, RESET, BOLD, ui_width

        await self.session.write(
            f"\r\n{FG['cyan']}Probing {bbs_row['hostname']}...{RESET}\r\n")
        loop = asyncio.get_running_loop()
        # query_systat() is a blocking socket call (up to 5s) -- offload
        # it so it doesn't stall every other node in this process, same
        # idiom as games/builtin_runner.py's run_in_executor use.
        text = await loop.run_in_executor(
            None, query_systat, bbs_row['hostname'], bbs_row['systat_port'], 5.0)
        online = parse_systat_response(text) if text else []
        if not online:
            await self.session.write(
                f"{FG['dim']}No reply, or nobody online there -- "
                f"switching to manual entry.{RESET}\r\n")
            await self.session.read_line("Press Enter...")
            return None

        MANUAL = object()  # sentinel row for the injected manual-entry option
        display_rows = [MANUAL] + online

        async def render_header():
            _w = ui_width(self.session)
            await self.session.write(banner(f"Online at {bbs_row['hostname']}", _w))
            await self.session.write(
                f"  {FG['cyan']}{BOLD}{'User':<22}  {'Action':<24}{RESET}\r\n"
                f"  {FG['gry']}{'─' * max(72, _w - 4)}{RESET}\r\n")

        def render_row(idx, row, selected):
            if row is MANUAL:
                return f"  {FG['yel']}[Manual entry -- type user@host]{RESET}"
            return (f"  {FG['grn']}{row['user'][:22]:<22}{RESET}  "
                    f"{FG['dim']}{row['action'][:24]}{RESET}")

        def render_hint(sel, total):
            return (f"  {FG['cyan']}{sel+1}/{total}{RESET} "
                    f"{FG['cyan']}Up/Dn{RESET}=move  {FG['cyan']}Enter{RESET}=pick  "
                    f"{FG['cyan']}Q{RESET}=cancel")

        result = await self._rss_lightbar(display_rows, render_header, render_row, render_hint)
        if result[0] != 'enter':
            return None
        picked = display_rows[result[1]]
        return None if picked is MANUAL else picked['user']

    async def _msp_pick_recipient(self):
        """Directory -> live-probe -> online-user picker, in one flow.
        Returns (username, host) or None if the whole flow fell through
        (empty directory, failed/empty probe, or cancelled) -- callers
        must fall back to their own manual user@host prompt on None."""
        bbs_row = await self._msp_pick_directory_bbs()
        if not bbs_row:
            return None
        username = await self._msp_pick_online_user(bbs_row)
        if not username:
            return None
        return (username, bbs_row['hostname'])

    async def send_imsg(self):
        """Compose a fresh InterBBS IM from the terminal. Tries the BBS
        directory + live-online picker first; falls back to manual
        user@host entry if the directory is empty, the probe finds
        nobody, or the sysop/user cancels out of either picker."""
        from anetbbs.msp.client import send_msp
        from anetbbs.msp.protocol import MSP_DEFAULT_PORT
        from .ansi_ui import banner, FG, RESET, ui_width

        picked = await self._msp_pick_recipient()
        if picked:
            target_user, target_host = picked
            _w = ui_width(self.session)
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Send Inter-BBS Instant Message', _w))
            await self.session.write(
                f"  {FG['cyan']}To:{RESET} {target_user}@{target_host}\r\n")
        else:
            _w = ui_width(self.session)
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Send Inter-BBS Instant Message', _w))
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
        from anetbbs.models import EchoArea, EchomailNetwork
        from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD, ui_width

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

        # NOTE: deliberately do NOT early-return here when net_rows is
        # empty. A fresh install (or any sysop who hasn't yet been
        # granted/subscribed to a network) has zero active networks and
        # zero subscribed areas by design -- exactly the sysop this
        # screen's "Apply for ANotherNetwork QWK node" option exists
        # for. Returning early used to make that option unreachable for
        # precisely the audience who'd need it.
        while True:
            _w = ui_width(self.session)
            _net_w = max(30, _w - 22)
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Echomail Networks', _w))
            if net_rows:
                await self.session.write(
                    f"  {FG['cyan']}{BOLD}{'#':>2}  {'Network':<{_net_w}} "
                    f"{'Type':<6} {'Areas':>5}{RESET}\r\n"
                    f"  {FG['gry']}{'─' * max(50, _w - 4)}{RESET}\r\n")
                for i, (_, name, ntype, count) in enumerate(net_rows, 1):
                    type_col = FG['cyan'] if ntype == 'binkp' else FG['yel']
                    await self.session.write(
                        f"  {FG['yel']}{BOLD}{i:2d}{RESET}  "
                        f"{FG['wht']}{name[:_net_w]:<{_net_w}}{RESET}  "
                        f"{type_col}{ntype[:5]:<5}{RESET}  "
                        f"{FG['grn']}{count:5d}{RESET}\r\n")
            else:
                await self.session.write(
                    f"  {FG['gry']}No echomail areas configured yet -- apply "
                    f"for a network below to get started.{RESET}\r\n")
            await self.session.write(
                f"\r\n  {FG['gry']}A = Apply for ANotherNetwork QWK node{RESET}\r\n")
            await self.session.write('\r\n' + footer(_w) + '\r\n')
            choice = (await self.session.read_line(
                _prompt('Choose network (number / A=apply / Q): ')) or '').strip().upper()
            if choice == 'Q' or not choice:
                return
            if choice == 'A':
                await self._apply_qwk_node()
                continue
            if not net_rows:
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
        from .ansi_ui import banner, FG, RESET, BOLD, ui_width

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
        COL_CAT  = 10
        # Name column expands: fixed = indent(2)+num(5)+tag(17)+cat(11)+msgs(7) = 42
        COL_NAME = max(24, ui_width(self.session) - 42)

        async def render_header_areas():
            _w = ui_width(self.session)
            _cn = max(24, _w - 42)
            await self.session.write(banner(net_name, _w))
            await self.session.write(
                f"  {FG['cyan']}{BOLD}"
                f"{'#':>3}  {'Tag':<{COL_TAG}} {'Name':<{_cn}} "
                f"{'Category':<{COL_CAT}} {'Msgs':>5}{RESET}\r\n"
                f"  {FG['gry']}{'─' * max(72, _w - 4)}{RESET}\r\n")

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
        """Walk the sysop through a QWK node application wizard.

        On the designated hub install (REGISTRY_MODE_ENABLED=true) this
        writes directly to the local QWKNodeRequest table -- no network
        round trip needed, since this IS the hub. On every other
        install it POSTs to the real hub's /qwkhub/apply API instead of
        writing locally, which is what this used to do incorrectly: the
        request would land in whichever BBS happened to run the wizard,
        not the actual hub ("all the sysops try to put in for a node
        and it goes to their system"). The local QWKNodeRequest row
        created afterward here is just a cache of the hub's answer,
        refreshed from the hub's /qwkhub/status/<token> on each visit
        -- not the authoritative copy.
        """
        import re
        from anetbbs.models import db, QWKNode, QWKNodeRequest
        from .ansi_ui import banner, prompt as _prompt, FG, RESET, BOLD, ui_width

        user     = self.session.user or {}
        user_id  = user.get('id')
        username = user.get('username', 'Guest')

        with _app().app_context():
            from flask import current_app
            is_hub = bool(current_app.config.get('REGISTRY_MODE_ENABLED'))
            registry_url = (current_app.config.get('REGISTRY_URL') or '').rstrip('/')
            # Terminal wizard is default-hub-identity-only by design (see
            # HubIdentity) -- an install running more than one hub
            # identity still only offers ITS default identity's network
            # from this screen; extra identities are web-admin-only.
            # Previously hardcoded "bbs.a-net.fyi" / "ANET.qwk" regardless
            # of what REGISTRY_URL/QWK_HUB_ID this particular install
            # actually had configured.
            hub_hostname = (registry_url.split('//', 1)[-1] or 'the hub') if registry_url else 'the hub'
            if is_hub:
                from anetbbs.echomail.qwk_hub_ftp import resolve_hub_id
                poll_hint = f'download {resolve_hub_id(None)}.qwk  /  upload <YOURID>.rep'
            else:
                poll_hint = "download the hub's QWK packet  /  upload <YOURID>.rep"
            existing = None
            if user_id:
                existing = (QWKNodeRequest.query
                            .filter_by(applied_by_user_id=user_id)
                            .order_by(QWKNodeRequest.created_at.desc())
                            .first())
            existing_id = existing.id if existing else None
        if existing_id is not None:
            # Pass the id, not the ORM row -- the row above was loaded
            # inside the app_context that just closed, so its attributes
            # aren't safe to touch afterward (SQLAlchemy raises
            # DetachedInstanceError on the first expired-attribute access
            # once the session is gone). _show_qwk_request_status()
            # re-queries fresh inside its own app_context instead.
            await self._show_qwk_request_status(existing_id, is_hub=is_hub,
                                                 registry_url=registry_url)
            return

        if not is_hub and not registry_url:
            await self.session.write(
                f"\r\n  {FG['red']}This BBS hasn't configured a hub to apply "
                f"to (REGISTRY_URL is blank) -- contact your sysop.{RESET}\r\n")
            await self.session.read_line(_prompt('Press Enter to continue...'))
            return

        _w = ui_width(self.session)
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner('ANotherNetwork — QWK Node Application', _w))
        await self.session.write(
            f"\r\n"
            f"  {FG['wht']}ANotherNetwork (Zone 1200) connects BBS systems via QWK echomail.{RESET}\r\n"
            f"  {FG['wht']}Register as a QWK node to exchange messages with other systems.{RESET}\r\n"
            f"\r\n"
            f"  {FG['cyan']}Hub  : {hub_hostname}  (FTP port 21){RESET}\r\n"
            f"  {FG['cyan']}Poll : {poll_hint}{RESET}\r\n"
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
            if is_hub:
                # We ARE the hub, so the local tables are authoritative --
                # check here rather than making the sysop fill out the
                # whole form first. On a peer install the local tables
                # mean nothing (the hub's are authoritative); the hub's
                # own /qwkhub/apply does this same check server-side.
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

        if is_hub:
            # We ARE the hub -- write directly, no round trip needed.
            import secrets as _secrets
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
                    request_token=_secrets.token_urlsafe(32),
                )
                db.session.add(req)
                db.session.commit()
                from .notify import notify_admins
                notify_admins(
                    'qwk_node_app',
                    title=f'QWK node application: {bbs_name} ({packet_id})',
                    body=f'{bbs_name} applied for packet ID {packet_id}. '
                         f'Review under Admin -> Echomail -> Hub -> Node Requests.',
                    target_url='/admin/echomail/hub/qwk/requests')

            await self.session.write(
                f"\r\n  {FG['grn']}{BOLD}Application submitted!{RESET}\r\n"
                f"  {FG['wht']}Review it under Admin -> Echomail -> Hub -> "
                f"Node Requests.{RESET}\r\n\r\n"
            )
            await self.session.read_line(_prompt('Press Enter to continue...'))
            return

        # Peer install -- actually POST to the real hub instead of
        # writing to our own local database (the original bug).
        import requests
        try:
            resp = requests.post(
                f'{registry_url}/qwkhub/apply',
                json={
                    'bbs_name': bbs_name, 'packet_id': packet_id,
                    'sysop_name': sysop_name, 'email': email,
                    'bbs_address': bbs_addr, 'notes': notes,
                    'applied_by_username': username,
                },
                timeout=15)
            body = (resp.json()
                    if resp.headers.get('content-type', '').startswith('application/json')
                    else {})
        except requests.RequestException as exc:
            await self.session.write(
                f"\r\n  {FG['red']}Could not reach the hub ({registry_url}): "
                f"{exc}{RESET}\r\n  {FG['wht']}Try again later.{RESET}\r\n\r\n")
            await self.session.read_line(_prompt('Press Enter to continue...'))
            return

        if resp.status_code != 200 or not body.get('ok'):
            err = body.get('error') or f'HTTP {resp.status_code}'
            await self.session.write(
                f"\r\n  {FG['red']}Hub rejected the application: {err}{RESET}\r\n\r\n")
            await self.session.read_line(_prompt('Press Enter to continue...'))
            return

        # Cache locally so _show_qwk_request_status can look this up on
        # a later visit -- this row mirrors the hub's answer, it is NOT
        # the authoritative copy (that lives on the hub itself).
        with _app().app_context():
            req = QWKNodeRequest(
                bbs_name=bbs_name,
                packet_id=packet_id,
                sysop_name=sysop_name,
                email=email,
                bbs_address=bbs_addr,
                notes=notes,
                applied_via='terminal_remote',
                applied_by_user_id=user_id,
                applied_by_username=username,
                request_token=body['request_token'],
            )
            db.session.add(req)
            db.session.commit()

        await self.session.write(
            f"\r\n  {FG['grn']}{BOLD}Application submitted to {registry_url}!{RESET}\r\n"
            f"  {FG['wht']}The hub sysop will review your request.{RESET}\r\n"
            f"  {FG['wht']}Return to this menu to check your status.{RESET}\r\n\r\n"
        )
        await self.session.read_line(_prompt('Press Enter to continue...'))

    async def _show_qwk_request_status(self, req_id, is_hub=True, registry_url=''):
        """Show the applicant their application status (pending/approved/denied).

        On a peer install (is_hub=False), the local row is only a cache
        of the hub's last known answer -- refresh it from the hub's
        /qwkhub/status/<token> first, so approvals/denials show up here
        without the sysop needing to do anything else.

        Takes an id, not an ORM row: every DB read/write happens inside
        one app_context block below, with all needed fields copied out
        to plain local variables before that block closes. Passing a
        SQLAlchemy row across an app_context boundary and touching its
        attributes afterward raises DetachedInstanceError the moment an
        expired attribute is accessed with no session left to refresh
        it from -- live-caught exactly this way (v1.0b2.37).
        """
        from anetbbs.models import db, QWKNodeRequest as _QNR
        from .ansi_ui import banner, prompt as _prompt, FG, RESET, BOLD, ui_width

        hub_hostname = (registry_url.split('//', 1)[-1] or 'the hub') if registry_url else 'the hub'
        if is_hub:
            from anetbbs.echomail.qwk_hub_ftp import resolve_hub_id
            with _app().app_context():
                download_hint = f'{resolve_hub_id(None)}.qwk'
        else:
            download_hint = "the hub's QWK packet"

        stale_note = ''
        with _app().app_context():
            req = _QNR.query.get(req_id)
            if req is None:
                await self.session.write(
                    f"\r\n  {FG['red']}That application no longer exists.{RESET}\r\n")
                await self.session.read_line(_prompt('\r\nPress Enter to continue...'))
                return

            if not is_hub and registry_url and req.request_token:
                import requests
                try:
                    resp = requests.get(
                        f'{registry_url}/qwkhub/status/{req.request_token}',
                        timeout=10)
                    body = (resp.json()
                            if resp.headers.get('content-type', '').startswith('application/json')
                            else {})
                    if resp.status_code == 200 and body.get('ok'):
                        req.status = body.get('status', req.status)
                        req.generated_password = body.get(
                            'generated_password', req.generated_password)
                        req.deny_reason = body.get('deny_reason', req.deny_reason)
                        db.session.commit()
                    else:
                        stale_note = 'showing last known status (hub returned an error)'
                except requests.RequestException:
                    stale_note = 'showing last known status (hub unreachable right now)'

            status = req.status
            packet_id = req.packet_id
            bbs_name = req.bbs_name
            date_str = fmt_eastern(req.created_at, '%Y-%m-%d', '?')
            generated_password = req.generated_password
            deny_reason = req.deny_reason

            if status == 'approved' and not req.seen_by_applicant:
                req.seen_by_applicant = True
                db.session.commit()

        _w = ui_width(self.session)
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner('ANotherNetwork — Node Application Status', _w))
        if stale_note:
            await self.session.write(f"\r\n  {FG['gry']}({stale_note}){RESET}\r\n")

        if status == 'pending':
            await self.session.write(
                f"\r\n  {FG['yel']}{BOLD}Status : PENDING REVIEW{RESET}\r\n"
                f"\r\n"
                f"  {FG['wht']}Packet ID requested : {FG['grn']}{packet_id}{RESET}\r\n"
                f"  {FG['wht']}BBS Name            : {FG['wht']}{bbs_name}{RESET}\r\n"
                f"  {FG['wht']}Applied             : {FG['gry']}{date_str}{RESET}\r\n"
                f"\r\n"
                f"  {FG['gry']}Your application is under review. Check back soon.{RESET}\r\n"
            )

        elif status == 'approved':
            await self.session.write(
                f"\r\n  {FG['grn']}{BOLD}Status : APPROVED{RESET}\r\n"
                f"\r\n"
                f"  {FG['wht']}Configure your BBS with these credentials:{RESET}\r\n"
                f"\r\n"
                f"  {FG['cyan']}Packet ID : {FG['grn']}{BOLD}{packet_id}{RESET}\r\n"
                f"  {FG['cyan']}Password  : {FG['grn']}{BOLD}{generated_password or '(contact hub sysop)'}{RESET}\r\n"
                f"  {FG['cyan']}Hub FTP   : {FG['wht']}{hub_hostname}  port 21{RESET}\r\n"
                f"  {FG['cyan']}Download  : {FG['wht']}{download_hint}{RESET}\r\n"
                f"  {FG['cyan']}Upload    : {FG['wht']}{packet_id}.rep{RESET}\r\n"
                f"\r\n"
                f"  {FG['gry']}Set BOTH \"QWK Username\" and \"QWK Packet ID\" to "
                f"{packet_id} on your Echomail Network settings -- the FTP "
                f"login uses the Username field, not the Packet ID field.{RESET}\r\n"
            )

        elif status == 'denied':
            await self.session.write(
                f"\r\n  {FG['red']}{BOLD}Status : NOT APPROVED{RESET}\r\n"
                f"\r\n"
                f"  {FG['wht']}Your application for {FG['grn']}{packet_id}{FG['wht']} was not approved.{RESET}\r\n"
            )
            if deny_reason:
                await self.session.write(
                    f"  {FG['yel']}Reason : {FG['wht']}{deny_reason}{RESET}\r\n"
                )
            await self.session.write(
                f"\r\n  {FG['gry']}You may apply again with a different packet ID.{RESET}\r\n"
            )

        await self.session.read_line(_prompt('\r\nPress Enter to continue...'))

    async def read_echo_area(self, area_id, tag):
        from anetbbs.models import EchomailMessage, EchoArea
        from .ansi_ui import banner, FG, RESET, BOLD, ui_width

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
            await self.session.write(banner(tag, ui_width(self.session)))
            await self.session.write(
                f"  {FG['gry']}(no messages in this area yet){RESET}\r\n")
            await self.session.read_line(f"  {FG['cyan']}Press Enter...{RESET}")
            return

        COL_FROM = 14
        # Subject col expands: fixed = indent(2)+num(5)+from(15)+date(7) = 29
        COL_SUBJ = max(36, ui_width(self.session) - 29)

        async def render_header_msgs():
            _w = ui_width(self.session)
            _cs = max(36, _w - 29)
            await self.session.write(banner(tag, _w))
            await self.session.write(
                f"  {FG['cyan']}{BOLD}"
                f"{'#':>3}  {'Subject':<{_cs}} "
                f"{'From':<{COL_FROM}} {'Date':<5}{RESET}\r\n"
                f"  {FG['gry']}{'─' * max(74, _w - 4)}{RESET}\r\n")

        def render_row_msg(idx, row, selected):
            _, subj, who, _, when, _ = row
            ts = fmt_eastern(when, '%m-%d', '  ?  ')
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
                ts = fmt_eastern(when, '%Y-%m-%d %H:%M', '?')
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
                        await self.session.write(banner('Compose in ' + tag, ui_width(self.session)))
                        compose_to = (await self.session.read_line(
                            f"  {FG['cyan']}To:{RESET} ") or 'All').strip()
                        compose_subj = (await self.session.read_line(
                            f"  {FG['cyan']}Subject:{RESET} ") or '').strip()
                        if not compose_subj:
                            continue
                    username = self.session.user.get('username', 'guest')
                    body_out = await launch_anedit(
                        self.session, quote=quote,
                        subject=compose_subj, username=username,
                        tagline_picker=lambda: _maybe_prompt_tagline(self))
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

            # ---- Lightbar area list (matches message areas/RSS pattern --
            # a plain top-to-bottom dump used to overflow a page for any
            # sysop with more than a screenful of file areas, forcing
            # reliance on the terminal's own scrollback to see the top
            # entries) ----
            from .ansi_ui import ui_width as _ui_width, banner

            lb_rows = []
            if top_cnt or is_sysop:
                lb_rows.append({
                    'id': None, 'name': 'General / Top-level', 'tag': '',
                    'cnt': top_cnt, 'inactive': False, 'sysop_only': False,
                    'can_upload': True, 'storage_path': '', 'is_top': True,
                })
            lb_rows.extend(area_rows)

            if not lb_rows:
                await self.session.write('\x1b[2J\x1b[H')
                await self.session.write(banner('File Library - Areas',
                                                 _ui_width(self.session)))
                await self.session.write(
                    f"  {FG['gry']}(no file areas configured){RESET}\r\n")
                await self.session.read_line(
                    f"\r\n{FG['cyan']}Press Enter to go back...{RESET}")
                return

            _w      = _ui_width(self.session)
            _name_w = max(30, _w - 26)   # leave room for #, files count, flags

            async def render_header_files():
                await self.session.write(banner('File Library - Areas', _w))
                await self.session.write(
                    f"  {FG['cyan']}{BOLD}"
                    f"{'#':>3}  {'Name':<{_name_w}} {'Files':>7}{RESET}\r\n"
                    f"  {FG['gry']}{'─' * max(50, _w - 4)}{RESET}\r\n")

            def render_row_files(idx, row, selected):
                flags = ''
                if row.get('inactive'):
                    flags += f" {FG['red']}[inactive]{RESET}"
                if row.get('sysop_only'):
                    flags += f" {FG['mag']}[sysop]{RESET}"
                n_col = FG['wht'] if not selected else ''
                return (f"  {FG['yel']}{idx+1:>3}{RESET}  "
                        f"{n_col}{row['name'][:_name_w]:<{_name_w}}{RESET} "
                        f"{FG['grn']}{row['cnt']:>7}{RESET}{flags}")

            def render_hint_files(sel, total):
                return (f"  {FG['cyan']}{sel+1}/{total}{RESET}  "
                        f"{FG['cyan']}Up/Dn PgUp/PgDn{RESET}=scroll  "
                        f"{FG['cyan']}Enter{RESET}=open  "
                        f"{FG['cyan']}A{RESET}=all files  "
                        f"{FG['cyan']}Q{RESET}=back")

            result = await self._rss_lightbar(
                lb_rows, render_header_files, render_row_files, render_hint_files)

            if result[0] == 'quit':
                return
            elif result[0] == 'enter':
                row = lb_rows[result[1]]
                if row.get('is_top'):
                    area_filter, area_name, area_id, can_up, stor = \
                        'top', 'General / Top-level', None, True, ''
                else:
                    area_filter, area_name, area_id, can_up, stor = \
                        'area', row['name'], row['id'], row['can_upload'], row['storage_path']
            elif result[0] == 'key' and result[1] == 'A':
                area_filter, area_name, area_id, can_up, stor = \
                    'all', 'All Files', None, False, ''
            else:
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
        from .ansi_ui import FG, RESET, BOLD, ui_width
        app = _app()
        PAGE = 9   # 9 × 2-line entries + 4 header + 2 nav = 24 lines, fits 80×25
        page = 0

        while True:
            _w = ui_width(self.session)
            _name_w = max(32, _w - 56)
            _desc_w = max(60, _w - 12)
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
                f"{'─'*_w}\r\n"
                f" {area_name}  "
                f"{FG['gry']}(page {page+1}/{pages}, {total} files){RESET}"
                f"{FG['cyan']}{BOLD}\r\n"
                f"{'─'*_w}{RESET}\r\n\r\n")

            if not page_files:
                await self.session.write(
                    f"  {FG['gry']}(no files here yet){RESET}\r\n")

            for i, f in enumerate(page_files, page * PAGE + 1):
                sz = (f"{f['size']:>10,}" if isinstance(f['size'], int)
                      else f"{str(f['size']):>10}")
                src = ('FTN' if f['tic_id'] else f['who'][:10])
                await self.session.write(
                    f"  {FG['yel']}{i:3d}.{RESET} "
                    f"{FG['wht']}{f['name'][:_name_w]:<{_name_w}}{RESET} "
                    f"{FG['gry']}{sz}  {src:<12}{RESET}\r\n")
                if f['desc']:
                    await self.session.write(
                        f"        {FG['dim']}{f['desc'][:_desc_w]}{RESET}\r\n")

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
                await self._upload_terminal_file(
                    area_id, uploads_dir, protos, storage_path)
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
        from .ansi_ui import FG, RESET, BOLD, ui_width
        name  = f['name']
        size  = f['size']
        desc  = f.get('desc_full') or f.get('desc') or ''
        date  = f.get('date')
        _w    = ui_width(self.session)

        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(
            f"{FG['cyan']}{BOLD}"
            f"{'─'*_w}\r\n"
            f" {name[:max(40, _w - 2)]}\r\n"
            f"{'─'*_w}{RESET}\r\n\r\n")

        sz_str = f"{size:,}" if isinstance(size, int) else str(size)
        await self.session.write(
            f"  {FG['yel']}Size:{RESET} {FG['wht']}{sz_str} bytes{RESET}\r\n")
        if date and hasattr(date, 'strftime'):
            await self.session.write(
                f"  {FG['yel']}Date:{RESET} "
                f"{FG['wht']}{fmt_eastern(date, '%Y-%m-%d')}{RESET}\r\n")

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
        from .ansi_ui import FG, RESET

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

        from .file_quota import check_quota, consume_quota

        for idx, f in enumerate(files, 1):
            fpath = f['path']
            name  = f['name']
            size  = f['size'] if isinstance(f.get('size'), int) else 0
            if not fpath or not os.path.isfile(fpath):
                await self.session.write(
                    f"  {FG['red']}[{idx}/{len(files)}] {name}: "
                    f"file not on disk - skipped{RESET}\r\n")
                continue

            # Daily download quota (FR: Firehawke, 2026-07-24) -- checked
            # per file since usage accumulates across the batch. Once
            # exceeded, every remaining file would also fail, so stop
            # the batch here rather than check-and-skip each one. Wrapped
            # in app_context() like every other DB-touching block here.
            with _app().app_context():
                quota_ok, quota_msg = check_quota(self.session.user, size)
            if not quota_ok:
                await self.session.write(
                    f"\r\n{FG['red']}{quota_msg}{RESET}\r\n"
                    f"{FG['red']}Stopping batch -- "
                    f"{len(files) - idx + 1} file(s) not sent.{RESET}\r\n")
                break

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
                with _app().app_context():
                    consume_quota(self.session.user, size)
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

        # Daily download quota (FR: Firehawke, 2026-07-24) -- see
        # features/file_quota.py. Checked here, right before the actual
        # transfer starts, not earlier -- the W=Web URL branch above
        # re-enters the web download route, which enforces its own
        # check independently, so gating it again here would be a
        # false-negative source (double-counting) rather than a gap.
        # Wrapped in app_context() like every other DB-touching block in
        # this method (no ambient context here).
        from .file_quota import check_quota, consume_quota
        quota_size = size if isinstance(size, int) else 0
        with _app().app_context():
            quota_ok, quota_msg = check_quota(self.session.user, quota_size)
        if not quota_ok:
            await self.session.write(f"\r\n{FG['red']}{quota_msg}{RESET}\r\n")
            await self.session.read_line("\r\nPress Enter...")
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
        if ok and quota_size:
            with _app().app_context():
                consume_quota(self.session.user, quota_size)

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

    async def _upload_terminal_file(self, area_id, uploads_dir, protos,
                                    storage_path=''):
        """Receive a file upload from the user via ZMODEM/YMODEM/XMODEM.

        *uploads_dir* is the generic fallback directory for areas with no
        disk storage configured (DB-backed FileUpload rows, matches the
        DB-fallback branch in _file_area_browse). *storage_path* is the
        area's own on-disk directory when it has one -- confirmed live
        that this was previously being ignored entirely: uploads always
        went into uploads_dir regardless of area, so a file uploaded
        into a disk-backed area got a FileUpload DB row pointing at
        uploads_dir, but the web view for that area (file_areas.py's
        _scan_area()) only ever scans area.storage_path and has no DB
        fallback -- the file existed, just never where the web looked."""
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

        # Disk-backed area (has its own storage_path): save the file
        # there under its real name, matching file_areas.py's web upload
        # route exactly. No FileUpload DB row -- _scan_area() has no DB
        # fallback and only ever lists what's physically on disk under
        # storage_path, same as every other file already in this area.
        use_disk_area = bool(storage_path)
        target_dir = storage_path if use_disk_area else uploads_dir
        os.makedirs(target_dir, exist_ok=True)

        app = _app()
        uid = self.session.user.get('id')
        saved = []

        for orig_name, tmp_path in received:
            try:
                if use_disk_area:
                    safe_name = os.path.basename(orig_name)
                    if not safe_name or safe_name.startswith('.'):
                        await self.session.write(
                            f"\r\nSkipped {orig_name!r}: invalid filename.\r\n")
                        continue
                    dest = os.path.join(target_dir, safe_name)
                    _shutil.move(tmp_path, dest)
                    saved.append(orig_name)
                else:
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
        render_row(idx, row, sel)— sync: returns a formatted string
        render_hint(sel, total)  — sync: returns the status+hints string
        Returns ('enter', idx) | ('key', char) | ('quit',)
        """
        from .ansi_ui import FG, ui_width

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
            _sw = ui_width(self.session)
            await self.session.write(
                f'{_at(self._LB_SEP_ROW)}{FG["gry"]}{"─" * _sw}{NORM}{EOL}')
            hint = render_hint(sel, len(rows))
            await self.session.write(f'{_at(self._LB_HINT_ROW)}{hint}{EOL}')
            # Hide cursor at a safe spot
            await self.session.write('\x1b[24;1H')

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
                # 3rd element (current selection index) is new -- existing
                # callers only ever index result[0]/result[1], never
                # tuple-unpack a fixed count, so this is backward compatible.
                return ('key', key, sel)

    # ── Generic sysop list+action screen ─────────────────────────────
    async def _sysop_record_list(self, title, fetch_rows, render_row,
                                  render_hint_extra, actions,
                                  empty_msg='Nothing here yet.'):
        """Generic sysop list+action screen built on _rss_lightbar.

        fetch_rows()   — sync, called fresh inside its own app_context
                         every time the list is (re)drawn. Must return a
                         plain list of tuples/dicts, never live ORM rows
                         (they aren't safe to hold across an await).
        render_row(idx, row, selected) — sync, same contract as
                         _rss_lightbar.
        render_hint_extra(sel, total)  — sync, returns the hotkey legend
                         suffix, e.g. "A=approve  X=reject  Enter=edit".
        actions: dict[str, async fn(self, row) -> bool | None], keyed by
                 the same uppercase letters advertised in
                 render_hint_extra, plus the special key 'ENTER' for the
                 Enter action. A handler returning False exits the whole
                 screen; anything else (including None) re-fetches and
                 redraws the list. Row-independent actions (e.g. "N" for
                 new, which doesn't need a selected record) also fire
                 when the list is empty -- their handler is called with
                 row=None, so they must tolerate that.
        """
        from .ansi_ui import banner, FG, RESET, ui_width

        while True:
            rows = fetch_rows()
            if not rows:
                await self.session.write('\x1b[2J\x1b[H')
                await self.session.write(banner(title, ui_width(self.session)))
                await self.session.write(f"  {FG['gry']}{empty_msg}{RESET}\r\n")
                key = (await self.session.read_line(
                    f"  {FG['cyan']}Press Enter, or a hotkey: {RESET}") or '').strip().upper()
                if key and key in actions:
                    cont = await actions[key](self, None)
                    if cont is False:
                        return
                    continue
                return

            async def render_header():
                await self.session.write(banner(title, ui_width(self.session)))

            def render_hint(sel, total):
                return (f"  {FG['cyan']}{sel+1}/{total}{RESET} "
                        f"{FG['cyan']}Up/Dn{RESET}=move  "
                        + render_hint_extra(sel, total))

            result = await self._rss_lightbar(rows, render_header, render_row,
                                              render_hint)

            if result[0] == 'quit':
                return
            elif result[0] == 'enter':
                handler = actions.get('ENTER')
                if handler:
                    cont = await handler(self, rows[result[1]])
                    if cont is False:
                        return
            elif result[0] == 'key':
                k = (result[1] or '').upper()
                handler = actions.get(k)
                if handler:
                    sel_idx = result[2] if len(result) > 2 else 0
                    cont = await handler(self, rows[sel_idx])
                    if cont is False:
                        return

    # ── Generic choice picker ─────────────────────────────────────────
    async def _pick_choice(self, title, choices):
        """Generic lightbar picker over (label, value) tuples.

        Returns (True, value) if something was picked, (False, None) if
        cancelled -- the bool distinguishes "cancelled" from "picked a
        row whose value is legitimately None" (e.g. a 'Default theme'
        choice), which a bare return value can't.
        """
        from .ansi_ui import banner, FG, RESET, ui_width

        async def render_header():
            await self.session.write(banner(title, ui_width(self.session)))

        def render_row(idx, row, selected):
            return f"  {FG['grn']}{row[0]}{RESET}"

        def render_hint(sel, total):
            return (f"  {FG['cyan']}{sel+1}/{total}{RESET} "
                    f"{FG['cyan']}Enter{RESET}=pick  {FG['cyan']}Q{RESET}=cancel")

        result = await self._rss_lightbar(choices, render_header, render_row, render_hint)
        if result[0] == 'enter':
            return True, choices[result[1]][1]
        return False, None

    # ── RSS article pager ────────────────────────────────────────────
    async def _rss_pager(self, lines, header_lines, hint_str):
        """Scrollable text pager. header_lines are fixed; lines scrolls.

        header_lines : list of already-formatted strings for the fixed header
        lines        : list of body text strings (plain, pre-wrapped)
        hint_str     : key-hint footer string
        """
        from .ansi_ui import FG, ui_width

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
                f'\x1b[{sep_row};1H{FG["gry"]}{"─" * ui_width(self.session)}{NORM}{EOL}')
            await self.session.write(
                f'\x1b[{hint_row};1H{hint_str}{pct}{EOL}')
            await self.session.write('\x1b[24;1H')

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
    async def _detect_sixel_support(self):
        """General-purpose sixel capability check -- usable by any
        feature, not just RSS. Cached per-session.

        Honors the user's `sixel_mode` profile preference first:
          'forced_off' -> always False, no DA1 round-trip at all.
          'forced_on'  -> always True (if img2sixel is installed --
                          still needed server-side to actually generate
                          sixel data), no DA1 round-trip. Covers
                          terminals that support sixel but don't
                          self-report it via DA1 (e.g. Windows Terminal
                          over SSH).
          'auto' (default) -> DA1 primary device attributes detection:
                          sends ESC [ 0 c and reads the response, which
                          looks like ESC [ ? 65 ; 1 ; 2 ; 4 ; 9 c -- flag
                          4 in the parameter list means sixel is
                          supported.

                          SyncTerm/CTerm is a special case: its DA1
                          reply doesn't use the '?'-prefixed flag list
                          at all. It spells "CTerm" in decimal ASCII
                          instead (CSI = 67;84;101;114;109;rev c), and
                          never reports sixel support there regardless
                          of whether it has it. Per the CTerm manual,
                          you have to detect that signature and then
                          send CTerm's own extended-DA query (CSI < 0 c)
                          to get CSI < 0 ; Ps... c back, where flag 4
                          means pixel/sixel graphics are supported. This
                          is why forcing sixel on/off always worked but
                          auto-detect never did on SyncTerm specifically
                          -- the standard DA1 parse can never see a "4"
                          in a response that's just spelling out a
                          product name.
        """
        if hasattr(self.session, '_sixel_ok'):
            return self.session._sixel_ok
        import shutil as _sh

        uid = (self.session.user or {}).get('id')
        mode = 'auto'
        if uid is not None:
            with _app().app_context():
                from ..models import User
                u = User.query.get(uid)
                if u is not None:
                    mode = u.sixel_mode or 'auto'

        if mode == 'forced_off':
            self.session._sixel_ok = False
            return False
        if mode == 'forced_on':
            self.session._sixel_ok = bool(_sh.which('img2sixel'))
            return self.session._sixel_ok

        # 'auto' -- DA1 detection.
        import re as _re
        # Cheap fast-path: if the tool isn't installed, skip the DA1 round-trip.
        if not _sh.which('img2sixel'):
            self.session._sixel_ok = False
            return False
        # Send DA1 query and read until 'c' or timeout.
        await self.session.write('\x1b[0c')
        resp = await self._read_escape_response(timeout_total=1.5)

        # SyncTerm/CTerm signature: "CSI = 67;84;101;114;109;rev c" is
        # "CTerm" spelled out in decimal ASCII, not a real flag list --
        # follow up with CTerm's own extended-DA query to get a real
        # capability list back.
        if '=67;84;101;114;109' in resp:
            await self.session.write('\x1b[<0c')
            resp = await self._read_escape_response(timeout_total=1.0)

        # Flag 4 = sixel: appears as ;4; or ;4c in the CSI response
        self.session._sixel_ok = bool(_re.search(r'[;?]4[;c]', resp))
        return self.session._sixel_ok

    async def _read_escape_response(self, terminator=b'c', timeout_total=1.5,
                                     timeout_per_read=0.3):
        """Read raw bytes from the session until `terminator` byte or timeout.
        Shared by DA1/CTDA-style device-attributes round trips."""
        import asyncio as _aio
        buf = b''
        try:
            deadline = _aio.get_event_loop().time() + timeout_total
            while _aio.get_event_loop().time() < deadline:
                remaining = deadline - _aio.get_event_loop().time()
                ch = await _aio.wait_for(
                    self.session.reader.read(1), timeout=min(timeout_per_read, remaining))
                if not ch:
                    break
                buf += ch
                if ch == terminator:
                    break
        except Exception:
            pass
        return buf.decode('latin-1', errors='replace')

    async def _rss_render_sixel(self, image_url, width_px=280):
        """Download image_url and emit sixel to terminal. Returns True if OK.

        Real SSRF-hardening gap found in a pre-release security audit:
        image_url comes from an individual RSS item, not the sysop-chosen
        feed source itself -- a subscribed feed's publisher (or anyone
        who compromises it) controls each item's content, including this
        URL. Scheme-restricted to http(s) so a malicious item can't use
        file:// to read local files off the server via urlopen (the
        underlying private/internal-IP SSRF surface is a separate, lower-
        severity residual risk noted but not fully closed here — same
        trust model as any other server-side image-embed feature).
        """
        import shutil, subprocess, tempfile, os, urllib.request as _ur
        from urllib.parse import urlparse as _urlparse
        if not shutil.which('img2sixel'):
            return False
        if _urlparse(image_url).scheme not in ('http', 'https'):
            return False
        tmp = None
        try:
            ext = image_url.split('?')[0].rsplit('.', 1)[-1].lower()
            if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'):
                ext = 'jpg'
            fd, tmp = tempfile.mkstemp(suffix=f'.{ext}')
            os.close(fd)
            req = _ur.Request(image_url, headers={'User-Agent': 'ANetBBS/1.0'})
            with _ur.urlopen(req, timeout=6) as resp:  # nosec B310 -- scheme restricted to http(s) above
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
        from .ansi_ui import banner, FG, RESET, BOLD, ui_width
        from sqlalchemy import func

        uid        = (self.session.user or {}).get('id')
        user_level = int((self.session.user or {}).get('access_level', 10))
        is_admin   = bool((self.session.user or {}).get('is_admin'))

        # Detect sixel support once per session -- honors the user's
        # sixel_mode profile preference (forced_on/forced_off/auto),
        # falling back to DA1 auto-detection for 'auto'. This used to
        # be an unconditional manual Y/N prompt shown every session,
        # which pre-populated the same _sixel_ok cache flag the DA1
        # detector checks first -- meaning the DA1 logic never actually
        # ran in practice. Now it does.
        await self._detect_sixel_support()

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
                _w0 = ui_width(self.session)
                await self.session.write(banner('RSS Reader', _w0))
                await self.session.write(
                    f"  {FG['gry']}No feeds configured. Add one at /admin/rss/.{RESET}\r\n")
                await self.session.read_line(f"  {FG['cyan']}Press Enter...{RESET}")
                return

            # Feed name column expands with terminal width.
            # Fixed cols: indent(2)+num(3)+sp(2)+cat(12)+sp(2)+items(7)+sp(2)+unread(8) = 38
            _rss_w   = ui_width(self.session)
            COL_W    = max(32, _rss_w - 38)

            async def render_header_rss():
                _w = ui_width(self.session)
                _cw = max(32, _w - 38)
                await self.session.write(banner('RSS Reader', _w))
                await self.session.write(
                    f"  {FG['cyan']}{BOLD}"
                    f"{'#':>2}  {'Feed':<{_cw}}  {'Category':<10}  "
                    f"{'Items':>5}  {'Unread':>6}{RESET}\r\n"
                    f"  {FG['gry']}{'─' * max(72, _w - 4)}{RESET}\r\n")

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

    async def show_guru(self):
        """Ask Anet -- the in-BBS help guru. Retrieval only (FTS5 search
        over the wiki + a fixed personality template), see
        anetbbs/guru/personality.py's DISCLOSURE -- shown here first,
        every time, so this is never mistaken for a live AI chatbot."""
        from .ansi_ui import banner, FG, RESET, BOLD, ui_width
        from anetbbs.guru import search as guru_search, personality
        from anetbbs.guru.render_plain import markdown_to_plain
        from anetbbs.models import WikiPage

        _w = ui_width(self.session)
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner('Ask Anet', _w))
        for line in self._wrap_text(personality.DISCLOSURE, max(60, _w - 4)):
            await self.session.write(f"  {FG['gry']}{line}{RESET}\r\n")
        await self.session.write(f"\r\n  {personality.intro()}\r\n\r\n")

        while True:
            q = await self.session.read_line(
                f"  {FG['cyan']}Ask a question (blank to leave):{RESET} ")
            q = (q or '').strip()
            if not q:
                return

            with _app().app_context():
                results = guru_search.search(q, limit=8)

            if not results:
                await self.session.write(f"\r\n  {personality.not_found()}\r\n\r\n")
                continue

            await self.session.write(f"\r\n  {personality.found_lead_in()}\r\n\r\n")

            # Fixed cols: indent(2)+title(28)+sp(2) = 32; snippet gets the
            # rest, so results still line up cleanly on an 80-col terminal
            # and use the extra room on a 132-col widescreen session.
            _gw       = ui_width(self.session)
            _title_w  = 28
            _snippet_w = max(20, _gw - 32)

            def _truncate(s, width):
                if len(s) <= width:
                    return s
                cut = s[:width - 3].rsplit(' ', 1)[0]
                return f'{cut}...' if cut else s[:width - 3] + '...'

            async def render_header():
                await self.session.write(banner('Ask Anet - results', ui_width(self.session)))
                await self.session.write(
                    f"  {FG['cyan']}{BOLD}{'Page':<{_title_w}}{'Match':<{_snippet_w}}{RESET}\r\n")

            def render_row(idx, row, selected):
                title = _truncate(row['title'], _title_w - 2)
                snip = _truncate(row['snippet'], _snippet_w - 1)
                return f"  {title:<{_title_w}}{snip:<{_snippet_w}}"

            def render_hint(sel, total):
                return (f"  {FG['cyan']}Enter{RESET}=read  "
                        f"{FG['cyan']}Q{RESET}=new question   {sel + 1}/{total}")

            result = await self._rss_lightbar(results, render_header, render_row, render_hint)
            if result[0] == 'enter':
                page = results[result[1]]
                with _app().app_context():
                    wp = WikiPage.query.filter_by(slug=page['slug'], is_deleted=False).first()
                if wp:
                    _art_w = max(72, _w - 4)
                    body_lines = []
                    for para in markdown_to_plain(wp.body).split('\n'):
                        body_lines.extend(self._wrap_text(para, _art_w) if para.strip() else [''])
                    hdr = [f"  {FG['cyan']}{BOLD}{wp.title}{RESET}",
                           f"  {FG['gry']}{'-' * _art_w}{RESET}"]
                    hint = f"  {FG['cyan']}Up/Dn{RESET}=scroll  {FG['cyan']}Q/Enter{RESET}=back"
                    await self._rss_pager(body_lines, hdr, hint)
            await self.session.write(f"\r\n  {personality.OUTRO}\r\n")

    async def _rss_feed_items(self, feed_id, feed_name):
        """Item list for one feed — arrow-key lightbar."""
        from anetbbs.models import RssItem, RssReadStatus, db
        from .ansi_ui import banner, FG, RESET, BOLD, ui_width

        uid = (self.session.user or {}).get('id')

        last_sel = 0
        while True:
            with _app().app_context():
                q = (RssItem.query.filter_by(feed_id=feed_id)
                     .order_by(RssItem.published_at.desc().nullslast()))
                all_items = q.limit(300).all()
                if not all_items:
                    await self.session.write('\x1b[2J\x1b[H')
                    await self.session.write(banner(feed_name[:36], ui_width(self.session)))
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

            _title_w = max(62, ui_width(self.session) - 14)

            async def render_header_items():
                _w = ui_width(self.session)
                _tw = max(62, _w - 14)
                await self.session.write(banner(feed_name[:36], _w))
                await self.session.write(
                    f"  {FG['cyan']}{BOLD}"
                    f"{'':>4}{'Date':>5}  {'Title':<{_tw}}{RESET}\r\n"
                    f"  {FG['gry']}{'─' * max(74, _w - 4)}{RESET}\r\n")

            def render_row_items(idx, row, selected):
                iid, title, ts, is_unread = row
                ts_s  = fmt_eastern(ts, '%m-%d', '  ?  ')
                mark  = f"{FG['yel']}*{RESET}" if is_unread else ' '
                tcol  = FG['wht'] if is_unread else FG['gry']
                n_col = f"{FG['grn']}{idx+1:>3}{RESET}"
                t_safe = BBSMenuUI._sanitize_cp437(title)
                return (f"  {n_col} {mark}{ts_s}  "
                        f"{tcol}{t_safe[:_title_w]:<{_title_w}}{RESET}")

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
        from .ansi_ui import banner, FG, RESET, BOLD, ui_width

        uid = (self.session.user or {}).get('id')

        with _app().app_context():
            app = _app()
            item = RssItem.query.get(item_id)
            if not item:
                return
            title     = self._sanitize_cp437(item.title or '(no title)')
            link      = item.link or ''
            author    = self._sanitize_cp437(item.author or '')
            pub_str   = (fmt_eastern(item.published_at, '%Y-%m-%d %H:%M %Z', ''))
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
        sixel_ok = await self._detect_sixel_support() if image_url else False

        # Build fixed header lines for the pager
        _w = ui_width(self.session)
        _art_w = max(72, _w - 4)
        hdr = []
        hdr.append("\x1b[2J\x1b[H")  # written by pager via draw(), not here
        # We build them as plain strings; _rss_pager starts at row 2.
        hdr_lines = []
        hdr_lines.append(
            f"  {FG['gry']}{feed_name[:40]}{RESET}"
            f"{'  ' + pub_str if pub_str else ''}")
        for tl in self._wrap_text(title, _art_w):
            hdr_lines.append(f"  {FG['cyan']}{BOLD}{tl}{RESET}")
        if author:
            hdr_lines.append(f"  {FG['gry']}by {author}{RESET}")
        if short_url:
            hdr_lines.append(f"  {FG['gry']}web:{RESET} {short_url}")
        hdr_lines.append(f"  {FG['gry']}{'─' * _art_w}{RESET}")

        # If sixel capable, show image in its own "pane" first
        if sixel_ok and image_url:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner(feed_name[:36], _w))
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
                body_lines.extend(self._wrap_text(para, _art_w))

        hint_str = (f"  {FG['cyan']}Up/Dn PgUp/PgDn{RESET}=scroll  "
                    f"{FG['cyan']}Home/End{RESET}=jump  "
                    f"{FG['cyan']}Q/Enter{RESET}=back")

        # Build the pager header (banner + article header)
        async def _show_pager():
            # The pager renders a banner itself as its first header rows.
            # We inline the banner into hdr_lines by prepending it.
            from .ansi_ui import ui_width as _uw
            _pw = _uw(self.session)
            full_hdr = []
            # banner outputs rows 2-4; row 1 is blank (before banner \r\n)
            # We pass the banner rows as the first header lines.
            full_hdr.append(f"  {FG['gry']}{feed_name[:40]}{RESET}"
                             f"{'  ' + pub_str if pub_str else ''}")
            for tl in self._wrap_text(title, max(72, _pw - 4)):
                full_hdr.append(f"  {FG['cyan']}{BOLD}{tl}{RESET}")
            if author:
                full_hdr.append(f"  {FG['gry']}by {author}{RESET}")
            if short_url:
                full_hdr.append(f"  {FG['gry']}web:{RESET} {short_url}")
            full_hdr.append(f"  {FG['gry']}{'─' * max(72, _pw - 4)}{RESET}")
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

    _URL_RE = re.compile(r'https?://[^\s<>"\'\x1b]+')

    @classmethod
    def _linkify_url_line(cls, line):
        """Wrap https:// URLs in OSC 8 terminal hyperlink escapes so
        supporting clients (Windows Terminal, iTerm2, kitty, gnome-terminal,
        etc.) make them clickable. Clients that don't understand OSC 8
        (most classic BBS terminals — SyncTERM, mTelnet, etc.) simply
        ignore the unrecognised escape sequence; the URL text itself
        still displays normally either way, so this never hides anything
        on an unsupporting terminal."""
        def sub(m):
            url = m.group(0)
            # Strip trailing punctuation that's almost always sentence
            # punctuation, not part of the URL (e.g. "see https://x.com.")
            trail = ''
            while url and url[-1] in '.,;:!?)]}\'"':
                trail = url[-1] + trail
                url = url[:-1]
            if not url:
                return m.group(0)
            return f'\x1b]8;;{url}\x1b\\{url}\x1b]8;;\x1b\\{trail}'
        return cls._URL_RE.sub(sub, line)

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
        from .ansi_ui import banner, FG, RESET, BOLD, ui_width

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
                    await self.session.write(banner('RSS River', ui_width(self.session)))
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
                _w = ui_width(self.session)
                _title_w = max(46, _w - 30)
                await self.session.write(banner('RSS River — All Feeds', _w))
                await self.session.write(
                    f"  {FG['cyan']}{BOLD}"
                    f"{'':>4}{'Date':>5}  {'Feed':<14}  {'Title':<{_title_w}}{RESET}\r\n"
                    f"  {FG['gry']}{'─' * max(74, _w - 4)}{RESET}\r\n")

            def render_row_river(idx, row, selected):
                iid, title, feed_name, ts, is_unread = row
                ts_s  = fmt_eastern(ts, '%m-%d', '  ?  ')
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

    # ── Ebook Reader (terminal) ──────────────────────────────────────
    # Reuses the web version's backend (anetbbs/web/ebooks.py: Gutendex
    # search, curl-based fetch, chapter splitting, EbookCache/Bookmark/
    # ReadingHistory models) -- only the UI layer here is new. Not a
    # door/game-session -- just a standalone menu feature, same as RSS.
    async def show_ebooks(self):
        """Ebook reader top menu."""
        from .ansi_ui import banner, FG, RESET, ui_width
        while True:
            _w = ui_width(self.session)
            menu = (
                '\x1b[2J\x1b[H' + banner('Ebook Reader', _w) +
                f"\r\n  {FG['cyan']}C{RESET}. Classics\r\n"
                f"  {FG['cyan']}S{RESET}. Search\r\n"
                f"  {FG['cyan']}H{RESET}. Continue Reading\r\n"
                f"  {FG['cyan']}B{RESET}. Bookmarks\r\n"
                f"  {FG['cyan']}Q{RESET}. Back\r\n\r\n"
                "Choice: "
            )
            # Single-key hotkey, no Enter -- matches the main menu's own
            # convention. Using read_line() here was a real bug: the main
            # menu's read_key() only consumes the single hotkey byte, so
            # any Enter a telnet client sends alongside it is left
            # sitting in the buffer -- a read_line() call right after
            # would consume that leftover byte as an instant blank
            # submit and return immediately without ever showing this
            # menu to the user.
            choice = (await self.session.read_key(menu) or '').strip().upper()
            if choice == 'Q':
                return
            elif choice == 'C':
                await self._ebook_classics()
            elif choice == 'S':
                await self._ebook_search()
            elif choice == 'H':
                await self._ebook_history()
            elif choice == 'B':
                await self._ebook_bookmarks()
            # empty/unrecognized choice just redraws (matches read_key's
            # own "bare Enter = redraw" convention)

    async def _ebook_pick_from_list(self, title, rows, empty_msg):
        """Generic title/author lightbar picker. rows: list of dicts with
        at least source_id/title/author. Returns the picked dict or None.
        """
        from .ansi_ui import banner, FG, RESET, BOLD, ui_width

        if not rows:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner(title, ui_width(self.session)))
            await self.session.write(f"  {FG['gry']}{empty_msg}{RESET}\r\n")
            await self.session.read_line(f"  {FG['cyan']}Press Enter...{RESET}")
            return None

        async def render_header():
            _w = ui_width(self.session)
            _tw = max(30, _w - 34)
            await self.session.write(banner(title, _w))
            await self.session.write(
                f"  {FG['cyan']}{BOLD}{'#':>2}  {'Title':<{_tw}}  {'Author':<24}{RESET}\r\n"
                f"  {FG['gry']}{'─' * max(72, _w - 4)}{RESET}\r\n")

        def render_row(idx, row, selected):
            _tw = max(30, ui_width(self.session) - 34)
            t = BBSMenuUI._sanitize_cp437(row['title'])[:_tw]
            a = BBSMenuUI._sanitize_cp437(row.get('author') or '')[:24]
            return (f"  {FG['grn']}{idx+1:>2}{RESET}  "
                    f"{t:<{_tw}}  {a:<24}")

        def render_hint(sel, total):
            return (f"  {FG['cyan']}{sel+1}/{total}{RESET} "
                    f"{FG['cyan']}Up/Dn{RESET}=move  {FG['cyan']}Enter{RESET}=open  "
                    f"{FG['cyan']}Q{RESET}=back")

        result = await self._rss_lightbar(rows, render_header, render_row, render_hint)
        if result[0] == 'enter':
            return rows[result[1]]
        return None

    async def _ebook_classics(self):
        from anetbbs.web.ebooks import get_classics_list
        await self.session.write('\x1b[2J\x1b[H\r\n  Loading classics...\r\n')
        try:
            with _app().app_context():
                rows = get_classics_list()
        except Exception:
            rows = []
        picked = await self._ebook_pick_from_list(
            'Ebook Classics', rows,
            'Could not load classics - check your connection.')
        if picked:
            await self._ebook_open_and_read(
                picked['source_id'], picked['title'], picked['author'])

    async def _ebook_search(self):
        from anetbbs.web.ebooks import search_books
        query = (await self.session.read_line(
            '\r\n  Search Project Gutenberg (title/author): ') or '').strip()
        if not query:
            return
        await self.session.write('\r\n  Searching...\r\n')
        try:
            with _app().app_context():
                rows = search_books(query)
        except Exception:
            rows = []
        picked = await self._ebook_pick_from_list(
            f'Search: {query[:30]}', rows, 'No books found.')
        if picked:
            await self._ebook_open_and_read(
                picked['source_id'], picked['title'], picked['author'])

    async def _ebook_history(self):
        from anetbbs.models import EbookReadingHistory
        uid = (self.session.user or {}).get('id')
        with _app().app_context():
            entries = (EbookReadingHistory.query.filter_by(user_id=uid)
                       .order_by(EbookReadingHistory.last_read_at.desc())
                       .limit(30).all())
            rows = [{'source_id': e.source_id, 'title': e.title,
                     'author': e.author, 'position': e.last_position}
                    for e in entries]
        picked = await self._ebook_pick_from_list(
            'Continue Reading', rows,
            "You haven't started reading anything yet.")
        if picked:
            await self._ebook_open_and_read(
                picked['source_id'], picked['title'], picked['author'],
                picked.get('position'))

    async def _ebook_bookmarks(self):
        from anetbbs.models import EbookBookmark, db
        from .ansi_ui import banner, FG, RESET, BOLD, ui_width

        uid = (self.session.user or {}).get('id')
        last_sel = 0
        while True:
            with _app().app_context():
                entries = (EbookBookmark.query.filter_by(user_id=uid)
                           .order_by(EbookBookmark.created_at.desc()).all())
                rows = [{'id': b.id, 'source_id': b.source_id, 'title': b.title,
                         'author': b.author, 'name': b.name, 'position': b.position}
                        for b in entries]

            if not rows:
                await self.session.write('\x1b[2J\x1b[H')
                await self.session.write(banner('Bookmarks', ui_width(self.session)))
                await self.session.write(f"  {FG['gry']}No bookmarks yet.{RESET}\r\n")
                await self.session.read_line(f"  {FG['cyan']}Press Enter...{RESET}")
                return

            async def render_header():
                _w = ui_width(self.session)
                await self.session.write(banner('Bookmarks', _w))
                await self.session.write(
                    f"  {FG['cyan']}{BOLD}{'#':>2}  {'Name':<24}  {'Book':<40}{RESET}\r\n"
                    f"  {FG['gry']}{'─' * max(72, _w - 4)}{RESET}\r\n")

            def render_row(idx, row, selected):
                n = BBSMenuUI._sanitize_cp437(row['name'] or '')[:24]
                t = BBSMenuUI._sanitize_cp437(row['title'])[:40]
                return (f"  {FG['grn']}{idx+1:>2}{RESET}  "
                        f"{n:<24}  {t:<40}")

            def render_hint(sel, total):
                return (f"  {FG['cyan']}{sel+1}/{total}{RESET} "
                        f"{FG['cyan']}Up/Dn{RESET}=move  {FG['cyan']}Enter{RESET}=open  "
                        f"{FG['cyan']}D{RESET}=delete  {FG['cyan']}Q{RESET}=back")

            result = await self._rss_lightbar(
                rows, render_header, render_row, render_hint, initial_sel=last_sel)
            if result[0] == 'quit':
                return
            elif result[0] == 'enter':
                last_sel = result[1]
                row = rows[last_sel]
                await self._ebook_open_and_read(
                    row['source_id'], row['title'], row['author'], row['position'])
            elif result[0] == 'key' and result[1] == 'D':
                row = rows[last_sel]
                with _app().app_context():
                    bm = EbookBookmark.query.get(row['id'])
                    if bm:
                        db.session.delete(bm)
                        db.session.commit()

    async def _ebook_pick_chapter(self, chapters):
        from .ansi_ui import banner, FG, RESET, ui_width

        async def render_header():
            await self.session.write(banner('Chapters', ui_width(self.session)))

        def render_row(idx, row, selected):
            t = BBSMenuUI._sanitize_cp437(row['title'])[:70]
            return f"  {FG['grn']}{idx+1:>3}{RESET}  {t}"

        def render_hint(sel, total):
            return (f"  {FG['cyan']}{sel+1}/{total}{RESET}  "
                    f"{FG['cyan']}Enter{RESET}=go  {FG['cyan']}Q{RESET}=cancel")

        result = await self._rss_lightbar(chapters, render_header, render_row, render_hint)
        if result[0] == 'enter':
            return result[1]
        return None

    def _ebook_save_history(self, uid, source_id, title, author, position):
        from anetbbs.models import EbookReadingHistory, db
        if not uid:
            return
        with _app().app_context():
            entry = EbookReadingHistory.query.filter_by(
                user_id=uid, source='gutenberg', source_id=str(source_id)).first()
            if entry is None:
                entry = EbookReadingHistory(
                    user_id=uid, source='gutenberg', source_id=str(source_id))
                db.session.add(entry)
            entry.title = (title or '')[:300]
            entry.author = (author or '')[:300]
            entry.last_position = position
            entry.last_read_at = datetime.utcnow()
            db.session.commit()

    async def _ebook_add_bookmark(self, uid, source_id, title, author, position, chapter_title):
        from anetbbs.models import EbookBookmark, db
        name = (await self.session.read_line(
            f"\r\n  Bookmark name [{chapter_title[:40]}]: ") or '').strip()
        if not name:
            name = chapter_title[:100]
        with _app().app_context():
            db.session.add(EbookBookmark(
                user_id=uid, source='gutenberg', source_id=str(source_id),
                title=(title or '')[:300], author=(author or '')[:300],
                name=name[:100], position=position, created_at=datetime.utcnow()))
            db.session.commit()
        await self.session.write("  Bookmark added.\r\n")

    async def _ebook_download(self, title, content):
        import tempfile
        from .xfer import send_file, available_protocols

        protocols = available_protocols()
        if not protocols:
            await self.session.write(
                "\r\n  No file-transfer protocol available on this server "
                "(sysop needs to install lrzsz).\r\n")
            await self.session.read_line("  Press Enter...")
            return
        safe_title = BBSMenuUI._sanitize_cp437(
            re.sub(r'[^A-Za-z0-9 _-]', '', title or 'book')).strip() or 'book'
        fd, path = tempfile.mkstemp(
            suffix='.txt', prefix=safe_title.replace(' ', '_') + '_')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(content)
            await self.session.write(
                f"\r\n  Starting {protocols[0]} download: {safe_title}.txt\r\n")
            await send_file(self.session, path, protocol=protocols[0])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    async def _ebook_open_and_read(self, source_id, title=None, author=None,
                                    resume_offset=None):
        """Fetch (or pull from cache) and enter the chapter-by-chapter
        reading loop for one book."""
        import json as _json
        from anetbbs.web.ebooks import _fetch_and_cache_book
        from .ansi_ui import FG, RESET, ui_width

        await self.session.write(
            f"\r\n  Fetching \"{title or source_id}\"... "
            f"(larger books can take a few seconds on first read)\r\n")
        try:
            with _app().app_context():
                book = _fetch_and_cache_book(str(source_id))
                if book is None:
                    await self.session.write(
                        f"  {FG['red']}No readable text format for this book.{RESET}\r\n")
                    await self.session.read_line("  Press Enter...")
                    return
                # Gutenberg text is full Unicode (smart quotes, em-dashes,
                # etc.) -- sanitize for CP437 terminals the same way the
                # RSS reader does for feed content, or these show up as
                # mangled '?' characters on a real terminal. Title/author
                # are small and safe to sanitize outright; the full book
                # `content` is NOT sanitized here -- chapters[i]
                # ['start_offset'] was computed against the original
                # unsanitized text, and several substitutions change
                # length (e.g. '…' -> '...'), which would silently shift
                # every offset after the first such character and throw
                # off chapter boundaries. Sanitize per-paragraph instead,
                # at display time, after slicing by the real offsets.
                book_title = BBSMenuUI._sanitize_cp437(book.title or '')
                book_author = BBSMenuUI._sanitize_cp437(book.author or '')
                content = book.content or ''
                chapters = _json.loads(book.chapters_json or '[]')
                for _ch in chapters:
                    _ch['title'] = BBSMenuUI._sanitize_cp437(_ch.get('title') or '')
        except Exception as exc:
            await self.session.write(f"  {FG['red']}Could not fetch book: {exc}{RESET}\r\n")
            await self.session.read_line("  Press Enter...")
            return

        chapter_idx = 0
        if resume_offset:
            for i in range(len(chapters) - 1, -1, -1):
                if chapters[i]['start_offset'] <= resume_offset:
                    chapter_idx = i
                    break

        uid = (self.session.user or {}).get('id')

        while True:
            bounds_start = chapters[chapter_idx]['start_offset']
            bounds_end = (chapters[chapter_idx + 1]['start_offset']
                          if chapter_idx + 1 < len(chapters) else len(content))
            chapter_text = content[bounds_start:bounds_end]

            _w = max(60, ui_width(self.session) - 4)
            body_lines = []
            for para in chapter_text.split('\n\n'):
                para = BBSMenuUI._sanitize_cp437(para.strip())
                if not para:
                    continue
                body_lines.extend(self._wrap_text(para, _w))
                body_lines.append('')

            hdr = [
                f"  {FG['cyan']}{book_title[:60]}{RESET} - {FG['gry']}{(book_author or '')[:40]}{RESET}",
                f"  {chapters[chapter_idx]['title'][:70]}",
                f"  {FG['gry']}{'─' * _w}{RESET}",
            ]
            hint = (f"  {FG['cyan']}Up/Dn PgUp/PgDn{RESET}=scroll  "
                    f"{FG['cyan']}Q/Enter{RESET}=menu")

            self._ebook_save_history(uid, source_id, book_title, book_author, bounds_start)

            await self._rss_pager(body_lines, hdr, hint)

            action_menu = (
                f"\r\n  {FG['cyan']}N{RESET}ext chapter  {FG['cyan']}P{RESET}rev chapter  "
                f"{FG['cyan']}C{RESET}hapters  {FG['cyan']}K{RESET}=bookmark here  "
                f"{FG['cyan']}D{RESET}ownload  {FG['cyan']}Q{RESET}uit to menu\r\n"
                "Choice: "
            )
            act = (await self.session.read_key(action_menu) or '').strip().upper()
            if act == 'Q':
                return
            elif act == 'N':
                if chapter_idx < len(chapters) - 1:
                    chapter_idx += 1
            elif act == 'P':
                if chapter_idx > 0:
                    chapter_idx -= 1
            elif act == 'C':
                picked_idx = await self._ebook_pick_chapter(chapters)
                if picked_idx is not None:
                    chapter_idx = picked_idx
            elif act == 'K':
                await self._ebook_add_bookmark(
                    uid, source_id, book_title, book_author, bounds_start,
                    chapters[chapter_idx]['title'])
            elif act == 'D':
                await self._ebook_download(book_title, content)
            # any other/empty input just redraws the same chapter

    async def show_profile(self):
        from anetbbs.models import User
        while True:
            with _app().app_context():
                u = User.query.get(self.session.user['id'])
                if not u:
                    await self.session.write("\r\nProfile not found.\r\n")
                    await self.session.read_line("\r\nPress Enter...")
                    return

            from .ansi_ui import banner, footer, FG, RESET, ui_width
            _w = ui_width(self.session)
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Your Profile', _w))
            for label, value in [
                ('Username', u.username),
                ('Display name', u.display_name or '-'),
                ('Email', u.email or '-'),
                ('Joined', fmt_eastern(u.created_at, '%Y-%m-%d', '?')),
                ('Last login', fmt_eastern(u.last_login, '%Y-%m-%d %H:%M', 'never')),
                ('Login count', str(u.login_count or 0)),
                ('Admin', 'yes' if u.is_admin else 'no'),
                ('Location', u.location or '-'),
                ('Bio', (u.bio or '-')[:max(60, _w - 20)]),
            ]:
                await self.session.write(
                    f"  {FG['cyan']}{label:<14}{RESET} "
                    f"{FG['gry']}:{RESET} "
                    f"{FG['grn']}{value}{RESET}\r\n")
            await self.session.write('\r\n' + footer(_w) + '\r\n')
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

async def _maybe_prompt_tagline(ui):
    """Let the user browse the shared tagline pool in a scrollable
    lightbar and pick one (or skip). Returns the picked tagline text, or
    None if skipped/pool empty.

    Passed into launch_anedit() as a `tagline_picker` callback, called
    ONCE at actual send time (Ctrl+W/`/send`), not before the user has
    even started typing -- reported live twice: first "it never asked
    me if I wanted to add a tagline" (fixed by adding this prompt at
    all), then "it should not ask you about a tag line until you send,
    when you send it should bring up the tagline" (fixed by moving the
    call from before launch_anedit() to inside ANEdit's own send
    handling).
    """
    with _app().app_context():
        from anetbbs.models import get_active_taglines
        taglines = [(t.id, t.text) for t in get_active_taglines()]
    if not taglines:
        return None

    from .ansi_ui import banner, FG, RESET, ui_width

    async def render_header():
        await ui.session.write(banner('Pick a Tagline', ui_width(ui.session)))

    def render_row(idx, row, selected):
        # _rss_lightbar's SEL wrapper (reverse+bold) rendered the
        # selected row completely invisible on the user's actual
        # terminal (SyncTERM) -- confirmed live via screenshot on TWO
        # separate attempts: first with an explicit color on the text
        # (bold-white competing with the reverse highlight), then with
        # NO color at all (matching every other lightbar row's
        # convention, which relies purely on reverse-video against
        # default colors). Both were still invisible, meaning
        # reverse+bold itself -- not what render_row() does with
        # color -- is the actual problem in this client. Rather than
        # keep guessing at SGR interactions blind, this sidesteps
        # reverse-video entirely for the selected row: explicitly
        # cancel SEL's escape codes (\x1b[0m) and draw a plain,
        # unambiguous marker + bright color instead. Guaranteed visible
        # on any ANSI terminal, since it doesn't depend on how a given
        # client's reverse-video implementation interacts with bold.
        #
        # Truncated to the terminal width minus a small margin -- a
        # tagline can be up to 200 chars (model column limit); an
        # untruncated row longer than the terminal width auto-wraps
        # onto the next line under this lightbar's absolute cursor
        # positioning, corrupting subsequent rows on redraw (the exact
        # bug class found live in the compose-echomail area picker's
        # column-width math).
        _w = ui_width(ui.session)
        text = row[1][:max(20, _w - 4)]
        if selected:
            return f"\x1b[0m{FG['yel']}> {text}{RESET}"
        return f"  {FG['wht']}{text}{RESET}"

    def render_hint(sel, total):
        return (f"  {FG['cyan']}{sel+1}/{total}{RESET}  "
                f"{FG['cyan']}Up/Dn PgUp/PgDn{RESET}=scroll  "
                f"{FG['cyan']}Enter{RESET}=use this one  "
                f"{FG['cyan']}Q{RESET}=no tagline")

    result = await ui._rss_lightbar(taglines, render_header, render_row, render_hint)
    if result[0] == 'enter':
        return taglines[result[1]][1]
    return None


async def _post_compose(self, board_id, board_name, parent_id=None):
    from anetbbs.models import db, Post, Board
    from .ansi_ui import banner, FG, RESET, BOLD, ui_width
    from .anedit import launch_anedit

    # Access control: web/boards.py's new_post() enforces Board.
    # min_write_level and reply_post() enforces Post.is_locked -- this
    # terminal composer (the single choke point for both the 'N' new-
    # thread and 'R'/'N' reply-from-ANView callers, see list_threads_v2/
    # read_thread_v2 below) checked neither, so any authenticated user
    # could post/reply here regardless of a board's configured posting
    # level or a moderator having locked the thread.
    user = self.session.user or {}
    is_admin = bool(user.get('is_admin'))
    with _app().app_context():
        board = Board.query.get(board_id)
        if board is None:
            return
        if not is_admin:
            write_lvl = (board.min_write_level
                        if board.min_write_level is not None
                        else board.min_access_level)
            if int(user.get('access_level', 0) or 0) < write_lvl:
                await self.session.write(
                    f"\r\n  {FG['red']}You don't have permission to post here.{RESET}\r\n")
                await self.session.read_line("  Press Enter...")
                return
        if parent_id and not is_admin:
            root = Post.query.get(parent_id)
            if root is not None and root.is_locked:
                await self.session.write(
                    f"\r\n  {FG['red']}This thread is locked.{RESET}\r\n")
                await self.session.read_line("  Press Enter...")
                return

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

    _w = ui_width(self.session)
    await self.session.write('\x1b[2J\x1b[H')
    if parent_id:
        await self.session.write(banner(f'Reply — {board_name}', _w))
    else:
        await self.session.write(banner(f'New Thread — {board_name}', _w))

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
                               subject=subject, username=username,
                               tagline_picker=lambda: _maybe_prompt_tagline(self))
    if body is None:
        await self.session.write(f"\r\n  {FG['gry']}Aborted.{RESET}\r\n")
        await self.session.read_line("  Press Enter...")
        return

    with _app().app_context():
        # Sysop-configured word-filter blocklist -- web/boards.py's
        # new_post()/reply_post() already run subject/content through
        # this before saving; neither terminal composer (this one or
        # PETSCII's _board_post) did, so a blocklisted word/phrase was
        # fully enforced on web but freely postable from telnet/SSH/
        # rlogin/PETSCII. Must run INSIDE the app context -- apply()
        # queries the WordFilter table.
        try:
            from .word_filter import apply as _wf_apply
            subject = _wf_apply(subject)
            body = _wf_apply(body)
        except Exception:
            pass

        p = Post(board_id=board_id, author_id=self.session.user['id'],
                 parent_id=parent_id, subject=subject[:200], content=body)
        db.session.add(p)
        db.session.commit()
        try:
            from .webhooks import fire
            fire('post', {'user': username, 'board_id': board_id,
                          'subject': p.subject, 'content': body})
        except Exception:
            pass
    await self.session.write(
        f"\r\n  {FG['grn']}{BOLD}[OK]{RESET} Posted (#{p.id}).\r\n")
    await self.session.read_line("\r\n  Press Enter...")
BBSMenuUI._post_compose = _post_compose


async def _send_pm(self):
    from anetbbs.models import db, User, PrivateMessage
    from .ansi_ui import banner, FG, RESET, BOLD, ui_width
    _w = ui_width(self.session)
    await self.session.write('\x1b[2J\x1b[H')
    await self.session.write(banner('Send Private Message', _w))
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
    body = await launch_anedit(self.session, subject=subject, username=username,
                               tagline_picker=lambda: _maybe_prompt_tagline(self))
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
    from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD, ui_width


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
        _w0 = ui_width(self.session)
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner('Compose Echomail', _w0))
        await self.session.write(
            f"  {FG['gry']}No echomail areas configured.{RESET}\r\n")
        await self.session.write('\r\n' + footer(_w0) + '\r\n')
        await self.session.read_line(f"  {FG['cyan']}Press Enter...{RESET}")
        return

    selected_area = None
    while selected_area is None:
        # Network chooser
        _w = ui_width(self.session)
        _net_w = max(30, _w - 22)
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner('Compose Echomail — Choose Network', _w))
        await self.session.write(
            f"  {FG['cyan']}{BOLD}{'#':>2}  {'Network':<{_net_w}} {'Type':<6} {'Areas':>5}{RESET}\r\n"
            f"  {FG['gry']}{'─' * max(50, _w - 4)}{RESET}\r\n")
        for i, (_, name, ntype, cnt) in enumerate(net_rows, 1):
            type_col = FG['cyan'] if ntype == 'binkp' else FG['yel']
            await self.session.write(
                f"  {FG['yel']}{BOLD}{i:2d}{RESET}  "
                f"{FG['wht']}{name[:_net_w]:<{_net_w}}{RESET}  "
                f"{type_col}{ntype[:5]:<5}{RESET}  "
                f"{FG['grn']}{cnt:5d}{RESET}\r\n")
        await self.session.write('\r\n' + footer(_w) + '\r\n')
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
        # Scrollable lightbar instead of the old numbered-list-with-
        # "-- more (Enter / Q) --" paging -- matches the read-side area
        # picker (_list_network_areas), which already uses _rss_lightbar
        # with category shown as a column rather than separator rows.
        with _app().app_context():
            q = EchoArea.query.filter_by(
                network_id=net_id, is_active=True, is_subscribed=True)
            if not is_admin:
                q = q.filter(EchoArea.is_sysop_only == False,
                             EchoArea.min_access_level <= user_level)
            areas = q.order_by(EchoArea.category, EchoArea.order,
                               EchoArea.name).all()
            a_list = []
            for area in areas:
                cat = area.category or 'General'
                a_list.append((area.id, area.tag, area.name,
                               area.network_id,
                               area.network.our_address or '1:1/1', cat))

        if not a_list:
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner(f'Compose — {net_name}', ui_width(self.session)))
            await self.session.write(
                f"  {FG['gry']}No areas in this network.{RESET}\r\n")
            await self.session.read_line(f"  {FG['cyan']}Press Enter...{RESET}")
            continue

        COL_TAG = 18

        async def render_header_carea():
            _w = ui_width(self.session)
            # Visible-char overhead per row: "  "(2) + idx(3) + "  "(2) +
            # tag(COL_TAG=18) + " "(1) + name(_nw) + " "(1) + cat(10)
            # = 37 + _nw. Previously subtracted 28 (wrong -- 9 short of
            # the real overhead), so on wide terminals (132-col SyncTERM,
            # confirmed live via screenshot) each row overflowed the
            # terminal width and auto-wrapped onto the next line,
            # corrupting subsequent rows once a partial-redraw (after
            # scrolling) didn't fully overwrite the wrapped-in fragments.
            _nw = max(24, _w - 38)
            await self.session.write(banner(f'Compose — {net_name}', _w))
            await self.session.write(
                f"  {FG['cyan']}{BOLD}{'#':>3}  {'Tag':<{COL_TAG}} "
                f"{'Name':<{_nw}} {'Category':<10}{RESET}\r\n"
                f"  {FG['gry']}{'─' * max(70, _w - 4)}{RESET}\r\n")

        def render_row_carea(idx, row, selected):
            _, tag, name, _, _, cat = row
            _w = ui_width(self.session)
            # Visible-char overhead per row: "  "(2) + idx(3) + "  "(2) +
            # tag(COL_TAG=18) + " "(1) + name(_nw) + " "(1) + cat(10)
            # = 37 + _nw. Previously subtracted 28 (wrong -- 9 short of
            # the real overhead), so on wide terminals (132-col SyncTERM,
            # confirmed live via screenshot) each row overflowed the
            # terminal width and auto-wrapped onto the next line,
            # corrupting subsequent rows once a partial-redraw (after
            # scrolling) didn't fully overwrite the wrapped-in fragments.
            _nw = max(24, _w - 38)
            return (f"  {FG['yel']}{idx+1:>3}{RESET}  "
                    f"{FG['cyan']}{tag[:COL_TAG]:<{COL_TAG}}{RESET} "
                    f"{FG['wht']}{name[:_nw]:<{_nw}}{RESET} "
                    f"{FG['gry']}{cat[:10]:<10}{RESET}")

        def render_hint_carea(sel, total):
            return (f"  {FG['cyan']}{sel+1}/{total}{RESET}  "
                    f"{FG['cyan']}Up/Dn PgUp/PgDn{RESET}=scroll  "
                    f"{FG['cyan']}Enter{RESET}=pick  "
                    f"{FG['cyan']}Q{RESET}=back")

        result = await self._rss_lightbar(
            a_list, render_header_carea, render_row_carea, render_hint_carea)
        if result[0] == 'quit':
            continue   # back to network chooser
        elif result[0] == 'enter':
            selected_area = a_list[result[1]]

    if selected_area is None:
        return

    area_id, area_tag, area_name, network_id, our_addr, _cat = selected_area

    # ── Step 3: compose the message ──────────────────────────────────
    await self.session.write('\x1b[2J\x1b[H')
    await self.session.write(banner(f'Compose — {area_tag}', ui_width(self.session)))
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
    body = await launch_anedit(self.session, subject=subject, username=username,
                               tagline_picker=lambda: _maybe_prompt_tagline(self))
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

        # Real gap found live: a hub with real downstream nodes (a real
        # sysop reported never receiving a single packet despite dozens
        # of real local messages existing in areas they're correctly
        # subscribed to) -- toss_message() was ONLY ever called from the
        # inbound-import paths (binkp_server.py/poller.py/qwk_hub_ftp.py/
        # web/qwk_hub.py), despite the tosser's own module docstring
        # explicitly saying it handles "locally composed" messages too.
        # None of the three local composers (this one, the web compose()
        # route, petscii_ui.py's _echo_compose) ever actually called it --
        # a message composed directly on the hub just sat there, visible
        # locally, but never queued for any downstream node at all.
        from ..echomail.tosser import toss_message
        toss_message(saved_id)

        # Real gap found live: a LOCAL user posting/replying directly in a
        # shared echo area never went through any of the three inbound-
        # network-import notify hooks (poller.py/binkp_server.py/
        # qwk_hub_ftp.py) -- those only fire when a message arrives FROM
        # an external network transfer. Two ANetBBS accounts replying to
        # each other in the same area is a fourth, previously-unhooked
        # write path into EchomailMessage.
        from ..echomail.notify_reply import maybe_notify_recipient
        from ..models import EchoArea, EchomailNetwork
        _area_obj = EchoArea.query.get(area_id)
        _network_obj = EchomailNetwork.query.get(network_id)
        if _area_obj is not None and _network_obj is not None:
            maybe_notify_recipient(em, _area_obj, _network_obj)

    await self.session.write(
        f"\r\n  {FG['grn']}{BOLD}Message queued (#{saved_id}).{RESET}"
        f"  {FG['gry']}Goes out on next poll.{RESET}\r\n")
    await self.session.read_line(f"\r\n{FG['cyan']}Press Enter...{RESET}")

BBSMenuUI.compose_echomail = _compose_echomail


_PROFILE_TEXT_FIELDS = (
    ('display_name', 'Display name', 100),
    ('email', 'Email', 120),
    ('location', 'Location', 100),
    ('website', 'Website', 255),
    ('bio', 'Bio (one line)', 500),
    ('signature', 'Signature (one line)', 500),
    ('tagline', 'FTN tagline', 160),
)
_PROFILE_SIXEL_CHOICES = (('Automatic (detect)', 'auto'),
                          ('Always on', 'forced_on'),
                          ('Always off', 'forced_off'))
_PROFILE_CODEPAGE_CHOICES = (('CP437 (DOS classic)', 'cp437'), ('UTF-8', 'utf8'))
_PROFILE_CURSOR_CHOICES = (('Default (unchanged)', 'default'),
                           ('Steady, no blink (accessibility)', 'steady'),
                           ('Spinning (Synchronet-style)', 'spinning'))


async def _edit_profile_field(self, kind, attr, label):
    """Edit one profile field, dispatched by kind. Never touches
    password_hash -- change_password() is the separate dedicated flow."""
    from anetbbs.models import db, User, Theme
    from .ansi_ui import FG, RESET

    _maxlen = dict((a, m) for a, _l, m in _PROFILE_TEXT_FIELDS)

    if kind == 'text':
        with _app().app_context():
            u = User.query.get(self.session.user['id'])
            current = getattr(u, attr, '') or ''
        new = (await self.session.read_line(
            f"\r\n{label} [{current[:40]}]: ") or '').strip()
        if new:
            with _app().app_context():
                u = User.query.get(self.session.user['id'])
                setattr(u, attr, new[:_maxlen.get(attr, 255)])
                db.session.commit()

    elif kind == 'bool':
        picked, value = await self._pick_choice(label, [('Yes', True), ('No', False)])
        if picked:
            with _app().app_context():
                u = User.query.get(self.session.user['id'])
                setattr(u, attr, value)
                db.session.commit()

    elif kind == 'date':
        new = (await self.session.read_line(
            f"\r\n{label} (YYYY-MM-DD, blank = clear): ") or '').strip()
        with _app().app_context():
            u = User.query.get(self.session.user['id'])
            if not new:
                u.date_of_birth = None
                db.session.commit()
                return
            try:
                u.date_of_birth = datetime.strptime(new, '%Y-%m-%d').date()
                db.session.commit()
            except ValueError:
                await self.session.write(f"\r\n{FG['red']}Bad date -- use YYYY-MM-DD.{RESET}\r\n")
                await self.session.read_line("Press Enter...")

    elif kind == 'theme':
        with _app().app_context():
            themes = Theme.query.filter_by(is_active=True).order_by(Theme.name).all()
            site_default = Theme.query.filter_by(is_default=True, is_active=True).first()
            default_label = f'Site Default: {site_default.display_name}' if site_default else 'Site Default: Classic Green'
            choices = [(default_label, None)] + [
                (t.display_name, t.id) for t in themes]
        picked, value = await self._pick_choice('Pick a Theme', choices)
        if picked:
            with _app().app_context():
                u = User.query.get(self.session.user['id'])
                u.theme_id = value
                db.session.commit()

    elif kind == 'sixel':
        picked, value = await self._pick_choice('Sixel Graphics', list(_PROFILE_SIXEL_CHOICES))
        if picked:
            with _app().app_context():
                u = User.query.get(self.session.user['id'])
                u.sixel_mode = value
                db.session.commit()

    elif kind == 'codepage':
        picked, value = await self._pick_choice('Codepage', list(_PROFILE_CODEPAGE_CHOICES))
        if picked:
            with _app().app_context():
                u = User.query.get(self.session.user['id'])
                u.codepage = value
                db.session.commit()

    elif kind == 'cursor':
        picked, value = await self._pick_choice('Cursor Style', list(_PROFILE_CURSOR_CHOICES))
        if picked:
            with _app().app_context():
                u = User.query.get(self.session.user['id'])
                u.cursor_style = value
                db.session.commit()
            await self.session.write(
                f"\r\n{FG['cyan']}Takes effect next time you connect.{RESET}\r\n")
            await self.session.read_line("Press Enter...")

    elif kind == 'lang':
        # No enforced list of valid language codes exists anywhere in
        # this codebase (MenuTranslation's docstring only gives
        # illustrative examples) -- light validation only, matching the
        # field's current unenforced state on the web side too.
        new = (await self.session.read_line(
            "\r\nLanguage code (2 lowercase letters, e.g. en/es/fr): ") or '').strip().lower()
        if new:
            if len(new) == 2 and new.isalpha():
                with _app().app_context():
                    u = User.query.get(self.session.user['id'])
                    u.language = new
                    db.session.commit()
            else:
                await self.session.write(
                    f"\r\n{FG['red']}Must be exactly 2 letters (ISO-639-1).{RESET}\r\n")
                await self.session.read_line("Press Enter...")
BBSMenuUI._edit_profile_field = _edit_profile_field


async def _edit_profile(self):
    """Single summary screen (all current settings) + a lightbar picker
    for which field to change -- replaces the old blind sequential
    prompt-per-field loop. Loops back to the picker after each edit so
    multiple fields can be changed in one visit; Q/Esc exits. Password
    is never shown here (change_password() is the separate flow)."""
    from anetbbs.models import User, Theme
    from .ansi_ui import banner, ui_width

    def _fetch_rows():
        with _app().app_context():
            u = User.query.get(self.session.user['id'])
            if not u:
                return None
            site_default = Theme.query.filter_by(is_default=True, is_active=True).first()
            theme_name = f'Site Default: {site_default.display_name}' if site_default else 'Site Default: Classic Green'
            if u.theme_id:
                t = Theme.query.get(u.theme_id)
                if t:
                    theme_name = t.display_name
            rows = [('text', attr, label, getattr(u, attr, '') or '')
                    for attr, label, _maxlen in _PROFILE_TEXT_FIELDS]
            rows.append(('bool', 'show_email', 'Show email publicly',
                        'Yes' if u.show_email else 'No'))
            rows.append(('date', 'date_of_birth', 'Date of birth',
                        u.date_of_birth.isoformat() if u.date_of_birth else '(not set)'))
            rows.append(('theme', 'theme_id', 'Theme', theme_name))
            rows.append(('sixel', 'sixel_mode', 'Sixel graphics',
                        dict((v, l) for l, v in _PROFILE_SIXEL_CHOICES).get(
                            u.sixel_mode or 'auto', u.sixel_mode)))
            rows.append(('codepage', 'codepage', 'Codepage',
                        dict((v, l) for l, v in _PROFILE_CODEPAGE_CHOICES).get(
                            u.codepage or 'cp437', u.codepage)))
            rows.append(('cursor', 'cursor_style', 'Cursor style',
                        dict((v, l) for l, v in _PROFILE_CURSOR_CHOICES).get(
                            u.cursor_style or 'default', u.cursor_style)))
            rows.append(('lang', 'language', 'Language code', u.language or 'en'))
            return rows

    while True:
        rows = _fetch_rows()
        if rows is None:
            return

        async def render_header():
            await self.session.write(banner('Edit Profile', ui_width(self.session)))

        def render_row(idx, row, selected):
            from .ansi_ui import FG, RESET
            _kind, _attr, label, value = row
            return f"  {FG['cyan']}{label:<24}{RESET} {FG['grn']}{str(value)[:40]}{RESET}"

        def render_hint(sel, total):
            from .ansi_ui import FG, RESET
            return (f"  {FG['cyan']}{sel+1}/{total}{RESET} "
                    f"{FG['cyan']}Up/Dn{RESET}=move  {FG['cyan']}Enter{RESET}=edit  "
                    f"{FG['cyan']}Q{RESET}=done")

        result = await self._rss_lightbar(rows, render_header, render_row, render_hint)
        if result[0] != 'enter':
            return
        kind, attr, label, _value = rows[result[1]]
        await self._edit_profile_field(kind, attr, label)
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
    from .ansi_ui import banner, footer, prompt as _prompt, FG, RESET, BOLD, ui_width
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

        _w = ui_width(self.session)
        _subj_w = max(35, _w - 40)
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner(board_name, _w))
        if not t_list:
            await self.session.write(f"  {FG['gry']}(no threads yet){RESET}\r\n")
        for i, (_, subj, who, when, n_replies) in enumerate(t_list, 1):
            ts = fmt_eastern(when, '%m-%d %H:%M', '?')
            rep = f"[{n_replies}]" if n_replies else "   "
            await self.session.write(
                f"  {FG['yel']}{BOLD}{i:2d}{RESET}"
                f"{FG['gry']}.{RESET} "
                f"{FG['cyan']}{rep:<5}{RESET}"
                f"{FG['wht']}{subj[:_subj_w]:<{_subj_w}}{RESET}  "
                f"{FG['grn']}{who[:14]:<14}{RESET}"
                f"{FG['gry']}{ts}{RESET}\r\n")
        await self.session.write('\r\n' + footer(_w) + '\r\n')
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
    """Scrollable ANView reader instead of the old page-break [MORE]
    pager -- matches how echo/private messages are already read.
    Local board posts, unlike bulletins, ARE composed at a real
    terminal via the same ANEdit editor echomail/PM use, so this
    renders each post's content through the same CP437-decode +
    pipe-code + VT-render pipeline launch_aneview() uses (via the
    shared render_message_body_lines() helper) rather than the
    plain-Unicode path bulletins use for web-authored text.

    This is the function actually reachable from the menu (via
    list_threads -> _list_threads_v2 -> read_thread_v2, wired at the
    bottom of this file) -- NOT the class-body read_thread()/
    list_threads(), which are shadowed dead code.
    """
    from anetbbs.models import Post, User
    from .anedit import ANView, render_message_body_lines
    from .ansi_ui import ui_width
    with _app().app_context():
        root = Post.query.get(post_id)
        # Defense in depth: the only current caller (_list_threads_v2)
        # always supplies a post_id already scoped to board_id, so this
        # isn't reachable today -- but there's no re-check here if that
        # ever changes (a future "jump to post #"/search/notification
        # deep-link feature), unlike most other post-by-id lookups in
        # this codebase. Cheap to close now rather than rely on every
        # future caller remembering to pre-filter correctly.
        if root is not None and root.board_id != board_id:
            return
        if not root:
            return
        posts = [root] + list(root.replies.order_by(Post.created_at).all())
        rendered = []
        for p in posts:
            author = User.query.get(p.author_id)
            rendered.append({
                'subject': p.subject, 'author': author.username if author else '?',
                'when': p.created_at, 'content': p.content,
            })

    col_w = ui_width(self.session)
    lines = []
    for i, p in enumerate(rendered):
        ts = fmt_eastern(p['when'], '%Y-%m-%d %H:%M', '?')
        tag = '[OP]' if i == 0 else f'[Reply {i}]'
        lines.append(f'\x1b[36m{tag} \x1b[0m{p["subject"] or "(no subject)"}')
        lines.append(f'\x1b[36mFrom:\x1b[0m {p["author"]}    \x1b[36mDate:\x1b[0m {ts}')
        lines.append('\x1b[36m' + '─' * col_w + '\x1b[0m')
        lines.extend(render_message_body_lines(p['content'] or ''))
        lines.append('')
    subject = rendered[0]['subject'] or '(no subject)' if rendered else '(no subject)'
    viewer = ANView(self.session, lines, subject=subject)
    view_result = await viewer.run()

    # R/N inside ANView always exit with a result (same viewer class
    # echomail reading uses) -- wire them up to the real reply/new-
    # thread flow instead of silently discarding them, matching how
    # read_echo_area() already handles the exact same two outcomes.
    if view_result == 'reply':
        await self._post_compose(board_id, board_name, parent_id=post_id)
    elif view_result == 'new':
        await self._post_compose(board_id, board_name, parent_id=None)
BBSMenuUI.read_thread_v2 = _read_thread_v2


# ---------------------------------------------------------------------------
# Phase 4: Sysop tools (admin users only)
# ---------------------------------------------------------------------------

async def _sysop_menu(self):
    """Top-level sysop menu — only shown to is_admin users.

    Scrollable category picker (each category is its own sub-screen
    built on _sysop_record_list) covering most of the terminal-feasible
    slice of the web admin surface. Deliberately does not attempt: the
    ANSI art editor/theme builder (rich canvas UI), file/avatar upload,
    backup restore, in-place upgrades, or the full network-join
    applicant-approval flow (multi-node-type creation + email) -- those
    stay web-only.
    """
    if not self.session.user.get('is_admin'):
        await self.session.write("\r\nSysop access required.\r\n")
        await self.session.read_line("Press Enter...")
        return
    from .ansi_ui import banner, FG, RESET, BOLD, ui_width

    categories = [
        ('U', 'Users',            self.sysop_users),
        ('B', 'Boards/Bulletins', self.sysop_boards),
        ('E', 'Echomail/Hub',     self.sysop_echomail),
        ('G', 'Games',            self.sysop_games),
        ('W', 'Wall',             self.sysop_wall),
        ('F', 'File Queue',       self.sysop_file_queue),
        ('V', 'Events',           self.sysop_events),
        ('R', 'RSS Feeds',        self.sysop_rss_admin),
        ('L', 'Login Modules',    self.sysop_login_modules),
        ('N', 'Notifications',    self.sysop_notifications),
        ('P', 'Registry/Peers',   self.sysop_registry),
        ('C', 'Caller Log',       self.sysop_callers),
        ('M', 'Node Monitor',     self.sysop_node_monitor),
        ('S', 'Server Status',    self.sysop_status),
    ]

    async def render_header():
        await self.session.write(banner('Sysop Tools', ui_width(self.session)))

    def render_row(idx, row, selected):
        hk, label, _fn = row
        return f"  {FG['yel']}{BOLD}[{hk}]{RESET} {FG['grn']}{label}{RESET}"

    def render_hint(sel, total):
        return (f"  {FG['cyan']}{sel+1}/{total}{RESET} "
                f"{FG['cyan']}Up/Dn{RESET}=move  {FG['cyan']}Enter{RESET}=open  "
                f"{FG['cyan']}Q{RESET}=exit")

    while True:
        result = await self._rss_lightbar(categories, render_header, render_row,
                                          render_hint)
        if result[0] == 'quit':
            return
        elif result[0] == 'enter':
            await categories[result[1]][2]()
        elif result[0] == 'key':
            k = (result[1] or '').upper()
            match = next((c for c in categories if c[0] == k), None)
            if match:
                await match[2]()
BBSMenuUI.sysop_menu = _sysop_menu


async def _sysop_users(self):
    from anetbbs.models import User
    from .ansi_ui import write_menu_art, banner, footer, FG, RESET, BOLD, prompt as _p, ui_width
    while True:
        with _app().app_context():
            users = User.query.order_by(User.id).all()
            u_list = [(u.id, u.username, u.email or '-', u.is_active, u.is_admin,
                       fmt_eastern(u.last_login, '%Y-%m-%d', 'never'),
                       u.login_count or 0) for u in users]
        _w = ui_width(self.session)
        if not await write_menu_art(self.session, 'sysop_users'):
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Manage Users', _w))
        await self.session.write(
            f"  {FG['cyan']}{BOLD}{'ID':<4}{'Username':<18}{'Active':<8}"
            f"{'Admin':<8}{'Last login':<14}Logins{RESET}\r\n"
            f"  {FG['gry']}{'─' * max(60, _w - 4)}{RESET}\r\n")
        for uid, name, _, active, admin, lastl, n in u_list:
            active_s = f"{FG['grn']}yes{RESET}" if active else f"{FG['red']} no{RESET}"
            admin_s  = f"{FG['yel']}yes{RESET}" if admin  else f"{FG['dim']} no{RESET}"
            await self.session.write(
                f"  {FG['wht']}{uid:<4}{RESET}"
                f"{FG['grn']}{name[:18]:<18}{RESET}"
                f"{active_s:<20}{admin_s:<20}"
                f"{FG['dim']}{lastl:<14}{RESET}"
                f"{FG['cyan']}{n}{RESET}\r\n")
        await self.session.write('\r\n' + footer(_w) + '\r\n')
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
    from .ansi_ui import banner, menu_item, footer, FG, RESET, prompt as _p, ui_width
    with _app().app_context():
        u = User.query.get(uid)
        if not u:
            await self.session.write(f"\r\n{FG['red']}No such user.{RESET}\r\n")
            await self.session.read_line("Press Enter...")
            return
        info = (u.username, u.email, u.is_active, u.is_admin)
    while True:
        _w = ui_width(self.session)
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner(f'Edit User #{uid}', _w))
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
            await self.session.write(menu_item(hk, lbl, _w) + '\r\n')
        await self.session.write('\r\n' + footer(_w) + '\r\n')
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
    from .ansi_ui import write_menu_art, banner, menu_item, footer, FG, RESET, prompt as _p, ui_width
    while True:
        with _app().app_context():
            boards = Board.query.order_by(Board.order, Board.name).all()
            b_list = [(b.id, b.name, b.description or '', b.is_active, b.posts.count()) for b in boards]
        _w = ui_width(self.session)
        _name_w = max(25, _w - 26)
        _desc_w = max(60, _w - 12)
        if not await write_menu_art(self.session, 'sysop_boards'):
            await self.session.write('\x1b[2J\x1b[H')
            await self.session.write(banner('Manage Boards', _w))
        for bid, name, desc, active, count in b_list:
            mark = f"{FG['grn']}*{RESET}" if active else f"{FG['red']}X{RESET}"
            await self.session.write(
                f"  [{mark}] {FG['wht']}{bid:<3}{RESET} "
                f"{FG['grn']}{name[:_name_w]:<{_name_w}}{RESET} "
                f"{FG['cyan']}({count:4d} threads){RESET}\r\n")
            if desc:
                await self.session.write(f"          {FG['dim']}{desc[:_desc_w]}{RESET}\r\n")
        await self.session.write('\r\n')
        await self.session.write(menu_item('N', 'New board', _w) + '\r\n')
        await self.session.write(menu_item('M', 'Bulletins', _w) + '\r\n')
        await self.session.write(menu_item('Q', 'Back', _w) + '\r\n')
        await self.session.write(f"  {FG['dim']}Or enter a board ID to edit{RESET}\r\n")
        await self.session.write('\r\n' + footer(_w) + '\r\n')
        choice = (await self.session.read_line(_p('Choice: ')) or '').strip()
        u = choice.upper()
        if u == 'Q' or not choice:
            return
        if u == 'M':
            await self.sysop_bulletins()
            continue
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


async def _sysop_bulletins(self):
    from anetbbs.models import db, Message as Bulletin
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            rows = Bulletin.query.order_by(Bulletin.created_at.desc()).limit(100).all()
            return [(b.id, b.title, b.is_pinned, b.created_at, b.expires_at) for b in rows]

    def render_row(idx, row, selected):
        bid, title, pinned, created, expires = row
        pin = f"{FG['yel']}PIN{RESET}" if pinned else '   '
        ts = fmt_eastern(created, '%Y-%m-%d', '?')
        return f"  {pin} {FG['wht']}{bid:>4}{RESET}  {ts}  {FG['grn']}{title[:50]}{RESET}"

    def render_hint_extra(sel, total):
        return (f"{FG['cyan']}P{RESET}=toggle pin  {FG['cyan']}X{RESET}=purge expired  "
                f"{FG['cyan']}D{RESET}=delete  {FG['cyan']}Q{RESET}=back")

    async def _toggle_pin(ui, row):
        bid = row[0]
        with _app().app_context():
            b = Bulletin.query.get(bid)
            if b:
                b.is_pinned = not b.is_pinned
                db.session.commit()

    async def _purge_expired(ui, row):
        with _app().app_context():
            n = Bulletin.query.filter(
                Bulletin.expires_at.isnot(None),
                Bulletin.expires_at < datetime.utcnow()).delete()
            db.session.commit()
        await ui.session.write(f"\r\n{FG['grn']}Purged {n} expired bulletin(s).{RESET}\r\n")
        await ui.session.read_line("Press Enter...")

    async def _delete(ui, row):
        bid, title = row[0], row[1]
        confirm = await ui.session.read_line(
            f"\r\n{FG['red']}Type DELETE to remove '{title[:40]}': {RESET}")
        if confirm == 'DELETE':
            with _app().app_context():
                b = Bulletin.query.get(bid)
                if b:
                    db.session.delete(b)
                    db.session.commit()

    await self._sysop_record_list(
        'Bulletins', _fetch_rows, render_row, render_hint_extra,
        actions={'P': _toggle_pin, 'X': _purge_expired, 'D': _delete},
        empty_msg='No bulletins yet.')
BBSMenuUI.sysop_bulletins = _sysop_bulletins


# ---------------------------------------------------------------------------
# Echomail/Hub — networks, areas, QWK node requests, bad areas
# ---------------------------------------------------------------------------

async def _sysop_echomail_networks(self):
    from anetbbs.models import db, EchomailNetwork
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            nets = EchomailNetwork.query.order_by(EchomailNetwork.name).all()
            return [(n.id, n.name, n.network_type, n.is_active) for n in nets]

    def render_row(idx, row, selected):
        nid, name, ntype, active = row
        mark = f"{FG['grn']}*{RESET}" if active else f"{FG['red']}X{RESET}"
        return f"  [{mark}] {FG['wht']}{nid:>3}{RESET}  {FG['grn']}{name[:30]:<30}{RESET} {FG['dim']}{ntype}{RESET}"

    def render_hint_extra(sel, total):
        return f"{FG['cyan']}T{RESET}=toggle active  {FG['cyan']}Enter{RESET}=areas  {FG['cyan']}Q{RESET}=back"

    async def _toggle(ui, row):
        nid = row[0]
        with _app().app_context():
            n = EchomailNetwork.query.get(nid)
            if n:
                n.is_active = not n.is_active
                db.session.commit()

    async def _open_areas(ui, row):
        await ui._sysop_echomail_areas(row[0], row[1])

    await self._sysop_record_list(
        'Echomail Networks', _fetch_rows, render_row, render_hint_extra,
        actions={'T': _toggle, 'ENTER': _open_areas},
        empty_msg='No echomail networks configured.')
BBSMenuUI.sysop_echomail_networks = _sysop_echomail_networks


async def _sysop_echomail_areas(self, network_id, network_name):
    from anetbbs.models import db, EchoArea
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            areas = (EchoArea.query.filter_by(network_id=network_id)
                     .order_by(EchoArea.tag).all())
            return [(a.id, a.tag, a.is_active, a.is_subscribed, a.total_messages)
                    for a in areas]

    def render_row(idx, row, selected):
        aid, tag, active, subbed, total = row
        a_mark = f"{FG['grn']}*{RESET}" if active else f"{FG['red']}X{RESET}"
        s_mark = f"{FG['cyan']}S{RESET}" if subbed else f"{FG['gry']}-{RESET}"
        return (f"  [{a_mark}{s_mark}] {FG['wht']}{aid:>4}{RESET}  "
                f"{FG['grn']}{tag[:35]:<35}{RESET} {FG['dim']}{total:>6} msgs{RESET}")

    def render_hint_extra(sel, total):
        return (f"{FG['cyan']}T{RESET}=toggle active  {FG['cyan']}S{RESET}=toggle subscribed  "
                f"{FG['cyan']}Q{RESET}=back")

    async def _toggle_active(ui, row):
        aid = row[0]
        with _app().app_context():
            a = EchoArea.query.get(aid)
            if a:
                a.is_active = not a.is_active
                db.session.commit()

    async def _toggle_sub(ui, row):
        aid = row[0]
        with _app().app_context():
            a = EchoArea.query.get(aid)
            if a:
                a.is_subscribed = not a.is_subscribed
                db.session.commit()

    await self._sysop_record_list(
        f'Areas: {network_name}', _fetch_rows, render_row, render_hint_extra,
        actions={'T': _toggle_active, 'S': _toggle_sub},
        empty_msg='No areas on this network.')
BBSMenuUI._sysop_echomail_areas = _sysop_echomail_areas


async def _sysop_qwk_requests(self):
    from anetbbs.models import db, QWKNodeRequest, QWKNode
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            reqs = (QWKNodeRequest.query.filter_by(status='pending')
                    .order_by(QWKNodeRequest.created_at.asc()).all())
            return [(r.id, r.bbs_name, r.packet_id, r.sysop_name or '',
                     r.email or '') for r in reqs]

    def render_row(idx, row, selected):
        rid, bbs, pid, sysop, email = row
        return (f"  {FG['wht']}{rid:>4}{RESET}  {FG['grn']}{bbs[:24]:<24}{RESET} "
                f"{FG['cyan']}{pid:<8}{RESET} {FG['dim']}{sysop[:20]}{RESET}")

    def render_hint_extra(sel, total):
        return f"{FG['cyan']}A{RESET}=approve  {FG['cyan']}X{RESET}=deny  {FG['cyan']}Q{RESET}=back"

    async def _approve(ui, row):
        import secrets, string
        rid = row[0]
        with _app().app_context():
            req = QWKNodeRequest.query.get(rid)
            if not req or req.status != 'pending':
                return
            pid = req.packet_id.upper()
            if QWKNode.query.filter_by(packet_id=pid).first():
                await ui.session.write(
                    f"\r\n{FG['red']}Packet ID {pid} already taken.{RESET}\r\n")
                await ui.session.read_line("Press Enter...")
                return
            alphabet = string.ascii_letters + string.digits
            password = ''.join(secrets.choice(alphabet) for _ in range(16))
            from anetbbs.models import _default_hub_identity_id
            node = QWKNode(packet_id=pid, name=req.bbs_name, sysop=req.sysop_name,
                           email=req.email, password=password, is_active=True,
                           hub_identity_id=req.hub_identity_id or _default_hub_identity_id(),
                           notes=f"Auto-created from node request #{req.id}.")
            db.session.add(node)
            db.session.flush()
            req.status = 'approved'
            req.reviewed_at = datetime.utcnow()
            req.reviewed_by = ui.session.user.get('username', 'sysop')
            req.generated_password = password
            req.node_id = node.id
            db.session.commit()
        await ui.session.write(f"\r\n{FG['grn']}Approved. QWK node {pid} created.{RESET}\r\n")
        await ui.session.read_line("Press Enter...")

    async def _deny(ui, row):
        rid, bbs = row[0], row[1]
        reason = (await ui.session.read_line(f"\r\nDeny reason for {bbs}: ") or '').strip()
        with _app().app_context():
            req = QWKNodeRequest.query.get(rid)
            if req and req.status == 'pending':
                req.status = 'denied'
                req.reviewed_at = datetime.utcnow()
                req.reviewed_by = ui.session.user.get('username', 'sysop')
                req.deny_reason = reason or None
                db.session.commit()

    await self._sysop_record_list(
        'QWK Node Requests', _fetch_rows, render_row, render_hint_extra,
        actions={'A': _approve, 'X': _deny},
        empty_msg='No pending QWK node requests.')
BBSMenuUI.sysop_qwk_requests = _sysop_qwk_requests


async def _sysop_bad_areas(self):
    from anetbbs.models import db, BadAreaLog
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            rows = (BadAreaLog.query.order_by(BadAreaLog.count.desc())
                    .limit(100).all())
            return [(r.id, r.tag, r.network_id, r.count, r.sample_from or '')
                    for r in rows]

    def render_row(idx, row, selected):
        rid, tag, net_id, count, sample = row
        return (f"  {FG['wht']}{rid:>4}{RESET}  {FG['yel']}{tag[:30]:<30}{RESET} "
                f"{FG['cyan']}x{count:<4}{RESET} {FG['dim']}{sample[:20]}{RESET}")

    def render_hint_extra(sel, total):
        return f"{FG['cyan']}D{RESET}=dismiss  {FG['cyan']}Q{RESET}=back"

    async def _dismiss(ui, row):
        rid = row[0]
        with _app().app_context():
            r = BadAreaLog.query.get(rid)
            if r:
                db.session.delete(r)
                db.session.commit()

    await self._sysop_record_list(
        'Bad Areas (unrecognized tags)', _fetch_rows, render_row, render_hint_extra,
        actions={'D': _dismiss},
        empty_msg='No bad-area reports.')
BBSMenuUI.sysop_bad_areas = _sysop_bad_areas


async def _sysop_echomail(self):
    """Echomail/Hub sub-menu: Networks/Areas, QWK Node Requests, Bad Areas.

    Deliberately out of scope: full NetworkJoinRequest applicant approval
    (creates multiple node types + emails credentials -- web-only) and
    BinkP/QWK hub peer node CRUD (many fields, edit on web).

    Also deliberately out of scope, by the same design as the two
    items above: multi-hub-identity awareness. QWK Node Requests here
    always targets the install's DEFAULT hub identity (both this
    screen's approval and the terminal application wizard,
    _apply_qwk_node) -- a sysop running more than one hub identity
    manages the extra ones only through the web admin (Hub Management ->
    Hub Identities). See models.HubIdentity.
    """
    from .ansi_ui import banner, FG, RESET, ui_width
    # No hotkey letters shown/handled here -- _rss_lightbar reserves 'Q'
    # itself as the universal quit key, so a displayed "[Q]" hotkey would
    # be misleading (pressing Q always exits, never opens a row). Enter
    # + arrow navigation only, same convention as _ebook_pick_from_list.
    qwk_requests_label = 'QWK Node Requests'
    try:
        with _app().app_context():
            from anetbbs.models import HubIdentity
            if HubIdentity.query.count() > 1:
                qwk_requests_label = 'QWK Node Requests (default hub identity only)'
    except Exception:
        pass
    rows = [
        ('Networks / Areas', self.sysop_echomail_networks),
        (qwk_requests_label, self.sysop_qwk_requests),
        ('Bad Areas', self.sysop_bad_areas),
    ]

    async def render_header():
        await self.session.write(banner('Echomail / Hub', ui_width(self.session)))

    def render_row(idx, row, selected):
        label, _fn = row
        return f"  {FG['grn']}{idx+1}. {label}{RESET}"

    def render_hint(sel, total):
        return (f"  {FG['cyan']}{sel+1}/{total}{RESET} {FG['cyan']}Enter{RESET}=open  "
                f"{FG['cyan']}Q{RESET}=back")

    while True:
        result = await self._rss_lightbar(rows, render_header, render_row, render_hint)
        if result[0] == 'quit':
            return
        elif result[0] == 'enter':
            await rows[result[1]][1]()
BBSMenuUI.sysop_echomail = _sysop_echomail


async def _sysop_games(self):
    """Active game session list (disconnect) + TW2002 universe reset.

    Door executable-path CRUD (Game.add/edit) is left web-only -- many
    fields, low sysop-facing value from a terminal.
    """
    from anetbbs.models import db, GameSession, Game
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            sessions = (GameSession.query.filter_by(status='active')
                       .order_by(GameSession.started_at.desc()).all())
            out = []
            for s in sessions:
                game = Game.query.get(s.game_id)
                out.append((s.id, game.name if game else '?', s.user_id,
                            s.node_number, s.started_at))
            return out

    def render_row(idx, row, selected):
        sid, gname, uid, node, started = row
        ts = fmt_eastern(started, '%H:%M', '?')
        return (f"  {FG['wht']}{sid:>4}{RESET}  {FG['grn']}{gname[:24]:<24}{RESET} "
                f"node {FG['cyan']}{node}{RESET}  since {FG['dim']}{ts}{RESET}")

    def render_hint_extra(sel, total):
        return (f"{FG['cyan']}D{RESET}=disconnect  {FG['cyan']}R{RESET}=reset TW2002 universe  "
                f"{FG['cyan']}Q{RESET}=back")

    async def _disconnect(ui, row):
        sid = row[0]
        from anetbbs.games.door_runner import terminate_session
        terminate_session(sid)
        with _app().app_context():
            gs = GameSession.query.get(sid)
            if gs:
                gs.status = 'completed'
                gs.ended_at = datetime.utcnow()
                db.session.commit()
        await ui.session.write(f"\r\n{FG['grn']}Session terminated.{RESET}\r\n")
        await ui.session.read_line("Press Enter...")

    async def _reset_tw2(ui, row):
        confirm = await ui.session.read_line(
            f"\r\n{FG['red']}Type DELETE to wipe the TW2002 universe: {RESET}")
        if confirm != 'DELETE':
            return
        import os as _os
        from anetbbs.web.games_admin import _tw2_db_dir, _tw2_legacy_db_dir, _tw2_game_ini
        with _app().app_context():
            wiped = []
            for d in (_tw2_db_dir(), _tw2_legacy_db_dir()):
                if not _os.path.isdir(d):
                    continue
                for f in _os.listdir(d):
                    if f.endswith(('.json', '.json.tmp')):
                        try:
                            _os.remove(_os.path.join(d, f))
                            wiped.append(f)
                        except OSError:
                            pass
            ini = _tw2_game_ini()
            if _os.path.isfile(ini):
                try:
                    _os.remove(ini)
                    wiped.append(ini)
                except OSError:
                    pass
        await ui.session.write(
            f"\r\n{FG['grn']}Universe reset -- {len(wiped)} file(s) removed.{RESET}\r\n"
            if wiped else f"\r\n{FG['dim']}Nothing to reset.{RESET}\r\n")
        await ui.session.read_line("Press Enter...")

    # TW2002 reset doesn't depend on any session being selected (or even
    # existing), so it can't be a row-relative _sysop_record_list action
    # -- that helper's empty-state path returns immediately with no
    # chance to press a hotkey when there are zero rows, which would
    # make reset unreachable on an install with nobody currently playing.
    # Offer it as its own prompt every time this screen is entered.
    resp = (await self.session.read_line(
        f"\r\n{FG['cyan']}R{RESET}=reset TW2002 universe, "
        f"{FG['cyan']}Enter{RESET}=view active sessions: ") or '').strip().upper()
    if resp == 'R':
        await _reset_tw2(self, None)
        return
    await self._sysop_record_list(
        'Active Game Sessions', _fetch_rows, render_row, render_hint_extra,
        actions={'D': _disconnect, 'R': _reset_tw2},
        empty_msg='No active game sessions.')
BBSMenuUI.sysop_games = _sysop_games


async def _sysop_wall(self):
    from anetbbs.models import db, WallPost
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            posts = (WallPost.query.filter_by(is_deleted=False)
                    .order_by(WallPost.created_at.desc()).limit(100).all())
            return [(p.id, p.username, p.line1, p.origin_bbs) for p in posts]

    def render_row(idx, row, selected):
        pid, uname, line1, origin = row
        tag = f" {FG['cyan']}[{origin}]{RESET}" if origin else ''
        clean = re.sub(r'\|\d{2}', '', line1 or '')
        return f"  {FG['wht']}{pid:>4}{RESET}  {FG['grn']}{uname[:15]:<15}{RESET}{tag} {clean[:40]}"

    def render_hint_extra(sel, total):
        return f"{FG['cyan']}D{RESET}=delete  {FG['cyan']}Q{RESET}=back"

    async def _delete(ui, row):
        pid = row[0]
        with _app().app_context():
            p = WallPost.query.get(pid)
            if p:
                p.is_deleted = True
                db.session.commit()

    await self._sysop_record_list(
        'Wall Moderation', _fetch_rows, render_row, render_hint_extra,
        actions={'D': _delete},
        empty_msg='No wall posts.')
BBSMenuUI.sysop_wall = _sysop_wall


async def _sysop_file_queue(self):
    from anetbbs.models import db, FileQueueEntry, FileArea
    from .ansi_ui import FG, RESET
    import os as _os

    def _fetch_rows():
        with _app().app_context():
            rows = (FileQueueEntry.query.filter_by(status='pending')
                    .order_by(FileQueueEntry.created_at.asc()).all())
            return [(e.id, e.filename, e.file_area_id, e.size_bytes or 0,
                     e.user_id) for e in rows]

    def render_row(idx, row, selected):
        eid, fname, area_id, size, uid = row
        kb = (size or 0) // 1024
        return f"  {FG['wht']}{eid:>4}{RESET}  {FG['grn']}{fname[:40]:<40}{RESET} {FG['dim']}{kb:>6} KB{RESET}"

    def render_hint_extra(sel, total):
        return f"{FG['cyan']}A{RESET}=approve  {FG['cyan']}X{RESET}=reject  {FG['cyan']}Q{RESET}=back"

    async def _approve(ui, row):
        eid = row[0]
        with _app().app_context():
            entry = FileQueueEntry.query.get(eid)
            if not entry or entry.status != 'pending':
                return
            area = FileArea.query.get(entry.file_area_id)
            if not area or not area.storage_path:
                await ui.session.write(f"\r\n{FG['red']}Area has no storage path.{RESET}\r\n")
                await ui.session.read_line("Press Enter...")
                return
            try:
                _os.makedirs(area.storage_path, exist_ok=True)
                safe_name = _os.path.basename(entry.filename)
                dest = _os.path.join(area.storage_path, safe_name)
                if _os.path.exists(dest):
                    base, ext = _os.path.splitext(safe_name)
                    dest = _os.path.join(area.storage_path, f'{base}-{entry.id}{ext}')
                _os.rename(entry.quarantine_path, dest)
            except OSError as exc:
                await ui.session.write(f"\r\n{FG['red']}Approve failed: {exc}{RESET}\r\n")
                await ui.session.read_line("Press Enter...")
                return
            entry.status = 'approved'
            entry.reviewed_by_id = ui.session.user.get('id')
            entry.reviewed_at = datetime.utcnow()
            db.session.commit()
            if area.network_id is not None:
                try:
                    from anetbbs.echomail.tic import hatch_local_file
                    hatch_local_file(area, dest, safe_name, entry.description or '')
                except Exception:
                    logger.exception('hatch_local_file failed for %s', safe_name)
        await ui.session.write(f"\r\n{FG['grn']}Approved.{RESET}\r\n")
        await ui.session.read_line("Press Enter...")

    async def _reject(ui, row):
        eid = row[0]
        reason = (await ui.session.read_line("\r\nReject reason: ") or '').strip()
        with _app().app_context():
            entry = FileQueueEntry.query.get(eid)
            if not entry or entry.status != 'pending':
                return
            try:
                if entry.quarantine_path and _os.path.isfile(entry.quarantine_path):
                    _os.remove(entry.quarantine_path)
            except OSError:
                logger.exception('Failed to delete quarantine file')
            entry.status = 'rejected'
            entry.rejection_reason = reason or None
            entry.reviewed_by_id = ui.session.user.get('id')
            entry.reviewed_at = datetime.utcnow()
            db.session.commit()

    await self._sysop_record_list(
        'File Upload Queue', _fetch_rows, render_row, render_hint_extra,
        actions={'A': _approve, 'X': _reject},
        empty_msg='No files pending review.')
BBSMenuUI.sysop_file_queue = _sysop_file_queue


def _schedule_to_text(sched):
    kind = (sched or {}).get('kind', 'daily')
    if kind == 'daily':
        return f"daily {sched.get('time', '03:00')}"
    if kind == 'hourly':
        return f"hourly {int(sched.get('minute', 0)):02d}"
    if kind == 'weekly':
        return f"weekly {int(sched.get('day', 6))} {sched.get('time', '04:00')}"
    if kind == 'interval':
        return f"interval {sched.get('minutes', 60)}"
    return f"unknown ({kind})"


def _parse_schedule_text(text):
    """Parse 'daily HH:MM' / 'hourly MM' / 'weekly D HH:MM' / 'interval N'.
    Returns (schedule_dict, error_or_None)."""
    parts = (text or '').strip().split()
    if not parts:
        return None, 'empty'
    kind = parts[0].lower()
    try:
        if kind == 'daily' and len(parts) == 2:
            return {'kind': 'daily', 'time': parts[1]}, None
        if kind == 'hourly' and len(parts) == 2:
            m = int(parts[1])
            return ({'kind': 'hourly', 'minute': m}, None) if 0 <= m < 60 else (None, 'minute must be 0..59')
        if kind == 'weekly' and len(parts) == 3:
            d = int(parts[1])
            return ({'kind': 'weekly', 'day': d, 'time': parts[2]}, None) if 0 <= d < 7 else (None, 'day must be 0..6 (0=Mon)')
        if kind == 'interval' and len(parts) == 2:
            n = int(parts[1])
            return ({'kind': 'interval', 'minutes': n}, None) if n >= 1 else (None, 'minutes must be >= 1')
    except ValueError:
        return None, 'bad number'
    return None, "format: 'daily HH:MM' / 'hourly MM' / 'weekly D HH:MM' / 'interval N'"


async def _sysop_events(self):
    """ScheduledEvent CRUD -- cron-style maintenance jobs (ScheduledEvent),
    not the user-facing CalendarEvent (that stays web-only)."""
    import json as _json
    from anetbbs.models import db, ScheduledEvent
    from anetbbs.events.handlers import REGISTRY, HANDLER_META
    from anetbbs.events.runner import fire
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            evs = ScheduledEvent.query.order_by(ScheduledEvent.name).all()
            return [(e.id, e.name, e.handler_key, e.is_enabled,
                     e.last_status or '-', e.schedule_json) for e in evs]

    def render_row(idx, row, selected):
        eid, name, hkey, enabled, status, _sched = row
        mark = f"{FG['grn']}*{RESET}" if enabled else f"{FG['red']}X{RESET}"
        st_color = FG['grn'] if status == 'ok' else (FG['red'] if status == 'fail' else FG['dim'])
        label = HANDLER_META.get(hkey, (hkey, ''))[0]
        return (f"  [{mark}] {FG['wht']}{eid:>3}{RESET}  {FG['grn']}{name[:26]:<26}{RESET} "
                f"{FG['cyan']}{label[:22]:<22}{RESET} {st_color}{status}{RESET}")

    def render_hint_extra(sel, total):
        return (f"{FG['cyan']}T{RESET}=toggle  {FG['cyan']}G{RESET}=run now  "
                f"{FG['cyan']}N{RESET}=new  {FG['cyan']}X{RESET}=delete  "
                f"{FG['cyan']}Enter{RESET}=edit  {FG['cyan']}Q{RESET}=back")

    async def _toggle(ui, row):
        eid = row[0]
        with _app().app_context():
            e = ScheduledEvent.query.get(eid)
            if e:
                e.is_enabled = not e.is_enabled
                db.session.commit()

    async def _run_now(ui, row):
        eid = row[0]
        app = _app()
        with app.app_context():
            ok, out = fire(app, eid)
        await ui.session.write(
            f"\r\n{FG['grn'] if ok else FG['red']}{'OK' if ok else 'FAILED'}{RESET}: "
            f"{(out or '')[:300]}\r\n")
        await ui.session.read_line("Press Enter...")

    async def _delete(ui, row):
        eid, name = row[0], row[1]
        confirm = await ui.session.read_line(
            f"\r\n{FG['red']}Type DELETE to remove '{name[:30]}': {RESET}")
        if confirm == 'DELETE':
            with _app().app_context():
                e = ScheduledEvent.query.get(eid)
                if e:
                    db.session.delete(e)
                    db.session.commit()

    async def _pick_handler(ui):
        keys = list(HANDLER_META.keys())
        rows = [(k, HANDLER_META[k][0], HANDLER_META[k][1]) for k in keys]

        async def render_header():
            from .ansi_ui import banner, ui_width
            await ui.session.write(banner('Pick a Handler', ui_width(ui.session)))

        def render_row2(idx, row, selected):
            k, label, desc = row
            return f"  {FG['grn']}{label[:28]:<28}{RESET} {FG['dim']}{desc[:40]}{RESET}"

        def render_hint2(sel, total):
            return f"  {FG['cyan']}{sel+1}/{total}{RESET} {FG['cyan']}Enter{RESET}=pick  {FG['cyan']}Q{RESET}=cancel"

        result = await ui._rss_lightbar(rows, render_header, render_row2, render_hint2)
        if result[0] == 'enter':
            return rows[result[1]][0]
        return None

    async def _new_or_edit(ui, row):
        eid = row[0] if row else None
        with _app().app_context():
            e = ScheduledEvent.query.get(eid) if eid else None
            cur_name = e.name if e else ''
            cur_hkey = e.handler_key if e else ''
            cur_sched = _json.loads(e.schedule_json) if e else {'kind': 'daily', 'time': '03:00'}
            cur_params = e.params_json if e else '{}'

        name = (await ui.session.read_line(
            f"Name [{cur_name}]: ") or '').strip() or cur_name
        if not name:
            return
        handler_key = await _pick_handler(ui)
        if not handler_key:
            handler_key = cur_hkey
        if not handler_key or handler_key not in REGISTRY:
            await ui.session.write(f"\r\n{FG['red']}A handler is required.{RESET}\r\n")
            await ui.session.read_line("Press Enter...")
            return
        sched_text = (await ui.session.read_line(
            f"Schedule [{_schedule_to_text(cur_sched)}]: ") or '').strip()
        sched, err = _parse_schedule_text(sched_text) if sched_text else (cur_sched, None)
        if err:
            await ui.session.write(f"\r\n{FG['red']}{err}{RESET}\r\n")
            await ui.session.read_line("Press Enter...")
            return
        params_text = (await ui.session.read_line(
            f"Params JSON [{cur_params}]: ") or '').strip() or cur_params
        try:
            parsed = _json.loads(params_text or '{}')
            if not isinstance(parsed, dict):
                raise ValueError
        except (ValueError, _json.JSONDecodeError):
            await ui.session.write(f"\r\n{FG['red']}Params must be a JSON object.{RESET}\r\n")
            await ui.session.read_line("Press Enter...")
            return
        with _app().app_context():
            if eid:
                e = ScheduledEvent.query.get(eid)
            else:
                e = ScheduledEvent(is_enabled=True)
                db.session.add(e)
            e.name = name[:120]
            e.handler_key = handler_key
            e.schedule_json = _json.dumps(sched)
            e.params_json = _json.dumps(parsed)
            db.session.commit()
        await ui.session.write(f"\r\n{FG['grn']}Saved.{RESET}\r\n")
        await ui.session.read_line("Press Enter...")

    await self._sysop_record_list(
        'Scheduled Events', _fetch_rows, render_row, render_hint_extra,
        actions={'T': _toggle, 'G': _run_now, 'X': _delete,
                 'N': lambda ui, row: _new_or_edit(ui, None),
                 'ENTER': _new_or_edit},
        empty_msg='No scheduled events. Press N to create one.')
BBSMenuUI.sysop_events = _sysop_events


async def _sysop_rss_admin(self):
    from anetbbs.models import db, RssFeed
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            feeds = RssFeed.query.order_by(RssFeed.sort_order, RssFeed.name).all()
            return [(f.id, f.name, f.url, f.category, f.is_active) for f in feeds]

    def render_row(idx, row, selected):
        fid, name, url, cat, active = row
        mark = f"{FG['grn']}*{RESET}" if active else f"{FG['red']}X{RESET}"
        return f"  [{mark}] {FG['wht']}{fid:>3}{RESET}  {FG['grn']}{name[:28]:<28}{RESET} {FG['dim']}{cat}{RESET}"

    def render_hint_extra(sel, total):
        return (f"{FG['cyan']}T{RESET}=toggle  {FG['cyan']}F{RESET}=refresh now  "
                f"{FG['cyan']}N{RESET}=new  {FG['cyan']}X{RESET}=delete  "
                f"{FG['cyan']}Enter{RESET}=edit  {FG['cyan']}Q{RESET}=back")

    async def _toggle(ui, row):
        fid = row[0]
        with _app().app_context():
            f = RssFeed.query.get(fid)
            if f:
                f.is_active = not f.is_active
                db.session.commit()

    async def _refresh(ui, row):
        fid = row[0]
        # _import_one_feed() pushes its own app_context internally --
        # don't wrap it in another one here.
        from anetbbs.rss.poller import _import_one_feed
        try:
            n = _import_one_feed(_app(), fid)
        except Exception as exc:
            await ui.session.write(f"\r\n{FG['red']}Refresh failed: {exc}{RESET}\r\n")
            await ui.session.read_line("Press Enter...")
            return
        await ui.session.write(f"\r\n{FG['grn']}{n} new item(s) fetched.{RESET}\r\n")
        await ui.session.read_line("Press Enter...")

    async def _delete(ui, row):
        fid, name = row[0], row[1]
        confirm = await ui.session.read_line(
            f"\r\n{FG['red']}Type DELETE to remove '{name[:30]}': {RESET}")
        if confirm == 'DELETE':
            with _app().app_context():
                f = RssFeed.query.get(fid)
                if f:
                    db.session.delete(f)
                    db.session.commit()

    async def _new_or_edit(ui, row):
        fid = row[0] if row else None
        with _app().app_context():
            f = RssFeed.query.get(fid) if fid else None
            cur_name, cur_url = (f.name, f.url) if f else ('', '')
            cur_cat = f.category if f else 'general'

        name = (await ui.session.read_line(f"Name [{cur_name}]: ") or '').strip() or cur_name
        url = (await ui.session.read_line(f"Feed URL [{cur_url}]: ") or '').strip() or cur_url
        if not name or not url:
            await ui.session.write(f"\r\n{FG['red']}Name and URL are required.{RESET}\r\n")
            await ui.session.read_line("Press Enter...")
            return
        cat = (await ui.session.read_line(f"Category [{cur_cat}]: ") or '').strip() or cur_cat
        with _app().app_context():
            if not fid and RssFeed.query.filter_by(url=url).first():
                await ui.session.write(f"\r\n{FG['red']}That URL is already subscribed.{RESET}\r\n")
                await ui.session.read_line("Press Enter...")
                return
            if fid:
                f = RssFeed.query.get(fid)
            else:
                f = RssFeed(url=url, is_active=True)
                db.session.add(f)
            f.name = name[:120]
            f.url = url[:500]
            f.category = cat[:60]
            db.session.commit()
            new_id = f.id
        await ui.session.write(f"\r\n{FG['grn']}Saved.{RESET}\r\n")
        try:
            from anetbbs.rss.poller import _import_one_feed
            _import_one_feed(_app(), new_id)
        except Exception:
            pass
        await ui.session.read_line("Press Enter...")

    await self._sysop_record_list(
        'RSS Feeds', _fetch_rows, render_row, render_hint_extra,
        actions={'T': _toggle, 'F': _refresh, 'X': _delete,
                 'N': lambda ui, row: _new_or_edit(ui, None),
                 'ENTER': _new_or_edit},
        empty_msg='No RSS feeds yet. Press N to add one.')
BBSMenuUI.sysop_rss_admin = _sysop_rss_admin


async def _sysop_login_modules(self):
    from anetbbs.models import db, LoginModule
    from .ansi_ui import FG, RESET

    _MODULE_TYPES = ('wall', 'ansi', 'shell', 'door_native', 'door_python')

    def _fetch_rows():
        with _app().app_context():
            mods = LoginModule.query.order_by(LoginModule.sort_order).all()
            return [(m.id, m.name, m.event_type, m.module_type, m.is_active)
                    for m in mods]

    def render_row(idx, row, selected):
        mid, name, ev, mtype, active = row
        mark = f"{FG['grn']}*{RESET}" if active else f"{FG['red']}X{RESET}"
        return (f"  [{mark}] {FG['wht']}{mid:>3}{RESET}  {FG['grn']}{name[:24]:<24}{RESET} "
                f"{FG['cyan']}{ev:<7}{RESET} {FG['dim']}{mtype}{RESET}")

    def render_hint_extra(sel, total):
        return (f"{FG['cyan']}T{RESET}=toggle  {FG['cyan']}N{RESET}=new  "
                f"{FG['cyan']}X{RESET}=delete  {FG['cyan']}Enter{RESET}=edit  "
                f"{FG['cyan']}Q{RESET}=back")

    async def _toggle(ui, row):
        mid = row[0]
        with _app().app_context():
            m = LoginModule.query.get(mid)
            if m:
                m.is_active = not m.is_active
                db.session.commit()

    async def _delete(ui, row):
        mid, name = row[0], row[1]
        confirm = await ui.session.read_line(
            f"\r\n{FG['red']}Type DELETE to remove '{name[:30]}': {RESET}")
        if confirm == 'DELETE':
            with _app().app_context():
                m = LoginModule.query.get(mid)
                if m:
                    db.session.delete(m)
                    db.session.commit()

    async def _new_or_edit(ui, row):
        mid = row[0] if row else None
        with _app().app_context():
            m = LoginModule.query.get(mid) if mid else None
            cur_name = m.name if m else ''
            cur_ev = m.event_type if m else 'logon'
            cur_mtype = m.module_type if m else 'wall'
            cur_params = m.params_json if m else '{}'

        name = (await ui.session.read_line(f"Name [{cur_name}]: ") or '').strip() or cur_name
        if not name:
            return
        ev = (await ui.session.read_line(
            f"Event type [logon/logoff] [{cur_ev}]: ") or '').strip().lower() or cur_ev
        if ev not in ('logon', 'logoff'):
            await ui.session.write(f"\r\n{FG['red']}Must be logon or logoff.{RESET}\r\n")
            await ui.session.read_line("Press Enter...")
            return
        mtype = (await ui.session.read_line(
            f"Module type [{'/'.join(_MODULE_TYPES)}] [{cur_mtype}]: ")
            or '').strip().lower() or cur_mtype
        if mtype not in _MODULE_TYPES:
            await ui.session.write(f"\r\n{FG['red']}Unknown module type.{RESET}\r\n")
            await ui.session.read_line("Press Enter...")
            return
        params = (await ui.session.read_line(
            f"Params JSON [{cur_params}]: ") or '').strip() or cur_params
        import json as _json
        try:
            parsed = _json.loads(params or '{}')
            if not isinstance(parsed, dict):
                raise ValueError
        except (ValueError, _json.JSONDecodeError):
            await ui.session.write(f"\r\n{FG['red']}Params must be a JSON object.{RESET}\r\n")
            await ui.session.read_line("Press Enter...")
            return
        with _app().app_context():
            if mid:
                m = LoginModule.query.get(mid)
            else:
                m = LoginModule(is_active=True)
                db.session.add(m)
            m.name = name[:100]
            m.event_type = ev
            m.module_type = mtype
            m.params_json = _json.dumps(parsed)
            db.session.commit()
        await ui.session.write(f"\r\n{FG['grn']}Saved.{RESET}\r\n")
        await ui.session.read_line("Press Enter...")

    await self._sysop_record_list(
        'Login/Logoff Modules', _fetch_rows, render_row, render_hint_extra,
        actions={'T': _toggle, 'X': _delete,
                 'N': lambda ui, row: _new_or_edit(ui, None),
                 'ENTER': _new_or_edit},
        empty_msg='No login/logoff modules yet. Press N to add one.')
BBSMenuUI.sysop_login_modules = _sysop_login_modules


_WEBHOOK_EVENTS = ('shout', 'post', 'bulletin', 'login', 'achievement',
                   'broadcast', 'sysop_page', 'echomail')


async def _sysop_webhooks(self):
    from anetbbs.models import db, Webhook
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            hooks = Webhook.query.order_by(Webhook.name).all()
            return [(w.id, w.name, w.event, w.url, w.is_active, w.last_status)
                    for w in hooks]

    def render_row(idx, row, selected):
        wid, name, event, url, active, last_status = row
        mark = f"{FG['grn']}*{RESET}" if active else f"{FG['red']}X{RESET}"
        st = f"{FG['dim']}{last_status or '-'}{RESET}"
        return (f"  [{mark}] {FG['wht']}{wid:>3}{RESET}  {FG['grn']}{name[:20]:<20}{RESET} "
                f"{FG['cyan']}{event:<11}{RESET} {st}")

    def render_hint_extra(sel, total):
        return (f"{FG['cyan']}T{RESET}=toggle  {FG['cyan']}N{RESET}=new  "
                f"{FG['cyan']}X{RESET}=delete  {FG['cyan']}Enter{RESET}=edit  "
                f"{FG['cyan']}Q{RESET}=back")

    async def _toggle(ui, row):
        wid = row[0]
        with _app().app_context():
            w = Webhook.query.get(wid)
            if w:
                w.is_active = not w.is_active
                db.session.commit()

    async def _delete(ui, row):
        wid, name = row[0], row[1]
        confirm = await ui.session.read_line(
            f"\r\n{FG['red']}Type DELETE to remove '{name[:30]}': {RESET}")
        if confirm == 'DELETE':
            with _app().app_context():
                w = Webhook.query.get(wid)
                if w:
                    db.session.delete(w)
                    db.session.commit()

    async def _new_or_edit(ui, row):
        wid = row[0] if row else None
        with _app().app_context():
            w = Webhook.query.get(wid) if wid else None
            cur_name = w.name if w else ''
            cur_url = w.url if w else ''
            cur_event = w.event if w else 'shout'
            cur_secret = w.secret if w else ''

        name = (await ui.session.read_line(f"Name [{cur_name}]: ") or '').strip() or cur_name
        url = (await ui.session.read_line(f"POST URL [{cur_url}]: ") or '').strip() or cur_url
        if not name or not url:
            await ui.session.write(f"\r\n{FG['red']}Name and URL are required.{RESET}\r\n")
            await ui.session.read_line("Press Enter...")
            return
        event = (await ui.session.read_line(
            f"Event [{'/'.join(_WEBHOOK_EVENTS)}] [{cur_event}]: ")
            or '').strip().lower() or cur_event
        if event not in _WEBHOOK_EVENTS:
            await ui.session.write(f"\r\n{FG['red']}Unknown event type.{RESET}\r\n")
            await ui.session.read_line("Press Enter...")
            return
        secret = (await ui.session.read_line(
            "Bearer secret (blank = keep current): ") or '').strip() or cur_secret
        with _app().app_context():
            if wid:
                w = Webhook.query.get(wid)
            else:
                w = Webhook(is_active=True)
                db.session.add(w)
            w.name = name[:120]
            w.url = url[:500]
            w.event = event
            w.secret = secret[:120] if secret else None
            db.session.commit()
        await ui.session.write(f"\r\n{FG['grn']}Saved.{RESET}\r\n")
        await ui.session.read_line("Press Enter...")

    await self._sysop_record_list(
        'Webhooks', _fetch_rows, render_row, render_hint_extra,
        actions={'T': _toggle, 'X': _delete,
                 'N': lambda ui, row: _new_or_edit(ui, None),
                 'ENTER': _new_or_edit},
        empty_msg='No webhooks configured. Press N to add one.')
BBSMenuUI.sysop_webhooks = _sysop_webhooks


async def _sysop_broadcast_compose(self):
    from anetbbs.models import db, SysopBroadcast
    from .ansi_ui import banner, FG, RESET, ui_width
    _w = ui_width(self.session)
    await self.session.write('\x1b[2J\x1b[H')
    await self.session.write(banner('Sysop Broadcast', _w))
    text = (await self.session.read_line(
        f"  {FG['cyan']}One-line broadcast:{RESET} ") or '').strip()
    if not text:
        return
    ttl = (await self.session.read_line(
        f"  {FG['cyan']}Expires in N minutes (blank = never):{RESET} ") or '').strip()
    expires = None
    if ttl:
        try:
            expires = datetime.utcnow() + timedelta(minutes=int(ttl))
        except ValueError:
            pass
    with _app().app_context():
        bcast = SysopBroadcast(sender_id=self.session.user.get('id'),
                               text=text[:2000], expires_at=expires)
        db.session.add(bcast)
        db.session.commit()
        bcast_id, created = bcast.id, bcast.created_at
    # Live push to web sysop tabs, mirroring admin.py:broadcast() -- only
    # actually delivers if this process also has web_app/socketio loaded,
    # same guard sysop_paging.py uses.
    try:
        import sys as _sys
        if 'anetbbs.web_app' in _sys.modules:
            socketio = _sys.modules['anetbbs.web_app'].socketio
            socketio.emit('sysop_broadcast', {
                'id': bcast_id, 'sender': self.session.user.get('username'),
                'text': text, 'when': created.isoformat() + 'Z'}, namespace='/')
    except Exception:
        pass
    await self.session.write(f"\r\n{FG['grn']}Broadcast sent.{RESET}\r\n")
    await self.session.read_line("Press Enter...")
BBSMenuUI.sysop_broadcast_compose = _sysop_broadcast_compose


async def _sysop_motd(self):
    from anetbbs.models import db, MotdEntry
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            rows = MotdEntry.query.order_by(MotdEntry.id).all()
            return [(m.id, m.text, m.weight, m.is_active) for m in rows]

    def render_row(idx, row, selected):
        mid, text, weight, active = row
        mark = f"{FG['grn']}*{RESET}" if active else f"{FG['red']}X{RESET}"
        clean = (text or '').replace('\n', ' ')
        return f"  [{mark}] {FG['wht']}{mid:>3}{RESET}  w{weight}  {FG['grn']}{clean[:45]}{RESET}"

    def render_hint_extra(sel, total):
        return (f"{FG['cyan']}T{RESET}=toggle  {FG['cyan']}N{RESET}=new  "
                f"{FG['cyan']}X{RESET}=delete  {FG['cyan']}Q{RESET}=back")

    async def _toggle(ui, row):
        mid = row[0]
        with _app().app_context():
            m = MotdEntry.query.get(mid)
            if m:
                m.is_active = not m.is_active
                db.session.commit()

    async def _delete(ui, row):
        mid = row[0]
        with _app().app_context():
            m = MotdEntry.query.get(mid)
            if m:
                db.session.delete(m)
                db.session.commit()

    async def _new(ui, row):
        text = (await ui.session.read_line("New MOTD text: ") or '').strip()
        if not text:
            return
        with _app().app_context():
            db.session.add(MotdEntry(text=text[:2000], weight=1, is_active=True))
            db.session.commit()

    await self._sysop_record_list(
        'Message of the Day', _fetch_rows, render_row, render_hint_extra,
        actions={'T': _toggle, 'X': _delete,
                 'N': lambda ui, row: _new(ui, None)},
        empty_msg='No MOTD entries yet. Press N to add one.')
BBSMenuUI.sysop_motd = _sysop_motd


async def _sysop_pages(self):
    from anetbbs.models import db, SysopPage
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            rows = (SysopPage.query.order_by(SysopPage.created_at.desc())
                   .limit(100).all())
            return [(p.id, p.user_id, p.service, p.message or '', p.answered,
                     p.created_at) for p in rows]

    def render_row(idx, row, selected):
        pid, uid, service, msg, answered, created = row
        mark = f"{FG['dim']}done{RESET}" if answered else f"{FG['yel']}NEW{RESET}"
        ts = fmt_eastern(created, '%m-%d %H:%M', '?')
        clean = (msg or '').replace('\n', ' ')
        return f"  [{mark}] {FG['wht']}{pid:>4}{RESET}  {ts}  {FG['grn']}{clean[:40]}{RESET}"

    def render_hint_extra(sel, total):
        return (f"{FG['cyan']}A{RESET}=mark answered  {FG['cyan']}R{RESET}=reply  "
                f"{FG['cyan']}Q{RESET}=back")

    async def _mark_answered(ui, row):
        pid = row[0]
        with _app().app_context():
            p = SysopPage.query.get(pid)
            if p:
                p.answered = True
                p.answered_at = datetime.utcnow()
                db.session.commit()

    async def _reply(ui, row):
        pid, uid = row[0], row[1]
        if not uid:
            await ui.session.write(f"\r\n{FG['red']}No user to reply to.{RESET}\r\n")
            await ui.session.read_line("Press Enter...")
            return
        text = (await ui.session.read_line("Reply: ") or '').strip()
        if not text:
            return
        from .sysop_paging import push_message
        push_message(uid, ui.session.user.get('username') or 'sysop', text)
        with _app().app_context():
            p = SysopPage.query.get(pid)
            if p:
                p.answered = True
                p.answered_at = datetime.utcnow()
                db.session.commit()
        await ui.session.write(f"\r\n{FG['grn']}Reply queued.{RESET}\r\n")
        await ui.session.read_line("Press Enter...")

    await self._sysop_record_list(
        'Sysop Pages', _fetch_rows, render_row, render_hint_extra,
        actions={'A': _mark_answered, 'R': _reply},
        empty_msg='No sysop pages.')
BBSMenuUI.sysop_pages = _sysop_pages


async def _sysop_notifications(self):
    """Notifications sub-menu: Webhooks, Broadcast, MOTD, Sysop Pages."""
    from .ansi_ui import banner, FG, RESET, ui_width
    rows = [
        ('Webhooks',            self.sysop_webhooks),
        ('Send Broadcast',      self.sysop_broadcast_compose),
        ('Message of the Day',  self.sysop_motd),
        ('Sysop Pages',         self.sysop_pages),
    ]

    async def render_header():
        await self.session.write(banner('Notifications', ui_width(self.session)))

    def render_row(idx, row, selected):
        label, _fn = row
        return f"  {FG['grn']}{idx+1}. {label}{RESET}"

    def render_hint(sel, total):
        return (f"  {FG['cyan']}{sel+1}/{total}{RESET} {FG['cyan']}Enter{RESET}=open  "
                f"{FG['cyan']}Q{RESET}=back")

    while True:
        result = await self._rss_lightbar(rows, render_header, render_row, render_hint)
        if result[0] == 'quit':
            return
        elif result[0] == 'enter':
            await rows[result[1]][1]()
BBSMenuUI.sysop_notifications = _sysop_notifications


async def _sysop_registry(self):
    """Registry/Peers sub-menu: RegistryEntry approve/reject (hub-mode
    federation applicants) + PeerBbs probe-now."""
    from anetbbs.models import db, RegistryEntry, PeerBbs
    from .ansi_ui import banner, FG, RESET, ui_width

    async def _registry_entries(ui):
        def _fetch_rows():
            with _app().app_context():
                rows = (RegistryEntry.query
                       .order_by(RegistryEntry.is_approved.asc(),
                                RegistryEntry.registered_at.desc())
                       .limit(100).all())
                return [(r.id, r.host, r.name, r.is_verified, r.is_approved,
                         r.is_listed) for r in rows]

        def render_row(idx, row, selected):
            rid, host, name, verified, approved, listed = row
            v = f"{FG['grn']}V{RESET}" if verified else f"{FG['gry']}-{RESET}"
            a = f"{FG['grn']}A{RESET}" if approved else f"{FG['gry']}-{RESET}"
            return f"  [{v}{a}] {FG['wht']}{rid:>4}{RESET}  {FG['grn']}{host[:28]:<28}{RESET} {FG['dim']}{name[:20]}{RESET}"

        def render_hint_extra(sel, total):
            return (f"{FG['cyan']}A{RESET}=approve  {FG['cyan']}X{RESET}=reject  "
                    f"{FG['cyan']}D{RESET}=delete  {FG['cyan']}Q{RESET}=back")

        async def _approve(u, row):
            rid = row[0]
            with _app().app_context():
                r = RegistryEntry.query.get(rid)
                if r:
                    r.is_approved = True
                    r.is_listed = r.is_verified and r.is_approved and r.is_active
                    db.session.commit()

        async def _reject(u, row):
            rid = row[0]
            with _app().app_context():
                r = RegistryEntry.query.get(rid)
                if r:
                    r.is_approved = False
                    r.is_listed = False
                    db.session.commit()

        async def _delete(u, row):
            rid, host = row[0], row[1]
            confirm = await u.session.read_line(
                f"\r\n{FG['red']}Type DELETE to remove '{host}': {RESET}")
            if confirm == 'DELETE':
                with _app().app_context():
                    r = RegistryEntry.query.get(rid)
                    if r:
                        db.session.delete(r)
                        db.session.commit()

        await ui._sysop_record_list(
            'Registry Entries', _fetch_rows, render_row, render_hint_extra,
            actions={'A': _approve, 'X': _reject, 'D': _delete},
            empty_msg='No registry entries (this install may not be a hub).')

    async def _peer_probes(ui):
        def _fetch_rows():
            with _app().app_context():
                peers = PeerBbs.query.order_by(PeerBbs.name).all()
                return [(p.id, p.name, p.hostname, p.online_count, p.last_error)
                        for p in peers]

        def render_row(idx, row, selected):
            pid, name, host, online, last_err = row
            err = f" {FG['red']}!{RESET}" if last_err else ''
            return (f"  {FG['wht']}{pid:>4}{RESET}  {FG['grn']}{name[:24]:<24}{RESET} "
                    f"{FG['dim']}{host[:24]}{RESET} {FG['cyan']}{online} online{RESET}{err}")

        def render_hint_extra(sel, total):
            return f"{FG['cyan']}P{RESET}=probe now  {FG['cyan']}Q{RESET}=back"

        async def _probe(u, row):
            pid = row[0]
            from anetbbs.web.peer_health import _systat_probe
            with _app().app_context():
                p = PeerBbs.query.get(pid)
                if not p:
                    return
                ok, ms, detail = _systat_probe(p.hostname, p.finger_port or 79)
                p.last_polled_at = datetime.utcnow()
                p.last_response = detail if ok else None
                p.last_error = None if ok else detail
                db.session.commit()
            await u.session.write(
                f"\r\n{FG['grn'] if ok else FG['red']}"
                f"{'OK' if ok else 'FAILED'} ({ms:.0f}ms): {detail[:120]}{RESET}\r\n")
            await u.session.read_line("Press Enter...")

        await ui._sysop_record_list(
            'Peer BBSes', _fetch_rows, render_row, render_hint_extra,
            actions={'P': _probe},
            empty_msg='No peer BBSes configured.')

    rows = [
        ('Registry Entries (federation applicants)', _registry_entries),
        ('Peer BBSes (probe now)',                    _peer_probes),
    ]

    async def render_header():
        await self.session.write(banner('Registry / Peers', ui_width(self.session)))

    def render_row(idx, row, selected):
        label, _fn = row
        return f"  {FG['grn']}{idx+1}. {label}{RESET}"

    def render_hint(sel, total):
        return (f"  {FG['cyan']}{sel+1}/{total}{RESET} {FG['cyan']}Enter{RESET}=open  "
                f"{FG['cyan']}Q{RESET}=back")

    while True:
        result = await self._rss_lightbar(rows, render_header, render_row, render_hint)
        if result[0] == 'quit':
            return
        elif result[0] == 'enter':
            await rows[result[1]][1](self)
BBSMenuUI.sysop_registry = _sysop_registry


async def _sysop_callers(self):
    """Read-only Last-Callers view + the one simple settings toggle
    (hide_sysop). InterBBS enable/network-picker stays web-only -- it
    also creates a ScheduledEvent + echomail area as a side effect,
    more than a single terminal toggle should silently trigger."""
    from anetbbs.models import CallerLog
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            rows = (CallerLog.query.order_by(CallerLog.started_at.desc())
                   .limit(200).all())
            return [(c.id, c.username or '?', c.service or '?',
                     c.started_at, c.origin_bbs) for c in rows]

    def render_row(idx, row, selected):
        cid, uname, service, started, origin = row
        ts = fmt_eastern(started, '%m-%d %H:%M', '?')
        tag = f" {FG['cyan']}[{origin}]{RESET}" if origin else ''
        return f"  {FG['dim']}{ts}{RESET}  {FG['grn']}{uname[:18]:<18}{RESET} {FG['gry']}{service}{RESET}{tag}"

    def render_hint_extra(sel, total):
        return f"{FG['cyan']}H{RESET}=toggle hide-sysop setting  {FG['cyan']}Q{RESET}=back"

    async def _toggle_hide_sysop(ui, row):
        import os
        from anetbbs.web.admin import _write_env_keys
        app = _app()
        with app.app_context():
            cur = bool(app.config.get('LASTCALLERS_HIDE_SYSOP', False))
            app.config['LASTCALLERS_HIDE_SYSOP'] = not cur
            env_path = os.path.abspath(os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                '..', '.env'))
            try:
                _write_env_keys(env_path, {
                    'LASTCALLERS_HIDE_SYSOP': 'true' if not cur else 'false'})
            except Exception:
                pass
        await ui.session.write(
            f"\r\n{FG['grn']}Hide-sysop is now {'ON' if not cur else 'OFF'}.{RESET}\r\n")
        await ui.session.read_line("Press Enter...")

    await self._sysop_record_list(
        'Caller Log', _fetch_rows, render_row, render_hint_extra,
        actions={'H': _toggle_hide_sysop},
        empty_msg='No caller log entries yet.')
BBSMenuUI.sysop_callers = _sysop_callers


async def _sysop_status(self):
    """Quick server-status snapshot for sysops (counts + recent activity)."""
    from anetbbs.models import (User, UserSession, Post, Message as Bulletin,
                                PrivateMessage, EchomailMessage, EchomailNetwork)
    from .ansi_ui import write_menu_art, banner, footer, FG, RESET, BOLD, prompt as _p, ui_width
    with _app().app_context():
        five = datetime.utcnow() - timedelta(minutes=5)
        # "Echo out (queue)" is scoped to BinkP only -- sent_at is
        # meaningless for QWK (delivery is tracked via a per-node
        # high-water mark instead, QWKNodeLastSent, and QWK never sets
        # this column), so counting QWK messages here made the number
        # climb forever regardless of whether anything was actually
        # stuck. Confirmed live, not a hypothetical.
        echo_out_queue = (EchomailMessage.query
                          .join(EchomailNetwork, EchomailMessage.network_id == EchomailNetwork.id)
                          .filter(EchomailMessage.direction == 'outbound',
                                  EchomailMessage.sent_at.is_(None),
                                  EchomailNetwork.network_type == 'binkp')
                          .count())
        stats = {
            'Users total':      User.query.count(),
            'Active (30d)':     User.query.filter(User.last_login >= datetime.utcnow() - timedelta(days=30)).count(),
            'Online now':       UserSession.query.filter(UserSession.last_seen >= five).count(),
            'Posts':            Post.query.count(),
            'Private msgs':     PrivateMessage.query.count(),
            'Bulletins':        Bulletin.query.count(),
            'Echo inbound':     EchomailMessage.query.filter_by(direction='inbound').count(),
            'Echo out (queue)': echo_out_queue,
            'Echo out (sent)':  EchomailMessage.query.filter(EchomailMessage.sent_at != None).count(),
        }
    _w = ui_width(self.session)
    if not await write_menu_art(self.session, 'sysop_status'):
        await self.session.write('\x1b[2J\x1b[H')
        await self.session.write(banner('Server Status', _w))
    for k, v in stats.items():
        await self.session.write(
            f"  {FG['cyan']}{k:<18}{RESET} "
            f"{FG['wht']}{BOLD}{v}{RESET}\r\n")
    await self.session.write('\r\n' + footer(_w) + '\r\n')
    await self.session.read_line(_p('Press Enter...'))
BBSMenuUI.sysop_status = _sysop_status


async def _sysop_node_monitor(self):
    """Scrollable live node monitor (Synchronet UNIX-Monitor style).

    Data source is NodeActivity, not multinode._NODES -- the latter is
    an in-process dict invisible across the telnet vs web process split,
    while NodeActivity is DB-backed and already the source the web
    NodeSpy panel (anetbbs/web/control.py:nodespy_json) uses. Kick uses
    the same DB flag NodeSpy's kick button sets (kick_requested/
    kick_reason), picked up by the existing 5s watchdog in
    core/session.py -- no session.py changes needed. Message uses
    sysop_paging.push_message directly since both ends of a "message a
    node" action are guaranteed to be terminal sessions in this same
    process (unlike a web-originated page reply, which is not).
    """
    from anetbbs.models import db, NodeActivity, UserActivity
    from .ansi_ui import FG, RESET

    def _fetch_rows():
        with _app().app_context():
            cutoff = datetime.utcnow() - timedelta(minutes=5)
            live = (NodeActivity.query
                    .filter(NodeActivity.last_seen >= cutoff)
                    .order_by(NodeActivity.slot).all())
            return [(r.slot, r.user_id, r.username or '?', r.protocol or '?',
                     r.page or '', r.action or '', r.last_seen)
                    for r in live]

    def render_row(idx, row, selected):
        slot, _uid, uname, proto, page, action, last_seen = row
        idle = datetime.utcnow() - last_seen
        secs = max(0, int(idle.total_seconds()))
        idle_s = f"{secs // 60}:{secs % 60:02d}"
        return (f"  {FG['wht']}{slot:>3}{RESET}  "
                f"{FG['grn']}{uname[:15]:<15}{RESET} "
                f"{FG['dim']}{proto[:6]:<6}{RESET} "
                f"{FG['cyan']}{page[:11]:<11}{RESET} "
                f"{FG['yel']}{action[:24]:<24}{RESET} "
                f"{FG['gry']}{idle_s:>6}{RESET}")

    def render_hint_extra(sel, total):
        return (f"{FG['cyan']}K{RESET}=kick  {FG['cyan']}M{RESET}=message  "
                f"{FG['cyan']}Q{RESET}=back")

    async def _do_kick(ui, row):
        slot, uid, uname, *_ = row
        reason = (await ui.session.read_line(
            "\r\nKick reason [Disconnected by sysop]: ") or '').strip()
        reason = (reason or 'Disconnected by sysop')[:200]
        with _app().app_context():
            live = NodeActivity.query.filter_by(slot=slot).first()
            if live:
                live.kick_requested = True
                live.kick_reason = reason
                db.session.add(UserActivity(
                    user_id=ui.session.user.get('id'),
                    activity_type='kick_node',
                    details=f'slot {slot} ({uname}): {reason}',
                    service=ui.session.user.get('service') or 'telnet'))
                db.session.commit()
                await ui.session.write(
                    f"\r\n{FG['grn']}Kick requested for {uname} "
                    f"(slot {slot}) -- disconnects within ~5s.{RESET}\r\n")
            else:
                await ui.session.write(
                    f"\r\n{FG['red']}Node no longer live.{RESET}\r\n")
        await ui.session.read_line("Press Enter...")

    async def _do_message(ui, row):
        slot, uid, uname, *_ = row
        if not uid:
            await ui.session.write(
                f"\r\n{FG['red']}Can't message an unauthenticated node.{RESET}\r\n")
            await ui.session.read_line("Press Enter...")
            return
        text = (await ui.session.read_line(
            f"\r\nMessage to {uname} (slot {slot}): ") or '').strip()
        if text:
            from .sysop_paging import push_message
            push_message(uid, ui.session.user.get('username') or 'sysop', text)
            await ui.session.write(f"\r\n{FG['grn']}Sent.{RESET}\r\n")
            await ui.session.read_line("Press Enter...")

    await self._sysop_record_list(
        'Node Monitor', _fetch_rows, render_row, render_hint_extra,
        actions={'K': _do_kick, 'M': _do_message},
        empty_msg='No live nodes right now.')
BBSMenuUI.sysop_node_monitor = _sysop_node_monitor


# ---------------------------------------------------------------------------
# Re-wire the main BBS menu to add Send PM, Compose Echomail, Edit profile,
# Change password, and Sysop menu (admin-gated).
# ---------------------------------------------------------------------------

async def _show_main_v2(self):
    while True:
        # Drain any sysop replies/Node-Monitor messages before drawing the
        # next menu -- same block as menu_engine.py:run_menu(). Installs
        # with an empty BbsMenu table fall back to this loop instead of
        # run_menu(), and without this they'd never see a pushed message.
        try:
            from .sysop_paging import pop_messages
            pending = pop_messages(self.session.user.get('id'))
            if pending:
                await self.session.write('\r\n\x1b[1;31m=== Sysop Reply ===\x1b[0m\r\n')
                for m in pending:
                    await self.session.write(
                        f'\x1b[31m[{m["sender"]}]\x1b[0m {m["text"]}\r\n')
                await self.session.write('\r\n')
        except Exception:
            pass
        is_sysop = self.session.user.get('is_admin')
        sysop_line = "║  S. Sysop tools                          ║\r\n" if is_sysop else ""
        with _app().app_context():
            from anetbbs.models import Game
            ebooks_game = Game.query.filter_by(slug='ebooks').first()
            ebooks_terminal_on = bool(
                ebooks_game and ebooks_game.is_active and ebooks_game.terminal_enabled)
        ebooks_line = "║  K. Ebook Reader                          ║\r\n" if ebooks_terminal_on else ""
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
            f"{ebooks_line}"
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
        elif choice == 'K' and ebooks_terminal_on:
            await self.show_ebooks()
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
