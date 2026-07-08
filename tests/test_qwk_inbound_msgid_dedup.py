"""Regression test for a live-caught QWK duplicate-import bug.

Jerry reported his Pi3 test install, subscribed as a QWK client to a
real external hub ("ANOTHERNETWORK"), showing wildly inflated area
message counts (e.g. 220 in an area that should have ~22) that did not
happen the same way on his main live server.

Root cause: `_parse_messages_dat()` (anetbbs/echomail/qwk.py) only set
a message's `msg_id` from a literal `@MSGID:` kludge line in the body.
Vanilla QWK hubs that don't tunnel FTN kludges (the normal case,
confirmed via the real ANOTHERNETWORK hub) never include one, so
`msg_id` came back `None` for nearly every inbound message. The dedup
check in `poller.py:_import_message` (`if msg_id: ...`) is silently
skipped whenever `msg_id` is falsy -- so with no per-network poll
checkpoint anywhere in this codebase either, any poll that received
overlapping content from the hub re-imported every message as brand
new, uncapped, on every single poll cycle. Because `EchoArea.
total_messages` only increments on a real committed insert (not a
cosmetic counter), this was genuine duplicate data accumulating in the
database -- and since `tosser.toss_message()` runs unconditionally
after any poll with `imported > 0`, those duplicates would also get
forwarded to any downstream nodes subscribed to the same area.

Fixed by synthesizing a deterministic content-hash `msg_id` whenever no
real `@MSGID:` is present, mirroring the stable-ID fallback that
already existed for the *outbound* REP-building path
(`_build_rep_packet`) but was missing on the inbound read side.
"""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.echomail.qwk import _parse_messages_dat, _parse_control_dat
from anetbbs.echomail.qwk_hub_ftp import _build_control_dat, _build_messages_dat


class FakeArea:
    def __init__(self, name):
        self.name = name


class FakeMsg:
    def __init__(self, from_name, to_name, subject, body, created_at=None):
        self.from_name = from_name
        self.to_name = to_name
        self.subject = subject
        self.body = body
        # Fixed, not datetime.utcnow() -- a real re-poll re-serves the
        # SAME historical message with its ORIGINAL date each time, so
        # tests simulating "re-parsed twice" must hold this fixed too.
        self.created_at = created_at or datetime(2026, 7, 7, 12, 34)


def _parse(messages_by_conf, hub_id='ANET'):
    conferences = {k: FakeArea(f'CONF{k}') for k in messages_by_conf}
    control = _build_control_dat(hub_id, conferences)
    info = _parse_control_dat(control)
    data = _build_messages_dat(messages_by_conf, hub_id)
    return _parse_messages_dat(data, info['conferences'])


class QwkInboundMsgIdDedupTests(unittest.TestCase):
    def test_message_without_msgid_kludge_still_gets_a_msg_id(self):
        """Vanilla QWK body (no @MSGID: line) must not come back with
        msg_id=None -- that's exactly what let the dedup check in
        poller.py silently no-op."""
        parsed = _parse({1: [FakeMsg('Tech News Bot', 'All',
                                      'Some tech headline', 'Plain body, no kludges.')]})
        self.assertEqual(len(parsed), 1)
        self.assertTrue(parsed[0]['msg_id'], 'msg_id must not be None/empty for a vanilla QWK message')

    def test_same_message_reparsed_twice_yields_identical_msg_id(self):
        """The whole fix hinges on determinism: re-downloading and
        re-parsing the SAME real message (e.g. an overlapping poll)
        must produce the SAME synthesized msg_id both times, or the
        dedup check in poller.py still can't catch the repeat."""
        by_conf = {1: [FakeMsg('StingRay', 'All', 'help wanted', 'Body two.')]}
        parsed_a = _parse(by_conf)
        parsed_b = _parse(by_conf)
        self.assertEqual(parsed_a[0]['msg_id'], parsed_b[0]['msg_id'])

    def test_different_messages_yield_different_msg_ids(self):
        parsed = _parse({1: [
            FakeMsg('A', 'All', 'Subject one', 'Body one.'),
            FakeMsg('B', 'All', 'Subject two', 'Body two.'),
        ]})
        self.assertEqual(len(parsed), 2)
        self.assertNotEqual(parsed[0]['msg_id'], parsed[1]['msg_id'])

    def test_real_msgid_kludge_still_wins_over_synthesized_fallback(self):
        """If a hub DOES tunnel a real @MSGID:, that must still be used
        verbatim -- the synthesized fallback is only for when one is
        genuinely absent. Tests _clean_body() directly (the function
        that actually extracts the kludge) rather than round-tripping
        through _build_messages_dat's CP437 encoding, which mangles a
        literal '\\xe3' paragraph-separator placeholder into '?' since
        U+00E3 has no CP437 mapping -- a separate, real bug, but not
        the one this file is regression-testing."""
        from anetbbs.echomail.qwk import _clean_body
        clean = _clean_body('@MSGID: 1:2/3.4 5a6b7c8d\nReal body text.')
        self.assertEqual(clean['msg_id'], '1:2/3.4 5a6b7c8d')
        self.assertEqual(clean['body'], 'Real body text.')

    def test_import_message_dedups_a_reparsed_repoll(self):
        """End-to-end through the real dedup gate in
        poller.py:_import_message -- simulates the actual bug: the
        same overlapping content served on two separate polls must
        only ever produce one EchomailMessage row."""
        import os
        import tempfile
        os.environ['FLASK_ENV'] = 'testing'
        import anetbbs.config as cfg_mod
        tmp_db = tempfile.mktemp(suffix='.db')
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmp_db}'
        from anetbbs.web_app import create_app
        from anetbbs.models import db, EchomailNetwork, EchomailMessage
        from anetbbs.echomail.poller import _import_message

        app = create_app('testing')
        try:
            with app.app_context():
                db.create_all()
                network = EchomailNetwork(name='TestNet', network_type='qwk')
                db.session.add(network)
                db.session.commit()

                by_conf = {1: [FakeMsg('StingRay', 'All', 'dup test', 'Same body both times.')]}
                parsed_a = _parse(by_conf)
                parsed_b = _parse(by_conf)  # simulates a second, overlapping poll

                for m in parsed_a:
                    _import_message(network, m)
                for m in parsed_b:
                    _import_message(network, m)

                count = EchomailMessage.query.filter_by(subject='dup test').count()
                self.assertEqual(count, 1,
                                  'the same message content parsed on two separate '
                                  '(overlapping) polls must only be imported once')
        finally:
            if os.path.exists(tmp_db):
                os.remove(tmp_db)


if __name__ == '__main__':
    unittest.main()
