"""Regression test for a real live bug Jerry reported (2026-09-01): the
federation registry's SYSTAT prober was too quick to drop a genuinely-
healthy peer from the public list ("if someone's system is offline, it
deactivates them too quickly... I feel like I am always going in and
re-listing sites"). Two compounding causes, both fixed here:

1. query_systat() is a single, unacknowledged UDP datagram with no
   retry -- one lost packet (ordinary packet loss, not the peer
   actually being offline) counted as a full probe failure. Combined
   with the old defaults (3 consecutive failures x a 1-hour interval =
   ~3 hours to delist), a peer with a merely flaky UDP path -- not
   actually down -- could get dropped from routine network noise, and
   if that same UDP unreliability persists, it might never get a clean
   probe again to trigger the code's own auto-re-list logic, needing
   Jerry to notice and manually re-approve/re-list it himself. Fixed
   with _probe_with_retries(): a few quick retries within the SAME
   probe pass before counting an entry as failed.

2. REGISTRY_PROBE_INTERVAL_SEC/REGISTRY_PROBE_FAILURE_THRESHOLD used to
   be read ONCE when the prober thread started and cached for the
   process lifetime -- a sysop changing them (previously .env-only,
   now also a real Admin -> Federation Registry setting) had no effect
   until the whole web service restarted. Fixed by re-reading them
   fresh every pass in probe.py's _loop().

Also raised the default threshold (~3 hours -> ~3 days at the default
1-hour interval, matching Jerry's own "3 days or something" ask) --
covered by test_config_defaults.py-style direct config assertion here
rather than a separate file, since it's a one-line check.
"""
import ast
import inspect
import os
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class RegistryDefaultThresholdTests(unittest.TestCase):
    def test_defaults_are_generous_not_aggressive(self):
        # ~3 days at the default 1-hour interval, not the old ~3 hours.
        self.assertEqual(cfg_mod.Config.REGISTRY_PROBE_INTERVAL_SEC, 3600)
        self.assertEqual(cfg_mod.Config.REGISTRY_PROBE_FAILURE_THRESHOLD, 72)
        self.assertEqual(cfg_mod.Config.REGISTRY_HEARTBEAT_STALE_HOURS, 72)


class ProbeRetryTests(unittest.TestCase):
    def test_succeeds_immediately_without_retrying_further(self):
        from anetbbs.msp import probe
        with patch.object(probe, 'query_systat', return_value='ok') as m, \
             patch.object(probe.time, 'sleep') as sleep_m:
            result = probe._probe_with_retries('example.com', 11)
        self.assertTrue(result)
        self.assertEqual(m.call_count, 1,
                         'must not burn extra retries once a probe succeeds')
        sleep_m.assert_not_called()

    def test_recovers_from_a_single_lost_packet(self):
        """The actual regression guard: one failed attempt followed by
        a success must still count as an overall success -- this is
        exactly what closes the "one dropped UDP packet delists a
        healthy peer" gap."""
        from anetbbs.msp import probe
        with patch.object(probe, 'query_systat',
                         side_effect=['', '', 'ok']) as m, \
             patch.object(probe.time, 'sleep'):
            result = probe._probe_with_retries('example.com', 11)
        self.assertTrue(result)
        self.assertEqual(m.call_count, 3)

    def test_fails_only_after_every_retry_is_exhausted(self):
        from anetbbs.msp import probe
        with patch.object(probe, 'query_systat', return_value='') as m, \
             patch.object(probe.time, 'sleep'):
            result = probe._probe_with_retries('example.com', 11)
        self.assertFalse(result)
        self.assertEqual(m.call_count, probe._PROBE_RETRIES)


class ProbeLoopReadsSettingsLiveTests(unittest.TestCase):
    """Deterministic structural guard (source-inspection, the
    established pattern in this codebase for logic embedded inside a
    long-running loop that can't practically be end-to-end unit-tested
    -- see test_time_budget_enforcement.py): the interval/threshold
    reads must happen INSIDE _loop()'s while body, not cached once
    before it, or an admin settings change needs a full service
    restart to take effect."""

    def test_settings_are_read_inside_the_loop_body_not_cached_before_it(self):
        from anetbbs.msp import probe
        raw = inspect.getsource(probe._loop)
        tree = ast.parse(textwrap.dedent(raw))
        func_node = tree.body[0]

        while_node = None
        for node in func_node.body:
            if isinstance(node, ast.While):
                while_node = node
                break
        self.assertIsNotNone(while_node, "_loop() must have a while loop")

        while_body_src = '\n'.join(
            ast.get_source_segment(textwrap.dedent(raw), n) or ''
            for n in while_node.body)
        self.assertIn(
            "app.config.get('REGISTRY_PROBE_INTERVAL_SEC'", while_body_src,
            'REGISTRY_PROBE_INTERVAL_SEC must be re-read inside the loop '
            'body every pass, not cached once before the loop starts -- '
            'otherwise a live admin settings change needs a full '
            'service restart to take effect')
        self.assertIn(
            "app.config.get('REGISTRY_PROBE_FAILURE_THRESHOLD'", while_body_src,
            'REGISTRY_PROBE_FAILURE_THRESHOLD must be re-read inside the '
            'loop body every pass, same reasoning as the interval above')


class ProbeOnceFunctionalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.registry_probe_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_entry(self, host, **overrides):
        from anetbbs.models import db, RegistryEntry
        defaults = dict(
            host=host, name='Peer BBS', contact_email='peer@example.com',
            is_verified=True, is_approved=True, is_listed=True,
            is_active=True, consecutive_probe_failures=0,
        )
        defaults.update(overrides)
        entry = RegistryEntry(**defaults)
        db.session.add(entry)
        db.session.commit()
        return entry.id

    def test_transient_probe_failure_does_not_delist_below_threshold(self):
        from anetbbs.models import db, RegistryEntry
        from anetbbs.msp import probe

        with self.app.app_context():
            entry_id = self._make_entry('flaky.example.com')

            with patch('anetbbs.msp.probe.query_systat', return_value=''), \
                 patch.object(probe.time, 'sleep'):
                probe._probe_once(self.app, db, RegistryEntry, fail_threshold=72)

            entry = RegistryEntry.query.get(entry_id)
            self.assertEqual(entry.consecutive_probe_failures, 1)
            self.assertTrue(
                entry.is_listed,
                'a single failed probe pass must not delist an entry '
                'when the threshold is 72 (matches the new default)')

    def test_recovery_after_a_prior_delisting_re_lists_automatically(self):
        from anetbbs.models import db, RegistryEntry
        from anetbbs.msp import probe

        with self.app.app_context():
            entry_id = self._make_entry(
                'recovered.example.com', is_listed=False,
                consecutive_probe_failures=80)

            with patch('anetbbs.msp.probe.query_systat', return_value='ok'), \
                 patch.object(probe.time, 'sleep'):
                probe._probe_once(self.app, db, RegistryEntry, fail_threshold=72)

            entry = RegistryEntry.query.get(entry_id)
            self.assertTrue(entry.is_listed,
                            'a successful probe must auto-re-list a '
                            'previously-delisted entry')
            self.assertEqual(entry.consecutive_probe_failures, 0)


if __name__ == '__main__':
    unittest.main()
