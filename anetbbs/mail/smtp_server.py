# anetbbs/mail/smtp_server.py
"""
Internet Mail inbound receiver — hand-rolled SMTP (RFC 5321), listens
on TCP 25 (and optionally 587) directly.

ANetBBS does NOT sit behind an external MTA. Checked against real
Synchronet BBS source (src/sbbs3/mailsrvr.cpp) before building this:
Synchronet's own mail server binds the port itself, resolves recipients
itself, and does its own DNSBL checks -- no Postfix/Exim in front of
it. This module follows that precedent, not the Postfix-fronted-LMTP
design ANetBBS briefly had and removed pre-1.0 (see docs/19-email.md).

No STARTTLS/AUTH in v1 -- inbound MX delivery doesn't need AUTH (random
sending MTAs don't authenticate to us), and STARTTLS's absence just
means plaintext transit, not a correctness blocker. A sender's address
isn't itself a recipient, but it still passes through MAIL FROM; the
null sender (MAIL FROM:<>, used for bounces/DSNs) is accepted (RFC 5321
4.5.5, audit F-12). This server never generates bounces or automatic
replies, so null-sender mail cannot start a loop.

Storage limits (anetbbs/mail/limits.py): a recipient whose mailbox is
already at its quota gets 452 4.2.2 at RCPT; a message that would push
any accepted recipient over quota gets 452 4.2.2 after DATA and nothing
is stored; too many / too-large attachments get 552 5.3.4 after DATA and
nothing is stored.

Usage from main.py / the systemd unit:
    asyncio.run(serve())
"""
import asyncio
from functools import lru_cache
import ipaddress
import logging
import os
import re
from email import message_from_bytes, policy as _email_policy
from email.parser import BytesHeaderParser
from email.policy import compat32 as _compat32
from email.utils import parseaddr, getaddresses

import dns.asyncresolver
import dns.resolver

from . import limits
from .policy import is_plausible_email

logger = logging.getLogger(__name__)

_ADDR_ANGLE_RE = re.compile(r'<([^<>]*)>')

# Defense in depth, same reasoning as BinkP's MAX_CONCURRENT_SESSIONS
# (binkp_server.py): port 25 is unauthenticated and directly
# internet-facing, so this bounds how many sessions -- each holding up
# to MailConfig.max_message_size in memory while reading DATA -- can be
# in flight at once.
MAX_CONCURRENT_SESSIONS = int(os.environ.get('SMTP_MAX_CONNECTIONS', '50'))
MAX_SESSIONS_PER_IP = 10
MAX_SESSION_SECONDS = 30 * 60
# Commands per session (audit F-11): NOOP resets the 120 s idle timer, so
# without a cap one client can hold a slot for the whole session lifetime.
MAX_COMMANDS_PER_SESSION = 1000
MAX_RECIPIENTS_PER_TRANSACTION = 100


def _flask_app():
    """Construct a minimal Flask app context for SQLAlchemy access.
    Cached. Same pattern as finger_server.py/binkp_server.py."""
    if not hasattr(_flask_app, '_app'):
        from flask import Flask
        from ..config import get_config
        from ..models import db
        app = Flask(__name__)
        app.config.from_object(
            get_config(os.environ.get('FLASK_ENV', 'production')))
        db.init_app(app)
        _flask_app._app = app
    return _flask_app._app


def _lookup_user(local_part, domain):
    """Resolve a `local_part@domain` recipient to a User row, or None.

    A user's ONLY address is the one policy.mail_local_part() shows them
    (explicit email_local_part, else the slugified username), compared
    case-insensitively. There is deliberately no raw-username fallback:
    usernames may contain spaces/apostrophes, so "O'Brien" is shown
    obrien@ while a different account can be *named* "obrien" -- a
    username fallback delivered O'Brien's mail to that other account
    (audit finding F-01).

    Fails closed: if more than one opted-in user has the same effective
    local part (legacy rows, or two opt-ins racing past set_mail_optin's
    uniqueness check), nobody gets the mail rather than guessing.

    Gates: `email_enabled=True`, MailConfig.enabled, and the domain must
    match MailConfig.domain (case-insensitive).
    """
    from types import SimpleNamespace
    from ..models import db, User, MailConfig
    from .policy import is_reserved_local_part, mail_local_part
    cfg = MailConfig.get()
    if not cfg.enabled or not cfg.domain:
        return None
    if domain.lower() != cfg.domain.lower():
        return None
    wanted = (local_part or '').strip().lower()
    if not wanted:
        return None
    # Role/system mailboxes never route to an ordinary user, even a legacy
    # row that claimed one before set_mail_optin refused it (F-09).
    if is_reserved_local_part(wanted):
        return None
    # Column-only scan (no ORM object per row): this runs on every RCPT
    # from unauthenticated senders, so keep it cheap. The address itself
    # is still computed by policy.mail_local_part -- one definition.
    # Suspended accounts (deactivated or sysop-locked: login refuses both,
    # web/auth.py) are not deliverable -- release acceptance check.
    rows = (db.session.query(User.id, User.username, User.email_local_part)
            .filter(User.email_enabled.is_(True),
                    User.is_active.isnot(False),
                    User.is_locked.isnot(True)).all())
    matches = [r.id for r in rows
               if mail_local_part(SimpleNamespace(
                   username=r.username,
                   email_local_part=r.email_local_part)).lower() == wanted]
    if len(matches) == 1:
        return db.session.get(User, matches[0])
    if len(matches) > 1:
        logger.warning('SMTP: mailbox %r is claimed by %d opted-in users; '
                       'refusing delivery until the conflict is resolved',
                       wanted, len(matches))
    return None


_DNSBL_LISTING_NETWORK = ipaddress.IPv4Network('127.0.0.0/8')
_DNSBL_ERROR_NETWORK = ipaddress.IPv4Network('127.255.255.0/24')
_DNSBL_SKIP_NETWORKS = tuple(ipaddress.IPv4Network(n) for n in (
    '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
    '169.254.0.0/16', '172.16.0.0/12', '192.168.0.0/16'))


@lru_cache(maxsize=64)
def _warn_dnsbl_error(zone):
    # A configured zone can contain a provider API key.
    logger.warning('SMTP: DNSBL provider returned an error code; check resolver access '
                   'and DNSBL configuration')


async def _dnsbl_check(remote_ip, zones):
    """Synchronet mailsrvr.cpp's dns_blacklisted() analogue: query each
    configured DNSBL zone for `remote_ip`. Fail OPEN on any resolver
    error/timeout -- a slow/unreachable DNSBL must never block
    legitimate mail -- only a positive, resolved listing rejects.
    IPv6 peers aren't checked (public DNSBL zones are overwhelmingly
    IPv4-only); treated as not-listed."""
    try:
        addr = ipaddress.ip_address(remote_ip)
    except ValueError:
        return False
    if addr.version != 4:
        return False
    # Internal peers (local relays, health checks, CGNAT neighbours) are
    # pointless to look up and leak internal addressing to a third party
    # (audit F-22). Explicit list: ipaddress.is_private/is_global also
    # treat documentation ranges as non-global.
    if any(addr in net for net in _DNSBL_SKIP_NETWORKS):
        return False
    reversed_ip = '.'.join(reversed(str(addr).split('.')))

    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = 3
    resolver.lifetime = 3
    for zone in zones:
        zone = zone.strip()
        if not zone:
            continue
        query = f'{reversed_ip}.{zone}'
        try:
            answers = await resolver.resolve(query, 'A')
        except dns.resolver.NXDOMAIN:
            continue  # not listed on this zone
        except Exception:
            # Timeout, SERVFAIL, no nameservers reachable, etc. --
            # fail open rather than reject legitimate mail.
            continue
        for record in answers:
            try:
                code = ipaddress.IPv4Address(record.address)
            except (AttributeError, ValueError):
                continue
            if code in _DNSBL_ERROR_NETWORK:
                _warn_dnsbl_error(zone)
            elif code in _DNSBL_LISTING_NETWORK:
                logger.info('SMTP: %s is listed by a configured DNSBL', remote_ip)
                return True
    return False


def _extract_addr(remainder):
    """Parse the address out of a MAIL FROM:/RCPT TO: argument,
    tolerating the optional <> angle brackets most senders use and any
    trailing ESMTP parameters (SIZE=, BODY=, ...) -- we don't act on
    those in v1, just don't choke on their presence. Returns None for
    an empty/malformed/implausible address, including the null sender
    MAIL FROM:<> (valid SMTP for bounces/DSNs, not accepted here)."""
    remainder = remainder.strip()
    m = _ADDR_ANGLE_RE.search(remainder)
    if m:
        addr = m.group(1).strip()
    else:
        parts = remainder.split()
        addr = parts[0] if parts else ''
    if not addr or not is_plausible_email(addr):
        return None
    return addr


_NULL_PATH_RE = re.compile(r'\s*<\s*>(?:\s|$)')
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f-\x9f]')


def _is_local_domain(addr, cfg):
    """True if `addr`'s domain is the BBS mail domain or a subdomain of
    it (case-insensitive)."""
    local_domain = (cfg.domain or '').strip().lower().rstrip('.')
    domain = (addr or '').rpartition('@')[2].strip().lower().rstrip('.')
    return bool(local_domain) and bool(domain) and (
        domain == local_domain or domain.endswith('.' + local_domain))


def _claims_local_sender(mail_from, raw_bytes, cfg):
    """True if the envelope sender, or any From:/Sender: header address,
    is in the BBS's own domain (audit finding F-10). Port 25 is
    unauthenticated and v1 has no SPF/DKIM, so such mail cannot be told
    apart from a forgery. It is NOT refused: the BBS's own notifications
    (password reset, verification) legitimately loop back here through
    the relay when a user's contact address is their BBS mailbox. It is
    stored with status='quarantine' and shown with a warning instead.
    A header that fails to parse counts as a claim (fail closed)."""
    if _is_local_domain(mail_from, cfg):
        return True
    try:
        hdrs = BytesHeaderParser(policy=_email_policy.default).parsebytes(raw_bytes)
        values = (hdrs.get_all('from') or []) + (hdrs.get_all('sender') or [])
        return any(_is_local_domain(a, cfg)
                   for _, a in getaddresses([str(v) for v in values]))
    except Exception:
        return True


DATA_OK, DATA_TOO_BIG, DATA_EOF, DATA_TIMEOUT, DATA_LINE_TOO_LONG = (
    'ok', 'too_big', 'eof', 'timeout', 'line_too_long')


async def _read_data(reader, max_size):
    """Read SMTP DATA until the terminating bare '.' line, undoing
    dot-stuffing (RFC 5321 4.5.2) as it goes. Returns (raw_bytes,
    outcome), outcome one of DATA_*:

      DATA_OK / DATA_TOO_BIG  terminator seen; on exceeding max_size the
          remaining lines are consumed and discarded up to the terminator
          so the session stays in sync.
      DATA_EOF      client closed before the terminator: the message was
          never handed over and must not be stored (audit F-06).
      DATA_TIMEOUT  client stalled: transient, caller answers 421 and
          closes, never a 5xx and never re-parses the body (audit F-07).
      DATA_LINE_TOO_LONG  a line beyond the StreamReader limit (readline
          raises ValueError): caller answers 500 and closes (audit F-17).
    """
    lines = []
    total = 0
    too_big = False
    while True:
        try:
            raw_line = await asyncio.wait_for(reader.readline(), timeout=120)
        except asyncio.TimeoutError:
            return b'', DATA_TIMEOUT
        except (ValueError, asyncio.LimitOverrunError):
            return b'', DATA_LINE_TOO_LONG
        if not raw_line or not raw_line.endswith(b'\n'):
            return b'', DATA_EOF       # EOF, possibly inside a partial line
        if raw_line in (b'.\r\n', b'.\n'):
            break
        if raw_line.startswith(b'.'):
            raw_line = raw_line[1:]
        total += len(raw_line)
        if max_size and total > max_size:
            too_big = True
            continue
        lines.append(raw_line)
    return b''.join(lines), DATA_TOO_BIG if too_big else DATA_OK


def _html_to_text(html):
    """Plain-text rendering of an HTML part for display (F-15): script and
    style content dropped, tags removed, entities decoded. Uses
    beautifulsoup4 (already a dependency) with the stdlib parser; never
    raises."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'head', 'title']):
            tag.decompose()
        text = soup.get_text('\n')
    except Exception:
        return ''
    lines = [ln.strip() for ln in text.replace('\xa0', ' ').splitlines()]
    return '\n'.join(ln for ln in lines if ln)


def _persist(mail_from, rcpt_tos, raw_bytes, recipient_ids=None):
    """Parse the RFC822 source + write one InternetMail row per
    accepted recipient. Multi-recipient mail fans out so every user has
    an independent row to read/delete. Ported from the removed
    anetbbs/mail/lmtp_server.py's AnetbbsLMTPHandler._persist().

    `recipient_ids` (the SMTP session passes it): [(user_id, addr)]
    accepted at RCPT time. Without it, `rcpt_tos` addresses are resolved
    now (direct callers/tests).

    Raises limits.DeliveryRefused (attachment limit, mailbox quota) with
    the SMTP reply to send; nothing is stored in that case."""
    from ..models import db, InternetMail, MailConfig, User

    try:
        msg = message_from_bytes(raw_bytes, policy=_email_policy.default)
    except Exception:
        # Even if MIME parsing dies, keep going with the raw bytes --
        # better than dropping the message on the floor.
        msg = None

    # Structured header access can raise on malformed input (CPython
    # 3.12.3 raised IndexError on 'Cc: ,,,<'). A parse failure must never
    # make a message permanently undeliverable (audit F-08): fall back to
    # an unstructured compat32 re-parse; the raw bytes are always kept.
    try:
        subject = str((msg.get('subject') if msg else '') or '')
        message_id = str((msg.get('message-id') if msg else '') or '')
        in_reply_to = str((msg.get('in-reply-to') if msg else '') or '')
        refs = str((msg.get('references') if msg else '') or '')
        from_addr_full = (msg.get('from') if msg else '') or mail_from or ''
        from_name, from_addr = parseaddr(str(from_addr_full))
        cc_list = ([a for _, a in getaddresses(
            [str(v) for v in (msg.get_all('cc') or [])])] if msg is not None else [])
    except Exception:
        logger.warning('SMTP: header parse failed; storing with unstructured headers')
        try:
            m2 = message_from_bytes(raw_bytes, policy=_compat32)
            _h = lambda k: str(m2.get(k) or '')
        except Exception:
            _h = lambda k: ''
        subject, message_id = _h('subject'), _h('message-id')
        in_reply_to, refs = _h('in-reply-to'), _h('references')
        from_name, from_addr, cc_list = '', '', []
    # Decoded header values can carry RFC 2047-encoded CR/LF, ESC or C1
    # controls; they feed reply pre-fill, page titles and terminal lists.
    # Replace them in the extracted fields only; raw keeps the original
    # (audit F-19).
    subject, from_name, message_id, in_reply_to, refs = (
        _CONTROL_CHARS_RE.sub(' ', v or '')
        for v in (subject, from_name, message_id, in_reply_to, refs))
    from_addr = from_addr or mail_from
    cfg = MailConfig.get()
    # Attachment count / per-attachment decoded size: refused before
    # anything is stored or any lock is taken.
    limits.check_attachment_limits(msg, cfg)
    unverified_local = (_claims_local_sender(mail_from, raw_bytes, cfg)
                        or _is_local_domain(from_addr, cfg))
    cc = ', '.join(cc_list)

    # Extract a plain-text body for the inbox view. A multipart message
    # with no text/plain part falls back to a text rendering of its first
    # text/html part (audit F-15); the raw blob always keeps the original.
    body = ''
    html_source = None
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
                    if html_source is None:
                        try:
                            html_source = part.get_content()
                        except Exception:
                            pass
            if not body and html_source:
                body = _html_to_text(html_source)
        else:
            ctype = msg.get_content_type()
            if ctype == 'text/html':
                has_html = True
            try:
                body = msg.get_content()
            except Exception:
                body = ''
    body = body or ''
    # Quota charge per stored copy: raw payload + decoded body, once each.
    charge = limits.stored_bytes_for(raw_bytes, body)

    if recipient_ids is None:
        resolved = []
        for rcpt in rcpt_tos:
            local, _, domain = rcpt.partition('@')
            user = _lookup_user(local, domain)
            if user is not None:
                resolved.append((user.id, rcpt))

    # End any read snapshot left from RCPT time so the mailbox lock below
    # is the first statement of a fresh transaction (limits.py docstring).
    db.session.rollback()
    try:
        if recipient_ids is not None:
            limits.lock_mailboxes(uid for uid, _ in recipient_ids)
            # Deliver to exactly the users accepted at RCPT time (audit F-18):
            # once RCPT got 250 the message is owed to that mailbox, so a
            # later opt-out/disable must not turn the final 250 into a silent
            # drop. Only a vanished user row is skipped.
            # Fresh read (bypass the identity map): SQLite does not enforce
            # the FK, so a stale cache would store a row for a deleted user.
            live = {r.id for r in db.session.query(User.id).filter(
                User.id.in_([uid for uid, _ in recipient_ids])).all()}
            targets = [(uid, addr) for uid, addr in recipient_ids if uid in live]
            if not targets:
                raise RuntimeError('no accepted recipient still exists')
        else:
            limits.lock_mailboxes(uid for uid, _ in resolved)
            targets = resolved

        # Under the lock: every recipient must have room, or nothing is
        # stored (one SMTP reply covers all recipients).
        limits.check_quota_locked(targets, charge, cfg)

        delivered = []
        for user_id, rcpt in targets:
            row = InternetMail(
                user_id=user_id,
                direction='inbound',
                status='quarantine' if unverified_local else 'received',
                from_addr=(from_addr or '')[:255],
                from_name=(from_name or '')[:255],
                to_addr=rcpt[:255],
                cc=cc,
                subject=subject[:998],
                body=body,
                has_html=has_html,
                has_attachments=has_attachments,
                raw=raw_bytes,
                raw_size=len(raw_bytes),
                stored_bytes=charge,
                message_id=message_id[:255] if message_id else None,
                in_reply_to=in_reply_to[:255] if in_reply_to else None,
                refs=refs[:2000] if refs else None,
                received_via='smtp',
            )
            db.session.add(row)
            delivered.append(row)
        db.session.commit()
    except BaseException:
        # Never leave the mailbox lock (an open write transaction) held
        # for the rest of the SMTP session.
        db.session.rollback()
        raise
    # Persist one user-scoped bell notification per accepted inbox copy.
    # Keep the title/body independent of the untrusted sender and subject.
    # The current receiver app has no Socket.IO extension, so notify() always
    # writes the durable notification and safely skips live socket delivery.
    try:
        from ..features.notify import notify
        for row in delivered:
            notify(row.user_id, 'internet_mail', title='New Internet Mail',
                   body='A new message is waiting in your Internet Mail inbox.',
                   target_url=f'/mail/{row.id}')
    except Exception:
        logger.exception('SMTP: could not create Internet Mail notification')
    # No subject in the service log (audit F-20): size, count, Message-ID.
    logger.info('SMTP: delivered %d byte(s) to %d recipient(s) (message-id=%s)',
                len(raw_bytes), len(delivered), (message_id or '-')[:255])


async def _handle(reader, writer):
    """One SMTP session. Every read/write is bounded by a timeout and
    the whole body runs inside one try/finally that always tears down
    the connection, same discipline this codebase already applies to
    finger/binkp (see the deadconn-freeze-audit convention) -- port 25
    is unauthenticated and directly internet-facing, same risk class."""
    peer = writer.get_extra_info('peername')
    remote_ip = peer[0] if peer else ''
    hostname = os.environ.get('BBS_NAME', 'ANetBBS')

    async def _send(line):
        writer.write((line + '\r\n').encode('utf-8', errors='replace'))
        await asyncio.wait_for(writer.drain(), timeout=10)

    async def _read_line():
        data = await asyncio.wait_for(reader.readline(), timeout=120)
        if not data:
            raise ConnectionError('client closed the connection')
        return data.decode('utf-8', errors='replace').rstrip('\r\n')

    try:
        # _flask_app() itself is inside this try (not evaluated before
        # it) so that any failure constructing the app context still
        # goes through the finally below and closes the connection --
        # the exact ordering bug a past security audit found and fixed
        # in finger_server.py's own _handle().
        app = _flask_app()
        with app.app_context():
            from ..models import MailConfig
            cfg = MailConfig.get()
            # Sysop limit (default 5), never above the hard ceiling (F-05).
            max_rcpts = max(1, min(getattr(cfg, 'max_recipients', None) or 5,
                                   MAX_RECIPIENTS_PER_TRANSACTION))
            zones = [z for z in
                    os.environ.get('SMTP_DNSBL_ZONES', 'zen.spamhaus.org').split(',')
                    if z.strip()]
            if remote_ip and await _dnsbl_check(remote_ip, zones):
                await _send(f'550 {hostname} refusing connection')
                return

            await _send(f'220 {hostname} ESMTP ANetBBS ready')

            mail_from = None
            rcpt_tos = {}
            commands = 0

            while True:
                try:
                    line = await _read_line()
                except (asyncio.TimeoutError, ConnectionError,
                        asyncio.LimitOverrunError, ValueError):
                    return

                commands += 1
                if commands > MAX_COMMANDS_PER_SESSION:
                    await _send(f'421 4.7.0 {hostname} too many commands, '
                                'closing connection')
                    return

                upper = line.upper()
                if upper.startswith('HELO') or upper.startswith('EHLO'):
                    mail_from, rcpt_tos = None, {}
                    await _send(f'250 {hostname}')
                elif upper.startswith('MAIL FROM:'):
                    remainder = line[len('MAIL FROM:'):]
                    # Null reverse-path for bounces/DSNs (RFC 5321 4.5.5,
                    # audit F-12): mail_from '' (not None) opens the
                    # transaction. Nothing in this BBS generates automatic
                    # replies, so accepting it cannot start a mail loop.
                    addr = '' if _NULL_PATH_RE.match(remainder) else _extract_addr(remainder)
                    if addr is None:
                        await _send('501 Syntax error in MAIL FROM command')
                        continue
                    mail_from, rcpt_tos = addr, {}
                    await _send('250 OK')
                elif upper.startswith('RCPT TO:'):
                    if mail_from is None:
                        await _send('503 Need MAIL FROM command first')
                        continue
                    addr = _extract_addr(line[len('RCPT TO:'):])
                    if addr is None:
                        await _send('501 Syntax error in RCPT TO command')
                        continue
                    local, _, domain = addr.partition('@')
                    recipient = _lookup_user(local, domain)
                    if recipient is None:
                        await _send(f'550 No such user: {addr}')
                        continue
                    if recipient.id not in rcpt_tos:
                        if len(rcpt_tos) >= max_rcpts:
                            await _send('452 Too many recipients')
                            continue
                        # Advisory early refusal (RFC 3463 4.2.2) when not
                        # even one more byte fits; the binding, locked
                        # check runs again at end of DATA.
                        if limits.mailbox_is_full(recipient.id, cfg):
                            await _send(limits.MAILBOX_FULL_RCPT_REPLY.format(addr=addr))
                            continue
                        rcpt_tos[recipient.id] = addr
                    await _send('250 OK')
                elif upper == 'DATA':
                    if not rcpt_tos:
                        await _send('554 No valid recipients')
                        continue
                    await _send('354 Start mail input; end with <CRLF>.<CRLF>')
                    raw, outcome = await _read_data(reader, cfg.max_message_size)
                    mail_from_for_log = mail_from
                    accepted = tuple(rcpt_tos.items())
                    mail_from, rcpt_tos = None, {}
                    if outcome == DATA_EOF:
                        return                      # never handed over (F-06)
                    if outcome == DATA_TIMEOUT:      # transient, close (F-07)
                        await _send(f'421 4.4.2 {hostname} timeout waiting '
                                    'for data, closing connection')
                        return
                    if outcome == DATA_LINE_TOO_LONG:   # F-17
                        await _send('500 5.5.6 Line too long, closing connection')
                        return
                    if outcome == DATA_TOO_BIG:
                        await _send('552 Message size exceeds fixed limit')
                        continue
                    try:
                        _persist(mail_from_for_log, [a for _, a in accepted], raw,
                                 recipient_ids=accepted)
                    except limits.DeliveryRefused as refused:
                        logger.info('SMTP: refused %d byte(s) for %d recipient(s): %s',
                                    len(raw), len(accepted), refused.reason)
                        await _send(refused.reply)
                    except Exception:
                        logger.exception(
                            'SMTP: failed to persist message from %s',
                            mail_from_for_log)
                        await _send('451 Temporary local error, try again later')
                    else:
                        await _send('250 OK: message accepted')
                elif upper == 'RSET':
                    mail_from, rcpt_tos = None, {}
                    await _send('250 OK')
                elif upper == 'NOOP':
                    await _send('250 OK')
                elif upper == 'QUIT':
                    await _send(f'221 {hostname} closing connection')
                    return
                else:
                    await _send('502 Command not implemented')
    except (OSError, ConnectionError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def serve():
    host = os.environ.get('SMTP_LISTEN_HOST', '0.0.0.0')
    port = int(os.environ.get('SMTP_LISTEN_PORT', '25'))

    session_slots = asyncio.Semaphore(MAX_CONCURRENT_SESSIONS)
    sessions_by_ip = {}

    async def _wrapper(reader, writer):
        peer = writer.get_extra_info('peername')
        remote_ip = peer[0] if peer else None
        peer_sessions = sessions_by_ip.get(remote_ip, 0)
        if session_slots.locked() or peer_sessions >= MAX_SESSIONS_PER_IP:
            try:
                writer.write(b'421 SMTP service busy, try again later\r\n')
                await asyncio.wait_for(writer.drain(), timeout=1)
            except (OSError, asyncio.TimeoutError):
                pass
            finally:
                writer.close()
            return

        async with session_slots:
            sessions_by_ip[remote_ip] = peer_sessions + 1
            try:
                await asyncio.wait_for(_handle(reader, writer), MAX_SESSION_SECONDS)
            except asyncio.TimeoutError:
                logger.info('SMTP: session lifetime exceeded for %s', remote_ip)
            finally:
                remaining = sessions_by_ip[remote_ip] - 1
                if remaining:
                    sessions_by_ip[remote_ip] = remaining
                else:
                    del sessions_by_ip[remote_ip]

    server = await asyncio.start_server(_wrapper, host=host, port=port)
    logger.info('Internet Mail (SMTP) listener on %s:%d', host, port)
    async with server:
        await server.serve_forever()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
