"""Regression test for a real live bug found 2026-09-22 investigating a
sysop report that the web MRC client needed /identify far more often
than the terminal client, even after a separate bridge-side reconnect-
ordering bug had already been fixed.

Root cause was in the nginx config, not the bridge: install.sh's
generated /mrcws and /socket.io/ location blocks each set their own
proxy_set_header lines (Upgrade/Connection, needed for the WebSocket
handshake) without re-declaring the server-level Host/X-Real-IP/
X-Forwarded-For/X-Forwarded-Proto block above them. nginx does not
merge proxy_set_header directives across levels -- any proxy_set_header
at a location level drops ALL inherited ones from the server level, not
just the one being overridden. So X-Forwarded-For never reached the
bridge for web-UI connections; mrc/bridge/main.py's _handle_join_room
correctly falls back to treating that the same as "no trusted caller
IP", which forces an extra /identify the terminal client (which sends
its own real IP explicitly in the join_room message, sidestepping
nginx entirely) never hit.

update.sh (v1.0.99) gained a self-healing patch for this, alongside the
already-fixed deploy/anetbbs-nginx.conf.template and install.sh
generator. This test runs the real patch loop copied out of update.sh
against synthetic nginx configs, in actual bash -- not a
reimplementation -- so it can't drift from what's actually shipped.

Fixture configs are wrapped in a real `server { ... }` block with
4-space-indented location bodies, matching install.sh's own generated
layout exactly -- the patch's own regex keys off that indentation
(`\n    }` as the closing brace) the same way the real file has it.
"""
import re
import subprocess
import unittest
from pathlib import Path


def _wrap(*location_blocks):
    body = '\n'.join(location_blocks)
    return 'server {\n' + body + '\n}\n'


class UpdateShMrcNginxForwardedHeadersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo_root = Path(__file__).resolve().parent.parent
        with open(repo_root / 'update.sh') as f:
            content = f.read()
        m = re.search(
            r'    for NGINX_WS_LOC in "/mrcws" "/socket\.io/"; do\n'
            r'(?:.*\n)*?'
            r'    done\n',
            content)
        assert m is not None, \
            "couldn't find the forwarded-headers patch loop in update.sh"
        cls.patch_loop = m.group(0)

    def _run(self, nginx_config_body):
        script = (
            'NGINX_AVAIL="$(mktemp)"\n'
            f'cat > "$NGINX_AVAIL" <<\'CONF\'\n{nginx_config_body}\nCONF\n'
            'info() { :; }\n'
            'ok() { :; }\n'
            'NGINX_CHANGED=false\n'
            f'{self.patch_loop}\n'
            'cat "$NGINX_AVAIL"\n'
            'echo "===CHANGED:$NGINX_CHANGED==="\n'
            'rm -f "$NGINX_AVAIL"\n'
        )
        out = subprocess.run(['bash', '-c', script],
                              capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, msg=out.stderr)
        body, _, tail = out.stdout.rpartition('===CHANGED:')
        changed = tail.split('===')[0] == 'true'
        return body, changed

    def test_mrcws_missing_headers_gets_patched(self):
        conf = _wrap(
            '    location /mrcws {\n'
            '        auth_request /mrc-auth-check;\n'
            '        proxy_pass         http://127.0.0.1:5001/ws;\n'
            '        proxy_http_version 1.1;\n'
            '        proxy_set_header   Upgrade    $http_upgrade;\n'
            '        proxy_set_header   Connection "upgrade";\n'
            '        proxy_read_timeout 86400s;\n'
            '    }'
        )
        out, changed = self._run(conf)
        self.assertTrue(changed)
        self.assertIn('X-Forwarded-For', out)
        self.assertIn('X-Real-IP', out)
        self.assertIn('proxy_set_header   Host', out)
        self.assertIn('X-Forwarded-Proto', out)

    def test_socketio_missing_headers_gets_patched(self):
        conf = _wrap(
            '    location /socket.io/ {\n'
            '        proxy_pass         http://127.0.0.1:5000/socket.io/;\n'
            '        proxy_http_version 1.1;\n'
            '        proxy_set_header   Upgrade    $http_upgrade;\n'
            '        proxy_set_header   Connection "upgrade";\n'
            '        proxy_read_timeout 86400s;\n'
            '    }'
        )
        out, changed = self._run(conf)
        self.assertTrue(changed)
        self.assertIn('X-Forwarded-For', out)

    def test_already_correct_block_is_left_alone(self):
        conf = _wrap(
            '    location /mrcws {\n'
            '        auth_request /mrc-auth-check;\n'
            '        proxy_pass         http://127.0.0.1:5001/ws;\n'
            '        proxy_http_version 1.1;\n'
            '        proxy_set_header   Host              $host;\n'
            '        proxy_set_header   X-Real-IP         $remote_addr;\n'
            '        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;\n'
            '        proxy_set_header   X-Forwarded-Proto $scheme;\n'
            '        proxy_set_header   Upgrade    $http_upgrade;\n'
            '        proxy_set_header   Connection "upgrade";\n'
            '        proxy_read_timeout 86400s;\n'
            '    }'
        )
        out, changed = self._run(conf)
        self.assertFalse(changed)
        self.assertEqual(out.count('X-Forwarded-For'), 1)

    def test_patch_is_idempotent_when_run_twice(self):
        conf = _wrap(
            '    location /mrcws {\n'
            '        proxy_pass         http://127.0.0.1:5001/ws;\n'
            '        proxy_http_version 1.1;\n'
            '        proxy_set_header   Upgrade    $http_upgrade;\n'
            '        proxy_set_header   Connection "upgrade";\n'
            '        proxy_read_timeout 86400s;\n'
            '    }'
        )
        once, changed_once = self._run(conf)
        self.assertTrue(changed_once)
        twice, changed_twice = self._run(once)
        self.assertFalse(changed_twice)
        self.assertEqual(twice.count('X-Forwarded-For'), 1)

    def test_unrelated_location_is_untouched(self):
        conf = _wrap(
            '    location /static/ {\n'
            '        alias /opt/anetbbs/anetbbs/static/;\n'
            '        add_header Cache-Control "public, max-age=86400";\n'
            '    }',
            '    location /mrcws {\n'
            '        proxy_pass         http://127.0.0.1:5001/ws;\n'
            '        proxy_http_version 1.1;\n'
            '        proxy_set_header   Upgrade    $http_upgrade;\n'
            '        proxy_set_header   Connection "upgrade";\n'
            '        proxy_read_timeout 86400s;\n'
            '    }'
        )
        out, changed = self._run(conf)
        self.assertTrue(changed)
        static_block = out.split('location /mrcws')[0]
        self.assertNotIn('X-Forwarded-For', static_block)


if __name__ == '__main__':
    unittest.main()
