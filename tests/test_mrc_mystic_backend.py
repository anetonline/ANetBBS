"""Mystic MRC bridge backend (mrc_backend: "mystic" in mrc/bridge/config.json).

Jerry supplied a real Mystic BBS MRC client release (pn-mrc137-alpha.zip,
StackFault/Phenom Productions) and asked for an option to use it "instead
of the ANetBBS client and bridge" -- his explicit choice (over porting its
logic into our own socket handling) was to run the actual vendored
mrc_client.py as a real subprocess against a synthetic Mystic directory
tree, translating its file-based IPC to/from BridgeApp's existing session
model. See mrc/mystic_client/fake_bbs.py and mrc/bridge/mystic_connection.py
for the implementation; this covers the translation layer without
depending on network access.
"""
import asyncio
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.mystic_client.fake_bbs import (
    ensure_fake_bbs_tree,
    bbs_config_to_mystic_fields,
    room_dir,
    chat_dat_path,
    inuse_marker_path,
    mrc_outbound_dir,
)
from mrc.bridge.mystic_connection import MysticMultiplexerConnection
from mrc.bridge.mrc_protocol import MRCProtocol


def _run(coro):
    return asyncio.run(coro)


class FakeBbsTreeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bbspath = Path(self._tmp.name) / 'mystic_mrc'
        self.cfg = {
            'bridge_bbs': 'ANetBBS Test',
            'mrc_host': 'mrc.example.net',
            'mrc_port': 5001,
            'use_ssl': True,
            'bbs_website': 'https://example.net',
            'bbs_telnet': 'example.net:23',
            'bbs_ssh': 'example.net:22',
            'bbs_sysop': 'StingRay',
            'bbs_description': 'Test BBS',
        }

    def test_creates_expected_directory_shape(self):
        script = ensure_fake_bbs_tree(self.bbspath, self.cfg)
        self.assertTrue(script.exists())
        self.assertEqual(script.name, 'mrc_client.py')
        self.assertTrue((self.bbspath / 'mrc_config.py').exists())
        self.assertTrue((self.bbspath / 'data' / 'users.dat').exists())
        self.assertTrue((self.bbspath / 'data' / 'mrc').is_dir())
        self.assertTrue((self.bbspath / 'temp').is_dir())

    def test_vendored_script_copy_matches_source_byte_for_byte(self):
        script = ensure_fake_bbs_tree(self.bbspath, self.cfg)
        vendor = Path(__file__).resolve().parents[1] / 'mrc' / 'mystic_client' / 'vendor' / 'mrc_client.py'
        self.assertEqual(script.read_bytes(), vendor.read_bytes())

    def test_generated_config_is_valid_python_with_expected_values(self):
        ensure_fake_bbs_tree(self.bbspath, self.cfg)
        ns = {}
        exec((self.bbspath / 'mrc_config.py').read_text(), ns)
        self.assertEqual(ns['bbsname'], 'ANetBBS Test')
        self.assertEqual(ns['host'], 'mrc.example.net')
        self.assertEqual(ns['sslport'], 5001)
        self.assertEqual(ns['usessl'], 1)
        self.assertEqual(ns['info_web'], 'https://example.net')
        self.assertEqual(ns['info_sysop'], 'StingRay')

    def test_use_ssl_false_maps_port_to_plainport(self):
        self.cfg['use_ssl'] = False
        self.cfg['mrc_port'] = 5000
        ensure_fake_bbs_tree(self.bbspath, self.cfg)
        ns = {}
        exec((self.bbspath / 'mrc_config.py').read_text(), ns)
        self.assertEqual(ns['usessl'], 0)
        self.assertEqual(ns['plainport'], 5000)

    def test_short_bbsname_raises_before_ever_spawning_the_subprocess(self):
        """mrc_client.py's own check_startup() calls sys.exit(1) for a
        bbsname under 5 chars -- fail loudly and early here instead of
        letting that surface as a silent subprocess crash loop."""
        self.cfg['bridge_bbs'] = 'Hi'
        with self.assertRaises(ValueError):
            ensure_fake_bbs_tree(self.bbspath, self.cfg)

    def test_bbsname_length_check_strips_pipe_color_codes_first(self):
        """A styled name like the config.example.json default
        ("|07My BBS |15MRC Bridge") shouldn't be penalized for its color
        codes when checked against the 5-char floor."""
        self.cfg['bridge_bbs'] = '|07Hi|15'  # 2 real chars after stripping
        with self.assertRaises(ValueError):
            ensure_fake_bbs_tree(self.bbspath, self.cfg)
        self.cfg['bridge_bbs'] = '|07Howdy|15'  # 5 real chars
        ensure_fake_bbs_tree(self.bbspath, self.cfg)  # must not raise


class MysticConnectionFileIpcTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bbspath = Path(self._tmp.name) / 'mystic_mrc'
        self.cfg = {
            'mystic_bbspath': str(self.bbspath),
            'mystic_poll_interval_seconds': 0.03,
            'bridge_bbs': 'ANetBBS Test',
            'mrc_host': 'mrc.example.net',
            'mrc_port': 5000,
        }
        ensure_fake_bbs_tree(self.bbspath, self.cfg)
        self.conn = MysticMultiplexerConnection(self.cfg)

    def test_send_packet_writes_a_file_under_data_mrc_with_exact_content(self):
        packet = MRCProtocol.create_message('StingRay', 'ANetBBS', 'lobby', 'NOTME', '', 'hello')
        _run(self.conn.send_packet(packet))
        files = list(mrc_outbound_dir(self.bbspath).glob('*.mrc'))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_text(), packet)
        # No leftover .tmp files from the write-then-rename
        self.assertEqual(list(mrc_outbound_dir(self.bbspath).glob('*.tmp')), [])

    def test_send_packet_ignores_empty_packet(self):
        _run(self.conn.send_packet(''))
        self.assertEqual(list(mrc_outbound_dir(self.bbspath).glob('*.mrc')), [])

    def test_sync_active_rooms_creates_and_removes_markers(self):
        _run(self.conn.sync_active_rooms({'lobby', 'sysop'}))
        self.assertTrue(inuse_marker_path(self.bbspath, 'lobby').exists())
        self.assertTrue(inuse_marker_path(self.bbspath, 'sysop').exists())
        self.assertTrue(chat_dat_path(self.bbspath, 'lobby').exists())

        _run(self.conn.sync_active_rooms({'lobby'}))
        self.assertTrue(inuse_marker_path(self.bbspath, 'lobby').exists())
        self.assertFalse(inuse_marker_path(self.bbspath, 'sysop').exists())
        # chat<room>.dat is a persistent registration, not removed just
        # because the room went idle -- only the inuse marker toggles.
        self.assertTrue(chat_dat_path(self.bbspath, 'sysop').exists())

    def test_sync_active_rooms_sanitizes_room_names_for_filesystem_safety(self):
        _run(self.conn.sync_active_rooms({'../../etc/evil'}))
        # _safe_room strips everything outside [A-Za-z0-9_-]
        self.assertFalse((self.bbspath / 'temp' / '../../etc/evil').resolve().exists())
        matches = list((self.bbspath / 'temp').glob('*'))
        self.assertTrue(all(re.fullmatch(r'[A-Za-z0-9_-]+', d.name) for d in matches))

    def test_inbound_packet_is_parsed_delivered_and_file_deleted(self):
        _run(self.conn.sync_active_rooms({'lobby'}))
        (room_dir(self.bbspath, 'lobby') / '00000001.mrc').write_text(
            'OtherUser~OtherBBS~lobby~~~lobby~hello there~')

        received = []

        async def cb(parsed):
            received.append(parsed)

        self.conn.add_message_callback(cb)

        async def drive():
            task = asyncio.create_task(self.conn._inbound_loop())
            await asyncio.sleep(0.2)
            self.conn._closing = True
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        _run(drive())
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]['from_user'], 'OtherUser')
        self.assertEqual(received[0]['message'], 'hello there')
        self.assertEqual(
            list(room_dir(self.bbspath, 'lobby').glob('*.mrc')), [])

    def test_inbound_ignores_rooms_with_no_active_marker(self):
        """A packet dropped for a room we never sync_active_rooms()'d into
        must not be picked up -- confirms polling is scoped to _active_rooms,
        not every directory under temp/."""
        stray_dir = self.bbspath / 'temp' / 'nobodyhome'
        stray_dir.mkdir(parents=True)
        (stray_dir / '00000001.mrc').write_text('X~Y~nobodyhome~~~nobodyhome~ping~')

        received = []

        async def cb(parsed):
            received.append(parsed)

        self.conn.add_message_callback(cb)

        async def drive():
            task = asyncio.create_task(self.conn._inbound_loop())
            await asyncio.sleep(0.2)
            self.conn._closing = True
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        _run(drive())
        self.assertEqual(received, [])
        # File is left alone since nobody's watching that room
        self.assertTrue((stray_dir / '00000001.mrc').exists())


class BridgeAppBackendSelectionTests(unittest.TestCase):
    """Full BridgeApp wiring -- requires aiohttp, so this class is skipped
    if it's not installed in the environment running the suite (matches
    the project's own convention of gating on real optional dependencies
    rather than failing the whole file's collection)."""

    @classmethod
    def setUpClass(cls):
        try:
            import aiohttp  # noqa: F401
        except ImportError:
            raise unittest.SkipTest('aiohttp not installed')

    def _make_config(self, tmp_dir, **overrides):
        import json
        cfg = json.loads((Path(__file__).resolve().parents[1] /
                           'mrc' / 'bridge' / 'config.example.json').read_text())
        cfg['data_dir'] = tmp_dir
        cfg.update(overrides)
        path = Path(tmp_dir) / 'config.json'
        path.write_text(json.dumps(cfg))
        return str(path)

    def test_mrc_backend_mystic_selects_mystic_connection(self):
        from mrc.bridge.main import BridgeApp
        with tempfile.TemporaryDirectory() as td:
            app = BridgeApp(self._make_config(td, mrc_backend='mystic'))
            self.assertIsInstance(app.mrc, MysticMultiplexerConnection)

    def test_mrc_backend_native_selects_mrc_connection(self):
        from mrc.bridge.main import BridgeApp, MRCConnection
        with tempfile.TemporaryDirectory() as td:
            app = BridgeApp(self._make_config(td, mrc_backend='native'))
            self.assertIsInstance(app.mrc, MRCConnection)

    def test_missing_mrc_backend_key_defaults_to_native(self):
        from mrc.bridge.main import BridgeApp, MRCConnection
        with tempfile.TemporaryDirectory() as td:
            path = self._make_config(td)
            import json
            cfg = json.loads(Path(path).read_text())
            del cfg['mrc_backend']
            Path(path).write_text(json.dumps(cfg))
            app = BridgeApp(path)
            self.assertIsInstance(app.mrc, MRCConnection)

    def test_sync_mystic_rooms_is_a_harmless_no_op_on_native_backend(self):
        """_sync_mystic_rooms() is called from several session mutation
        sites unconditionally -- confirm it doesn't blow up when the
        active backend has no such method."""
        from mrc.bridge.main import BridgeApp
        with tempfile.TemporaryDirectory() as td:
            app = BridgeApp(self._make_config(td, mrc_backend='native'))
            _run(app._sync_mystic_rooms())  # must not raise


class _FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)


class MidSessionRoomChangeSyncTests(unittest.TestCase):
    """Real bug found live on the Pi: a caller already connected who
    does /join <room> (a SEPARATE code path from the initial auto-join --
    _handle_server_cmd's own NEWROOM handling, not
    _complete_join_after_identify) never told the mystic backend which
    room to start listening on. Outbound packets went out fine; the
    hub's own join confirmation/MOTD/userlist came back and was
    silently dropped because nothing was polling that room's temp/
    directory yet. Reported live as "/join ... does not appear to be
    joining" even though the connection itself was healthy (confirmed
    separately: the very first auto-join on connect worked correctly,
    since _complete_join_after_identify was already correctly hooked)."""

    def setUp(self):
        try:
            import aiohttp  # noqa: F401
        except ImportError:
            raise unittest.SkipTest('aiohttp not installed')

    def _make_bridge(self, tmp_dir, mrc):
        from mrc.bridge.main import BridgeApp
        from mrc.bridge.db import BridgeDB
        app = object.__new__(BridgeApp)
        app.config = {"bridge_bbs": "TestBBS"}
        app.db = BridgeDB(tmp_dir)
        app.websockets = {}
        app.mrc = mrc
        app.join_packet_delay_ms = 0
        app.announce_join_part = False
        app.request_banners_on_join = False
        app.request_motd_on_join = False
        app.join_message_tpl = ""
        app.exit_message_tpl = ""
        return app

    def test_join_command_while_connected_activates_the_new_room_for_mystic_backend(self):
        with tempfile.TemporaryDirectory() as td:
            bbspath = Path(td) / 'mystic_mrc'
            cfg = {'mystic_bbspath': str(bbspath), 'bridge_bbs': 'TestBBS',
                   'mrc_host': 'x', 'mrc_port': 5000}
            ensure_fake_bbs_tree(bbspath, cfg)
            mrc = MysticMultiplexerConnection(cfg)

            app = self._make_bridge(td, mrc)
            ws_id = 1
            app.websockets[ws_id] = _FakeWs()
            app.db.save_session(str(ws_id), {
                "handle": "StingRay", "nick": "StingRay",
                "room": "lobby", "in_room": True,
            })
            # Already active in #lobby (as if from the initial auto-join,
            # which already correctly hooks _sync_mystic_rooms via
            # _complete_join_after_identify).
            _run(mrc.sync_active_rooms({'lobby'}))
            self.assertIn('lobby', mrc._active_rooms)

            _run(app._handle_server_cmd(ws_id, {"command": "JOIN sysop"}))

            self.assertIn('sysop', mrc._active_rooms,
                           "mystic backend never learned about the new room "
                           "after a mid-session /join -- its file-IPC watcher "
                           "would silently drop the hub's own reply")


if __name__ == '__main__':
    unittest.main()
