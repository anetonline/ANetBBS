"""Unit tests for anetbbs.core.text_safety.strip_untrusted_escapes() --
the shared helper closing a real cross-user/cross-network ANSI-escape-
injection vulnerability class found in a security audit (independently
in core/session.py's login notification popup + sysop broadcasts, and
core/finger_server.py's profile display; see
test_notification_login_popup.py and test_sysop_broadcast_ansi_
injection.py for the integration-level regression tests).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.core.text_safety import strip_untrusted_escapes


class StripUntrustedEscapesTests(unittest.TestCase):
    def test_none_and_empty_return_empty_string(self):
        self.assertEqual(strip_untrusted_escapes(None), '')
        self.assertEqual(strip_untrusted_escapes(''), '')

    def test_plain_text_is_unaffected(self):
        self.assertEqual(strip_untrusted_escapes('Hello, World!'),
                         'Hello, World!')

    def test_csi_clear_screen_and_home_removed_cleanly(self):
        self.assertEqual(
            strip_untrusted_escapes('\x1b[2J\x1b[HGotcha'), 'Gotcha')

    def test_csi_color_codes_removed_cleanly(self):
        self.assertEqual(
            strip_untrusted_escapes('\x1b[31mRed text\x1b[0m'), 'Red text')

    def test_osc_title_injection_removed_cleanly(self):
        self.assertEqual(
            strip_untrusted_escapes('\x1b]0;pwned\x07Hi'), 'Hi')

    def test_fake_password_prompt_spoof_neutralized(self):
        result = strip_untrusted_escapes(
            '\x1b[2J\x1b[HPassword: \x1b[8m')  # \x1b[8m = conceal
        self.assertNotIn('\x1b', result)
        self.assertIn('Password:', result)

    def test_bare_or_malformed_escape_never_survives(self):
        # Even something that doesn't match the tidy CSI/OSC pattern
        # must still be caught by the blanket control-byte safety net.
        for payload in ('\x1b', '\x1bQQQQ', '\x1b\x1b\x1b', '\x1b[999999999z'):
            result = strip_untrusted_escapes(payload)
            self.assertNotIn('\x1b', result, f'ESC survived for {payload!r}')

    def test_other_c0_control_bytes_stripped_too(self):
        result = strip_untrusted_escapes('Hi\x07\x08\x0cthere')
        self.assertEqual(result, 'Hithere')

    def test_unicode_text_passes_through_unaffected(self):
        self.assertEqual(strip_untrusted_escapes('café ☃'),
                         'café ☃')


if __name__ == '__main__':
    unittest.main()
