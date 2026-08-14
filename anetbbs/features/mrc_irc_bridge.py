# anetbbs/features/mrc_irc_bridge.py
"""
MRC ↔ IRC bridge runner.

Each MrcIrcBridge row spawns one bridge process that:
    1. Opens a websocket to the local MRC bridge (anetbbs MRC service).
    2. Opens a TCP socket to the configured IRC server, joins the channel.
    3. Bidirectionally relays messages with "[mrc:user]" / "[irc:nick]"
       prefixes so participants on either side know the source.

Run a bridge via the systemd unit anetbbs-mrc-irc-bridge.service which
calls `python -m anetbbs.features.mrc_irc_bridge --bridge-id N`.
"""
import argparse
import asyncio
import json
import logging
import re
import ssl as ssl_module
import sys
from datetime import datetime

import aiohttp


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# IRC client (asyncio version, mIRC-color-stripping)
# ---------------------------------------------------------------------------

_MIRC_COLOR_RE = re.compile(
    r'\x03(\d{1,2}(,\d{1,2})?)?|\x04[0-9a-fA-F]{6}|[\x02\x0f\x16\x1d\x1e\x1f]'
)


def _strip_mirc(s):
    return _MIRC_COLOR_RE.sub('', s or '')


class _IrcLeg:
    """Minimal asyncio IRC client for the bridge — speaks just enough to
    join one channel and pump messages."""

    def __init__(self, server, port, use_ssl, nick, channel, channel_key='',
                 sasl_user=None, sasl_pass=None,
                 on_message=None, on_action=None, on_join=None, on_part=None):
        self.server = server
        self.port = port
        self.use_ssl = use_ssl
        self.nick = nick
        self.channel = channel
        self.channel_key = channel_key
        self.sasl_user = sasl_user
        self.sasl_pass = sasl_pass
        self.reader = None
        self.writer = None
        self.connected = False
        self.on_message = on_message
        self.on_action = on_action
        self.on_join = on_join
        self.on_part = on_part
        self._sasl_state = 'pending' if (sasl_user and sasl_pass) else None

    async def connect(self):
        ctx = ssl_module.create_default_context() if self.use_ssl else None
        # Real gap found in a security/performance audit: no explicit
        # `limit=` was passed here, leaving readline() (below, in run())
        # relying on asyncio's undocumented-at-this-call-site default
        # (65536 bytes). More importantly, if a peer ever DID send a
        # line exceeding that limit with no '\n', StreamReader.readline()
        # raises asyncio.LimitOverrunError -- NOT a subclass of OSError/
        # ConnectionError, so it fell straight through run()'s except
        # clause uncaught, silently killing the read loop instead of
        # disconnecting cleanly like every other failure mode here does.
        # An explicit, modest limit (real IRC lines are bounded to 512
        # bytes by RFC 2812/1459; 8 KiB is generous headroom for servers
        # that don't enforce that) plus catching the error in run()
        # below closes both gaps.
        self.reader, self.writer = await asyncio.open_connection(
            self.server, self.port, ssl=ctx, limit=8192)
        if self._sasl_state == 'pending':
            await self._send('CAP LS 302')
        await self._send(f'NICK {self.nick}')
        await self._send(f'USER {self.nick} 0 * :ANetBBS bridge')
        self.connected = True

    async def _send(self, line):
        if not self.writer:
            return
        try:
            self.writer.write((line + '\r\n').encode('utf-8', errors='replace'))
            await self.writer.drain()
        except (OSError, ConnectionError):
            self.connected = False

    async def say(self, text):
        await self._send(f'PRIVMSG {self.channel} :{text}')

    async def quit(self, msg='bridge stopping'):
        if self.connected:
            try:
                await self._send(f'QUIT :{msg}')
            finally:
                self.connected = False
                if self.writer:
                    try:
                        self.writer.close()
                        await self.writer.wait_closed()
                    except (OSError, ConnectionError):
                        pass

    async def run(self):
        while self.connected and self.reader:
            try:
                raw = await self.reader.readline()
            except (OSError, ConnectionError):
                break
            except asyncio.LimitOverrunError:
                # A line exceeded the connect()-time limit with no '\n'
                # -- treat exactly like any other unrecoverable read
                # failure (disconnect) rather than letting it propagate
                # uncaught, which used to silently kill this loop with
                # no cleanup and left the offending bytes sitting in the
                # reader's internal buffer forever (readline() doesn't
                # discard them on this error).
                logger.warning('IRC leg %s: line exceeded read limit, disconnecting',
                               self.server)
                break
            if not raw:
                break
            try:
                await self._handle(raw.decode('utf-8', errors='replace').rstrip('\r\n'))
            except Exception:
                logger.exception('IRC handler crashed')
        self.connected = False

    async def _handle(self, line):
        if line.startswith('PING'):
            payload = line[5:] if len(line) > 5 else ''
            await self._send(f'PONG {payload}')
            return
        if line.startswith('AUTHENTICATE '):
            chal = line[len('AUTHENTICATE '):].strip()
            if chal == '+' and self._sasl_state == 'auth_sent':
                import base64
                payload = (f'\x00{self.sasl_user}\x00{self.sasl_pass}').encode()
                await self._send('AUTHENTICATE ' +
                                 base64.b64encode(payload).decode('ascii'))
            return

        prefix = ''
        rest = line
        if line.startswith(':'):
            prefix, _, rest = line[1:].partition(' ')
        trailing = ''
        if ' :' in rest:
            head, trailing = rest.split(' :', 1)
            args = head.split()
        else:
            args = rest.split()
        if not args:
            return
        cmd = args[0].upper()
        params = args[1:]
        sender = prefix.split('!', 1)[0]

        if cmd == 'CAP' and len(params) >= 2:
            sub = params[1].upper()
            caps = (trailing or '').split()
            if sub == 'LS' and 'sasl' in caps and self._sasl_state == 'pending':
                await self._send('CAP REQ :sasl')
            elif sub == 'ACK' and 'sasl' in caps:
                await self._send('AUTHENTICATE PLAIN')
                self._sasl_state = 'auth_sent'
            elif sub in ('NAK',) or 'sasl' not in caps:
                await self._send('CAP END')
                self._sasl_state = 'failed'
        elif cmd == '903':
            self._sasl_state = 'done'
            await self._send('CAP END')
        elif cmd in ('902', '904', '905', '906', '907'):
            self._sasl_state = 'failed'
            await self._send('CAP END')
        elif cmd == '001':   # registered
            join_line = (f'JOIN {self.channel} {self.channel_key}'
                         if self.channel_key else f'JOIN {self.channel}')
            await self._send(join_line)
        elif cmd == 'PRIVMSG' and len(params) >= 1 and params[0] == self.channel:
            text = trailing
            if text.startswith('\x01ACTION ') and text.endswith('\x01'):
                if self.on_action:
                    await self.on_action(sender, _strip_mirc(text[8:-1]))
            else:
                if self.on_message:
                    await self.on_message(sender, _strip_mirc(text))
        elif cmd == 'JOIN' and (params and params[0] == self.channel
                                or trailing == self.channel):
            if self.on_join and sender != self.nick:
                await self.on_join(sender)
        elif cmd == 'PART' and params and params[0] == self.channel:
            if self.on_part and sender != self.nick:
                await self.on_part(sender)


# ---------------------------------------------------------------------------
# MRC websocket leg
# ---------------------------------------------------------------------------

class _MrcLeg:
    """Connects to the local MRC bridge via WebSocket and joins one room."""

    def __init__(self, ws_url, room, handle,
                 on_message=None, on_action=None, on_join=None, on_part=None):
        self.ws_url = ws_url
        self.room = room
        self.handle = handle
        self.on_message = on_message
        self.on_action = on_action
        self.on_join = on_join
        self.on_part = on_part
        self.session = None
        self.ws = None
        self.connected = False

    async def connect(self):
        self.session = aiohttp.ClientSession()
        # Real gap found in a security/performance audit: no explicit
        # max_msg_size was set here, leaving this connection to the
        # upstream MRC bridge relying on aiohttp's own default (4 MiB)
        # rather than a limit chosen for what this protocol actually
        # needs -- a single chat message/event never needs anywhere
        # close to that. An explicit, much smaller cap is defense in
        # depth: it doesn't depend on a third-party library's default
        # staying safe across future aiohttp versions, and bounds how
        # much memory one oversized frame from a compromised/malicious
        # upstream bridge can force this process to hold.
        self.ws = await self.session.ws_connect(
            self.ws_url, heartbeat=30, max_msg_size=262144)
        # MRC bridge protocol — handshake message naming the handle and room.
        await self._send_json({'type': 'login', 'handle': self.handle,
                               'room': self.room})
        self.connected = True

    async def _send_json(self, obj):
        if self.ws is None or self.ws.closed:
            return
        try:
            await self.ws.send_json(obj)
        except Exception:
            logger.exception('MRC send failed')
            self.connected = False

    async def say(self, text):
        await self._send_json({'type': 'message', 'room': self.room,
                               'handle': self.handle, 'text': text})

    async def close(self):
        try:
            if self.ws and not self.ws.closed:
                await self.ws.close()
            if self.session and not self.session.closed:
                await self.session.close()
        except Exception:
            pass
        self.connected = False

    async def run(self):
        if self.ws is None:
            return
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except (ValueError, TypeError):
                    continue
                # MRC bridge message format varies — handle the common ones.
                t = (data.get('type') or '').lower()
                room = data.get('room') or data.get('channel')
                if room and room != self.room:
                    continue
                user = data.get('handle') or data.get('user') or data.get('nick') or ''
                text = data.get('text') or data.get('message') or ''
                if t in ('message', 'msg', 'chat'):
                    if user == self.handle:
                        continue   # don't echo ourselves
                    if self.on_message:
                        await self.on_message(user, text)
                elif t == 'action':
                    if user == self.handle:
                        continue
                    if self.on_action:
                        await self.on_action(user, text)
                elif t in ('join', 'enter'):
                    if user != self.handle and self.on_join:
                        await self.on_join(user)
                elif t in ('part', 'leave'):
                    if user != self.handle and self.on_part:
                        await self.on_part(user)
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                break
        self.connected = False


# ---------------------------------------------------------------------------
# Bridge orchestration
# ---------------------------------------------------------------------------

async def run_bridge(bridge_id):
    """Run one MrcIrcBridge row until both sides die."""
    # We import models inside an app context so we hit the same DB the web uses.
    import os
    from flask import Flask
    from anetbbs.config import get_config
    from anetbbs.models import db, MrcIrcBridge

    app = Flask(__name__)
    app.config.from_object(get_config(os.environ.get('FLASK_ENV', 'production')))
    db.init_app(app)
    with app.app_context():
        cfg = MrcIrcBridge.query.get(bridge_id)
        if cfg is None:
            logger.error('bridge id %s not found', bridge_id)
            return
        if not cfg.is_active:
            logger.warning('bridge %s is not active; refusing to run', bridge_id)
            return
        # Snapshot config to plain values so we can drop the app context.
        c = {
            'name': cfg.name, 'mrc_room': cfg.mrc_room,
            'mrc_handle': cfg.mrc_handle or 'ircbridge',
            'mrc_ws_url': cfg.mrc_ws_url or 'ws://127.0.0.1:8080/ws',
            'irc_server': cfg.irc_server, 'irc_port': cfg.irc_port or 6667,
            'irc_use_ssl': bool(cfg.irc_use_ssl),
            'irc_nick': cfg.irc_nick or 'ANETBridge',
            'irc_channel': cfg.irc_channel,
            'irc_channel_key': cfg.irc_channel_key or '',
            'sasl_user': cfg.sasl_user or None,
            'sasl_pass': cfg.sasl_pass or None,
        }
        cfg.last_started_at = datetime.utcnow()
        cfg.last_error = None
        db.session.commit()

    # ------- relay callbacks (use closures over both legs) -------
    irc_leg = mrc_leg = None

    async def on_mrc_msg(user, text):
        if irc_leg and irc_leg.connected:
            await irc_leg.say(f'[mrc:{user}] {text}')

    async def on_mrc_action(user, text):
        if irc_leg and irc_leg.connected:
            await irc_leg.say(f'\x01ACTION [mrc] {user} {text}\x01')

    async def on_mrc_join(user):
        if irc_leg and irc_leg.connected:
            await irc_leg.say(f'\x01ACTION [mrc] {user} joined\x01')

    async def on_mrc_part(user):
        if irc_leg and irc_leg.connected:
            await irc_leg.say(f'\x01ACTION [mrc] {user} left\x01')

    async def on_irc_msg(nick, text):
        if mrc_leg and mrc_leg.connected:
            await mrc_leg.say(f'[irc:{nick}] {text}')

    async def on_irc_action(nick, text):
        if mrc_leg and mrc_leg.connected:
            await mrc_leg.say(f'* [irc] {nick} {text}')

    async def on_irc_join(nick):
        if mrc_leg and mrc_leg.connected:
            await mrc_leg.say(f'* [irc] {nick} joined')

    async def on_irc_part(nick):
        if mrc_leg and mrc_leg.connected:
            await mrc_leg.say(f'* [irc] {nick} left')

    irc_leg = _IrcLeg(
        c['irc_server'], c['irc_port'], c['irc_use_ssl'],
        c['irc_nick'], c['irc_channel'], c['irc_channel_key'],
        c['sasl_user'], c['sasl_pass'],
        on_message=on_irc_msg, on_action=on_irc_action,
        on_join=on_irc_join, on_part=on_irc_part)
    mrc_leg = _MrcLeg(
        c['mrc_ws_url'], c['mrc_room'], c['mrc_handle'],
        on_message=on_mrc_msg, on_action=on_mrc_action,
        on_join=on_mrc_join, on_part=on_mrc_part)

    try:
        await asyncio.gather(irc_leg.connect(), mrc_leg.connect())
    except Exception as exc:
        logger.exception('bridge connect failed: %s', exc)
        with app.app_context():
            cfg = MrcIrcBridge.query.get(bridge_id)
            if cfg:
                cfg.last_error = f'connect: {exc}'
                db.session.commit()
        await mrc_leg.close()
        await irc_leg.quit()
        return

    logger.info('bridge "%s" up: %s <-> %s on %s',
                c['name'], c['mrc_room'], c['irc_channel'], c['irc_server'])
    try:
        await asyncio.gather(irc_leg.run(), mrc_leg.run(),
                             return_exceptions=True)
    finally:
        await mrc_leg.close()
        await irc_leg.quit()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    p = argparse.ArgumentParser()
    p.add_argument('--bridge-id', type=int, required=True,
                   help='MrcIrcBridge.id row to run')
    args = p.parse_args()
    try:
        asyncio.run(run_bridge(args.bridge_id))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == '__main__':
    main()
