"""Regression tests for anetbbs/games/msgbase_bridge.py -- the
synchronous CLI bridge that lets a Synchronet-JS door's real MsgBase
calls (synchronet_compat.py's MsgBase class) reach ANetBBS's actual
echomail data. Built to make Minesweeper's real InterBBS DOVE-Net score
sharing work against a real EchoArea, not a stub.

Each op is invoked as a real subprocess (matching exactly how the
Node-side MsgBase class calls it via child_process.spawnSync) against a
real throwaway SQLite DB seeded with a real EchomailNetwork/EchoArea, so
these tests exercise the actual code path end to end, not an in-process
shortcut.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BRIDGE = _REPO_ROOT / 'anetbbs' / 'games' / 'msgbase_bridge.py'


class MsgBaseBridgeTests(unittest.TestCase):
    def setUp(self):
        self._db_path = str(Path(__file__).resolve().parent / '.msgbase_bridge_test.db')
        if os.path.exists(self._db_path):
            os.remove(self._db_path)

        import anetbbs.config as cfg_mod
        # In-process seeding/verification uses TestingConfig (matches
        # create_app('testing') below); the bridge itself runs as a real
        # SEPARATE subprocess and gets DevelopmentConfig via the
        # FLASK_ENV/DATABASE_URL env vars set in _run_bridge() --
        # TestingConfig hardcodes sqlite:///:memory: and ignores
        # DATABASE_URL entirely, so it can't be used for the subprocess
        # side, only DevelopmentConfig/ProductionConfig read the env var.
        # Both configs are pointed at the SAME on-disk file so the two
        # processes share real state.
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{self._db_path}'
        cfg_mod.DevelopmentConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{self._db_path}'
        from anetbbs.web_app import create_app
        app = create_app('testing')
        with app.app_context():
            from anetbbs.models import db, EchomailNetwork, EchoArea, BinkPNode, EchoAreaNode
            db.session.execute(db.text('DELETE FROM echomail_messages'))
            db.session.execute(db.text('DELETE FROM echo_areas'))
            db.session.execute(db.text('DELETE FROM echomail_networks'))
            db.session.commit()
            net = EchomailNetwork(name='DOVE-Net', network_type='binkp',
                                  our_address='1:2/3')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='SYNCDATA', name='Synchronet Data')
            db.session.add(area)
            db.session.commit()
            self.area_id = area.id
            self.network_id = net.id
            # A downstream node subscribed to this area -- so save_msg's
            # own toss_message() call has something real to queue for,
            # letting the BinkPHoldQueue assertion mean something.
            node = BinkPNode(ftn_address='1:2/9', name='Downstream',
                             password='testpass')
            db.session.add(node)
            db.session.commit()
            db.session.add(EchoAreaNode(echo_area_id=area.id, node_id=node.id))
            db.session.commit()
            self.node_id = node.id

    def tearDown(self):
        for suffix in ('', '-wal', '-shm'):
            path = self._db_path + suffix
            if os.path.exists(path):
                os.remove(path)

    def _run_bridge(self, *args):
        env = dict(os.environ)
        env['FLASK_ENV'] = 'development'
        env['DATABASE_URL'] = f'sqlite:///{self._db_path}'
        result = subprocess.run(
            [sys.executable, str(_BRIDGE)] + list(args),
            capture_output=True, text=True, timeout=30, env=env,
        )
        self.assertEqual(result.returncode, 0,
                         msg=f'bridge crashed: {result.stderr}')
        return json.loads(result.stdout)

    def test_open_reports_correct_area_and_last_msg(self):
        result = self._run_bridge('open', 'SYNCDATA')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['area_id'], self.area_id)
        self.assertEqual(result['network_id'], self.network_id)
        self.assertEqual(result['last_msg'], 0)

    def test_open_unknown_area_reports_not_ok(self):
        result = self._run_bridge('open', 'NOSUCHAREA')
        self.assertFalse(result['ok'])

    def test_open_is_case_insensitive(self):
        result = self._run_bridge('open', 'syncdata')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['area_id'], self.area_id)

    def test_save_msg_creates_real_row_and_queues_for_tossing(self):
        payload = json.dumps({'to': 'Synchronet Minesweeper', 'from': 'StingRay',
                              'subject': 'Winner', 'body': '{"ok":true}'})
        result = self._run_bridge('save_msg', 'SYNCDATA', payload)
        self.assertTrue(result['ok'], result)
        msg_id = result['id']

        import anetbbs.config as cfg_mod
        cfg_mod.DevelopmentConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{self._db_path}'
        from anetbbs.web_app import create_app
        app = create_app('development')
        with app.app_context():
            from anetbbs.models import EchomailMessage, BinkPHoldQueue
            row = EchomailMessage.query.get(msg_id)
            self.assertIsNotNone(row)
            self.assertEqual(row.direction, 'outbound')
            self.assertEqual(row.from_name, 'StingRay')
            self.assertEqual(row.to_name, 'Synchronet Minesweeper')
            self.assertEqual(row.subject, 'Winner')
            self.assertEqual(row.body, '{"ok":true}')
            self.assertEqual(row.area_id, self.area_id)
            # Proves toss_message() actually fired, not just that the
            # row exists -- the real point of calling it.
            hold = BinkPHoldQueue.query.filter_by(message_id=msg_id).first()
            self.assertIsNotNone(hold, 'save_msg must call toss_message() '
                                 'so the win actually reaches the network')
            self.assertEqual(hold.node_id, self.node_id)

    def test_get_index_returns_raw_text_not_hashed(self):
        payload = json.dumps({'to': 'Synchronet Minesweeper', 'from': 'StingRay',
                              'subject': 'Winner', 'body': 'x'})
        self._run_bridge('save_msg', 'SYNCDATA', payload)

        result = self._run_bridge('get_index', 'SYNCDATA', '0')
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(result['entries']), 1)
        entry = result['entries'][0]
        self.assertEqual(entry['to'], 'Synchronet Minesweeper')
        self.assertEqual(entry['subject'], 'Winner')
        self.assertIn('number', entry)

    def test_get_index_embeds_header_and_body_fields_inline(self):
        """Regression for a real report: DOVE-Net score-sharing in
        Minesweeper's own get_winners() looked like a total lockup on
        "view winners" -- not an infinite loop, but hundreds of
        sequential get_header/get_body subprocess spawns (one PER
        matching message) in a tight loop, each paying fresh Python +
        Flask + SQLAlchemy startup cost. get_index's one query already
        has every field a header/body fetch would need loaded, so it's
        returned inline here -- letting the JS-side MsgBase shim
        (synchronet_compat.py) cache it and serve get_msg_header()/
        get_msg_body() from memory instead of shelling out again per
        message. See test_synchronet_compat_missing_globals.py's
        test_get_msg_header_and_body_serve_from_cache_after_get_index
        for the JS-side half of this fix."""
        payload = json.dumps({'to': 'Synchronet Minesweeper', 'from': 'StingRay',
                              'subject': 'Winner', 'body': '{"score":42}'})
        self._run_bridge('save_msg', 'SYNCDATA', payload)

        result = self._run_bridge('get_index', 'SYNCDATA', '0')
        entry = result['entries'][0]
        self.assertEqual(entry['from'], 'StingRay')
        self.assertEqual(entry['body'], '{"score":42}')
        self.assertFalse(entry['from_net_type'],
                         'a freshly outbound-posted message must not look '
                         'like a real network win to itself')
        self.assertIn('from_net_addr', entry)

    def test_get_index_after_id_filters_out_already_seen(self):
        for i in range(3):
            self._run_bridge('save_msg', 'SYNCDATA', json.dumps(
                {'to': 'x', 'from': 'x', 'subject': str(i), 'body': 'x'}))
        all_entries = self._run_bridge('get_index', 'SYNCDATA', '0')['entries']
        self.assertEqual(len(all_entries), 3)
        first_id = all_entries[0]['number']
        newer = self._run_bridge('get_index', 'SYNCDATA', str(first_id))['entries']
        self.assertEqual(len(newer), 2)

    def test_get_header_reflects_direction_as_from_net_type(self):
        payload = json.dumps({'to': 'X', 'from': 'Y', 'subject': 'Z', 'body': 'x'})
        msg_id = self._run_bridge('save_msg', 'SYNCDATA', payload)['id']

        result = self._run_bridge('get_header', 'SYNCDATA', str(msg_id))
        self.assertTrue(result['ok'], result)
        header = result['header']
        self.assertEqual(header['from'], 'Y')
        self.assertEqual(header['to'], 'X')
        self.assertEqual(header['subject'], 'Z')
        self.assertEqual(header['number'], msg_id)
        self.assertFalse(header['from_net_type'],
                         'a freshly outbound-posted message must NOT look like '
                         'a real network win to itself until it round-trips')

    def test_get_header_for_inbound_message_reports_from_net_type_true(self):
        import anetbbs.config as cfg_mod
        cfg_mod.DevelopmentConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{self._db_path}'
        from anetbbs.web_app import create_app
        app = create_app('development')
        with app.app_context():
            from anetbbs.models import db, EchomailMessage
            row = EchomailMessage(
                area_id=self.area_id, network_id=self.network_id,
                from_name='OtherBBS', to_name='Synchronet Minesweeper',
                subject='Winner', body='{"ok":true}', direction='inbound',
                from_address='1:2/99')
            db.session.add(row)
            db.session.commit()
            msg_id = row.id

        result = self._run_bridge('get_header', 'SYNCDATA', str(msg_id))
        self.assertTrue(result['header']['from_net_type'])
        self.assertEqual(result['header']['from_net_addr'], '1:2/99')

    def test_get_body_round_trips(self):
        payload = json.dumps({'to': 'X', 'from': 'Y', 'subject': 'Z',
                              'body': '{"complex":"json","n":42}'})
        msg_id = self._run_bridge('save_msg', 'SYNCDATA', payload)['id']
        result = self._run_bridge('get_body', 'SYNCDATA', str(msg_id))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['body'], '{"complex":"json","n":42}')

    def test_get_header_unknown_message_reports_not_ok(self):
        result = self._run_bridge('get_header', 'SYNCDATA', '999999')
        self.assertFalse(result['ok'])


if __name__ == '__main__':
    unittest.main()
