"""Real end-to-end test for SSH public-key authentication (gap-analysis
follow-up round, Phase D): a genuine asyncssh client, with a real
private key, connecting to the actual running SSH server
(anetbbs.core.ssh_server.start_ssh_server) started in this test, over
a real loopback socket -- not a unit test of the pieces in isolation.

Confirms the two things that actually matter for this feature:
  1. A registered key logs straight in with no password prompt.
  2. An unregistered key does NOT grant access (falls through to
     password auth being offered instead, same as any account with no
     key registered at all -- password_auth_supported() stays True
     throughout, this feature is purely additive).
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncssh

import anetbbs.config as cfg_mod

SSH_PORT = 12234  # unlikely to collide with the real service (2234)


class SshPublicKeyAuthE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_db = str(Path(__file__).resolve().parent / '.ssh_pubkey_e2e_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, UserSSHKey
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

        cls._key_dir = tempfile.mkdtemp()
        cls.client_key = asyncssh.generate_private_key('ssh-ed25519')
        cls.unregistered_key = asyncssh.generate_private_key('ssh-ed25519')
        pub_text = cls.client_key.export_public_key().decode()
        fingerprint = asyncssh.import_public_key(pub_text).get_fingerprint()

        with cls.app.app_context():
            db.create_all()
            db.session.query(UserSSHKey).delete()
            db.session.query(User).filter_by(username='e2ekeytest').delete()
            u = User(username='e2ekeytest', email='e2ekeytest@example.com')
            u.set_password('a-real-password-not-used-by-this-test')
            db.session.add(u)
            db.session.commit()
            db.session.add(UserSSHKey(user_id=u.id, public_key=pub_text,
                                      fingerprint=fingerprint, label='test'))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        import shutil
        shutil.rmtree(cls._key_dir, ignore_errors=True)

    def _run(self, coro, timeout=10):
        return asyncio.run(asyncio.wait_for(coro, timeout=timeout))

    async def _start_server(self):
        # DATABASE_URL must be set before user_manager.py's own module-
        # level engine resolves it (see that module's docstring on why
        # it has an independent engine from the Flask app) -- setUpClass
        # already points TestingConfig at our tmp DB and every other
        # test file in this run shares process state, so this should
        # already be correct by the time this test runs; set it again
        # explicitly here for this test's own clarity/safety.
        os.environ['DATABASE_URL'] = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

        from anetbbs.core.ssh_server import start_ssh_server
        # Real bug found writing this test: a `with tempfile.
        # TemporaryDirectory():` scoped only around start_ssh_server()
        # deletes the host-key file the instant this function returns
        # (the with-block's __exit__ runs as part of unwinding the
        # `return`), while the server itself keeps running -- confirmed
        # live as the actual cause of a ConnectionLost failure that
        # looked identical to an auth bug at first glance. The temp dir
        # must outlive the server, so it's created once in setUpClass
        # and cleaned up in tearDownClass instead of scoped per call.
        key_file = os.path.join(self._key_dir, f'host_key_{id(self)}')
        bbs_config = {'server': {'host': '127.0.0.1', 'port': 0}}
        server = await start_ssh_server('127.0.0.1', SSH_PORT, key_file, bbs_config)
        return server

    async def _connect_and_read_banner(self, client_key, username='e2ekeytest'):
        # encoding=None matches the server's own create_server(encoding=
        # None) -- the BBS speaks raw CP437 bytes over the wire (real
        # box-drawing/high-bit bytes like 0xFF), not UTF-8 text. A
        # client left at asyncssh's UTF-8 default crashes with a
        # ProtocolError decode failure the moment any such byte arrives
        # -- confirmed live while writing this test (auth itself
        # succeeded; only the client-side text-mode assumption broke).
        async with asyncssh.connect(
                '127.0.0.1', SSH_PORT, username=username,
                client_keys=[client_key], known_hosts=None,
                preferred_auth=['publickey', 'password'],
                encoding=None) as conn:
            process = await conn.create_process(term_type='ansi', encoding=None)
            # A single read() can return as soon as ANY bytes are
            # available, not once the full banner has arrived (real
            # gap found writing this test -- a plain single read()
            # captured only an early few bytes, well before the
            # session's own `await asyncio.sleep(1)` welcome pause had
            # even finished). Accumulate reads for a fixed window
            # instead so slower/chunked delivery doesn't look like a
            # missing banner.
            banner = b''
            deadline = asyncio.get_event_loop().time() + 3
            while asyncio.get_event_loop().time() < deadline:
                try:
                    chunk = await asyncio.wait_for(
                        process.stdout.read(4096),
                        timeout=deadline - asyncio.get_event_loop().time())
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                banner += chunk
            process.stdin.write_eof()
            try:
                process.close()
            except Exception:
                pass
            return banner.decode('utf-8', errors='replace')

    def test_registered_key_logs_in_with_no_password_prompt(self):
        async def scenario():
            server = await self._start_server()
            try:
                banner = await self._connect_and_read_banner(self.client_key)
                return banner
            finally:
                server.close()
                await server.wait_closed()

        banner = self._run(scenario())
        self.assertIn('e2ekeytest', banner)
        self.assertIn('SSH key', banner,
                      'banner should say the login was via SSH key, not password')
        self.assertNotIn('Password for', banner,
                         'a registered key must never trigger a password prompt')

    def test_unregistered_key_does_not_grant_pubkey_access(self):
        """An unregistered key must not authenticate via public-key --
        asyncssh should fall through and offer password auth instead,
        which then fails without a real password (none supplied here),
        so the connection attempt should fail outright rather than
        silently logging in as anyone."""
        async def scenario():
            server = await self._start_server()
            try:
                with self.assertRaises(asyncssh.PermissionDenied):
                    async with asyncssh.connect(
                            '127.0.0.1', SSH_PORT, username='e2ekeytest',
                            client_keys=[self.unregistered_key],
                            known_hosts=None,
                            preferred_auth=['publickey']):
                        pass
            finally:
                server.close()
                await server.wait_closed()

        self._run(scenario())


if __name__ == '__main__':
    unittest.main()
