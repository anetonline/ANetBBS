"""Tests for InterBBS Wall (anetbbs/echomail/interbbs_sync.py) -- posts
shared with other ANetBBS installs over a dedicated echomail area,
riding the existing QWK/BinkP transport rather than a bespoke sync
protocol, matching how fsxnet's wall/last-caller areas work.

Covers the safety-critical properties the design depends on:

  - Loop prevention: a WallPost with origin_bbs already set (i.e.
    imported from another BBS) must NEVER be relayed back out -- doing
    so would compose a brand-new EchomailMessage with a brand-new
    msg_id (binkp.py only reuses msg_id if already set on the ORM
    object, never regenerates deterministically from content), so no
    downstream dedup could ever catch a re-relayed loop.
  - Global dedup: the inbound sync scan must catch the same msg_id
    arriving via two different areas/networks, not just within one.
  - NULL msg_id rows (malformed/legacy packets) must be skipped, not
    materialized -- they can never be deduped on a later scan.
  - ensure_special_area() must gate the AreaFix follow-up request on
    network_type == 'binkp' -- QWK networks have no AreaFix protocol.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_app(db_path):
    import anetbbs.config as cfg_mod
    if os.path.exists(db_path):
        os.remove(db_path)
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    os.environ['FLASK_ENV'] = 'testing'
    from anetbbs.web_app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


class InterbbsSyncTests(unittest.TestCase):
    # _make_app() overwrites the shared TestingConfig.SQLALCHEMY_DATABASE_URI
    # class attribute on every call -- restore it after this class runs, or
    # any test file that runs later in the same pytest process inherits a
    # scratch-DB path that's already been deleted by our own tempdir
    # cleanup (confirmed: this exact leak broke test_mrc_integration.py
    # when run as part of the full suite). Same pattern as
    # test_qwk_hub_gating.py's teardown.
    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_app(str(Path(self._tmp.name) / 'a.db'))
        with self.app.app_context():
            from anetbbs.models import db
            db.create_all()

    def _network(self, network_type='binkp', hub_address=None):
        from anetbbs.models import db, EchomailNetwork
        net = EchomailNetwork(
            name='ANotherNetwork', network_type=network_type,
            hub_address=hub_address, our_address='1:2/3.4',
            areafix_password='secret' if hub_address else None,
        )
        db.session.add(net)
        db.session.commit()
        return net

    # ------------------------------------------------------------------
    # ensure_special_area()
    # ------------------------------------------------------------------

    def test_ensure_special_area_creates_once_idempotent(self):
        from anetbbs.echomail.interbbs_sync import ensure_special_area, WALL_AREA_TAG
        from anetbbs.models import EchoArea
        with self.app.app_context():
            net = self._network()
            a1 = ensure_special_area(net, WALL_AREA_TAG)
            a2 = ensure_special_area(net, WALL_AREA_TAG)
            self.assertEqual(a1.id, a2.id)
            self.assertEqual(EchoArea.query.filter_by(
                network_id=net.id, tag=WALL_AREA_TAG).count(), 1)

    def test_ensure_special_area_reasserts_sysop_only_flag(self):
        """A sysop's own DB edit (or a template default) flipping
        is_sysop_only off must be repaired on the next call -- this area
        is a sync channel, not a normal user-browsable discussion area."""
        from anetbbs.echomail.interbbs_sync import ensure_special_area, WALL_AREA_TAG
        from anetbbs.models import db
        with self.app.app_context():
            net = self._network()
            self.app.config['WALL_INTERBBS_NETWORK_ID'] = net.id
            area = ensure_special_area(net, WALL_AREA_TAG)
            area.is_sysop_only = False
            db.session.commit()

            area2 = ensure_special_area(net, WALL_AREA_TAG)
            self.assertTrue(area2.is_sysop_only)

    def test_ensure_special_area_skips_areafix_for_qwk_network(self):
        """send_areafix_request() itself only gates on hub_address being
        set, NOT on network_type -- QWK networks have no AreaFix
        protocol at all, so ensure_special_area() must add that gate
        itself or it would queue a NetmailMessage the QWK transport can
        never send."""
        from anetbbs.echomail.interbbs_sync import ensure_special_area, WALL_AREA_TAG
        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            net = self._network(network_type='qwk', hub_address='hub.example.com')
            ensure_special_area(net, WALL_AREA_TAG)
            self.assertEqual(NetmailMessage.query.count(), 0,
                              'no AreaFix netmail should be queued for a QWK network')

    def test_ensure_special_area_sends_areafix_for_binkp_spoke(self):
        from anetbbs.echomail.interbbs_sync import ensure_special_area, WALL_AREA_TAG
        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            net = self._network(network_type='binkp', hub_address='hub.example.com')
            ensure_special_area(net, WALL_AREA_TAG)
            self.assertEqual(NetmailMessage.query.count(), 1,
                              'a BinkP spoke should request the area via AreaFix')

    def test_ensure_special_area_does_not_resend_areafix_once_already_correct(self):
        """Regression test for a real bug found in a full echomail-
        subsystem audit: this AreaFix request used to fire
        unconditionally on EVERY call to ensure_special_area(), not
        just when the area was actually just created/repaired. Since
        this function runs on every single Wall post, logged caller,
        and new personal-best game score, a moderately active install
        flooded its upstream hub's AreaFix bot with a redundant
        subscribe request (plus a synchronous DB commit) on every one
        of those events, even though the very first call already
        subscribed successfully."""
        from anetbbs.echomail.interbbs_sync import ensure_special_area, WALL_AREA_TAG
        from anetbbs.models import NetmailMessage
        with self.app.app_context():
            net = self._network(network_type='binkp', hub_address='hub.example.com')
            ensure_special_area(net, WALL_AREA_TAG)
            self.assertEqual(NetmailMessage.query.count(), 1)

            # Simulate what post_wall_to_interbbs/post_lastcaller_to_interbbs/
            # post_score_to_interbbs actually do: call this on every event,
            # not just once.
            for _ in range(5):
                ensure_special_area(net, WALL_AREA_TAG)

            self.assertEqual(
                NetmailMessage.query.count(), 1,
                'repeat calls once the area is already correct must not '
                'send additional redundant AreaFix requests')

    def test_ensure_special_area_resends_areafix_if_repaired(self):
        """The gate must not be so broad it silently stops requesting
        AreaFix forever -- if a sysop's own edit knocks the area out of
        the expected state (covered by test_ensure_special_area_
        reasserts_sysop_only_flag), the repair should still trigger a
        fresh AreaFix request, same as the original creation did."""
        from anetbbs.echomail.interbbs_sync import ensure_special_area, WALL_AREA_TAG
        from anetbbs.models import db, NetmailMessage
        with self.app.app_context():
            net = self._network(network_type='binkp', hub_address='hub.example.com')
            area = ensure_special_area(net, WALL_AREA_TAG)
            self.assertEqual(NetmailMessage.query.count(), 1)

            area.is_subscribed = False
            db.session.commit()

            ensure_special_area(net, WALL_AREA_TAG)
            self.assertEqual(
                NetmailMessage.query.count(), 2,
                'repairing a knocked-out area should still trigger a '
                'fresh AreaFix request')

    # ------------------------------------------------------------------
    # Loop prevention
    # ------------------------------------------------------------------

    def test_local_post_relays_outbound(self):
        from anetbbs.echomail.interbbs_sync import post_wall_to_interbbs
        from anetbbs.models import db, WallPost, EchomailMessage
        with self.app.app_context():
            self.app.config['WALL_INTERBBS_ENABLED'] = True
            net = self._network()
            self.app.config['WALL_INTERBBS_NETWORK_ID'] = net.id

            wp = WallPost(username='jerry', display_name='StingRay', line1='hi')
            db.session.add(wp)
            db.session.commit()
            post_wall_to_interbbs(wp)

            msgs = EchomailMessage.query.filter_by(direction='outbound').all()
            self.assertEqual(len(msgs), 1)
            self.assertEqual(msgs[0].subject, 'ANET-WALL-POST')

    def test_imported_post_never_relays_back_out(self):
        """THE critical loop-prevention test. A WallPost with origin_bbs
        already set must produce zero outbound EchomailMessage rows,
        regardless of how WALL_INTERBBS_ENABLED is configured."""
        from anetbbs.echomail.interbbs_sync import post_wall_to_interbbs
        from anetbbs.models import db, WallPost, EchomailMessage
        with self.app.app_context():
            self.app.config['WALL_INTERBBS_ENABLED'] = True
            net = self._network()
            self.app.config['WALL_INTERBBS_NETWORK_ID'] = net.id

            wp = WallPost(username='remote', display_name='Other BBS User',
                          line1='hi', origin_bbs='OtherBBS', remote_msg_id='ABC123')
            db.session.add(wp)
            db.session.commit()
            post_wall_to_interbbs(wp)

            self.assertEqual(EchomailMessage.query.filter_by(direction='outbound').count(), 0)

    def test_disabled_feature_never_relays(self):
        from anetbbs.echomail.interbbs_sync import post_wall_to_interbbs
        from anetbbs.models import db, WallPost, EchomailMessage
        with self.app.app_context():
            self.app.config['WALL_INTERBBS_ENABLED'] = False
            net = self._network()
            self.app.config['WALL_INTERBBS_NETWORK_ID'] = net.id

            wp = WallPost(username='jerry', line1='hi')
            db.session.add(wp)
            db.session.commit()
            post_wall_to_interbbs(wp)

            self.assertEqual(EchomailMessage.query.filter_by(direction='outbound').count(), 0)

    def test_qwk_configured_network_never_relays(self):
        """QWK areas are identified by numeric conference number end to
        end -- a symbolic tag like ANET_WALL can never receive real QWK
        traffic no matter how the EchoArea row is created. Even if
        WALL_INTERBBS_NETWORK_ID somehow ends up pointing at a QWK
        network (e.g. a stale .env value from before the admin UI
        started filtering to BinkP-only), _configured_network() must
        refuse it rather than silently creating a useless area."""
        from anetbbs.echomail.interbbs_sync import post_wall_to_interbbs
        from anetbbs.models import db, WallPost, EchomailMessage, EchoArea
        with self.app.app_context():
            self.app.config['WALL_INTERBBS_ENABLED'] = True
            net = self._network(network_type='qwk')
            self.app.config['WALL_INTERBBS_NETWORK_ID'] = net.id

            wp = WallPost(username='jerry', line1='hi')
            db.session.add(wp)
            db.session.commit()
            post_wall_to_interbbs(wp)

            self.assertEqual(EchomailMessage.query.filter_by(direction='outbound').count(), 0)
            self.assertEqual(EchoArea.query.filter_by(network_id=net.id).count(), 0,
                              'no ANET_WALL area should be created on a QWK network at all')

    # ------------------------------------------------------------------
    # Inbound sync: global dedup + NULL msg_id handling
    # ------------------------------------------------------------------

    def test_inbound_sync_materializes_new_message(self):
        from anetbbs.echomail.interbbs_sync import sync_wall_inbound, ensure_special_area, WALL_AREA_TAG
        from anetbbs.models import db, EchomailMessage, WallPost
        with self.app.app_context():
            net = self._network()
            self.app.config['WALL_INTERBBS_NETWORK_ID'] = net.id
            area = ensure_special_area(net, WALL_AREA_TAG)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='MSG-1',
                from_name='Firehawke', to_name='All', subject='ANET-WALL-POST',
                body='hello\nworld', direction='inbound',
            ))
            db.session.commit()

            ok, out = sync_wall_inbound(self.app, {})
            self.assertTrue(ok, out)
            posts = WallPost.query.all()
            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0].line1, 'hello')
            self.assertEqual(posts[0].line2, 'world')
            self.assertEqual(posts[0].remote_msg_id, 'MSG-1')
            self.assertIsNotNone(posts[0].origin_bbs)

    def test_inbound_sync_dedup_is_global_not_per_area(self):
        """The same msg_id arriving via TWO different areas (e.g. two
        overlapping network subscriptions) must only ever materialize
        ONE WallPost -- the transport layer's own dedup (poller.py) is
        scoped to (msg_id, area_id), so this scan must not rely on
        that."""
        from anetbbs.echomail.interbbs_sync import sync_wall_inbound, ensure_special_area, WALL_AREA_TAG
        from anetbbs.models import db, EchomailMessage, EchoArea, WallPost
        with self.app.app_context():
            net = self._network()
            self.app.config['WALL_INTERBBS_NETWORK_ID'] = net.id
            area1 = ensure_special_area(net, WALL_AREA_TAG)
            # Simulate a second area somehow carrying the same tag (e.g.
            # a duplicate-tag network topology) with an identical msg_id.
            area2 = EchoArea(network_id=net.id, tag='ANET_WALL_DUP',
                             name='dup', is_active=True, is_subscribed=True)
            db.session.add(area2)
            db.session.commit()

            for area in (area1, area2):
                db.session.add(EchomailMessage(
                    area_id=area.id, network_id=net.id, msg_id='SAME-ID',
                    from_name='X', to_name='All', subject='ANET-WALL-POST',
                    body='dup test', direction='inbound',
                ))
            db.session.commit()

            # Both areas carry tag ANET_WALL via ensure_special_area's own
            # tag-based lookup only finds area1 normally; to actually
            # exercise cross-area dedup, sync scans by tag == WALL_AREA_TAG.
            # Retag area2 to match so both are in-scope for the scan.
            area2.tag = WALL_AREA_TAG
            db.session.commit()

            ok, out = sync_wall_inbound(self.app, {})
            self.assertTrue(ok, out)
            self.assertEqual(WallPost.query.count(), 1,
                              'the same msg_id across two areas must only import once')

    def test_inbound_sync_skips_null_msg_id(self):
        """Rows with msg_id IS NULL (malformed/legacy packets) must be
        skipped, not materialized -- they can never be deduped on a
        later scan and would otherwise re-import forever."""
        from anetbbs.echomail.interbbs_sync import sync_wall_inbound, ensure_special_area, WALL_AREA_TAG
        from anetbbs.models import db, EchomailMessage, WallPost
        with self.app.app_context():
            net = self._network()
            self.app.config['WALL_INTERBBS_NETWORK_ID'] = net.id
            area = ensure_special_area(net, WALL_AREA_TAG)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id=None,
                from_name='X', to_name='All', subject='ANET-WALL-POST',
                body='no msgid', direction='inbound',
            ))
            db.session.commit()

            ok, out = sync_wall_inbound(self.app, {})
            self.assertTrue(ok, out)
            self.assertEqual(WallPost.query.count(), 0)
            self.assertIn('skipped 1', out)

    def test_inbound_sync_does_not_reimport_already_known_message(self):
        """Running the scan twice must not create a second WallPost for
        the same inbound message."""
        from anetbbs.echomail.interbbs_sync import sync_wall_inbound, ensure_special_area, WALL_AREA_TAG
        from anetbbs.models import db, EchomailMessage, WallPost
        with self.app.app_context():
            net = self._network()
            self.app.config['WALL_INTERBBS_NETWORK_ID'] = net.id
            area = ensure_special_area(net, WALL_AREA_TAG)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='REPEAT-1',
                from_name='X', to_name='All', subject='ANET-WALL-POST',
                body='once please', direction='inbound',
            ))
            db.session.commit()

            sync_wall_inbound(self.app, {})
            sync_wall_inbound(self.app, {})
            self.assertEqual(WallPost.query.count(), 1)

    def test_soft_deleted_imported_post_is_not_reimported(self):
        """A sysop soft-deleting a specific imported post (local
        moderation) must not cause it to reappear on the next scan."""
        from anetbbs.echomail.interbbs_sync import sync_wall_inbound, ensure_special_area, WALL_AREA_TAG
        from anetbbs.models import db, EchomailMessage, WallPost
        with self.app.app_context():
            net = self._network()
            self.app.config['WALL_INTERBBS_NETWORK_ID'] = net.id
            area = ensure_special_area(net, WALL_AREA_TAG)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='MOD-1',
                from_name='X', to_name='All', subject='ANET-WALL-POST',
                body='spam probably', direction='inbound',
            ))
            db.session.commit()
            sync_wall_inbound(self.app, {})

            post = WallPost.query.filter_by(remote_msg_id='MOD-1').first()
            post.is_deleted = True
            db.session.commit()

            sync_wall_inbound(self.app, {})
            self.assertEqual(WallPost.query.filter_by(remote_msg_id='MOD-1').count(), 1,
                              'soft-deleted imported post must not be re-materialized')


class InterbbsLastCallersTests(unittest.TestCase):
    """Same loop-prevention/dedup properties as InterbbsSyncTests, for
    the Last Callers half -- plus the privacy invariant that ip_address
    must never be relayed or materialized for imported entries."""

    # See InterbbsSyncTests' setUpClass/tearDownClass comment -- same leak,
    # same fix, needed independently here since this is a separate class.
    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_app(str(Path(self._tmp.name) / 'lc.db'))
        with self.app.app_context():
            from anetbbs.models import db
            db.create_all()

    def _network(self):
        from anetbbs.models import db, EchomailNetwork
        net = EchomailNetwork(name='ANotherNetwork', network_type='binkp',
                              our_address='1:2/3.4')
        db.session.add(net)
        db.session.commit()
        return net

    def test_local_caller_relays_outbound_without_ip(self):
        from anetbbs.echomail.interbbs_sync import post_lastcaller_to_interbbs
        from anetbbs.models import db, CallerLog, EchomailMessage
        with self.app.app_context():
            self.app.config['LASTCALLERS_INTERBBS_ENABLED'] = True
            net = self._network()
            self.app.config['LASTCALLERS_INTERBBS_NETWORK_ID'] = net.id

            cl = CallerLog(username='jerry', service='telnet', ip_address='203.0.113.5')
            db.session.add(cl)
            db.session.commit()
            post_lastcaller_to_interbbs(cl)

            msgs = EchomailMessage.query.filter_by(direction='outbound').all()
            self.assertEqual(len(msgs), 1)
            self.assertNotIn('203.0.113.5', msgs[0].body,
                              'ip_address must never appear in the relayed body')

    def test_imported_caller_never_relays_back_out(self):
        from anetbbs.echomail.interbbs_sync import post_lastcaller_to_interbbs
        from anetbbs.models import db, CallerLog, EchomailMessage
        with self.app.app_context():
            self.app.config['LASTCALLERS_INTERBBS_ENABLED'] = True
            net = self._network()
            self.app.config['LASTCALLERS_INTERBBS_NETWORK_ID'] = net.id

            cl = CallerLog(username='remote', service='telnet',
                           origin_bbs='OtherBBS', remote_msg_id='LC-1')
            db.session.add(cl)
            db.session.commit()
            post_lastcaller_to_interbbs(cl)

            self.assertEqual(EchomailMessage.query.filter_by(direction='outbound').count(), 0)

    def test_hide_sysop_blocks_relay_for_admin_login(self):
        """Real report: a sysop who tests heavily flooded every other
        BBS's Last Callers area on the shared network with their own
        account. LASTCALLERS_HIDE_SYSOP already hid sysop logins from
        the LOCAL displays (see test_lastcallers_hide_sysop.py) but
        this outbound relay never checked it -- a sysop login got
        relayed regardless. Same toggle now gates the relay too."""
        from anetbbs.echomail.interbbs_sync import post_lastcaller_to_interbbs
        from anetbbs.models import db, User, CallerLog, EchomailMessage
        with self.app.app_context():
            self.app.config['LASTCALLERS_INTERBBS_ENABLED'] = True
            self.app.config['LASTCALLERS_HIDE_SYSOP'] = True
            net = self._network()
            self.app.config['LASTCALLERS_INTERBBS_NETWORK_ID'] = net.id

            sysop = User(username='stingray', email='stingray@example.com',
                        password_hash='x', is_admin=True)
            db.session.add(sysop)
            db.session.commit()

            cl = CallerLog(user_id=sysop.id, username='stingray', service='telnet')
            db.session.add(cl)
            db.session.commit()
            post_lastcaller_to_interbbs(cl)

            self.assertEqual(EchomailMessage.query.filter_by(direction='outbound').count(), 0)

    def test_hide_sysop_does_not_block_relay_for_regular_user(self):
        from anetbbs.echomail.interbbs_sync import post_lastcaller_to_interbbs
        from anetbbs.models import db, User, CallerLog, EchomailMessage
        with self.app.app_context():
            self.app.config['LASTCALLERS_INTERBBS_ENABLED'] = True
            self.app.config['LASTCALLERS_HIDE_SYSOP'] = True
            net = self._network()
            self.app.config['LASTCALLERS_INTERBBS_NETWORK_ID'] = net.id

            regular = User(username='jerry_user', email='ju@example.com',
                           password_hash='x', is_admin=False)
            db.session.add(regular)
            db.session.commit()

            cl = CallerLog(user_id=regular.id, username='jerry_user', service='telnet')
            db.session.add(cl)
            db.session.commit()
            post_lastcaller_to_interbbs(cl)

            self.assertEqual(EchomailMessage.query.filter_by(direction='outbound').count(), 1)

    def test_sysop_login_still_relays_when_hide_sysop_is_off(self):
        """Default-off behavior must be unchanged -- this is an opt-in
        toggle, not a new default."""
        from anetbbs.echomail.interbbs_sync import post_lastcaller_to_interbbs
        from anetbbs.models import db, User, CallerLog, EchomailMessage
        with self.app.app_context():
            self.app.config['LASTCALLERS_INTERBBS_ENABLED'] = True
            self.app.config['LASTCALLERS_HIDE_SYSOP'] = False
            net = self._network()
            self.app.config['LASTCALLERS_INTERBBS_NETWORK_ID'] = net.id

            sysop = User(username='stingray', email='stingray@example.com',
                        password_hash='x', is_admin=True)
            db.session.add(sysop)
            db.session.commit()

            cl = CallerLog(user_id=sysop.id, username='stingray', service='telnet')
            db.session.add(cl)
            db.session.commit()
            post_lastcaller_to_interbbs(cl)

            self.assertEqual(EchomailMessage.query.filter_by(direction='outbound').count(), 1)

    def test_inbound_sync_materializes_without_ip_and_dedups_globally(self):
        from anetbbs.echomail.interbbs_sync import (
            sync_lastcallers_inbound, ensure_special_area, LASTCALLERS_AREA_TAG)
        from anetbbs.models import db, EchomailMessage, EchoArea, CallerLog
        with self.app.app_context():
            net = self._network()
            self.app.config['LASTCALLERS_INTERBBS_NETWORK_ID'] = net.id
            area1 = ensure_special_area(net, LASTCALLERS_AREA_TAG)
            area2 = EchoArea(network_id=net.id, tag='ANET_LASTCALLERS_DUP',
                             name='dup', is_active=True, is_subscribed=True)
            db.session.add(area2)
            db.session.commit()
            area2.tag = LASTCALLERS_AREA_TAG
            db.session.commit()

            for area in (area1, area2):
                db.session.add(EchomailMessage(
                    area_id=area.id, network_id=net.id, msg_id='LC-SAME',
                    from_name='Firehawke', to_name='All', subject='ANET-LASTCALLER',
                    body='telnet\n2026-07-08T12:00:00', direction='inbound',
                ))
            db.session.commit()

            ok, out = sync_lastcallers_inbound(self.app, {})
            self.assertTrue(ok, out)
            entries = CallerLog.query.filter_by(remote_msg_id='LC-SAME').all()
            self.assertEqual(len(entries), 1,
                              'the same msg_id across two areas must only import once')
            self.assertIsNone(entries[0].ip_address,
                              'imported caller entries must never have an ip_address')
            self.assertEqual(entries[0].service, 'telnet')

    def test_inbound_sync_skips_null_msg_id(self):
        from anetbbs.echomail.interbbs_sync import (
            sync_lastcallers_inbound, ensure_special_area, LASTCALLERS_AREA_TAG)
        from anetbbs.models import db, EchomailMessage, CallerLog
        with self.app.app_context():
            net = self._network()
            self.app.config['LASTCALLERS_INTERBBS_NETWORK_ID'] = net.id
            area = ensure_special_area(net, LASTCALLERS_AREA_TAG)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id=None,
                from_name='X', to_name='All', subject='ANET-LASTCALLER',
                body='telnet\n', direction='inbound',
            ))
            db.session.commit()

            ok, out = sync_lastcallers_inbound(self.app, {})
            self.assertTrue(ok, out)
            self.assertEqual(CallerLog.query.filter(CallerLog.origin_bbs.isnot(None)).count(), 0,
                              'a NULL msg_id row must not be materialized')


class InterbbsScoresTests(unittest.TestCase):
    """InterBBS door/web game score sharing -- same loop-prevention/dedup
    shape as Wall/Last Callers, plus properties unique to scores: only
    NEW PERSONAL BESTS relay (not every submission), a double opt-in
    gate (sender's Game.share_scores_interbbs AND the receiver's own
    matching Game must both be opted in), a ghost placeholder User for
    the hard NOT NULL user_id FK, and play_count must never be touched
    by inbound sync (materializing a remote score is not a local play).
    """

    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_app(str(Path(self._tmp.name) / 'scores.db'))
        with self.app.app_context():
            from anetbbs.models import db
            db.create_all()

    def _network(self, network_type='binkp'):
        from anetbbs.models import db, EchomailNetwork
        net = EchomailNetwork(name='ANotherNetwork', network_type=network_type,
                              our_address='1:2/3.4')
        db.session.add(net)
        db.session.commit()
        return net

    def _game(self, slug='zztestgame', share=True):
        from anetbbs.models import db, Game
        game = Game(name=slug.title(), slug=slug, game_type='builtin_web',
                   share_scores_interbbs=share)
        db.session.add(game)
        db.session.commit()
        return game

    def _user(self, username='jerry'):
        from anetbbs.models import db, User
        user = User(username=username, email=f'{username}@example.com',
                   password_hash='x')
        db.session.add(user)
        db.session.commit()
        return user

    def _score(self, game, user, value):
        from anetbbs.models import db, GameScore
        gs = GameScore(game_id=game.id, user_id=user.id, score=value)
        db.session.add(gs)
        db.session.commit()
        return gs

    # ------------------------------------------------------------------
    # Outbound: personal-best gating
    # ------------------------------------------------------------------

    def test_first_score_relays_as_personal_best(self):
        """The first score for any user+game is trivially a personal
        best -- nothing prior to beat -- so it relays immediately."""
        from anetbbs.echomail.interbbs_sync import post_score_to_interbbs
        from anetbbs.models import EchomailMessage
        with self.app.app_context():
            self.app.config['GAMES_INTERBBS_ENABLED'] = True
            net = self._network()
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            game = self._game()
            user = self._user()

            gs = self._score(game, user, 100)
            post_score_to_interbbs(gs)

            msgs = EchomailMessage.query.filter_by(direction='outbound').all()
            self.assertEqual(len(msgs), 1)
            self.assertEqual(msgs[0].subject, 'ANET-GAMESCORE')
            self.assertIn('zztestgame', msgs[0].body)
            self.assertIn('100', msgs[0].body)

    def test_lower_or_equal_score_does_not_relay(self):
        from anetbbs.echomail.interbbs_sync import post_score_to_interbbs
        from anetbbs.models import EchomailMessage
        with self.app.app_context():
            self.app.config['GAMES_INTERBBS_ENABLED'] = True
            net = self._network()
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            game = self._game()
            user = self._user()

            post_score_to_interbbs(self._score(game, user, 100))
            post_score_to_interbbs(self._score(game, user, 100))  # tied
            post_score_to_interbbs(self._score(game, user, 50))   # lower

            msgs = EchomailMessage.query.filter_by(direction='outbound').all()
            self.assertEqual(len(msgs), 1, 'only the first (personal-best) score should relay')

    def test_higher_score_relays_again(self):
        from anetbbs.echomail.interbbs_sync import post_score_to_interbbs
        from anetbbs.models import EchomailMessage
        with self.app.app_context():
            self.app.config['GAMES_INTERBBS_ENABLED'] = True
            net = self._network()
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            game = self._game()
            user = self._user()

            post_score_to_interbbs(self._score(game, user, 100))
            post_score_to_interbbs(self._score(game, user, 50))    # lower, no relay
            post_score_to_interbbs(self._score(game, user, 200))   # new best, relays

            msgs = EchomailMessage.query.filter_by(direction='outbound').all()
            self.assertEqual(len(msgs), 2)
            bodies = [m.body for m in msgs]
            self.assertTrue(any('100' in b for b in bodies))
            self.assertTrue(any('200' in b for b in bodies))

    def test_opted_out_game_never_relays(self):
        from anetbbs.echomail.interbbs_sync import post_score_to_interbbs
        from anetbbs.models import EchomailMessage
        with self.app.app_context():
            self.app.config['GAMES_INTERBBS_ENABLED'] = True
            net = self._network()
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            game = self._game(share=False)
            user = self._user()

            post_score_to_interbbs(self._score(game, user, 100))

            self.assertEqual(EchomailMessage.query.filter_by(direction='outbound').count(), 0)

    def test_imported_score_never_relays_back_out(self):
        """THE critical loop-prevention test -- mirrors WallPost's."""
        from anetbbs.echomail.interbbs_sync import post_score_to_interbbs
        from anetbbs.models import db, GameScore, EchomailMessage
        with self.app.app_context():
            self.app.config['GAMES_INTERBBS_ENABLED'] = True
            net = self._network()
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            game = self._game()
            user = self._user()

            gs = GameScore(game_id=game.id, user_id=user.id, score=999,
                          origin_bbs='OtherBBS', remote_msg_id='ABC123')
            db.session.add(gs)
            db.session.commit()
            post_score_to_interbbs(gs)

            self.assertEqual(EchomailMessage.query.filter_by(direction='outbound').count(), 0)

    def test_disabled_feature_never_relays(self):
        from anetbbs.echomail.interbbs_sync import post_score_to_interbbs
        from anetbbs.models import EchomailMessage
        with self.app.app_context():
            self.app.config['GAMES_INTERBBS_ENABLED'] = False
            net = self._network()
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            game = self._game()
            user = self._user()

            post_score_to_interbbs(self._score(game, user, 100))

            self.assertEqual(EchomailMessage.query.filter_by(direction='outbound').count(), 0)

    def test_qwk_configured_network_never_relays(self):
        from anetbbs.echomail.interbbs_sync import post_score_to_interbbs
        from anetbbs.models import EchomailMessage, EchoArea
        with self.app.app_context():
            self.app.config['GAMES_INTERBBS_ENABLED'] = True
            net = self._network(network_type='qwk')
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            game = self._game()
            user = self._user()

            post_score_to_interbbs(self._score(game, user, 100))

            self.assertEqual(EchomailMessage.query.filter_by(direction='outbound').count(), 0)
            self.assertEqual(EchoArea.query.filter_by(network_id=net.id).count(), 0)

    # ------------------------------------------------------------------
    # Inbound: double opt-in gate, ghost user, play_count invariant
    # ------------------------------------------------------------------

    def test_inbound_sync_materializes_for_opted_in_game(self):
        from anetbbs.echomail.interbbs_sync import (
            sync_scores_inbound, ensure_special_area, GAMES_AREA_TAG)
        from anetbbs.models import db, EchomailMessage, GameScore
        with self.app.app_context():
            net = self._network()
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            area = ensure_special_area(net, GAMES_AREA_TAG)
            game = self._game(slug='zztestgame', share=True)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='MSG-1',
                from_name='Firehawke', to_name='All', subject='ANET-GAMESCORE',
                body='zztestgame\n500\nFirehawke', direction='inbound',
            ))
            db.session.commit()

            ok, out = sync_scores_inbound(self.app, {})
            self.assertTrue(ok, out)
            scores = GameScore.query.all()
            self.assertEqual(len(scores), 1)
            self.assertEqual(scores[0].game_id, game.id)
            self.assertEqual(scores[0].score, 500)
            self.assertEqual(scores[0].remote_msg_id, 'MSG-1')
            self.assertIsNotNone(scores[0].origin_bbs)

    def test_inbound_sync_skips_when_local_game_opted_out(self):
        from anetbbs.echomail.interbbs_sync import (
            sync_scores_inbound, ensure_special_area, GAMES_AREA_TAG)
        from anetbbs.models import db, EchomailMessage, GameScore
        with self.app.app_context():
            net = self._network()
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            area = ensure_special_area(net, GAMES_AREA_TAG)
            self._game(slug='zztestgame', share=False)  # opted OUT locally
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='MSG-2',
                from_name='Firehawke', to_name='All', subject='ANET-GAMESCORE',
                body='zztestgame\n500\nFirehawke', direction='inbound',
            ))
            db.session.commit()

            ok, out = sync_scores_inbound(self.app, {})
            self.assertTrue(ok, out)
            self.assertEqual(GameScore.query.count(), 0)

    def test_inbound_sync_skips_when_no_local_game_matches_slug(self):
        from anetbbs.echomail.interbbs_sync import (
            sync_scores_inbound, ensure_special_area, GAMES_AREA_TAG)
        from anetbbs.models import db, EchomailMessage, GameScore
        with self.app.app_context():
            net = self._network()
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            area = ensure_special_area(net, GAMES_AREA_TAG)
            # No local 'zztestgame' game exists at all in this test.
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='MSG-3',
                from_name='Firehawke', to_name='All', subject='ANET-GAMESCORE',
                body='zztestgame\n500\nFirehawke', direction='inbound',
            ))
            db.session.commit()

            ok, out = sync_scores_inbound(self.app, {})
            self.assertTrue(ok, out)
            self.assertEqual(GameScore.query.count(), 0)

    def test_inbound_sync_uses_ghost_user_and_remote_username(self):
        from anetbbs.echomail.interbbs_sync import (
            sync_scores_inbound, ensure_special_area, GAMES_AREA_TAG)
        from anetbbs.models import db, EchomailMessage, GameScore, User
        with self.app.app_context():
            net = self._network()
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            area = ensure_special_area(net, GAMES_AREA_TAG)
            self._game(slug='zztestgame', share=True)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='MSG-4',
                from_name='Firehawke', to_name='All', subject='ANET-GAMESCORE',
                body='zztestgame\n500\nFirehawke', direction='inbound',
            ))
            db.session.commit()

            sync_scores_inbound(self.app, {})

            score = GameScore.query.first()
            ghost = User.query.get(score.user_id)
            self.assertEqual(ghost.username, '__interbbs_import__')
            self.assertFalse(ghost.is_active)
            self.assertEqual(score.remote_username, 'Firehawke')
            self.assertEqual(score.display_username, 'Firehawke')
            # Only one ghost user ever, even across multiple imports.
            self.assertEqual(User.query.filter_by(username='__interbbs_import__').count(), 1)

    def test_inbound_sync_does_not_increment_play_count(self):
        """Materializing an imported score is not a local play --
        play_count must only ever reflect actual local play activity."""
        from anetbbs.echomail.interbbs_sync import (
            sync_scores_inbound, ensure_special_area, GAMES_AREA_TAG)
        from anetbbs.models import db, EchomailMessage
        with self.app.app_context():
            net = self._network()
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            area = ensure_special_area(net, GAMES_AREA_TAG)
            game = self._game(slug='zztestgame', share=True)
            before = game.play_count or 0
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='MSG-5',
                from_name='Firehawke', to_name='All', subject='ANET-GAMESCORE',
                body='zztestgame\n500\nFirehawke', direction='inbound',
            ))
            db.session.commit()

            sync_scores_inbound(self.app, {})

            db.session.refresh(game)
            self.assertEqual(game.play_count or 0, before,
                             'inbound sync must never touch play_count')

    def test_inbound_sync_dedup_is_global(self):
        from anetbbs.echomail.interbbs_sync import (
            sync_scores_inbound, ensure_special_area, GAMES_AREA_TAG)
        from anetbbs.models import db, EchomailMessage, EchoArea, GameScore
        with self.app.app_context():
            net = self._network()
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            area1 = ensure_special_area(net, GAMES_AREA_TAG)
            area2 = EchoArea(network_id=net.id, tag='ANET_GAMESCORES_DUP',
                             name='dup', is_active=True, is_subscribed=True)
            db.session.add(area2)
            db.session.commit()
            self._game(slug='zztestgame', share=True)

            for area in (area1, area2):
                db.session.add(EchomailMessage(
                    area_id=area.id, network_id=net.id, msg_id='SAME-ID',
                    from_name='X', to_name='All', subject='ANET-GAMESCORE',
                    body='zztestgame\n500\nX', direction='inbound',
                ))
            db.session.commit()
            area2.tag = GAMES_AREA_TAG
            db.session.commit()

            ok, out = sync_scores_inbound(self.app, {})
            self.assertTrue(ok, out)
            self.assertEqual(GameScore.query.count(), 1)

    def test_inbound_sync_skips_null_msg_id(self):
        from anetbbs.echomail.interbbs_sync import (
            sync_scores_inbound, ensure_special_area, GAMES_AREA_TAG)
        from anetbbs.models import db, EchomailMessage, GameScore
        with self.app.app_context():
            net = self._network()
            self.app.config['GAMES_INTERBBS_NETWORK_ID'] = net.id
            area = ensure_special_area(net, GAMES_AREA_TAG)
            self._game(slug='zztestgame', share=True)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id=None,
                from_name='X', to_name='All', subject='ANET-GAMESCORE',
                body='zztestgame\n500\nX', direction='inbound',
            ))
            db.session.commit()

            ok, out = sync_scores_inbound(self.app, {})
            self.assertTrue(ok, out)
            self.assertEqual(GameScore.query.count(), 0)
            self.assertIn('skipped 1 (no msg_id)', out)


class TransportInvariantTests(unittest.TestCase):
    """This whole design's loop-safety depends on one transport-layer
    fact: relaying a message never silently regenerates a msg_id that
    was already set on the ORM object. Pin that invariant directly."""

    def test_binkp_packer_reuses_existing_msg_id(self):
        import inspect
        from anetbbs.echomail import binkp
        src = inspect.getsource(binkp)
        self.assertIn('if msg.msg_id:', src,
                      "binkp.py's packet builder must still gate msg_id "
                      "generation on 'not already set' -- if this ever "
                      "changes to unconditionally regenerate, the "
                      "InterBBS Wall loop-prevention design breaks.")


class InboundSyncPushesDedupIntoSqlTests(unittest.TestCase):
    """Regression test for a real Medium-severity finding from a
    security/performance audit (2026-08-31): all three inbound sync
    handlers (sync_wall_inbound, sync_lastcallers_inbound,
    sync_scores_inbound) used to load the FULL set of already-imported
    remote_msg_ids AND the FULL set of every inbound EchomailMessage
    ever received in the relevant areas into Python on EVERY scheduled
    tick, forever -- cost grows with total historical volume, not with
    what's new since the last run. Fixed by pushing the "already
    imported" check into the query itself (an indexed NOT IN subquery
    against the target table's remote_msg_id column).

    The functional correctness of this (same rows imported, dedup
    still works, NULL msg_id still re-scanned) is already covered
    exhaustively by InterbbsSyncTests/InterbbsLastCallersTests/
    InterbbsScoresTests above, and all of those still pass unmodified
    against this fix. What's new here is a direct check that the
    *mechanism* changed: capture the actual SQL sent to the DB during
    a sync call and confirm the SELECT against echomail_messages
    itself carries a NOT IN subquery, rather than an unfiltered SELECT
    followed by Python-side filtering against a fully-materialized set."""

    @classmethod
    def setUpClass(cls):
        import anetbbs.config as cfg_mod
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI

    @classmethod
    def tearDownClass(cls):
        import anetbbs.config as cfg_mod
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_app(str(Path(self._tmp.name) / 'a.db'))
        with self.app.app_context():
            from anetbbs.models import db
            db.create_all()

    def _network(self):
        from anetbbs.models import db, EchomailNetwork
        net = EchomailNetwork(
            name='ANotherNetwork', network_type='binkp',
            our_address='1:2/3.4')
        db.session.add(net)
        db.session.commit()
        return net

    def test_wall_sync_filters_already_known_messages_in_sql_not_python(self):
        from sqlalchemy import event
        from anetbbs.echomail.interbbs_sync import (
            sync_wall_inbound, ensure_special_area, WALL_AREA_TAG)
        from anetbbs.models import db, EchomailMessage

        with self.app.app_context():
            net = self._network()
            self.app.config['WALL_INTERBBS_NETWORK_ID'] = net.id
            area = ensure_special_area(net, WALL_AREA_TAG)
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, msg_id='MSG-CAPTURE-1',
                from_name='Someone', to_name='All', subject='ANET-WALL-POST',
                body='captured\nquery', direction='inbound',
            ))
            db.session.commit()

            captured_statements = []

            def _capture(conn, cursor, statement, parameters, context, executemany):
                captured_statements.append(statement)

            event.listen(db.engine, 'before_cursor_execute', _capture)
            try:
                ok, out = sync_wall_inbound(self.app, {})
            finally:
                event.remove(db.engine, 'before_cursor_execute', _capture)
            self.assertTrue(ok, out)

            select_statements = [
                s for s in captured_statements
                if 'FROM echomail_messages' in s and 'SELECT' in s.upper()]
            self.assertTrue(select_statements,
                            'expected at least one SELECT against '
                            'echomail_messages')
            self.assertTrue(
                any('NOT IN' in s.upper() for s in select_statements),
                'the SELECT against echomail_messages must carry the '
                'dedup filter (NOT IN a subquery against '
                'wall_posts.remote_msg_id) in the SQL itself -- if this '
                'regresses back to an unfiltered SELECT, every tick '
                'goes back to loading full inbound-message history into '
                'Python again')


if __name__ == '__main__':
    unittest.main()
