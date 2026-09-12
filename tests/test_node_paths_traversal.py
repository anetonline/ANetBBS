"""Regression test for a path-traversal gap in
anetbbs/games/node_paths.py's build_token_context(), found in a
security/performance audit of the door-game session/process-management
layer -- same bug shape as the already-fixed ANetCraft save-path issue.

build_token_context()'s %r/%R (display_name) and %L (location) tokens
are user-controlled and get expand_tokens()'d into Game.working_directory
/ executable_path / command_line_args / drop_file_path when a sysop's
door template uses them (e.g. working_directory="%P%R/" -- "this
node's scratch dir, in a subfolder named after the player"). The
result is then resolved via door_runner._resolve_path(), which is a
plain os.path.normpath(os.path.join(...)) with no boundary check --
so a display_name containing "../" sequences can walk the resulting
path out of the per-node scratch directory entirely.

Registration restricts `username` to a safe charset (web/auth.py's
RegisterForm Regexp), but display_name/location/email have no such
restriction (web/profile.py, web/admin.py), and an admin-edited
username (web/admin.py's UserForm) isn't run through that same regex
either -- so this isn't just a defense-in-depth nicety.

Fixed by stripping '/' and '\\' (in addition to the CR/LF already
stripped for a prior DOS-command-injection fix) from every
user-controlled value in build_token_context()'s _u() helper, the one
place all of them funnel through.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TokenContextTraversalTests(unittest.TestCase):
    def _ctx(self, **user_fields):
        from anetbbs.games.node_paths import build_token_context
        user = {'username': 'tester', 'id': 1, 'is_admin': False}
        user.update(user_fields)
        return build_token_context(user=user, node_number=3,
                                   minutes_left=30, bbs_name='ANetBBS',
                                   sysop_name='Sysop')

    def test_display_name_traversal_sequence_is_neutralized(self):
        ctx = self._ctx(display_name='../../../../tmp/evil')
        self.assertNotIn('/', ctx['%r'])
        self.assertNotIn('/', ctx['%R'])
        self.assertNotIn('\\', ctx['%r'])

    def test_location_traversal_sequence_is_neutralized(self):
        ctx = self._ctx(location='../../../../tmp/evil')
        self.assertNotIn('/', ctx['%L'])
        self.assertNotIn('\\', ctx['%L'])

    def test_absolute_path_in_display_name_is_neutralized(self):
        ctx = self._ctx(display_name='/etc/cron.d/evil')
        self.assertNotIn('/', ctx['%R'])

    def test_ordinary_display_name_unaffected(self):
        ctx = self._ctx(display_name="O'Brien Jr.")
        self.assertEqual(ctx['%R'], "O'Brien Jr.")

    def test_expand_tokens_stays_inside_node_dir_end_to_end(self):
        """Integration check: even with a sysop template that puts a
        per-user token directly into a working_directory string (a
        realistic, foreseeable customization -- e.g. "%P%R/" for
        'a subfolder named after the player inside this node's
        scratch dir'), the resolved path can no longer escape the
        node's own temp directory."""
        from anetbbs.games.node_paths import build_token_context, expand_tokens, node_dir
        from anetbbs.games.door_runner import _resolve_path

        ctx = self._ctx(display_name='../../../../../../tmp/escaped')
        template = '%P%R/'
        raw = expand_tokens(template, ctx)
        resolved = _resolve_path(raw)
        nd = os.path.normpath(node_dir(3))
        self.assertTrue(
            resolved == nd or resolved.startswith(nd + os.sep),
            f'resolved path {resolved!r} escaped node dir {nd!r}')


if __name__ == '__main__':
    unittest.main()
