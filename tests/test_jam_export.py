"""Regression tests for anetbbs/echomail/jam_export.py's JAM
read-only message-base export.

Two things get verified here, matching the module's own documented
verification approach (see its docstring):

1. jam_crc32() against CRC-32/JAMCRC's own published check value --
   the exact bug class (an off-by-one CRC variant) that has bitten
   this project's FTN work before (the BinkP FTS-0001 byte-swap bug).
2. A full export_echo_area() run, decoded back byte-for-byte with a
   hand-rolled reader built from the module's own verified struct
   constants (never a second, independently retyped format string --
   see _struct_format()'s own docstring for why that matters), across
   two messages so the reply-CRC threading between msg_id/reply_id
   subfields is exercised end to end, not just a single-message case.

Also covered: the EchoArea.jam_export_enabled migration (fresh-install
default and the _ensure_column() backfill for a pre-existing table
that predates the column), the export_jam_message_bases scheduled-
event handler, and the web admin form's create/edit wiring for the
new checkbox.
"""
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class JamCrc32Tests(unittest.TestCase):
    def test_matches_published_jamcrc_check_value(self):
        from anetbbs.echomail.jam_export import jam_crc32
        self.assertEqual(jam_crc32('123456789'), 0x340bc6d9)

    def test_empty_string(self):
        from anetbbs.echomail.jam_export import jam_crc32
        # No final XOR means the empty-input CRC is the raw init value.
        self.assertEqual(jam_crc32(''), 0xFFFFFFFF)

    def test_case_insensitive(self):
        from anetbbs.echomail.jam_export import jam_crc32
        self.assertEqual(jam_crc32('AbC'), jam_crc32('abc'))


class StructFormatSizesTests(unittest.TestCase):
    def test_base_header_is_1024_bytes(self):
        from anetbbs.echomail import jam_export
        self.assertEqual(struct.calcsize(jam_export._BASE_HEADER_FMT), 1024)

    def test_msg_header_is_76_bytes(self):
        from anetbbs.echomail import jam_export
        self.assertEqual(struct.calcsize(jam_export._MSG_HEADER_FMT), 76)

    def test_subfield_header_is_8_bytes(self):
        from anetbbs.echomail import jam_export
        self.assertEqual(struct.calcsize(jam_export._SUBFIELD_HDR_FMT), 8)

    def test_index_record_is_8_bytes(self):
        from anetbbs.echomail import jam_export
        self.assertEqual(struct.calcsize(jam_export._INDEX_FMT), 8)


class ExportEchoAreaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.jam_export_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _make_area_with_messages(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchomailMessage

        net = EchomailNetwork(name='JamExportNet', network_type='binkp',
                              our_address='9:9/1')
        db.session.add(net)
        db.session.flush()
        area = EchoArea(tag='JAM.TEST', name='JAM Export Test',
                        network_id=net.id, is_active=True)
        db.session.add(area)
        db.session.flush()

        msg1 = EchomailMessage(
            area_id=area.id, network_id=net.id,
            msg_id='9:9/1.1 abcdef01', reply_id=None,
            from_name='Alice', to_name='All', subject='First post',
            body='Hello, this is the first message.',
            direction='outbound')
        db.session.add(msg1)
        db.session.flush()

        msg2 = EchomailMessage(
            area_id=area.id, network_id=net.id,
            msg_id='9:9/2.1 fedcba98', reply_id=msg1.msg_id,
            from_name='Bob', to_name='Alice', subject='Re: First post',
            body='Replying to your message.',
            direction='inbound')
        db.session.add(msg2)
        db.session.commit()
        return area, msg1, msg2

    def _decode_export(self, jhr_path, jdt_path, jdx_path):
        """Hand-rolled JAM reader, built only from jam_export's own
        verified format constants -- never a separately retyped
        format string (see module docstring)."""
        from anetbbs.echomail.jam_export import (
            _BASE_HEADER_FMT, _BASE_HEADER_FIELDS, _MSG_HEADER_FMT,
            _MSG_HEADER_FIELDS, _SUBFIELD_HDR_FMT, _INDEX_FMT)

        with open(jhr_path, 'rb') as f:
            jhr_data = f.read()
        with open(jdt_path, 'rb') as f:
            jdt_data = f.read()
        with open(jdx_path, 'rb') as f:
            jdx_data = f.read()

        base_size = struct.calcsize(_BASE_HEADER_FMT)
        base_fields = struct.unpack(_BASE_HEADER_FMT, jhr_data[:base_size])
        base = dict(zip([f[0] for f in _BASE_HEADER_FIELDS], base_fields))

        msg_hdr_size = struct.calcsize(_MSG_HEADER_FMT)
        field_names = [f[0] for f in _MSG_HEADER_FIELDS]

        messages = []
        pos = base_size
        while pos < len(jhr_data):
            hdr = dict(zip(
                field_names,
                struct.unpack(_MSG_HEADER_FMT, jhr_data[pos:pos + msg_hdr_size])))
            pos += msg_hdr_size

            subfields = {}
            sf_end = pos + hdr['subfield_len']
            while pos < sf_end:
                loid, hiid, datlen = struct.unpack(
                    _SUBFIELD_HDR_FMT, jhr_data[pos:pos + 8])
                pos += 8
                subfields[loid] = jhr_data[pos:pos + datlen].decode('cp437')
                pos += datlen

            body = jdt_data[hdr['offset']:hdr['offset'] + hdr['txt_len']].decode('cp437')
            messages.append({'header': hdr, 'subfields': subfields, 'body': body})

        index_size = struct.calcsize(_INDEX_FMT)
        index_records = [
            struct.unpack(_INDEX_FMT, jdx_data[i:i + index_size])
            for i in range(0, len(jdx_data), index_size)]

        return base, messages, index_records

    def test_export_round_trips_byte_exact(self):
        from anetbbs.echomail.jam_export import (
            export_echo_area, jam_crc32, MSG_LOCAL, MSG_TYPEECHO,
            LOID_SENDERNAME, LOID_RECEIVERNAME, LOID_SUBJECT,
            LOID_MSGID, LOID_REPLYID)

        with self.app.app_context():
            area, msg1, msg2 = self._make_area_with_messages()

            with tempfile.TemporaryDirectory() as out_dir:
                summary = export_echo_area(area, out_dir)

                self.assertEqual(summary['message_count'], 2)

                jhr_path = os.path.join(out_dir, 'jam.test.jhr')
                jdt_path = os.path.join(out_dir, 'jam.test.jdt')
                jdx_path = os.path.join(out_dir, 'jam.test.jdx')
                self.assertTrue(os.path.exists(jhr_path))
                self.assertTrue(os.path.exists(jdt_path))
                self.assertTrue(os.path.exists(jdx_path))
                self.assertEqual(os.path.getsize(jhr_path), summary['jhr_bytes'])
                self.assertEqual(os.path.getsize(jdt_path), summary['jdt_bytes'])
                self.assertEqual(os.path.getsize(jdx_path), summary['jdx_bytes'])

                base, messages, index_records = self._decode_export(
                    jhr_path, jdt_path, jdx_path)

                self.assertEqual(base['signature'], b'JAM\x00')
                self.assertEqual(base['active_msgs'], 2)
                self.assertEqual(base['password_crc'], 0xFFFFFFFF)

                self.assertEqual(len(messages), 2)
                self.assertEqual(len(index_records), 2)

                m1, m2 = messages[0], messages[1]

                # Field-level content round-trip.
                self.assertEqual(m1['subfields'][LOID_SENDERNAME], 'Alice')
                self.assertEqual(m1['subfields'][LOID_RECEIVERNAME], 'All')
                self.assertEqual(m1['subfields'][LOID_SUBJECT], 'First post')
                self.assertEqual(m1['subfields'][LOID_MSGID], msg1.msg_id)
                self.assertNotIn(LOID_REPLYID, m1['subfields'])
                self.assertEqual(m1['body'], msg1.body)

                self.assertEqual(m2['subfields'][LOID_SENDERNAME], 'Bob')
                self.assertEqual(m2['subfields'][LOID_RECEIVERNAME], 'Alice')
                self.assertEqual(m2['subfields'][LOID_REPLYID], msg1.msg_id)
                self.assertEqual(m2['body'], msg2.body)

                # MSG_LOCAL only on the outbound message.
                self.assertTrue(m1['header']['attribute'] & MSG_LOCAL)
                self.assertFalse(m2['header']['attribute'] & MSG_LOCAL)
                self.assertTrue(m1['header']['attribute'] & MSG_TYPEECHO)
                self.assertTrue(m2['header']['attribute'] & MSG_TYPEECHO)

                # Cross-message reply-CRC threading: msg2's REPLYcrc
                # (computed from its REPLYID subfield/msg1's msg_id)
                # must equal msg1's own MSGIDcrc.
                self.assertEqual(m1['header']['msgid_crc'], jam_crc32(msg1.msg_id))
                self.assertEqual(m2['header']['reply_crc'], jam_crc32(msg1.msg_id))
                self.assertEqual(m1['header']['msgid_crc'], m2['header']['reply_crc'])

                # .jdx index: recipient-name CRC + correct header offset.
                self.assertEqual(index_records[0][0], jam_crc32(msg1.to_name))
                self.assertEqual(index_records[1][0], jam_crc32(msg2.to_name))

    def test_export_with_no_messages_writes_empty_but_valid_base(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        from anetbbs.echomail.jam_export import export_echo_area

        with self.app.app_context():
            net = EchomailNetwork(name='JamExportEmptyNet', network_type='binkp',
                                  our_address='9:9/3')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(tag='JAM.EMPTY', name='JAM Empty Area',
                            network_id=net.id, is_active=True)
            db.session.add(area)
            db.session.commit()

            with tempfile.TemporaryDirectory() as out_dir:
                summary = export_echo_area(area, out_dir)
                self.assertEqual(summary['message_count'], 0)

                base, messages, index_records = self._decode_export(
                    os.path.join(out_dir, 'jam.empty.jhr'),
                    os.path.join(out_dir, 'jam.empty.jdt'),
                    os.path.join(out_dir, 'jam.empty.jdx'))
                self.assertEqual(base['active_msgs'], 0)
                self.assertEqual(len(messages), 0)
                self.assertEqual(len(index_records), 0)

    def test_base_name_override(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        from anetbbs.echomail.jam_export import export_echo_area

        with self.app.app_context():
            net = EchomailNetwork(name='JamExportOverrideNet', network_type='binkp',
                                  our_address='9:9/4')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(tag='JAM.OVERRIDE', name='JAM Override Area',
                            network_id=net.id, is_active=True)
            db.session.add(area)
            db.session.commit()

            with tempfile.TemporaryDirectory() as out_dir:
                export_echo_area(area, out_dir, base_name='custom_name')
                self.assertTrue(os.path.exists(
                    os.path.join(out_dir, 'custom_name.jhr')))
                self.assertFalse(os.path.exists(
                    os.path.join(out_dir, 'jam.override.jhr')))


class JamExportColumnMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.jam_export_migration_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _has_column(self):
        from sqlalchemy import inspect as _inspect
        from anetbbs.models import db
        insp = _inspect(db.engine)
        return any(c['name'] == 'jam_export_enabled'
                  for c in insp.get_columns('echo_areas'))

    def test_fresh_install_has_the_column(self):
        with self.app.app_context():
            self.assertTrue(self._has_column())

    def test_fresh_install_defaults_to_false(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        with self.app.app_context():
            net = EchomailNetwork(name='JamMigrationNet', network_type='binkp',
                                  our_address='9:9/5')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(tag='JAM.MIGRATION', name='JAM Migration Area',
                            network_id=net.id, is_active=True)
            db.session.add(area)
            db.session.commit()
            self.assertFalse(area.jam_export_enabled)

    def test_backfills_column_on_a_table_that_predates_it(self):
        """Simulates an upgrading install: drop the column (as if the
        table were created before jam_export_enabled existed), leave a
        pre-existing row behind exactly as a real upgrading sysop's
        database would have, then confirm _lightweight_migrate() both
        adds the column back AND backfills that existing row to 0/False
        -- not just NULL, which is all the generic auto-sweep elsewhere
        in _lightweight_migrate() would give it (see the explicit
        NOT NULL DEFAULT 0 DDL's own comment in web_app.py)."""
        from anetbbs.models import db, EchomailNetwork
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            self.assertTrue(self._has_column())

            net = EchomailNetwork(name='JamPredatesColumnNet',
                                  network_type='binkp', our_address='9:9/9')
            db.session.add(net)
            db.session.commit()
            net_id = net.id

            db.session.execute(db.text(
                'ALTER TABLE echo_areas DROP COLUMN jam_export_enabled'))
            db.session.execute(db.text(
                "INSERT INTO echo_areas (network_id, tag, name, is_active) "
                "VALUES (:nid, 'JAM.PREDATES', 'Predates Column', 1)"),
                {'nid': net_id})
            db.session.commit()
            self.assertFalse(self._has_column())

            _lightweight_migrate(self.app)

            self.assertTrue(self._has_column())
            value = db.session.execute(db.text(
                "SELECT jam_export_enabled FROM echo_areas "
                "WHERE tag = 'JAM.PREDATES'")).scalar()
            self.assertEqual(value, 0,
                "a pre-existing row's new column must backfill to 0, "
                "not NULL")

    def test_migration_is_idempotent(self):
        from anetbbs.web_app import _lightweight_migrate
        with self.app.app_context():
            _lightweight_migrate(self.app)
            _lightweight_migrate(self.app)
            self.assertTrue(self._has_column())


class ExportJamMessageBasesHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.jam_export_handler_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'
        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        """Each test starts from an empty table set -- an EchoArea left
        over from a sibling test (e.g. one with jam_export_enabled=True)
        would otherwise silently leak into a later "no areas enabled"
        assertion, since all tests in this class share one on-disk DB."""
        from anetbbs.models import db, EchomailMessage, EchoArea, EchomailNetwork
        with self.app.app_context():
            EchomailMessage.query.delete()
            EchoArea.query.delete()
            EchomailNetwork.query.delete()
            db.session.commit()

    def test_no_areas_enabled_is_a_clean_noop(self):
        from anetbbs.events.handlers import export_jam_message_bases
        with self.app.app_context():
            ok, msg = export_jam_message_bases(self.app, {})
            self.assertTrue(ok)
            self.assertIn('nothing to do', msg)

    def test_enabled_area_gets_exported_to_output_dir(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea, EchomailMessage
        from anetbbs.events.handlers import export_jam_message_bases

        with self.app.app_context():
            net = EchomailNetwork(name='JamHandlerNet', network_type='binkp',
                                  our_address='9:9/6')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(tag='JAM.HANDLER', name='JAM Handler Area',
                            network_id=net.id, is_active=True,
                            jam_export_enabled=True)
            db.session.add(area)
            db.session.flush()
            db.session.add(EchomailMessage(
                area_id=area.id, network_id=net.id, from_name='Carl',
                to_name='All', subject='Handler test', body='Body text.',
                direction='outbound'))
            db.session.commit()

            with tempfile.TemporaryDirectory() as out_dir:
                ok, msg = export_jam_message_bases(
                    self.app, {'output_dir': out_dir})
                self.assertTrue(ok)
                self.assertIn('JAM.HANDLER', msg)
                self.assertIn('1 msg(s)', msg)
                self.assertTrue(os.path.exists(
                    os.path.join(out_dir, 'jam.handler.jhr')))

    def test_disabled_areas_are_skipped(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        from anetbbs.events.handlers import export_jam_message_bases

        with self.app.app_context():
            net = EchomailNetwork(name='JamHandlerDisabledNet',
                                  network_type='binkp', our_address='9:9/7')
            db.session.add(net)
            db.session.flush()
            db.session.add(EchoArea(tag='JAM.DISABLED', name='JAM Disabled Area',
                                    network_id=net.id, is_active=True,
                                    jam_export_enabled=False))
            db.session.commit()

            ok, msg = export_jam_message_bases(self.app, {})
            self.assertTrue(ok)
            self.assertIn('nothing to do', msg)


class EchoAreaFormJamExportFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.jam_export_form_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='jamexportadmintest', email='jeat@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            db.session.add(admin)
            db.session.commit()
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as_admin(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def _make_network(self):
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            net = EchomailNetwork(name='JamFormNet', network_type='binkp',
                                  our_address='9:9/8')
            db.session.add(net)
            db.session.commit()
            return net.id

    def test_new_area_checkbox_checked_sets_column_true(self):
        from anetbbs.models import EchoArea
        network_id = self._make_network()
        client = self._client_as_admin()
        client.post('/admin/echomail/areas/new', data={
            'network_id': str(network_id), 'tag': 'JAM.FORM.NEW.ON',
            'name': 'Form New On', 'order': '0', 'min_access_level': '10',
            'jam_export_enabled': 'y',
        }, follow_redirects=True)
        with self.app.app_context():
            area = EchoArea.query.filter_by(tag='JAM.FORM.NEW.ON').first()
            self.assertIsNotNone(area)
            self.assertTrue(area.jam_export_enabled)

    def test_new_area_checkbox_unchecked_leaves_column_false(self):
        from anetbbs.models import EchoArea
        network_id = self._make_network()
        client = self._client_as_admin()
        client.post('/admin/echomail/areas/new', data={
            'network_id': str(network_id), 'tag': 'JAM.FORM.NEW.OFF',
            'name': 'Form New Off', 'order': '0', 'min_access_level': '10',
        }, follow_redirects=True)
        with self.app.app_context():
            area = EchoArea.query.filter_by(tag='JAM.FORM.NEW.OFF').first()
            self.assertIsNotNone(area)
            self.assertFalse(area.jam_export_enabled)

    def test_edit_area_can_toggle_it_on(self):
        from anetbbs.models import db, EchoArea
        network_id = self._make_network()
        with self.app.app_context():
            area = EchoArea(tag='JAM.FORM.EDIT', name='Form Edit',
                            network_id=network_id, is_active=True,
                            jam_export_enabled=False)
            db.session.add(area)
            db.session.commit()
            area_id = area.id

        client = self._client_as_admin()
        client.post(f'/admin/echomail/areas/{area_id}/edit', data={
            'network_id': str(network_id), 'tag': 'JAM.FORM.EDIT',
            'name': 'Form Edit', 'order': '0', 'min_access_level': '10',
            'jam_export_enabled': 'y',
        }, follow_redirects=True)

        with self.app.app_context():
            refreshed = EchoArea.query.get(area_id)
            self.assertTrue(refreshed.jam_export_enabled)

    def test_area_form_page_renders_the_checkbox(self):
        """area_form.html must actually render the field -- the POST
        tests above never render this template (a successful POST
        redirects), so this is the only coverage of the template
        wiring itself."""
        self._make_network()
        client = self._client_as_admin()
        resp = client.get('/admin/echomail/areas/new')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'jam_export_enabled', resp.data)
        self.assertIn(b'Export to JAM Message Base', resp.data)


if __name__ == '__main__':
    unittest.main()
