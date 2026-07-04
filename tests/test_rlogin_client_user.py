"""Regression tests for rlogin_bridge.build_client_user.

History: the first version of the A-Net Game Server integration
(2026-07-04) hyphen-joined the BBS tag with no space (`@USER@-ANET`).
The sysop then corrected this to a space + `-s-` prefix
(`username -s-ANET`), matching Synchronet's own native `?rlogin -s-TAG`
client convention -- but that correction turned out to be wrong too:
ANetBBS presents its rlogin connections in the Mystic-style convention,
not Synchronet's own, and the sysop's own game server actually expects
the original hyphen-joined form (`username-ANET`, no space, no `-s-`).
Confirmed directly by the sysop after the wrong "corrected" version
had already been tested. The Synchronet `-s-TAG` convention only
applies when a real Synchronet BBS calls another real Synchronet BBS.

build_client_user is the one place this gets assembled, shared by both
the web and terminal rlogin launch paths so there's only one place to
get it right (and one place to test it) -- which is exactly why it was
worth fixing twice in one session rather than being scattered inline.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest
from anetbbs.games.rlogin_bridge import build_client_user, expand_user_template


class BuildClientUserTests(unittest.TestCase):
    def test_no_tag_is_just_the_expanded_template(self):
        result = build_client_user('@USER@', 'stingray', 'StingRay', '')
        self.assertEqual(result, 'stingray')

    def test_tag_is_hyphen_joined_with_no_space(self):
        result = build_client_user('@USER@', 'stingray', 'StingRay', 'ANET')
        self.assertEqual(result, 'stingray-ANET')

    def test_tag_does_not_use_the_synchronet_space_s_dash_convention(self):
        # ANetBBS presents its rlogin connections Mystic-style, not as a
        # native Synchronet client -- that convention (space + "-s-"
        # prefix) only applies BBS-to-BBS between two real Synchronet
        # installs, and was mistakenly applied here for one release
        # before the sysop caught it.
        result = build_client_user('@USER@', 'stingray', 'StingRay', 'ANET')
        self.assertNotIn(' -s-', result)
        self.assertNotEqual(result, 'stingray -s-ANET')

    def test_none_tag_behaves_like_no_tag(self):
        result = build_client_user('@USER@', 'stingray', 'StingRay', None)
        self.assertEqual(result, 'stingray')

    def test_tag_with_surrounding_whitespace_is_stripped(self):
        result = build_client_user('@USER@', 'stingray', 'StingRay', '  ANET  ')
        self.assertEqual(result, 'stingray-ANET')

    def test_alias_and_percent_tokens_still_expand_before_tag_is_appended(self):
        self.assertEqual(
            build_client_user('@ALIAS@', 'stingray', 'StingRay', 'ANET'),
            'StingRay-ANET')
        self.assertEqual(
            build_client_user('%u', 'stingray', 'StingRay', 'ANET'),
            'stingray-ANET')

    def test_expand_user_template_alone_is_unaffected_by_the_new_helper(self):
        # build_client_user wraps expand_user_template rather than
        # replacing it -- direct-to-door / DoorParty-style rlogin.
        # configs with no tag still work exactly as before.
        self.assertEqual(
            expand_user_template('@USER@', 'stingray', 'StingRay'),
            'stingray')


if __name__ == '__main__':
    unittest.main()
