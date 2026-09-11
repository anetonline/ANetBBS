"""Regression test for a real finding from a security/performance audit
(2026-09-10): anedit.py's launch_anedit() spliced the caller-supplied
`username` straight into the draft-file path with NO sanitization at
all -- `dpath = os.path.join(ddir, f"{username}.txt")` -- relying
entirely on upstream registration validation (web/auth.py's
RegisterForm regex) to keep '/'/'..' out of it. That validation isn't
universal: web/admin.py's own AddUserForm ("sysop Add User") has no
equivalent character restriction on its username field, so an
admin-created account could still carry a literal '/' into this path.
Separately, even for normally-registered usernames (which the regex
DOES allow to contain space/'.'/"'"), two different usernames differing
only by those characters used to collide onto the exact same draft
file -- 'bob smith' and 'bob.smith' both interpolate to 'bob smith.txt'
/ 'bob.smith.txt' distinctly today, but before this fix a scheme that
merely stripped punctuation (the same class of bug found in
anetcraft.py's _safe_username()) would have collapsed them.

Fixed by adding a local _safe_username() (percent-encoding via
urllib.parse.quote, safe='' so '/' is escaped too -- same approach
used to fix anetcraft.py/darkforces_term.py's save paths) and routing
launch_anedit()'s draft-path construction through it.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anetbbs.features.anedit as anedit_mod
from anetbbs.features.anedit import ANEdit, _safe_username, launch_anedit


class FakeSession:
    def __init__(self):
        self.window_size = (80, 24)
        self.encoding = 'cp437'
        self.written = []

    async def write(self, text):
        self.written.append(text)

    async def read_raw(self, n):
        # Never actually reached -- the capturing subclass below marks
        # the editor done before run()'s key-read loop executes.
        await asyncio.sleep(0)
        return b''


class AneditDraftPathSanitizationTests(unittest.TestCase):
    def test_safe_username_has_no_literal_slash_for_traversal_attempt(self):
        self.assertNotIn('/', _safe_username('../../../../tmp/evil-anedit'))

    def test_safe_username_does_not_collide_for_punctuation_variants(self):
        variants = ['bob smith', 'bob.smith', "bob'smith", 'bobsmith']
        sanitized = [_safe_username(v) for v in variants]
        self.assertEqual(len(set(sanitized)), len(variants),
                         f'expected all distinct, got {sanitized}')

    def test_launch_anedit_builds_draft_path_from_sanitized_username(self):
        """End-to-end: launch_anedit() must construct ANEdit's
        draft_path using the sanitized username, not the raw one --
        verified by capturing the real constructor argument rather than
        re-deriving the expected path separately."""
        captured = {}
        orig_init = ANEdit.__init__

        def capturing_init(self, *args, **kwargs):
            captured['draft_path'] = kwargs.get('draft_path')
            orig_init(self, *args, **kwargs)
            # Mark done immediately so run()'s key-read loop never
            # actually executes -- mirrors the tagline-toggle test's
            # own "set done before run()" pattern for a fast, real run().
            self.done = True

        anedit_mod.ANEdit.__init__ = capturing_init
        try:
            asyncio.run(launch_anedit(
                FakeSession(), username='../../../../tmp/evil-anedit'))
        finally:
            anedit_mod.ANEdit.__init__ = orig_init

        self.assertIsNotNone(captured.get('draft_path'))
        # The resolved path must stay inside data/anedit/drafts/ -- NOT
        # just "the basename has no slash": os.path.basename() on an
        # unsanitized '../../../../tmp/evil-anedit.txt' joined onto
        # ddir would itself report a slash-free basename ('evil-
        # anedit.txt') while the actual resolved file still escaped the
        # drafts directory entirely, so that's not a safe check on its
        # own -- resolve the real path and compare against the real
        # drafts dir instead.
        # Mirrors launch_anedit()'s own here/root derivation exactly:
        # here = dirname(this file) = .../anetbbs/features, root = two
        # more levels up = the repo root.
        here = os.path.dirname(os.path.abspath(anedit_mod.__file__))
        root = os.path.abspath(os.path.join(here, '..', '..'))
        drafts_dir = os.path.join(root, 'data', 'anedit', 'drafts')
        resolved = os.path.realpath(captured['draft_path'])
        self.assertTrue(
            resolved.startswith(os.path.realpath(drafts_dir) + os.sep),
            f'draft path escaped drafts dir: {resolved}')


if __name__ == '__main__':
    unittest.main()
