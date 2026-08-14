# anetbbs/web/irc_web.py
"""
Web-based IRC client.

Provides a browser-side IRC chat experience similar to the MRC web bridge:
each authenticated user opens /irc/ in their browser, configures a server,
nick, and channels, and the page maintains a persistent connection through
Flask-SocketIO + an eventlet greenthread that runs a sync-socket IRC client.

Architecture:
    Browser  ──socketio──▶  Flask process  ──TCP──▶  IRC server (Libera, etc.)

We do NOT use the asyncio-based IRCClient in features/irc.py because mixing
asyncio + eventlet inside a single Flask process is fragile. Instead we keep
this self-contained using a tiny synchronous IRC client that runs in an
eventlet greenthread, emitting events back to the user's socketio session.
"""
import logging
import os
import socket
import ssl as ssl_module
import threading
import time
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from flask_socketio import emit

from ..models import db, IrcServerConfig, IrcPreset
from ..features.irc_format import to_html as _irc_to_html, strip_codes as _irc_strip


logger = logging.getLogger(__name__)


def _irc_logging_enabled():
    """Returns True if env var IRC_LOG_CHANNELS=1 (or similar) is set."""
    val = (os.environ.get('IRC_LOG_CHANNELS', '') or '').strip().lower()
    return val in ('1', 'true', 'yes', 'on')


def _log_irc(user_id, server, channel, nick, kind, text):
    """Best-effort row insert into IrcChannelLog. Silent on failure."""
    if not _irc_logging_enabled():
        return
    try:
        from ..models import IrcChannelLog
        db.session.add(IrcChannelLog(
            user_id=user_id, server=server[:120] if server else '',
            channel=channel[:80] if channel else '',
            nick=nick[:40] if nick else '',
            kind=kind, text=(text or '')[:4000]))
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

irc_bp = Blueprint('irc', __name__, url_prefix='/irc')


# Per-socketio-sid IRC session state. Keyed by sid so each browser tab gets
# its own connection — and disconnects cleanly when the tab closes.
_sessions = {}
_sessions_lock = threading.Lock()

# Per-USER (not sid) scrollback — survives reconnects of the same browser tab
# or moves to another device, as long as the IRC server connection itself
# is still alive. Each entry is a deque of (event, payload) tuples.
from collections import deque
_SCROLLBACK_LIMIT = 500
_scrollback = {}                # user_id -> deque
_scrollback_lock = threading.Lock()


def _push_scroll(user_id, event, payload):
    """Append a recent line to the user's per-user scrollback buffer."""
    if user_id is None:
        return
    with _scrollback_lock:
        d = _scrollback.get(user_id)
        if d is None:
            d = deque(maxlen=_SCROLLBACK_LIMIT)
            _scrollback[user_id] = d
        d.append((event, payload))


def _replay_scroll(user_id, sid, socketio):
    """Replay buffered events to a freshly-connected socketio session."""
    with _scrollback_lock:
        d = _scrollback.get(user_id)
        if not d:
            return
        # Snapshot to release the lock before emit (which may yield).
        items = list(d)
    for ev, payload in items:
        socketio.emit(ev, dict(payload, replayed=True), to=sid, namespace='/irc')


class _IrcSession:
    """One user's IRC connection. Runs the read-loop in an eventlet greenthread."""

    # Events worth keeping in the per-user scrollback ring buffer. We
    # deliberately exclude noisy server numerics — only conversation
    # content and join/part/topic events.
    SCROLLABLE_EVENTS = frozenset({
        'irc_message', 'irc_action', 'irc_notice',
        'irc_join', 'irc_part', 'irc_quit', 'irc_kick', 'irc_nick',
        'irc_topic',
    })

    def __init__(self, sid, server, port, use_ssl, nick, username, realname,
                 sasl_user=None, sasl_pass=None, user_id=None,
                 sasl_mechanism='PLAIN',
                 client_cert_path=None, client_key_path=None):
        self.sid = sid
        self.user_id = user_id
        self.server = server
        self.port = port
        self.use_ssl = use_ssl
        self.nick = nick
        self.username = username
        self.realname = realname
        self.sock = None
        self.recv_task = None
        self.connected = False
        self.channels = set()
        self.current_channel = ''
        # SASL state — None = not attempted, 'pending'/'auth_sent'/'done'/'failed'.
        # `sasl_mechanism` is 'PLAIN' (user/pass) or 'EXTERNAL' (CertFP from
        # the TLS client cert presented at connect time).
        self.sasl_user = sasl_user
        self.sasl_pass = sasl_pass
        self.sasl_mechanism = (sasl_mechanism or 'PLAIN').upper()
        self.client_cert_path = client_cert_path
        self.client_key_path = client_key_path
        if self.sasl_mechanism == 'EXTERNAL':
            self.sasl_state = 'pending'
        elif sasl_user and sasl_pass:
            self.sasl_state = 'pending'
        else:
            self.sasl_state = None
        # Channel keys (for joining password-protected channels)
        self.channel_keys = {}

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def connect(self):
        """Open the TCP socket, optionally start CAP/SASL, then send NICK/USER.

        SASL handshake state is driven by `_handle_line` reading server replies;
        we only kick off the handshake here. If SASL isn't configured we skip
        CAP entirely and just register normally."""
        try:
            sock = socket.create_connection((self.server, self.port), timeout=20)
            # The connect timeout sticks on the socket → blocking recv() will
            # return socket.timeout after 20s of no traffic. Clear it so
            # recv() blocks forever; we rely on TCP keepalive + IRC PING/PONG
            # to detect disconnects.
            sock.settimeout(None)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except OSError:
                pass
            if self.use_ssl:
                ctx = ssl_module.create_default_context()
                if (self.sasl_mechanism == 'EXTERNAL'
                        and self.client_cert_path and self.client_key_path):
                    # Load the user's TLS client cert so the server can
                    # match its fingerprint to a registered NickServ account.
                    try:
                        ctx.load_cert_chain(certfile=self.client_cert_path,
                                            keyfile=self.client_key_path)
                    except Exception as exc:
                        self._emit('irc_error',
                                   {'message': f'CertFP load failed: {exc}'})
                        return False
                sock = ctx.wrap_socket(sock, server_hostname=self.server)
            self.sock = sock
            # Begin CAP negotiation BEFORE NICK/USER so SASL can complete
            # before the server registers us. The server will hold off the
            # 001 welcome until we send CAP END.
            if self.sasl_state == 'pending':
                self.send_raw('CAP LS 302')
            self.send_raw(f'NICK {self.nick}')
            self.send_raw(f'USER {self.username} 0 * :{self.realname}')
            self.connected = True
            return True
        except (OSError, ssl_module.SSLError) as exc:
            self._emit('irc_error', {'message': f'Connect failed: {exc}'})
            return False

    def send_raw(self, line):
        if self.sock is None:
            return
        try:
            self.sock.sendall((line + '\r\n').encode('utf-8', errors='replace'))
        except OSError as exc:
            logger.warning('IRC sid=%s send failed: %s', self.sid, exc)
            self._emit('irc_error', {'message': f'Send failed: {exc}'})
            self.connected = False

    def quit(self, message='Goodbye'):
        if self.connected:
            try:
                self.send_raw(f'QUIT :{message}')
            finally:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.connected = False

    # ------------------------------------------------------------------
    # Receive loop — runs in eventlet greenthread
    # ------------------------------------------------------------------

    def run(self):
        """Blocking read loop, pumps incoming IRC lines to socketio."""
        # Real gap found in a security/performance audit: nothing ever
        # capped how large `buf` could grow while waiting for a '\r\n'.
        # A malicious or just broken IRC server (or a MITM on the
        # connection) sending an endless stream of bytes with no line
        # terminator at all would make this process buffer literally
        # everything in RAM forever, per active web-IRC session --
        # real lines are bounded to 512 bytes by RFC 2812/1459; a much
        # more generous 8 KiB cap still comfortably fits every
        # legitimate line while making the failure mode "disconnect",
        # not "grow without bound".
        _MAX_LINE_BUF = 8192
        buf = b''
        reason = 'connection closed'
        started = time.time()
        line_count = 0
        try:
            while self.connected and self.sock:
                try:
                    chunk = self.sock.recv(4096)
                except OSError as exc:
                    reason = f'recv error: {exc}'
                    break
                if not chunk:
                    reason = 'remote closed (EOF)'
                    break
                buf += chunk
                if len(buf) > _MAX_LINE_BUF and b'\r\n' not in buf:
                    reason = 'line exceeded max length with no terminator'
                    logger.warning(
                        'IRC sid=%s: %s, disconnecting', self.sid, reason)
                    break
                while b'\r\n' in buf:
                    line, buf = buf.split(b'\r\n', 1)
                    line_count += 1
                    try:
                        self._handle_line(
                            line.decode('utf-8', errors='replace'))
                    except Exception as exc:
                        # Don't let a single broken line tear down the session.
                        logger.exception(
                            'IRC handler crashed on line: %r', line)
                        self._emit('irc_error',
                                   {'message': f'Line handler error: {exc}'})
        except Exception as exc:
            reason = f'recv loop crashed: {exc}'
            logger.exception('IRC recv loop crashed for sid=%s', self.sid)
        finally:
            self.connected = False
            duration = int(time.time() - started)
            logger.info('IRC session sid=%s ended after %ds (%d lines): %s',
                        self.sid, duration, line_count, reason)
            self._emit('irc_disconnected', {'reason': reason})
            with _sessions_lock:
                _sessions.pop(self.sid, None)
            # Real gap found in a security/performance audit: _scrollback
            # is deliberately keyed per-user (not per-sid) so it survives
            # a browser reconnect while the underlying IRC connection is
            # still alive -- see this dict's own comment above. But once
            # THIS read loop ends, that underlying IRC connection is
            # gone, so there's nothing left to "survive a reconnect" for
            # -- previously the only way to clear an entry was the user
            # explicitly running /clearscroll, so every distinct user who
            # ever connected got a permanent entry for the life of the
            # process. A fresh connection starts a fresh buffer anyway.
            with _scrollback_lock:
                _scrollback.pop(self.user_id, None)

    def _handle_line(self, line):
        # PING / PONG
        if line.startswith('PING'):
            payload = line[5:] if len(line) > 5 else ''
            self.send_raw(f'PONG {payload}')
            return

        # AUTHENTICATE — server prompt during SASL handshake
        if line.startswith('AUTHENTICATE '):
            challenge = line[len('AUTHENTICATE '):].strip()
            if challenge == '+' and self.sasl_state == 'auth_sent':
                if self.sasl_mechanism == 'EXTERNAL':
                    # EXTERNAL: empty payload (server uses our cert fingerprint).
                    self.send_raw('AUTHENTICATE +')
                else:
                    import base64
                    payload = (f'\x00{self.sasl_user}\x00{self.sasl_pass}'
                               ).encode('utf-8')
                    b64 = base64.b64encode(payload).decode('ascii')
                    self.send_raw(f'AUTHENTICATE {b64 or "+"}')
            return

        prefix = ''
        if line.startswith(':'):
            prefix, _, rest = line[1:].partition(' ')
            line = rest

        # Split out trailing argument (after " :")
        trailing = ''
        if ' :' in line:
            head, trailing = line.split(' :', 1)
            args = head.split()
        else:
            args = line.split()
        if not args:
            return
        command = args[0].upper()
        params = args[1:]
        sender = prefix.split('!', 1)[0] if prefix else ''

        # IMPORTANT: do NOT emit a generic `irc_raw` for every line. The flood
        # of MOTD/welcome lines on connect was causing socketio to drop the
        # session. Only emit raw for unhandled commands at the bottom.
        handled = True

        if command == 'PRIVMSG' and len(params) >= 1:
            target = params[0]
            # A PRIVMSG whose target is OUR OWN nick is a direct message --
            # per the IRC protocol the "target" param is always the
            # recipient, so on a DM that's always self.nick, not something
            # that distinguishes one conversation from another. Route it
            # by the SENDER instead so the client can group it into a
            # per-contact pane (e.g. a NickServ reply lands in a "NickServ"
            # tab, not one shared bucket literally named after yourself).
            # Real gap found live: /msg nickserv ... replies were silently
            # dropped into an untitled, never-created UI tab.
            if target == self.nick and sender:
                target = sender
            text = trailing
            # CTCP ACTION → render as "* nick action"
            if text.startswith('\x01ACTION ') and text.endswith('\x01'):
                action_text = text[8:-1]
                _log_irc(self.user_id, self.server, target, sender,
                         'action', _irc_strip(action_text))
                self._emit('irc_action', {
                    'from': sender, 'target': target,
                    'text': _irc_to_html(action_text),
                })
            elif text.startswith('\x01') and text.endswith('\x01') and len(text) >= 3:
                # Generic CTCP request — auto-reply via NOTICE.
                ctcp = text[1:-1]
                ctcp_name, _, ctcp_arg = ctcp.partition(' ')
                ctcp_name = ctcp_name.upper()
                reply = None
                if ctcp_name == 'PING':
                    reply = f'\x01PING {ctcp_arg}\x01'
                elif ctcp_name == 'VERSION':
                    reply = '\x01VERSION ANetBBS web client v1.0 — built on Flask-SocketIO\x01'
                elif ctcp_name == 'TIME':
                    import datetime as _dt
                    reply = f'\x01TIME {_dt.datetime.utcnow().strftime("%a %b %d %H:%M:%S %Y UTC")}\x01'
                elif ctcp_name == 'CLIENTINFO':
                    reply = '\x01CLIENTINFO PING VERSION TIME CLIENTINFO ACTION SOURCE\x01'
                elif ctcp_name == 'SOURCE':
                    reply = '\x01SOURCE https://github.com/anetonline/ANetBBS\x01'
                if reply:
                    self.send_raw(f'NOTICE {sender} :{reply}')
                # Also surface to UI as a system note so we have a record.
                self._emit('irc_system',
                           {'text': f'[CTCP] {sender} → {ctcp_name} '
                                    + ('(replied)' if reply else '(ignored)')})
            else:
                _log_irc(self.user_id, self.server, target, sender,
                         'message', _irc_strip(text))
                self._emit('irc_message', {
                    'from': sender, 'target': target,
                    'html': _irc_to_html(text),
                    'plain': _irc_strip(text),
                })
        elif command == 'NOTICE' and len(params) >= 1:
            notice_target = params[0]
            # Same self-targeted-DM routing fix as PRIVMSG above -- this is
            # the more common case in practice, since NickServ/ChanServ
            # replies are conventionally sent as NOTICE, not PRIVMSG.
            if notice_target == self.nick and sender:
                notice_target = sender
            self._emit('irc_notice', {
                'from': sender or 'server',
                'target': notice_target,
                'html': _irc_to_html(trailing),
            })
        elif command == 'JOIN':
            chan = params[0] if params else trailing
            if sender == self.nick:
                self.channels.add(chan)
                self.current_channel = chan
            _log_irc(self.user_id, self.server, chan, sender, 'join', '')
            self._emit('irc_join', {'nick': sender, 'channel': chan})
        elif command == 'PART':
            chan = params[0] if params else trailing
            if sender == self.nick:
                self.channels.discard(chan)
                if self.current_channel == chan:
                    self.current_channel = next(iter(self.channels), '')
            _log_irc(self.user_id, self.server, chan, sender, 'part',
                     trailing if params else '')
            self._emit('irc_part', {'nick': sender, 'channel': chan,
                                    'reason': trailing if params else ''})
        elif command == 'QUIT':
            self._emit('irc_quit', {'nick': sender, 'reason': trailing})
        elif command == 'KICK' and len(params) >= 2:
            self._emit('irc_kick', {
                'channel': params[0], 'kicker': sender,
                'victim': params[1], 'reason': trailing,
            })
            if params[1] == self.nick:
                self.channels.discard(params[0])
        elif command == 'NICK':
            new = trailing or (params[0] if params else '')
            if sender == self.nick:
                self.nick = new
            self._emit('irc_nick', {'old': sender, 'new': new})
        elif command == 'MODE' and len(params) >= 1:
            self._emit('irc_mode', {
                'target': params[0],
                'modes': ' '.join(params[1:]) + (' ' + trailing if trailing else ''),
                'by': sender,
            })
        elif command == 'TOPIC' and len(params) >= 1:
            self._emit('irc_topic', {
                'channel': params[0], 'topic': trailing, 'by': sender})
        elif command == 'ERROR':
            self._emit('irc_error', {'message': trailing or ' '.join(params)})
        elif command == 'CAP' and len(params) >= 2:
            # CAP * LS :sasl ... | CAP * ACK :sasl | CAP * NAK :sasl
            cap_subcmd = params[1].upper()
            cap_list = (trailing or '').split()
            if cap_subcmd == 'LS':
                if 'sasl' in cap_list and self.sasl_state == 'pending':
                    self.send_raw('CAP REQ :sasl')
                else:
                    # Server doesn't offer SASL — skip auth, finish CAP
                    self.send_raw('CAP END')
                    if self.sasl_state == 'pending':
                        self._emit('irc_system',
                                   {'text': 'SASL not offered by server; '
                                            'connecting unauthenticated.'})
                        self.sasl_state = 'failed'
            elif cap_subcmd == 'ACK' and 'sasl' in cap_list:
                self.send_raw(f'AUTHENTICATE {self.sasl_mechanism}')
                self.sasl_state = 'auth_sent'
            elif cap_subcmd == 'NAK':
                self._emit('irc_error', {'message': f'CAP NAK: {trailing}'})
                self.send_raw('CAP END')
                self.sasl_state = 'failed'
        elif command == '903':       # SASL successful
            self.sasl_state = 'done'
            self._emit('irc_system', {'text': 'SASL authentication succeeded.'})
            self.send_raw('CAP END')
        elif command in ('902', '904', '905', '906', '907'):  # SASL failures
            self.sasl_state = 'failed'
            self._emit('irc_error',
                       {'message': f'SASL failed ({command}): {trailing}'})
            self.send_raw('CAP END')
        # ----- Numeric replies -----
        elif command in ('001', '002', '003', '004', '005',
                         '251', '252', '253', '254', '255',
                         '265', '266', '375', '372', '376'):
            # Welcome / server info / MOTD — show in (server) channel.
            msg = trailing or ' '.join(params[1:])
            self._emit('irc_system', {'text': msg})
        elif command == '332' and len(params) >= 2:
            # RPL_TOPIC
            self._emit('irc_topic', {'channel': params[1], 'topic': trailing,
                                     'by': ''})
        elif command == '353' and len(params) >= 3:
            # RPL_NAMREPLY: 353 mynick = #chan :user1 user2 user3
            chan = params[2] if len(params) > 2 else (params[1] if len(params) > 1 else '')
            self._emit('irc_names', {
                'channel': chan, 'names': trailing.split()})
        elif command == '366':
            # End of NAMES — no need to emit (client builds list off 353)
            pass
        elif command in ('311', '312', '313', '317', '318', '319', '330',
                         '671', '276', '338'):
            # WHOIS replies — params layout varies; surface trailing+params
            self._emit('irc_whois', {
                'numeric': command,
                'target': params[1] if len(params) > 1 else '',
                'text': ' '.join(params[2:]) + (' :' + trailing if trailing else ''),
            })
        elif command in ('321', '322', '323'):
            # /LIST replies
            if command == '322' and len(params) >= 3:
                self._emit('irc_list', {
                    'channel': params[1], 'users': params[2],
                    'topic': trailing,
                })
            elif command == '323':
                self._emit('irc_list_end', {})
        elif command in ('432', '433', '436'):
            self._emit('irc_error',
                       {'message': f'Nickname unavailable: {trailing}'})
        elif command in ('401', '403', '404', '405', '442', '482'):
            self._emit('irc_error',
                       {'message': f'{trailing} ({command})'})
        else:
            handled = False

        # Only forward truly-unhandled lines as raw — keeps the firehose down.
        if not handled:
            self._emit('irc_raw', {'line': line, 'prefix': prefix})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit(self, event, payload):
        """Emit to this session's socketio sid + push to user scrollback."""
        from ..web_app import socketio
        socketio.emit(event, payload, to=self.sid, namespace='/irc')
        if event in self.SCROLLABLE_EVENTS:
            _push_scroll(self.user_id, event, payload)


# ---------------------------------------------------------------------------
# HTTP route — render the chat UI
# ---------------------------------------------------------------------------

@irc_bp.route('/')
@login_required
def index():
    """IRC web client page."""
    saved = (IrcServerConfig.query
             .filter_by(user_id=current_user.id).first())
    # Real gap found live: with no per-user saved config, the connect form
    # fell back to a hardcoded irc.libera.chat/6667/no-SSL -- completely
    # ignoring Admin -> IRC Server Presets, which only ever drove the
    # TERMINAL IRC client (features/irc_chat.py), never this page. Use the
    # sysop's top-priority active preset as the same kind of fallback here.
    default_preset = None
    if saved is None:
        default_preset = (IrcPreset.query
                          .filter_by(is_active=True)
                          .order_by(IrcPreset.order, IrcPreset.name)
                          .first())
    return render_template('irc/index.html',
                           saved=saved,
                           default_preset=default_preset,
                           default_nick=current_user.username)


@irc_bp.route('/save', methods=['POST'])
@login_required
def save_settings():
    """Persist a user's preferred IRC server / nick / channels."""
    server = (request.form.get('server') or '').strip()
    port_raw = (request.form.get('port') or '6667').strip()
    use_ssl = bool(request.form.get('use_ssl'))
    nick = (request.form.get('nick') or current_user.username).strip()
    channels = (request.form.get('channels') or '').strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 6667
    cfg = IrcServerConfig.query.filter_by(user_id=current_user.id).first()
    if cfg is None:
        cfg = IrcServerConfig(user_id=current_user.id)
        db.session.add(cfg)
    cfg.server = server
    cfg.port = port
    cfg.use_ssl = use_ssl
    cfg.nick = nick
    cfg.channels = channels
    db.session.commit()
    return ('', 204)


# ---------------------------------------------------------------------------
# SocketIO handlers — registered by web_app.py after the socketio is bound
# ---------------------------------------------------------------------------

def register_socketio_handlers(socketio):
    """Wire up the IRC namespace handlers. Called from web_app.create_app()."""

    @socketio.on('connect', namespace='/irc')
    def on_connect():
        if not getattr(current_user, 'is_authenticated', False):
            return False  # reject unauthenticated socket
        # Replay any scrollback this user accumulated in earlier sessions.
        # Tagged with replayed=True so the client UI can mark them.
        sid = request.sid
        try:
            _replay_scroll(current_user.id, sid, socketio)
        except Exception:
            logger.exception('IRC scrollback replay failed')
        emit('irc_system', {'text': 'IRC namespace ready. Click Connect.'})

    @socketio.on('disconnect', namespace='/irc')
    def on_disconnect():
        sid = request.sid
        with _sessions_lock:
            sess = _sessions.pop(sid, None)
        if sess:
            sess.quit()

    @socketio.on('irc_connect', namespace='/irc')
    def on_irc_connect(data):
        if not getattr(current_user, 'is_authenticated', False):
            return False
        sid = request.sid
        with _sessions_lock:
            existing = _sessions.get(sid)
        if existing and existing.connected:
            emit('irc_error', {'message': 'Already connected. Disconnect first.'})
            return

        server = (data.get('server') or 'irc.libera.chat').strip()
        port = int(data.get('port') or 6667)
        use_ssl = bool(data.get('use_ssl'))
        nick = (data.get('nick') or current_user.username).strip()
        username = (data.get('username') or nick).strip().lower()
        realname = (data.get('realname') or nick).strip()
        sasl_user = (data.get('sasl_user') or '').strip() or None
        sasl_pass = (data.get('sasl_pass') or '').strip() or None

        sess = _IrcSession(sid, server, port, use_ssl, nick, username, realname,
                           sasl_user=sasl_user, sasl_pass=sasl_pass,
                           user_id=current_user.id)
        if not sess.connect():
            return

        with _sessions_lock:
            _sessions[sid] = sess
        # Run the read loop in an eventlet greenthread.
        socketio.start_background_task(sess.run)
        emit('irc_connected', {
            'server': server, 'port': port, 'nick': nick, 'use_ssl': use_ssl,
            'sasl': bool(sasl_user)})

        # Auto-join configured channels — supports "#chan" or "#chan key"
        for ch in (data.get('autojoin') or []):
            ch = (ch or '').strip()
            if not ch:
                continue
            parts = ch.split(None, 1)
            chan = parts[0] if parts[0].startswith('#') else '#' + parts[0]
            key = parts[1] if len(parts) > 1 else ''
            if key:
                sess.channel_keys[chan] = key
                sess.send_raw(f'JOIN {chan} {key}')
            else:
                sess.send_raw(f'JOIN {chan}')

    @socketio.on('irc_send', namespace='/irc')
    def on_irc_send(data):
        sid = request.sid
        with _sessions_lock:
            sess = _sessions.get(sid)
        if not sess or not sess.connected:
            emit('irc_error', {'message': 'Not connected.'})
            return
        line = (data.get('line') or '').strip()
        if not line:
            return

        # Slash-commands
        if line.startswith('/'):
            parts = line[1:].split(None, 1)
            cmd = parts[0].lower() if parts else ''
            arg = parts[1] if len(parts) > 1 else ''
            if cmd == 'join' and arg:
                # /join #chan [key]
                parts = arg.strip().split(None, 1)
                ch = parts[0] if parts[0].startswith('#') else '#' + parts[0]
                key = parts[1] if len(parts) > 1 else ''
                if key:
                    sess.channel_keys[ch] = key
                    sess.send_raw(f'JOIN {ch} {key}')
                else:
                    sess.send_raw(f'JOIN {ch}')
            elif cmd == 'part':
                ch = (arg.split()[0] if arg else sess.current_channel)
                if ch:
                    sess.send_raw(f'PART {ch}')
            elif cmd == 'nick' and arg:
                sess.nick = arg.strip()
                sess.send_raw(f'NICK {sess.nick}')
            elif cmd == 'msg' and arg:
                target_msg = arg.split(None, 1)
                if len(target_msg) == 2:
                    msg_target, msg_body = target_msg
                    sess.send_raw(f'PRIVMSG {msg_target} :{msg_body}')
                    # Real gap found live: /msg never echoed anything back to
                    # the sender (IRC servers don't echo your own PRIVMSGs),
                    # so e.g. `/msg nickserv identify ...` looked like it did
                    # nothing at all -- you'd only find out it worked if
                    # NickServ's reply happened to land somewhere visible,
                    # which it usually didn't (see the irc_switch emit below).
                    emit('irc_message', {
                        'from': sess.nick, 'target': msg_target,
                        'html': _irc_to_html(msg_body),
                        'plain': _irc_strip(msg_body),
                        'self': True})
                    # Ensure a tab exists for this DM target client-side --
                    # without this, a reply from msg_target (very often a
                    # NOTICE, e.g. NickServ) has nowhere visible to land.
                    emit('irc_switch', {'channel': msg_target, 'switch': False})
            elif cmd == 'query' and arg:
                # Classic IRC client convention: open/focus a private
                # message pane with a nick, without sending anything yet.
                target_nick = arg.strip().split()[0]
                sess.current_channel = target_nick
                emit('irc_switch', {'channel': target_nick, 'switch': True})
            elif cmd == 'help':
                emit('irc_system', {'text': (
                    'Commands: /join #chan [key], /part [#chan], /nick '
                    'newnick, /msg target text, /query nick, /switch '
                    'target, /me action, /whois nick, /list [pattern], '
                    '/topic [#chan] text, /mode args, /ctcp nick TYPE '
                    '[args], /raw|/quote text, /quit [message]')})
            elif cmd == 'me' and arg and sess.current_channel:
                sess.send_raw(f'PRIVMSG {sess.current_channel} :\x01ACTION {arg}\x01')
            elif cmd == 'whois' and arg:
                sess.send_raw(f'WHOIS {arg.split()[0]}')
            elif cmd == 'list':
                sess.send_raw(f'LIST {arg}' if arg.strip() else 'LIST')
            elif cmd == 'topic' and arg:
                # /topic [#channel] new topic
                parts = arg.split(None, 1)
                if parts[0].startswith('#'):
                    chan = parts[0]
                    topic = parts[1] if len(parts) > 1 else ''
                else:
                    chan = sess.current_channel
                    topic = arg
                if topic:
                    sess.send_raw(f'TOPIC {chan} :{topic}')
                else:
                    sess.send_raw(f'TOPIC {chan}')
            elif cmd == 'mode' and arg:
                sess.send_raw(f'MODE {arg}')
            elif cmd == 'ctcp' and arg:
                # /ctcp nick TYPE [args]
                parts = arg.split(None, 2)
                if len(parts) >= 2:
                    target_nick = parts[0]
                    ctcp_type = parts[1].upper()
                    extra = ' ' + parts[2] if len(parts) > 2 else ''
                    sess.send_raw(
                        f'PRIVMSG {target_nick} :\x01{ctcp_type}{extra}\x01')
                    emit('irc_system', {'text': f'CTCP {ctcp_type} → {target_nick}'})
                else:
                    emit('irc_error', {'message': 'Usage: /ctcp <nick> <TYPE> [args]'})
            elif cmd in ('quit', 'q'):
                sess.quit(message=arg or 'Goodbye')
            elif cmd in ('raw', 'quote') and arg:
                sess.send_raw(arg)
            elif cmd == 'switch' and arg.strip():
                sess.current_channel = arg.strip().split()[0]
                emit('irc_switch', {'channel': sess.current_channel, 'switch': True})
            else:
                emit('irc_error', {'message': f'Unknown command: /{cmd}'})
            return

        # Plain text → privmsg to current channel
        target = (data.get('target') or sess.current_channel).strip()
        if not target:
            emit('irc_error', {'message': 'No channel selected.'})
            return
        sess.send_raw(f'PRIVMSG {target} :{line}')
        # Echo ourselves locally — IRC servers don't echo your own PRIVMSGs back
        emit('irc_message', {
            'from': sess.nick, 'target': target,
            'html': _irc_to_html(line),
            'plain': _irc_strip(line),
            'self': True})

    @socketio.on('irc_disconnect', namespace='/irc')
    def on_irc_disconnect():
        sid = request.sid
        with _sessions_lock:
            sess = _sessions.pop(sid, None)
        if sess:
            sess.quit()
            emit('irc_disconnected', {'reason': 'user requested'})

    @socketio.on('irc_clear_scrollback', namespace='/irc')
    def on_clear_scrollback():
        if not getattr(current_user, 'is_authenticated', False):
            return False
        with _scrollback_lock:
            _scrollback.pop(current_user.id, None)
        emit('irc_system', {'text': 'Scrollback cleared.'})
