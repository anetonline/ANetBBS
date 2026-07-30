"""Regression test for a live bug reported by a real sysop right after
running update.sh: the MRC nginx-proxy verification check
(anetbbs/../update.sh, added v1.0b2.232-235) warned "nginx /mrcws proxy
points at port 127\\n0\\n0\\n1\\n8080" instead of a clean port number
-- on an install that was actually configured correctly.

Root cause: `grep -oE '127\\.0\\.0\\.1:[0-9]+/ws;' ... | grep -oE
'[0-9]+'` extracts EVERY run of digits from the matched line, not just
the port -- "127.0.0.1:8080/ws;" contains five separate digit runs
(127, 0, 0, 1, 8080), so $NGINX_MRC_PORT became a 5-line string that
could never equal $MRC_PORT_CHECK ("8080"), a a false-positive
mismatch warning on every correctly-configured install. Fixed by
capturing just the port group with sed instead of a second blind
digit-extraction grep.

This test runs the real extraction line copied out of update.sh
against a synthetic nginx config, in actual bash -- not a
reimplementation -- so it can't drift from what's actually shipped.
"""
import re
import subprocess
import unittest
from pathlib import Path


class UpdateShMrcNginxPortExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo_root = Path(__file__).resolve().parent.parent
        with open(repo_root / 'update.sh') as f:
            content = f.read()
        m = re.search(r'NGINX_MRC_PORT=\$\(.*\)$', content, re.MULTILINE)
        assert m is not None, "couldn't find the NGINX_MRC_PORT= line in update.sh"
        cls.extraction_line = m.group(0)

    def _extract(self, nginx_config_body):
        with_tmp = (
            'NGINX_AVAIL="$(mktemp)"\n'
            f'cat > "$NGINX_AVAIL" <<\'CONF\'\n{nginx_config_body}\nCONF\n'
            f'{self.extraction_line}\n'
            'echo "$NGINX_MRC_PORT"\n'
            'rm -f "$NGINX_AVAIL"\n'
        )
        out = subprocess.run(['bash', '-c', with_tmp],
                              capture_output=True, text=True)
        return out.stdout

    def test_standard_port_extracts_cleanly(self):
        conf = 'location /mrcws {\n    proxy_pass http://127.0.0.1:8080/ws;\n}\n'
        self.assertEqual(self._extract(conf).strip(), '8080')

    def test_extraction_is_single_line_not_split_digit_by_digit(self):
        """The exact bug reported live: 127.0.0.1:8080/ws; used to
        yield a 5-line '127\\n0\\n0\\n1\\n8080' instead of just '8080'."""
        conf = 'location /mrcws {\n    proxy_pass http://127.0.0.1:8080/ws;\n}\n'
        out = self._extract(conf)
        self.assertEqual(len(out.strip().splitlines()), 1,
                          f'expected a single-line port, got {out!r}')

    def test_non_default_port_extracts_cleanly(self):
        conf = 'location /mrcws {\n    proxy_pass http://127.0.0.1:19080/ws;\n}\n'
        self.assertEqual(self._extract(conf).strip(), '19080')


if __name__ == '__main__':
    unittest.main()
