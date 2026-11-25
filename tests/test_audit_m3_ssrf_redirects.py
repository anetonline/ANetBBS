"""M-3: outbound fetches of user/publisher-influenced URLs must not reach
internal addresses -- neither directly (100.64.0.0/10 used to pass the
guard) nor via an HTTP redirect (`curl -L`, urllib and requests followed
redirects on their own after only the first hop was checked).
"""
import http.server
import ipaddress
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from anetbbs.core import net_safety


class AddressPolicy(unittest.TestCase):
    def test_non_global_addresses_rejected(self):
        for a in ('100.64.0.1', '100.100.100.100', '100.100.100.200', '10.0.0.1',
                  '127.0.0.1', '169.254.169.254', '192.168.1.1', '0.0.0.0',
                  '198.18.0.1', '192.0.0.170', '::1', 'fe80::1', 'fc00::1',
                  '::ffff:127.0.0.1', '::ffff:100.64.0.1', '::ffff:10.0.0.1',
                  '2002:7f00:0001::1', '224.0.0.1'):
            with self.subTest(a=a):
                self.assertFalse(net_safety.is_public_address(ipaddress.ip_address(a)))

    def test_public_addresses_allowed(self):
        for a in ('1.1.1.1', '8.8.8.8', '2606:4700:4700::1111'):
            with self.subTest(a=a):
                self.assertTrue(net_safety.is_public_address(ipaddress.ip_address(a)))

    def test_any_internal_record_rejects_the_name(self):
        with mock.patch('socket.getaddrinfo', return_value=[
                (2, 1, 6, '', ('1.1.1.1', 80)), (2, 1, 6, '', ('10.0.0.5', 80))]):
            _f, _s, err = net_safety.resolve_safe_destination('mixed.example', 80)
        self.assertTrue(err)

    def test_resolve_guard_rejects_cgnat(self):
        with mock.patch('socket.getaddrinfo',
                        return_value=[(2, 1, 6, '', ('100.100.100.100', 80))]):
            _f, _s, err = net_safety.resolve_safe_destination('tailnet.example', 80)
        self.assertTrue(err)


class _Handler(http.server.BaseHTTPRequestHandler):
    hits = None
    routes = None

    def do_GET(self):  # noqa: N802
        type(self).hits.append((self.server.server_port, self.path))
        status, headers, body = type(self).routes.get(
            (self.server.server_port, self.path), (404, {}, b'nope'))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@unittest.skipUnless(shutil.which('curl'), 'curl not installed')
class RedirectHops(unittest.TestCase):
    def setUp(self):
        _Handler.hits = []
        _Handler.routes = {}
        self.servers = []
        for _ in range(2):
            srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.servers.append(srv)
            self.addCleanup(srv.shutdown)
        self.pub, self.internal = (s.server_port for s in self.servers)
        real = net_safety.resolve_safe_destination
        pub_port = self.pub

        def fake_resolve(host, port, own_ports=None):
            # 'public.test' stands in for an internet host; it is served by
            # local server A. Everything else goes through the real guard,
            # so 127.0.0.1 (server B, "internal") is refused.
            if host in ('public.test', 'public2.test') and port == pub_port:
                return 2, ('127.0.0.1', port), None
            return real(host, port, own_ports)
        p = mock.patch.object(net_safety, 'resolve_safe_destination', side_effect=fake_resolve)
        p.start()
        self.addCleanup(p.stop)

    def test_redirect_to_internal_is_refused_and_never_fetched(self):
        _Handler.routes[(self.pub, '/feed')] = (
            302, {'Location': f'http://127.0.0.1:{self.internal}/latest/meta-data'}, b'')
        _Handler.routes[(self.internal, '/latest/meta-data')] = (200, {}, b'SECRET')
        body, err = net_safety.safe_http_fetch(f'http://public.test:{self.pub}/feed')
        self.assertIsNone(body)
        self.assertIn('refused', err)
        self.assertNotIn(self.internal, [port for port, _p in _Handler.hits])

    def test_redirect_to_public_host_is_followed(self):
        _Handler.routes[(self.pub, '/a')] = (
            301, {'Location': f'http://public2.test:{self.pub}/b'}, b'')
        _Handler.routes[(self.pub, '/b')] = (200, {}, b'<rss>ok</rss>')
        body, err = net_safety.safe_http_fetch(f'http://public.test:{self.pub}/a')
        self.assertIsNone(err)
        self.assertEqual(body, b'<rss>ok</rss>')

    def test_redirect_loop_bounded(self):
        _Handler.routes[(self.pub, '/loop')] = (
            302, {'Location': f'http://public.test:{self.pub}/loop'}, b'')
        body, err = net_safety.safe_http_fetch(f'http://public.test:{self.pub}/loop',
                                               max_redirects=3)
        self.assertIsNone(body)
        self.assertIn('redirects', err)
        self.assertEqual(len(_Handler.hits), 4)

    def test_rss_poller_does_not_store_internal_content(self):
        import anetbbs.config as cfg_mod
        orig = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        tmp = tempfile.mkdtemp(prefix='m3-')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmp}/t.db'
        try:
            os.environ['FLASK_ENV'] = 'testing'
            from anetbbs.web_app import create_app
            from anetbbs.models import db, RssFeed, RssItem
            from anetbbs.rss.poller import _import_one_feed
            app = create_app('testing')
            with app.app_context():
                db.create_all()
                f = RssFeed(name='evil', url=f'http://public.test:{self.pub}/feed',
                            is_active=True)
                db.session.add(f)
                db.session.commit()
                fid = f.id
            _Handler.routes[(self.pub, '/feed')] = (
                302, {'Location': f'http://127.0.0.1:{self.internal}/x'}, b'')
            _Handler.routes[(self.internal, '/x')] = (
                200, {'Content-Type': 'application/rss+xml'},
                b'<rss><channel><item><title>SECRET</title></item></channel></rss>')
            self.assertEqual(_import_one_feed(app, fid), 0)
            with app.app_context():
                self.assertEqual(RssItem.query.count(), 0)
                self.assertIn('refused', RssFeed.query.get(fid).last_error)
            self.assertNotIn(self.internal, [port for port, _p in _Handler.hits])
        finally:
            cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = orig


class WebhooksDoNotFollowRedirects(unittest.TestCase):
    def test_allow_redirects_false(self):
        from anetbbs.features import webhooks
        with mock.patch.object(webhooks, 'resolve_safe_destination',
                               return_value=(2, ('1.1.1.1', 443), None)), \
                mock.patch.object(webhooks.requests, 'post') as post:
            post.return_value.status_code = 204
            status, err = webhooks._do_post('https://hooks.example/x', '{}', {}, timeout=5)
        self.assertEqual(status, 204)
        self.assertIs(post.call_args.kwargs.get('allow_redirects'), False)


if __name__ == '__main__':
    unittest.main()
