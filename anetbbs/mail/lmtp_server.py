"""LMTP receiver for inbound internet mail.

ANetBBS does **not** bind port 25 — that fights with the sysop's existing
MTA (Postfix on most BBS hosts). Instead, the sysop configures a
virtual-alias in Postfix that routes mail addressed to
`*@<MAIL_DOMAIN>` to `lmtp:unix:<MAIL_LMTP_SOCKET>`. Postfix does all
the dirty work — RBL, greylisting, TLS, deferred retries, queueing —
and hands us only validated mail.

We implement the receiving half of LMTP (RFC 2033). The protocol is
SMTP with three rule differences:
  - `LHLO` instead of `EHLO`
  - one response per RCPT during `DATA`, not just one for the batch
  - intended for trusted local delivery (no MX semantics)

Day-1 scope: parse + persist. The user-facing inbox view is Day 2's
work; for now the LMTP server's job is "validated message arrives →
`InternetMail` row appears in the DB."
"""
import asyncio
import email
import logging
import os
import re
import stat as _stat
from datetime import datetime
from email import policy
from email.utils import parseaddr, getaddresses

from aiosmtpd.controller import UnixSocketController
from aiosmtpd.lmtp import LMTP
from aiosmtpd.smtp import Envelope, Session

logger = logging.getLogger(__name__)


_ADDR_RE = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')


def _slug_local_part(username):
    """Default mailbox name when User.email_local_part is blank."""
    s = (username or '').strip().lower()
    # Permissive: letters/digits/dot/underscore/hyphen, RFC 5322 atext minus +
    s = re.sub(r'[^a-z0-9._\-]', '', s)
    return s or 'user'


def _lookup_user(local_part, domain, app):
    """Resolve a `local_part@domain` recipient to a User row, or None.

    Match order:
      1. exact `email_local_part` (case-insensitive)
      2. slugified username (so 'StingRay' → 'stingray@...')
    Both gates require `email_enabled=True` (sysop-approved policy).
    Domain must match `MAIL_DOMAIN` from config exactly.
    """
    from ..models import User
    if domain.lower() != (app.config.get('MAIL_DOMAIN') or '').lower():
        return None
    with app.app_context():
        # 1. explicit local-part override
        u = (User.query
             .filter(User.email_enabled.is_(True),
                     User.email_local_part.ilike(local_part))
             .first())
        if u:
            return u
        # 2. username slug
        return (User.query
                .filter(User.email_enabled.is_(True),
                        User.username.ilike(local_part))
                .first())


class AnetbbsLMTPHandler:
    """aiosmtpd handler — one instance per Controller. Stateless aside
    from the Flask app it queries on each delivery."""

    def __init__(self, app):
        self.app = app

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        # Reject obviously malformed addresses before reading DATA.
        if not _ADDR_RE.match(address):
            return f"550 Invalid recipient address: {address}"
        local, _, domain = address.partition('@')
        user = _lookup_user(local, domain, self.app)
        if user is None:
            return f"550 No such user: {address}"
        envelope.rcpt_tos.append(address)
        return '250 OK'

    async def handle_DATA(self, server, session, envelope):
        # aiosmtpd's LMTP class returns one status per recipient by
        # joining our return string with "\r\n". We always accept here
        # because handle_RCPT already gated who's allowed in.
        try:
            self._persist(envelope, session)
        except Exception:
            logger.exception('LMTP: failed to persist message from %s',
                             envelope.mail_from)
            return '\r\n'.join(
                f"451 Temporary local error — try again ({rcpt})"
                for rcpt in envelope.rcpt_tos)
        return '\r\n'.join(f"250 OK ({rcpt})" for rcpt in envelope.rcpt_tos)

    def _persist(self, envelope, session):
        """Parse the RFC822 source + write one InternetMail row per
        accepted recipient. Multi-recipient mail gets fanned out so
        every user has an independent row to read / delete."""
        from ..models import db, User, InternetMail

        raw = envelope.original_content or envelope.content or b''
        try:
            msg = email.message_from_bytes(raw, policy=policy.default)
        except Exception:
            # Even if MIME parsing dies, keep the raw bytes — better
            # than dropping mail on the floor.
            msg = None

        subject = (msg.get('subject') if msg else '') or ''
        message_id = (msg.get('message-id') if msg else '') or ''
        in_reply_to = (msg.get('in-reply-to') if msg else '') or ''
        refs = (msg.get('references') if msg else '') or ''
        from_addr_full = (msg.get('from') if msg else '') or ''
        from_name, from_addr = parseaddr(from_addr_full)
        # CC list — flatten for the index column, the canonical list is
        # parseable via the raw blob if needed.
        cc_list = []
        if msg is not None:
            cc_list = [a for _, a in getaddresses(msg.get_all('cc') or [])]
        cc = ', '.join(cc_list)

        # Extract a plain-text body for the inbox view. Defaults to ''
        # if the message is HTML-only — the raw blob still has it.
        body = ''
        has_html = False
        has_attachments = False
        if msg is not None:
            if msg.is_multipart():
                for part in msg.walk():
                    cd = (part.get('content-disposition') or '').lower()
                    if 'attachment' in cd:
                        has_attachments = True
                        continue
                    ctype = part.get_content_type()
                    if ctype == 'text/plain' and not body:
                        try:
                            body = part.get_content()
                        except Exception:
                            pass
                    elif ctype == 'text/html':
                        has_html = True
            else:
                ctype = msg.get_content_type()
                if ctype == 'text/html':
                    has_html = True
                try:
                    body = msg.get_content()
                except Exception:
                    body = ''

        # SPF result from Postfix's `Authentication-Results` header, if
        # the sysop has it set up. Best-effort — we don't fail on absence.
        spf_result = ''
        dkim_result = ''
        if msg is not None:
            for hdr in msg.get_all('authentication-results') or []:
                if 'spf=' in hdr.lower() and not spf_result:
                    m = re.search(r'spf=(\w+)', hdr, re.I)
                    if m: spf_result = m.group(1).lower()
                if 'dkim=' in hdr.lower() and not dkim_result:
                    m = re.search(r'dkim=(\w+)', hdr, re.I)
                    if m: dkim_result = m.group(1).lower()

        with self.app.app_context():
            for rcpt in envelope.rcpt_tos:
                local, _, domain = rcpt.partition('@')
                user = _lookup_user(local, domain, self.app)
                if user is None:
                    continue  # already validated, but recheck race-safe
                im = InternetMail(
                    user_id=user.id,
                    direction='inbound',
                    status='received',
                    from_addr=from_addr or from_addr_full[:255],
                    from_name=(from_name or '')[:255],
                    to_addr=rcpt[:255],
                    to_name='',
                    cc=cc,
                    subject=subject[:998],
                    body=body or '',
                    has_html=has_html,
                    has_attachments=has_attachments,
                    raw=raw,
                    raw_size=len(raw),
                    message_id=message_id[:255] if message_id else None,
                    in_reply_to=in_reply_to[:255] if in_reply_to else None,
                    refs=refs[:2000] if refs else None,
                    spf_result=spf_result[:20] if spf_result else None,
                    dkim_result=dkim_result[:20] if dkim_result else None,
                    received_via='lmtp',
                    created_at=datetime.utcnow(),
                )
                db.session.add(im)
            db.session.commit()
            logger.info('LMTP: delivered %d byte(s) to %s (subject=%r)',
                        len(raw), envelope.rcpt_tos, subject[:80])


def _prepare_socket(socket_path):
    """Ensure the socket parent exists, clear any stale socket, and
    return the chmod we want post-bind (Postfix → us)."""
    parent = os.path.dirname(socket_path) or '.'
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(socket_path):
        try:
            os.unlink(socket_path)
        except OSError:
            pass


def run(app):
    """Daemon-thread entry point. Binds an LMTP listener on the unix
    socket configured by `MAIL_LMTP_SOCKET` and blocks forever. The
    socket is chmoded 0666 after bind so Postfix (which usually runs
    as `postfix` user) can connect to it without group acrobatics."""
    socket_path = app.config.get('MAIL_LMTP_SOCKET', '/run/anetbbs/lmtp.sock')
    _prepare_socket(socket_path)

    handler = AnetbbsLMTPHandler(app)

    # We need an LMTP server, not the SMTP default that UnixSocketController
    # would use. Subclass the controller so the factory returns an LMTP
    # session — the protocol difference is subtle but real (LHLO not EHLO,
    # one DATA reply per recipient).
    class _LMTPController(UnixSocketController):
        def factory(self):
            return LMTP(self.handler)

    controller = _LMTPController(handler, unix_socket=socket_path)
    controller.start()
    try:
        os.chmod(socket_path, 0o666)
    except OSError:
        pass
    logger.info('Mail: LMTP listening on unix:%s (max size=%d bytes)',
                socket_path, app.config.get('MAIL_MAX_SIZE', 25 * 1024 * 1024))

    # Block the thread forever — Controller's start() is non-blocking.
    try:
        # If the calling thread already has an event loop, just sleep on it.
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_forever()
    finally:
        try: controller.stop()
        except Exception: pass
        try: os.unlink(socket_path)
        except OSError: pass


def build_minimal_app():
    """Minimal Flask app — same pattern as the FTP server. Skips
    blueprints/pollers so we don't trigger the threading-vs-asyncio
    issues that broke the previous FTP integration."""
    from flask import Flask
    from ..config import get_config
    from ..models import db

    env = os.environ.get('FLASK_ENV', 'production')
    app = Flask(__name__)
    app.config.from_object(get_config(env))
    db.init_app(app)
    return app
