"""Regression tests for anetbbs.echomail.areafix._passwords_match() and
its use in areafix.py/filefix.py/tic.py's netmail-based robot auth.

Real gap found in a security/performance audit: these three modules
compared attacker-supplied passwords (a netmail Subject line, or a TIC
manifest's Pw: field) with Python's plain ==, a timing side-channel --
the same bug class already fixed for BinkP's own M_PWD/CRAM-MD5 check
(binkp_server.py), just never swept into these netmail-based auth
paths. Fixed via a shared hmac.compare_digest-based helper.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.echomail.areafix import _passwords_match


class PasswordsMatchTests(unittest.TestCase):
    def test_matching_passwords_return_true(self):
        self.assertTrue(_passwords_match('hunter2', 'hunter2'))

    def test_mismatched_passwords_return_false(self):
        self.assertFalse(_passwords_match('hunter2', 'wrongpass'))

    def test_empty_vs_empty_returns_true(self):
        # The caller is responsible for the "no password configured"
        # unconditional-reject check (`not expected_pw or ...`) --
        # _passwords_match() itself is a pure comparison.
        self.assertTrue(_passwords_match('', ''))

    def test_none_is_treated_as_empty_string(self):
        self.assertTrue(_passwords_match(None, None))
        self.assertFalse(_passwords_match(None, 'x'))
        self.assertFalse(_passwords_match('x', None))

    def test_different_lengths_do_not_match(self):
        self.assertFalse(_passwords_match('short', 'a-much-longer-password'))

    def test_case_sensitive(self):
        self.assertFalse(_passwords_match('Hunter2', 'hunter2'))

    def test_non_ascii_is_handled_without_raising(self):
        self.assertTrue(_passwords_match('pässwörd', 'pässwörd'))
        self.assertFalse(_passwords_match('pässwörd', 'password'))


class FilefixReusesTheSameHelperTests(unittest.TestCase):
    def test_filefix_imports_the_shared_areafix_helper(self):
        """Guards against a future refactor reintroducing a separate,
        possibly-non-constant-time copy in filefix.py."""
        from anetbbs.echomail import filefix
        self.assertIs(filefix._passwords_match, _passwords_match)


class TicReusesTheSameHelperTests(unittest.TestCase):
    def test_tic_imports_the_shared_areafix_helper(self):
        from anetbbs.echomail import tic
        self.assertIs(tic._passwords_match, _passwords_match)


class FreqReusesTheSameHelperTests(unittest.TestCase):
    """Real gap found in a LATER security/performance audit round
    (2026-08-31): freq.py's per-area FREQ password check was missed
    by the original sweep above -- it still compared with plain !=.
    Same fix, same shared helper, same identity-check guard against a
    future refactor reintroducing a separate copy. Functional
    correctness of the per-area password gate itself (right password
    matches, wrong one doesn't) is already covered end-to-end by
    tests/test_freq_inbound_matching.py's own
    test_area_password_required_and_enforced -- this only confirms the
    comparison is now routed through the constant-time helper."""
    def test_freq_imports_the_shared_areafix_helper(self):
        from anetbbs.echomail import freq
        self.assertIs(freq._passwords_match, _passwords_match)


if __name__ == '__main__':
    unittest.main()
