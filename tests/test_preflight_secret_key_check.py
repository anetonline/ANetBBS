"""Regression test for a real Medium finding from a security/performance
audit (2026-09-02): anetbbs/web/preflight.py's _check_secret_key()
compared the configured SECRET_KEY against the literal string
'change-me-in-production' -- a string this codebase has never actually
used as its insecure default. The REAL default (anetbbs/config.py's
Config.SECRET_KEY fallback, also checked by web_app.py's own boot-time
`_bad_defaults` guard and installer/upgrade.py's auto-heal step) is
'dev-secret-key-change-in-production' -- a different, 35-character
string containing no "insecure" substring and not under the 32-char
length floor either, so none of the old check's conditions caught it.
An install still running the actual known-insecure default got a
false-positive "ok" on the Preflight Checklist page -- the one page
whose whole job is to catch this before a sysop exposes the BBS
publicly.

Fixed by checking against the same known-bad-key set web_app.py's own
`_bad_defaults` uses.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.web.preflight import _check_secret_key


class PreflightSecretKeyCheckTests(unittest.TestCase):
    def test_the_actual_codebase_default_is_flagged_as_weak(self):
        """Direct regression test: config.py's REAL fallback value must
        fail this check, not silently pass it."""
        result = _check_secret_key({'SECRET_KEY': 'dev-secret-key-change-in-production'})
        self.assertEqual(result['status'], 'fail',
                         f'the real insecure default must fail, got: {result}')

    def test_other_known_bad_defaults_are_flagged(self):
        for bad in ('changeme', 'your-secret-key-here', ''):
            result = _check_secret_key({'SECRET_KEY': bad})
            self.assertEqual(result['status'], 'fail', f'{bad!r} -> {result}')

    def test_short_key_still_fails(self):
        result = _check_secret_key({'SECRET_KEY': 'short'})
        self.assertEqual(result['status'], 'fail')

    def test_a_real_random_key_passes(self):
        result = _check_secret_key(
            {'SECRET_KEY': 'a' * 48})  # stand-in for secrets.token_urlsafe(48)
        self.assertEqual(result['status'], 'ok', result)


if __name__ == '__main__':
    unittest.main()
