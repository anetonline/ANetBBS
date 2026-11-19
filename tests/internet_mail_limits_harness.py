"""Shared fixtures/helpers for the Internet Mail storage-limit tests
(mailbox quota, attachment limits, atomic outbound rate limits).

Not collected by pytest (no test_ prefix). Every database is a throwaway
SQLite file under pytest's tmp_path; every listener binds 127.0.0.1 on an
OS-chosen port; DNSBL lookups are patched out; outbound relay sends are
replaced with an in-process fake -- nothing leaves loopback.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

SRC = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).resolve().parent / 'internet_mail_concurrency_worker.py'
MAIL_DOMAIN = 'bbs.limits.test'


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope='module')
def mail_app(tmp_path_factory):
    import anetbbs.config as cfg_mod
    orig = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
    db_path = tmp_path_factory.mktemp('maillimits') / 'limits.db'
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    os.environ['SMTP_LISTEN_HOST'] = '127.0.0.1'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['_LIMITS_DB_PATH'] = str(db_path)
    yield app
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = orig


@pytest.fixture
def clean_mail(mail_app):
    """Fresh users/mail rows and default-limit config before each test."""
    from anetbbs.mail import smtp_server
    from anetbbs.models import (db, User, InternetMail, MailConfig, SmtpConfig,
                                MAILBOX_QUOTA_DEFAULT_BYTES, MAX_ATTACHMENTS_DEFAULT,
                                MAX_ATTACHMENT_BYTES_DEFAULT)
    if hasattr(smtp_server._flask_app, '_app'):
        del smtp_server._flask_app._app
    with mail_app.app_context():
        InternetMail.query.delete()
        User.query.filter(User.username.like('lim_%')).delete(synchronize_session=False)
        cfg = MailConfig.get()
        cfg.enabled = True
        cfg.domain = MAIL_DOMAIN
        cfg.min_access_level = 0
        cfg.max_message_size = 4 * 1024 * 1024
        cfg.max_recipients = 5
        cfg.outbound_per_hour = 10
        cfg.outbound_per_day = 50
        cfg.mailbox_quota_bytes = MAILBOX_QUOTA_DEFAULT_BYTES
        cfg.max_attachments = MAX_ATTACHMENTS_DEFAULT
        cfg.max_attachment_bytes = MAX_ATTACHMENT_BYTES_DEFAULT
        smtp = SmtpConfig.get()
        smtp.enabled = False
        db.session.commit()
    yield mail_app


def set_cfg(app, **values):
    from anetbbs.models import db, MailConfig
    with app.app_context():
        cfg = MailConfig.get()
        for k, v in values.items():
            setattr(cfg, k, v)
        db.session.commit()


def make_user(app, username, *, enabled=True, access_level=10):
    from anetbbs.models import db, User
    with app.app_context():
        u = User(username=username, email=f'{username}@example.invalid',
                 password_hash='x', email_enabled=enabled,
                 email_local_part=username, access_level=access_level)
        db.session.add(u)
        db.session.commit()
        return u.id


def addr(username):
    return f'{username}@{MAIL_DOMAIN}'


def usage(app, user_id):
    from anetbbs.mail.limits import mailbox_usage_bytes
    with app.app_context():
        return mailbox_usage_bytes(user_id)


def inbound_rows(app, user_id=None):
    from anetbbs.models import InternetMail
    with app.app_context():
        q = InternetMail.query.filter_by(direction='inbound')
        if user_id is not None:
            q = q.filter_by(user_id=user_id)
        return [(m.id, m.user_id, m.subject, m.stored_bytes, len(m.raw or b''),
                 len((m.body or '').encode('utf-8')))
                for m in q.order_by(InternetMail.id).all()]


def plain_message(subject, body_text):
    """CRLF RFC 5322 text message, no dot-stuffing needed (no leading dots)."""
    return (f'From: sender@remote.test\r\nSubject: {subject}\r\n'
            f'Content-Type: text/plain; charset=utf-8\r\n\r\n{body_text}\r\n').encode('utf-8')


def charge_of(app, raw_bytes):
    """Exactly what one stored copy of raw_bytes charges (parse it the way
    the server does)."""
    from email import message_from_bytes, policy
    from anetbbs.mail.limits import stored_bytes_for
    msg = message_from_bytes(raw_bytes, policy=policy.default)
    return stored_bytes_for(raw_bytes, msg.get_content())


# ---------------------------------------------------------------------------
# Wire harness
# ---------------------------------------------------------------------------

class SmtpClient:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    async def reply(self, timeout=10):
        line = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
        return line.decode('utf-8', 'replace').rstrip('\r\n')

    async def send(self, line, timeout=10):
        self.writer.write((line + '\r\n').encode('utf-8'))
        await self.writer.drain()
        return await self.reply(timeout)

    async def send_data(self, raw_bytes, timeout=20):
        """Send a message body + terminator (dot-stuffing applied)."""
        lines = raw_bytes.split(b'\r\n')
        if lines and lines[-1] == b'':
            lines = lines[:-1]
        out = b''.join((b'.' + ln if ln.startswith(b'.') else ln) + b'\r\n' for ln in lines)
        self.writer.write(out + b'.\r\n')
        await self.writer.drain()
        return await self.reply(timeout)

    async def close(self):
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


def run_with_server(client_coro_fn):
    from anetbbs.mail import smtp_server
    port = free_port()
    os.environ['SMTP_LISTEN_PORT'] = str(port)

    async def connect(local_ip='127.0.0.1'):
        r, w = await asyncio.open_connection('127.0.0.1', port, local_addr=(local_ip, 0))
        c = SmtpClient(r, w)
        greeting = await c.reply()
        assert greeting.startswith('220'), greeting
        return c

    async def main():
        with mock.patch('anetbbs.mail.smtp_server._dnsbl_check', return_value=False):
            task = asyncio.ensure_future(smtp_server.serve())
            try:
                for _ in range(100):
                    await asyncio.sleep(0.02)
                    if task.done():
                        task.result()
                    try:
                        # probe from another loopback source address so the
                        # per-IP session cap is untouched for the test
                        _r, _w = await asyncio.open_connection(
                            '127.0.0.1', port, local_addr=('127.0.0.3', 0))
                        await asyncio.wait_for(_r.readline(), 2)
                        _w.close()
                        await _w.wait_closed()
                        break
                    except OSError:
                        continue
                return await client_coro_fn(connect)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    return asyncio.run(main())


async def transaction(connect, rcpts, raw_bytes, *, local_ip='127.0.0.1'):
    """One full SMTP transaction. Returns {'rcpt': [...], 'data': str|None}."""
    c = await connect(local_ip)
    out = {'rcpt': [], 'data': None}
    assert (await c.send('EHLO client.test')).startswith('250')
    assert (await c.send('MAIL FROM:<sender@remote.test>')).startswith('250')
    for r in rcpts:
        out['rcpt'].append(await c.send(f'RCPT TO:<{r}>'))
    if any(x.startswith('250') for x in out['rcpt']):
        assert (await c.send('DATA')).startswith('354')
        out['data'] = await c.send_data(raw_bytes)
    await c.send('QUIT')
    await c.close()
    return out


# ---------------------------------------------------------------------------
# Cross-process concurrency
# ---------------------------------------------------------------------------

def run_workers(app, mode, user_id, *, procs, per_proc, tmp_path, raw=b''):
    """Run `procs` OS processes that each perform `per_proc` deliveries
    (mode='deliver') or outbound sends (mode='send') for `user_id` against
    this module's SQLite file, all released at the same instant. Returns
    the flat list of per-attempt results."""
    raw_path = tmp_path / 'payload.eml'
    raw_path.write_bytes(raw)
    start_at = time.time() + 2.0
    env = dict(os.environ, PYTHONPATH=str(SRC), FLASK_ENV='testing',
               PYTHONDONTWRITEBYTECODE='1')
    children = []
    for i in range(procs):
        out = tmp_path / f'worker-{mode}-{i}.json'
        children.append((out, subprocess.Popen(
            [sys.executable, str(WORKER), app.config['_LIMITS_DB_PATH'], mode,
             str(user_id), str(per_proc), repr(start_at), str(raw_path),
             addr(f'lim_target'), str(out)],
            cwd=str(SRC), env=env)))
    results = []
    for out, proc in children:
        assert proc.wait(timeout=120) == 0, f'worker failed: {out}'
        results.extend(json.loads(out.read_text()))
    return results
