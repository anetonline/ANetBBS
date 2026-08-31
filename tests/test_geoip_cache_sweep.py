"""Regression test for a real Medium/High finding from a security/
performance audit (2026-08-31): web/auth.py's _geoip_cache (ip ->
(country_code, expiry_timestamp), used to gate BLOCKED_COUNTRIES)
was written on every cache miss but never evicted -- expiry was only
checked on read. On a long-running production process, every distinct
attacking source IP that ever gets checked (this feature exists
specifically to reject scanner/credential-stuffing traffic) leaves a
permanent entry, unbounded growth over the process lifetime -- same
shape as the v1.0.21 incident. Fixed with the same probabilistic-sweep
pattern features/rate_limit.py's own _buckets dict already uses.
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.web.auth as auth_mod


class GeoipCacheSweepTests(unittest.TestCase):
    def setUp(self):
        self._orig_cache = dict(auth_mod._geoip_cache)
        auth_mod._geoip_cache.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        auth_mod._geoip_cache.clear()
        auth_mod._geoip_cache.update(self._orig_cache)

    def test_sweep_removes_only_expired_entries(self):
        now = time.time()
        auth_mod._geoip_cache['1.1.1.1'] = ('US', now - 10)   # expired
        auth_mod._geoip_cache['2.2.2.2'] = ('CA', now + 3600)  # still fresh
        auth_mod._geoip_cache['3.3.3.3'] = ('RU', now - 1)    # expired

        auth_mod._sweep_stale_geoip_entries()

        self.assertNotIn('1.1.1.1', auth_mod._geoip_cache)
        self.assertNotIn('3.3.3.3', auth_mod._geoip_cache)
        self.assertIn('2.2.2.2', auth_mod._geoip_cache)

    def test_sweep_is_a_noop_on_an_empty_or_all_fresh_cache(self):
        now = time.time()
        auth_mod._geoip_cache['9.9.9.9'] = ('DE', now + 3600)
        auth_mod._sweep_stale_geoip_entries()
        self.assertIn('9.9.9.9', auth_mod._geoip_cache)

        auth_mod._geoip_cache.clear()
        auth_mod._sweep_stale_geoip_entries()  # must not raise
        self.assertEqual(auth_mod._geoip_cache, {})

    def test_ip_country_blocked_eventually_triggers_a_sweep(self):
        """End-to-end: repeated distinct-IP lookups (each a cache miss,
        matching the real attacking-scanner-traffic scenario) must
        eventually shrink the cache back down via the write-time
        sweep, not just grow forever."""
        from unittest import mock

        now = time.time()
        # Seed a large number of already-expired stale entries.
        for i in range(2000):
            auth_mod._geoip_cache[f'10.0.{i // 256}.{i % 256}'] = ('US', now - 100)

        fake_app = mock.MagicMock()
        fake_app.config.get.return_value = 'CA'

        # Force the sweep to fire on the very first write by making the
        # RNG deterministic, instead of relying on real 1-in-500 odds.
        with mock.patch('anetbbs.web.auth.current_app', fake_app), \
             mock.patch('anetbbs.web.auth.random.randint', return_value=1), \
             mock.patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = mock.MagicMock()
            mock_resp.read.return_value = b'{"countryCode": "FR"}'
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            auth_mod._ip_country_blocked('192.0.2.1')

        self.assertLess(len(auth_mod._geoip_cache), 2000,
                        'a write-time sweep must shrink a cache full of stale '
                        'entries, not just keep adding to it forever')


if __name__ == '__main__':
    unittest.main()
