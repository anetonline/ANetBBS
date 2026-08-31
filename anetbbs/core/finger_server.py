# anetbbs/core/finger_server.py
"""
Finger service (RFC 1288) — listens on TCP 79.

Behavior:
    "" (just CRLF)            -> list of currently-online users
    "username\r\n"           -> profile + plan for that user
    "/W username\r\n"        -> verbose mode (same content for now)

Usage from main.py / the systemd unit:
    asyncio.run(serve())
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _online_users():
    """Return list of currently-online users via UserSession."""
    try:
        from ..models import UserSession, User
        from .text_safety import strip_untrusted_escapes as _sue
        cutoff = datetime.utcnow() - timedelta(minutes=5)
        rows = (UserSession.query
                .filter(UserSession.last_seen >= cutoff)
                .join(User, User.id == UserSession.user_id)
                .all())
        # Real gap found in a security/performance audit, follow-up to
        # the profile-field fix in _handle() below: 'where' is mostly
        # built from sysop-configured menu labels / fixed literals
        # (SessionPresence.set_page()'s callers), but at least one path
        # (mrc_chat.py's chat:mrc #<room> presence) can carry a room
        # name a user typed themselves -- same cross-user display path
        # (finger, no local account needed by the viewer) as the other
        # profile fields, so sanitized the same way for consistency
        # rather than trusting every current and future set_page()
        # caller to only ever pass safe text.
        return [{
            'username': r.user.username,
            'where': _sue(r.page or ''),
            'last_seen': r.last_seen,
        } for r in rows]
    except Exception:
        logger.exception('finger online list failed')
        return []


def _user_info(name):
    """Look up a user by name. Returns dict or None."""
    try:
        from ..models import User
        u = User.query.filter_by(username=name).first()
        if u is None:
            return None
        return {
            'username': u.username,
            'display_name': u.display_name or u.username,
            'last_login': u.last_login,
            'login_count': u.login_count or 0,
            'bio': u.bio or '',
            'location': u.location or '',
            'tagline': u.tagline or '',
            'is_admin': bool(u.is_admin),
        }
    except Exception:
        logger.exception('finger user lookup failed for %r', name)
        return None


def _flask_app():
    """Construct a minimal Flask app context for SQLAlchemy access. Cached."""
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


async def _handle(reader, writer):
    # Real gap found in a security/performance audit: finger (port 79,
    # RFC 1288) is unauthenticated and directly internet-facing (its
    # own systemd unit, no auth of any kind). The old code only caught
    # asyncio.TimeoutError around reader.readline() -- readline() has
    # no explicit limit= here, so it uses asyncio.start_server()'s
    # default 64KiB buffer, and any client sending >64KB with no \n
    # raises asyncio.LimitOverrunError, which was NOT caught. That
    # exception propagated straight out of _handle(), skipping the
    # writer.close()/wait_closed() cleanup entirely (it only ran inside
    # a LATER try/finally, past the failure point) -- a trivial,
    # remote, unauthenticated, repeatable fd leak. Wrapping the whole
    # handler in one try/finally guarantees the connection is always
    # torn down on every exit path, not just the ones an earlier
    # narrower except clause happened to anticipate.
    peer = writer.get_extra_info('peername')
    raw = ''
    payload = b''
    try:
        try:
            data = await asyncio.wait_for(reader.readline(), timeout=10)
        except (asyncio.TimeoutError, asyncio.LimitOverrunError, ValueError):
            return

        raw = data.decode('utf-8', errors='replace').strip()
        # Strip the optional /W (RFC 1288) verbose flag
        if raw.lower().startswith('/w '):
            raw = raw[3:].strip()
        elif raw.lower() == '/w':
            raw = ''

        bbs_name = os.environ.get('BBS_NAME', 'ANetBBS')
        out_lines = [f'== {bbs_name} finger service ==', '']

        app = _flask_app()
        with app.app_context():
            if not raw:
                users = _online_users()
                if users:
                    out_lines.append(f'Currently online ({len(users)}):')
                    for u in users:
                        out_lines.append(
                            f"  {u['username']:<20} "
                            f"{u['where'][:30]:<30} "
                            f"(last seen {u['last_seen'].strftime('%H:%M')})")
                else:
                    out_lines.append('No users online right now.')
            else:
                info = _user_info(raw)
                if info is None:
                    out_lines.append(f'No such user: {raw}')
                else:
                    # Real gap found in a security audit: profile fields
                    # are set by the account owner and shown here to
                    # whoever fingers them -- a user could put ANSI
                    # escapes in their own display name/location/tagline/
                    # bio and have them fire on anyone (including an
                    # admin) who looks them up through a real terminal-
                    # based finger client. See text_safety.py.
                    from .text_safety import strip_untrusted_escapes as _sue
                    out_lines.append(f"Login:    {info['username']}")
                    out_lines.append(f"Name:     {_sue(info['display_name'])}")
                    if info['location']:
                        out_lines.append(f"Location: {_sue(info['location'])}")
                    if info['last_login']:
                        out_lines.append(
                            f"Last on:  {info['last_login'].strftime('%Y-%m-%d %H:%M UTC')}")
                    out_lines.append(f"Calls:    {info['login_count']}")
                    if info['is_admin']:
                        out_lines.append('Status:   Sysop')
                    if info['tagline']:
                        out_lines.append('')
                        out_lines.append(f'Tagline:  {_sue(info["tagline"])}')
                    if info['bio']:
                        out_lines.append('')
                        out_lines.append('Plan:')
                        for line in info['bio'].splitlines():
                            out_lines.append(f'  {_sue(line)}')

        out_lines.append('')
        payload = ('\r\n'.join(out_lines) + '\r\n').encode('utf-8', errors='replace')
        try:
            writer.write(payload)
            await writer.drain()
        except (OSError, ConnectionError):
            pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
    logger.info('finger query from %s: %r (%d bytes)',
                peer, raw, len(payload))


async def serve():
    host = os.environ.get('FINGER_LISTEN_HOST', '0.0.0.0')
    port = int(os.environ.get('FINGER_LISTEN_PORT', '79'))
    server = await asyncio.start_server(_handle, host=host, port=port)
    logger.info('Finger listener on %s:%d', host, port)
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
