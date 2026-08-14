"""Regression test: docker/compose/docker-compose.yml's mrc-bridge
service was the only one of the 5 core services that ran through
docker/entrypoint.sh WITHOUT setting env_file/environment -- and
entrypoint.sh unconditionally hard-fails ("FATAL: ANETBBS_DB_URL is
not set") if ANETBBS_DB_URL isn't present, so this container could
never actually start via `docker compose up -d` at all; it would
crash-loop immediately every time. Found in a security/performance
audit.

Fixed by adding the same env_file + anetbbs-db-env anchor every
sibling service (web/terminal/finger/binkp) already has.

No YAML parser dependency exists in this project (checked
requirements*.txt), so this is a lightweight text-based check rather
than a full YAML parse -- consistent with this repo's existing
precedent that shell/infra-config fixes without a real test harness
are verified this way rather than pulling in a new dependency for one
regression guard.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_COMPOSE_PATH = (Path(__file__).resolve().parents[1] /
                 'docker' / 'compose' / 'docker-compose.yml')


def _service_block(text, name):
    """Extract one top-level `  <name>:` service's block of text, up to
    (but not including) the next top-level `  <other-name>:` line or
    the `volumes:` section at the end."""
    pattern = re.compile(
        r'^  ' + re.escape(name) + r':\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|^volumes:\n)',
        re.DOTALL | re.MULTILINE)
    m = pattern.search(text)
    assert m, f'could not find service block for {name!r}'
    return m.group(1)


class DockerComposeMrcBridgeEnvTests(unittest.TestCase):
    def setUp(self):
        self.text = _COMPOSE_PATH.read_text()

    def test_mrc_bridge_sets_the_shared_db_env_anchor(self):
        block = _service_block(self.text, 'mrc-bridge')
        self.assertIn('*anetbbs-db-env', block,
                      'mrc-bridge must set the shared DB env anchor -- '
                      'entrypoint.sh hard-fails without ANETBBS_DB_URL, '
                      'and mrc-bridge runs through entrypoint.sh just '
                      'like every other service')
        self.assertIn('env_file: [.env]', block)

    def test_every_entrypoint_sh_service_sets_the_db_env_anchor(self):
        """Broader guard: any CURRENT or future service invoking the
        plain entrypoint.sh (not entrypoint-web.sh, which handles its
        own docker.sock GID setup separately) must set the DB env
        anchor -- catches this exact class of drift recurring for a
        service added later."""
        service_pattern = re.compile(
            r'^  ([a-z0-9_-]+):\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|^volumes:\n)',
            re.DOTALL | re.MULTILINE)
        missing = []
        for m in service_pattern.finditer(self.text):
            name, block = m.group(1), m.group(2)
            if '"/usr/local/bin/entrypoint.sh"' not in block:
                continue
            if '*anetbbs-db-env' not in block:
                missing.append(name)
        self.assertEqual(missing, [],
                         f'these entrypoint.sh services are missing the '
                         f'DB env anchor and will crash-loop on startup: {missing}')


if __name__ == '__main__':
    unittest.main()
