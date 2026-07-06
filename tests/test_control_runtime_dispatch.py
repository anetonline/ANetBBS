"""Regression tests for the Sysop Control Panel's Docker runtime
dispatch, added 2026-07-04 as part of the ANetBBS containerization work.

anetbbs/web/control.py previously only knew how to talk to systemd
(systemctl/journalctl). Since there's no systemd inside a container,
it now dispatches on an ANETBBS_RUNTIME module-level constant to one
of three backends: systemd (unchanged), docker-single (supervisorctl,
for the single-container quick-start image), docker-compose (the
Docker Engine API via anetbbs/web/control_docker.py, for the
multi-container "correct" deployment).

These tests patch anetbbs.web.control._RUNTIME directly (module-level
constant, read once at import time in real usage) rather than actually
setting the ANETBBS_RUNTIME env var and reimporting, since that's a
simpler and more reliable way to exercise each dispatch branch.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# anetbbs.web.control <-> anetbbs.core.session circular-import gotcha,
# same as elsewhere in this project's tests -- import anetbbs.core first.
import anetbbs.core  # noqa: F401
from anetbbs.web import control


class SupervisorStatusParsingTests(unittest.TestCase):
    """_unit_state_supervisor() parses `supervisorctl status <program>`
    text output -- exercise its real parsing logic against realistic
    output shapes, not just a mocked return value."""

    def test_running_with_pid_parses_active_and_pid(self):
        with patch.object(control, '_supervisorctl',
                          return_value=(True, 'web   RUNNING   pid 123, uptime 0:05:23\n', '')):
            state = control._unit_state_supervisor('anetbbs-web')
        self.assertEqual(state['ActiveState'], 'active')
        self.assertEqual(state['SubState'], 'running')
        self.assertEqual(state['MainPID'], '123')

    def test_stopped_parses_inactive(self):
        with patch.object(control, '_supervisorctl',
                          return_value=(True, 'binkp   STOPPED   Not started\n', '')):
            state = control._unit_state_supervisor('anetbbs-binkp')
        self.assertEqual(state['ActiveState'], 'inactive')
        self.assertEqual(state['SubState'], 'stopped')

    def test_unmapped_unit_returns_unknown_without_crashing(self):
        state = control._unit_state_supervisor('anetbbs-does-not-exist')
        self.assertEqual(state['ActiveState'], 'unknown')

    def test_empty_supervisorctl_output_returns_unknown(self):
        with patch.object(control, '_supervisorctl', return_value=(True, '', '')):
            state = control._unit_state_supervisor('anetbbs-web')
        self.assertEqual(state['ActiveState'], 'unknown')


class UnitStateDispatchTests(unittest.TestCase):
    def test_dispatches_to_systemd_by_default(self):
        with patch.object(control, '_RUNTIME', 'systemd'), \
             patch.object(control, '_unit_state_systemd', return_value={'ActiveState': 'active'}) as m:
            result = control._unit_state('anetbbs-web')
        m.assert_called_once_with('anetbbs-web')
        self.assertEqual(result['ActiveState'], 'active')

    def test_dispatches_to_supervisor_for_docker_single(self):
        with patch.object(control, '_RUNTIME', 'docker-single'), \
             patch.object(control, '_unit_state_supervisor', return_value={'ActiveState': 'active'}) as m:
            result = control._unit_state('anetbbs-web')
        m.assert_called_once_with('anetbbs-web')
        self.assertEqual(result['ActiveState'], 'active')

    def test_dispatches_to_control_docker_for_docker_compose(self):
        fake_module = MagicMock()
        fake_module.unit_state.return_value = {'ActiveState': 'active'}
        with patch.object(control, '_RUNTIME', 'docker-compose'), \
             patch.dict(sys.modules, {'anetbbs.web.control_docker': fake_module}):
            result = control._unit_state('anetbbs-web')
        fake_module.unit_state.assert_called_once_with('anetbbs-web')
        self.assertEqual(result['ActiveState'], 'active')


class ChangeStateDispatchTests(unittest.TestCase):
    def test_docker_single_restart_maps_reload_to_restart(self):
        with patch.object(control, '_RUNTIME', 'docker-single'), \
             patch.object(control, '_supervisorctl', return_value=(True, 'ok', '')) as m:
            ok, out, err = control._change_state('anetbbs-web', 'reload')
        m.assert_called_once_with('restart', 'web')
        self.assertTrue(ok)

    def test_docker_single_unmapped_unit_fails_cleanly(self):
        with patch.object(control, '_RUNTIME', 'docker-single'):
            ok, out, err = control._change_state('not-a-real-unit', 'restart')
        self.assertFalse(ok)
        self.assertIn('no supervisor program mapped', err)

    def test_systemd_falls_through_to_systemctl_change(self):
        with patch.object(control, '_RUNTIME', 'systemd'), \
             patch.object(control, '_systemctl_change', return_value=(True, 'ok', '')) as m:
            ok, out, err = control._change_state('anetbbs-web', 'restart')
        m.assert_called_once_with('restart', 'anetbbs-web')
        self.assertTrue(ok)


class ReadJournalDispatchTests(unittest.TestCase):
    def test_dispatches_to_supervisor_tail_for_docker_single(self):
        with patch.object(control, '_RUNTIME', 'docker-single'), \
             patch.object(control, '_supervisorctl', return_value=(True, 'log output', '')) as m:
            log = control._read_journal('anetbbs-web', lines=50)
        m.assert_called_once_with('tail', '-50', 'web')
        self.assertEqual(log, 'log output')


class ControlDockerBackendTests(unittest.TestCase):
    """anetbbs/web/control_docker.py, exercised against a mocked
    docker.from_env() client (no real daemon available in CI/sandbox)."""

    def _fake_container(self, status='running', pid=123, started_at='2026-07-04T00:00:00Z'):
        c = MagicMock()
        c.status = status
        c.attrs = {'State': {'Pid': pid, 'StartedAt': started_at}}
        return c

    def test_unit_state_reports_running_container(self):
        from anetbbs.web import control_docker
        fake_client = MagicMock()
        fake_client.containers.list.return_value = [self._fake_container()]
        with patch.object(control_docker, '_client', return_value=fake_client):
            state = control_docker.unit_state('anetbbs-web')
        self.assertEqual(state['ActiveState'], 'active')
        self.assertEqual(state['MainPID'], '123')
        fake_client.containers.list.assert_called_once_with(
            all=True, filters={'label': 'com.docker.compose.service=web'})

    def test_unit_state_no_container_found(self):
        from anetbbs.web import control_docker
        fake_client = MagicMock()
        fake_client.containers.list.return_value = []
        with patch.object(control_docker, '_client', return_value=fake_client):
            state = control_docker.unit_state('anetbbs-web')
        self.assertEqual(state['LoadState'], 'not-found')
        self.assertEqual(state['ActiveState'], 'unknown')

    def test_change_state_restart_calls_container_restart(self):
        from anetbbs.web import control_docker
        container = self._fake_container()
        fake_client = MagicMock()
        fake_client.containers.list.return_value = [container]
        with patch.object(control_docker, '_client', return_value=fake_client):
            ok, out, err = control_docker.change_state('anetbbs-web', 'restart')
        self.assertTrue(ok)
        container.restart.assert_called_once_with(timeout=10)

    def test_change_state_unmapped_unit_fails_cleanly(self):
        from anetbbs.web import control_docker
        ok, out, err = control_docker.change_state('not-a-real-unit', 'restart')
        self.assertFalse(ok)
        self.assertIn('no compose service mapped', err)

    def test_read_logs_returns_decoded_text(self):
        from anetbbs.web import control_docker
        container = self._fake_container()
        container.logs.return_value = b'hello from the container\n'
        fake_client = MagicMock()
        fake_client.containers.list.return_value = [container]
        with patch.object(control_docker, '_client', return_value=fake_client):
            log = control_docker.read_logs('anetbbs-web', lines=100)
        self.assertIn('hello from the container', log)
        container.logs.assert_called_once_with(tail=100, timestamps=True)

    def test_spawn_container_upgrade_requires_compose_dir_env(self):
        from anetbbs.web import control_docker
        import os
        old = os.environ.pop('ANETBBS_COMPOSE_DIR', None)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                control_docker.spawn_container_upgrade('v1.2.3')
            self.assertIn('ANETBBS_COMPOSE_DIR', str(ctx.exception))
        finally:
            if old is not None:
                os.environ['ANETBBS_COMPOSE_DIR'] = old

    def test_spawn_container_upgrade_mounts_host_dir_not_container_path(self):
        from anetbbs.web import control_docker
        import os
        container = self._fake_container()
        container.image = 'anetbbs:latest'
        fake_client = MagicMock()
        fake_client.containers.list.return_value = [container]
        os.environ['ANETBBS_COMPOSE_DIR'] = '/home/sysop/anetbbs-docker'
        try:
            with patch.object(control_docker, '_client', return_value=fake_client):
                control_docker.spawn_container_upgrade('v1.2.3')
        finally:
            del os.environ['ANETBBS_COMPOSE_DIR']
        _, kwargs = fake_client.containers.run.call_args
        self.assertIn('/home/sysop/anetbbs-docker', kwargs['volumes'])
        self.assertEqual(kwargs['volumes']['/home/sysop/anetbbs-docker']['bind'], '/project')


if __name__ == '__main__':
    unittest.main()
