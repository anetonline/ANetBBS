"""Regression tests for a real field-injection bug found in a full
access-control audit: anetbbs/games/dropfile.py and
anetbbs/games/node_paths.py both spliced user-controlled fields
(username/display_name/email/location) verbatim into generated content
-- an embedded CR/LF in one of those fields could inject extra
physical lines into fixed-position drop-file formats (DOOR.SYS etc.,
read positionally by door games), or extra command lines into
DOSBox/dosemu2 autoexec content built from %-token expansion
(door_runner.py). Both now strip CR/LF from every user-supplied field
before it's used.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class DropfileInjectionTests(unittest.TestCase):
    def test_embedded_crlf_in_username_does_not_inject_extra_lines(self):
        from anetbbs.games.dropfile import generate_door_sys
        evil_user = {
            'id': 1,
            'username': 'evil\r\nSTATUS_LEVEL: 255',
            'display_name': 'Evil',
            'email': 'evil@example.com',
            'access_level': 10,
        }
        content = generate_door_sys(evil_user, node_number=1)
        lines = content.split('\r\n')
        # The injected fake line must not appear as its own line -- it
        # should still be glued onto whatever line the username field
        # legitimately occupies.
        self.assertNotIn('STATUS_LEVEL: 255', lines)

    def test_normal_username_still_works(self):
        from anetbbs.games.dropfile import generate_door_sys
        user = {'id': 1, 'username': 'normaluser', 'display_name': 'Normal User',
                'email': 'n@example.com', 'access_level': 10}
        content = generate_door_sys(user, node_number=1)
        self.assertIn('normaluser', content)


class NodePathsTokenInjectionTests(unittest.TestCase):
    def test_embedded_crlf_in_display_name_is_stripped_from_token_context(self):
        from anetbbs.games.node_paths import build_token_context
        evil_user = {'id': 1, 'username': 'evil', 'display_name': 'Evil\r\nMOUNT C /',
                    'email': 'evil@example.com'}
        ctx = build_token_context(user=evil_user, node_number=1)
        self.assertNotIn('\r', ctx['%r'])
        self.assertNotIn('\n', ctx['%r'])
        self.assertNotIn('\r', ctx['%R'])
        self.assertNotIn('\n', ctx['%R'])

    def test_normal_display_name_unaffected(self):
        from anetbbs.games.node_paths import build_token_context
        user = {'id': 1, 'username': 'normal', 'display_name': 'Normal Name',
                'email': 'n@example.com'}
        ctx = build_token_context(user=user, node_number=1)
        self.assertEqual(ctx['%r'], 'Normal Name')


if __name__ == '__main__':
    unittest.main()
