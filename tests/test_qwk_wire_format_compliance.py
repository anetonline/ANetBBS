"""Regression tests for real QWK wire-format bugs found while comparing
ANetBBS's own QWK-packet writers against Synchronet's reference
implementation (msgtoqwk.cpp / qwktomsg.cpp / pack_qwk.cpp).

This codebase has FOUR independent, hand-rolled implementations of the
same 128-byte QWK message header + CONTROL.DAT format
(anetbbs/echomail/qwk.py's _build_rep_packet, anetbbs/echomail/
qwk_hub_ftp.py's _build_messages_dat, anetbbs/web/qwk_hub.py, and
anetbbs/web/qwk_user.py's _build_qwk_blob) -- exactly the kind of setup
where one gets fixed and the others quietly drift. Two real bugs were
found:

1. qwk_hub_ftp.py wrote 0xE1 (the byte-122 "active" marker constant)
   into byte 0, which is actually the message status flag and must be
   a printable ASCII character (' '/'-'/'+'/'*'). Confirmed against
   Synchronet's own reader (qwktomsg.cpp), which checks byte 0 against
   exactly those characters -- 0xE1 was never a valid status value.
   ANetBBS's own reader is lenient here (anything but '*'/'+' reads as
   public), which is why this went unnoticed talking to itself.

2. qwk_user.py wrote a fabricated binary "messages left" uint16 into
   header bytes 125-126. Those two bytes are simply unused, literal
   space characters in the real format -- confirmed from Synchronet's
   own format string, which spells out two explicit ' ' fields there.

Also fixed (not a header bug, but a real misalignment): qwk_hub.py and
qwk_user.py's CONTROL.DAT writers were missing a blank placeholder
line and the mandatory "0"/"E-mail" pair for the reserved conference-0
(netmail) slot, which shifted every subsequent line -- including the
conference count and every real conference entry -- out of position.
ANetBBS's own reader (qwk.py's _parse_control_dat) already expected
conference pairs starting at line index 10, matching the CORRECT
Synchronet-standard layout -- so this wasn't just a third-party
compliance gap, it was misaligned against ANetBBS's own reader too.
"""
import os
import struct
import sys
import unittest
from pathlib import Path
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _QwkDbTestBase(unittest.TestCase):
    """Shared app+DB fixture for tests that need real EchoArea/
    EchomailMessage/QWKNode rows."""

    DB_SUFFIX = 'base'

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                          f'.qwk_wire_format_{cls.DB_SUFFIX}_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)


class QwkHubFtpMessageHeaderTests(_QwkDbTestBase):
    """anetbbs/echomail/qwk_hub_ftp.py's _build_messages_dat()."""

    DB_SUFFIX = 'hub_ftp'

    def test_header_byte_zero_is_status_char_not_marker(self):
        from anetbbs.echomail.qwk_hub_ftp import _build_messages_dat

        class _FakeMsg:
            from_name = 'Alice'
            to_name = 'Bob'
            subject = 'Test'
            body = 'Hello world'
            created_at = None

        data = _build_messages_dat({1: [_FakeMsg()]}, 'TESTNODE')
        # First 128 bytes = welcome block; message header starts at 128.
        header = data[128:256]

        status_byte = header[0:1]
        self.assertIn(status_byte, (b' ', b'-', b'+', b'*'),
                      f'byte 0 must be a printable status char, got {status_byte!r}')
        self.assertNotEqual(header[0], 0xe1,
                            'byte 0 must not be the 0xE1 marker constant')
        # The marker constant belongs at byte 122, and only there.
        self.assertEqual(header[122], 0xe1)


class QwkUserBlobHeaderTests(_QwkDbTestBase):
    """anetbbs/web/qwk_user.py's _build_qwk_blob()."""

    DB_SUFFIX = 'user_blob'

    def test_header_bytes_125_126_are_spaces_not_binary_field(self):
        from anetbbs.models import db, User, EchoArea, EchomailNetwork, EchomailMessage

        with self.app.app_context():
            db.create_all()
            net = EchomailNetwork(name='Test', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='1', name='General', is_active=True)
            db.session.add(area)
            db.session.commit()
            user = User(username='wiretest', email='wiretest@example.com',
                       password_hash='x')
            db.session.add(user)
            db.session.commit()
            msg = EchomailMessage(area_id=area.id, network_id=net.id,
                                  from_name='Alice', to_name='Bob',
                                  subject='Test', body='Hello')
            db.session.add(msg)
            db.session.commit()

            from anetbbs.web.qwk_user import _build_qwk_blob
            blob = _build_qwk_blob(user)

            with zipfile.ZipFile(blob) as zf:
                msgs_dat = zf.read('MESSAGES.DAT')

            header = msgs_dat[128:256]  # first message header after welcome block
            self.assertEqual(header[125:127], b'  ',
                             f'bytes 125-126 must be literal spaces, got {header[125:127]!r}')
            self.assertEqual(header[122], 0xe1)
            db.drop_all()

    def test_control_dat_conference_zero_is_email_at_correct_line(self):
        from anetbbs.models import db, User, EchoArea, EchomailNetwork
        from anetbbs.echomail.qwk import _parse_control_dat

        with self.app.app_context():
            db.create_all()
            net = EchomailNetwork(name='Test', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='1', name='General', is_active=True)
            db.session.add(area)
            db.session.commit()
            user = User(username='wiretest2', email='wiretest2@example.com',
                       password_hash='x')
            db.session.add(user)
            db.session.commit()

            # create_app() auto-seeds a bunch of default active areas --
            # count what's actually there rather than assuming an empty
            # DB, since only the *structure* is under test here.
            expected_count = EchoArea.query.filter_by(is_active=True).count()

            from anetbbs.web.qwk_user import _build_qwk_blob
            blob = _build_qwk_blob(user)

            with zipfile.ZipFile(blob) as zf:
                control = zf.read('CONTROL.DAT').decode('cp437')

            lines = control.split('\r\n')
            # Line index 10 is the conference count; the mandatory
            # conference-0/E-mail pair follows at 11-12, matching
            # anetbbs/echomail/qwk.py's _parse_control_dat() and
            # Synchronet's own CONTROL.DAT layout.
            self.assertEqual(lines[10], str(expected_count),
                             f'conference count must be at line index 10, got: {lines[:13]!r}')
            self.assertEqual(lines[11], '0',
                             f'conference-0 number must be at line index 11, got: {lines[:13]!r}')
            self.assertEqual(lines[12], 'E-mail',
                             f'conference-0 name must be at line index 12, got: {lines[:13]!r}')

            parsed = _parse_control_dat(control)
            # _build_qwk_blob() uses area.tag (not .name) as the
            # CONTROL.DAT conference label -- pre-existing behaviour,
            # unrelated to this fix. Confirm our own area round-trips
            # to *some* conference number, and that every seeded area
            # plus the mandatory conference-0 slot is accounted for.
            self.assertIn(area.tag, parsed['conferences'].values(),
                          f'our test area not found in parsed conferences: {parsed}')
            self.assertEqual(len(parsed['conferences']), expected_count + 1,
                             'every real area plus the reserved conference-0 '
                             'slot must round-trip')
            db.drop_all()


class QwkHubControlDatTests(_QwkDbTestBase):
    """anetbbs/web/qwk_hub.py's _build_qwk_hub_packet()."""

    DB_SUFFIX = 'hub'

    def test_control_dat_conference_zero_is_email_at_correct_line(self):
        from anetbbs.models import db, EchoArea, EchomailNetwork, QWKNode, QWKNodeLastSent

        with self.app.app_context():
            db.create_all()
            net = EchomailNetwork(name='Test', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='1', name='General', is_active=True)
            db.session.add(area)
            db.session.commit()
            node = QWKNode(packet_id='TESTND', name='Test Node', password='x')
            db.session.add(node)
            db.session.commit()
            sub = QWKNodeLastSent(node_id=node.id, echo_area_id=area.id,
                                  conf_number=1, last_message_id=None)
            db.session.add(sub)
            db.session.commit()

            from anetbbs.web.qwk_hub import _build_qwk_hub_packet
            packet_bytes, _new_hwm, _total_msgs = _build_qwk_hub_packet(node)

            with zipfile.ZipFile(__import__('io').BytesIO(packet_bytes)) as zf:
                control = zf.read('CONTROL.DAT').decode('cp437', errors='replace')

            lines = control.split('\r\n')
            from anetbbs.echomail.qwk import _parse_control_dat
            parsed = _parse_control_dat(control)
            # qwk_hub.py stores the area's tag (not its display name) as
            # the CONTROL.DAT conference name -- pre-existing behaviour,
            # unrelated to this fix, so just confirm the round-trip.
            self.assertEqual(parsed['conferences'].get(1), area.tag,
                             f'real conference 1 not parsed correctly: {parsed}')
            self.assertEqual(lines[10], '1',
                             f'conference count must be at line index 10, got: {lines[:13]!r}')
            self.assertEqual(lines[11], '0',
                             f'conference-0 number must be at line index 11, got: {lines[:13]!r}')
            self.assertEqual(lines[12], 'E-mail',
                             f'conference-0 name must be at line index 12, got: {lines[:13]!r}')
            db.drop_all()


class QwkHubFtpControlDatTests(_QwkDbTestBase):
    """anetbbs/echomail/qwk_hub_ftp.py's generate_node_packet() /
    _build_control_dat() -- a real audit found this was the THIRD,
    independent CONTROL.DAT writer with the same off-by-one bug already
    fixed twice (qwk_hub.py, qwk_user.py -- see QwkHubControlDatTests /
    QwkUserBlobHeaderTests above), plus a second bug where the FTP
    hub's high-water mark was never advanced at all, so every FTP
    login re-sent the identical batch of messages forever."""

    DB_SUFFIX = 'hub_ftp_control'

    def _seed(self):
        from anetbbs.models import db, EchoArea, EchomailNetwork, QWKNode, QWKNodeLastSent
        net = EchomailNetwork(name='Test', network_type='qwk')
        db.session.add(net)
        db.session.commit()
        area = EchoArea(network_id=net.id, tag='1', name='General', is_active=True)
        db.session.add(area)
        db.session.commit()
        node = QWKNode(packet_id='FTPNODE', name='FTP Test Node', password='x')
        db.session.add(node)
        db.session.commit()
        from anetbbs.models import EchomailMessage
        msg = EchomailMessage(area_id=area.id, network_id=net.id,
                              from_name='Alice', to_name='All',
                              subject='Test', body='Hello from FTP hub')
        db.session.add(msg)
        db.session.commit()
        sub = QWKNodeLastSent(node_id=node.id, echo_area_id=area.id,
                              conf_number=1, last_message_id=None)
        db.session.add(sub)
        db.session.commit()
        return node, area, msg, sub

    def test_control_dat_conference_zero_is_email_at_correct_line(self):
        from anetbbs.models import db
        from anetbbs.echomail.qwk import _parse_control_dat

        with self.app.app_context():
            db.create_all()
            node, area, msg, sub = self._seed()

            from anetbbs.echomail.qwk_hub_ftp import generate_node_packet
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                out_path = generate_node_packet(node, tmpdir)
                self.assertTrue(out_path and os.path.exists(out_path))

                with zipfile.ZipFile(out_path) as zf:
                    control = zf.read('CONTROL.DAT').decode('cp437', errors='replace')

            lines = control.split('\r\n')
            parsed = _parse_control_dat(control)
            self.assertEqual(parsed['conferences'].get(1), area.name,
                             f'real conference 1 not parsed correctly: {parsed}')
            self.assertEqual(lines[10], '1',
                             f'conference count must be at line index 10, got: {lines[:13]!r}')
            self.assertEqual(lines[11], '0',
                             f'conference-0 number must be at line index 11, got: {lines[:13]!r}')
            self.assertEqual(lines[12], 'E-mail',
                             f'conference-0 name must be at line index 12, got: {lines[:13]!r}')
            db.drop_all()

    def test_high_water_mark_advances_after_packet_build(self):
        from anetbbs.models import db, QWKNodeLastSent

        with self.app.app_context():
            db.create_all()
            node, area, msg, sub = self._seed()
            self.assertIsNone(sub.last_message_id)

            from anetbbs.echomail.qwk_hub_ftp import generate_node_packet
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                generate_node_packet(node, tmpdir)

            refreshed = QWKNodeLastSent.query.filter_by(
                node_id=node.id, echo_area_id=area.id).first()
            self.assertEqual(refreshed.last_message_id, msg.id,
                             'HWM must advance to the last message sent, or a '
                             'node polling via FTP re-downloads the same batch '
                             'forever')
            db.drop_all()

    def test_second_call_with_no_new_messages_sends_empty_conference(self):
        """After the HWM catches up, a re-poll with nothing new must not
        re-include the already-delivered message."""
        from anetbbs.models import db, QWKNodeLastSent

        with self.app.app_context():
            db.create_all()
            node, area, msg, sub = self._seed()

            from anetbbs.echomail.qwk_hub_ftp import generate_node_packet
            from anetbbs.echomail.qwk import _parse_control_dat
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                generate_node_packet(node, tmpdir)
                out_path2 = generate_node_packet(node, tmpdir)
                with zipfile.ZipFile(out_path2) as zf:
                    control = zf.read('CONTROL.DAT').decode('cp437', errors='replace')
                    messages_dat = zf.read('MESSAGES.DAT')

            parsed = _parse_control_dat(control)
            self.assertEqual(parsed['conferences'].get(1), area.name)
            # Only the welcome block + EOF marker -- no message header/body.
            self.assertEqual(len(messages_dat), 128 + 1)
            db.drop_all()


class QwkReplyThreadingTests(_QwkDbTestBase):
    """Regression tests for a real gap found in a full echomail-
    subsystem audit: QWK reply-chain threading never round-tripped
    anywhere in the subsystem. QWK's own binary "reference message
    number" header field only has meaning WITHIN one packet's own
    numbering (can't durably reference a message in a DIFFERENT
    packet), so the fix mirrors qwk.py's already-working @MSGID:
    plain-text-kludge convention for @REPLY: instead -- injected into
    the outbound body on both hub writers, and already correctly
    extracted on the way back in by qwk.py's _clean_body() (which the
    two _parse_messages_dat()-based importers already call; only
    qwk_user.py's upload() did its own raw parse and skipped it,
    fixed separately)."""

    DB_SUFFIX = 'reply_threading'

    def test_qwk_hub_ftp_injects_reply_kludge_into_body(self):
        from anetbbs.echomail.qwk_hub_ftp import _build_messages_dat

        class _FakeMsg:
            from_name = 'Alice'
            to_name = 'Bob'
            subject = 'Re: Test'
            body = 'Thanks for the info!'
            reply_id = '1:114/30 abc123'
            created_at = None

        data = _build_messages_dat({1: [_FakeMsg()]}, 'TESTNODE')
        body_bytes = data[128 + 128:]  # welcome block + this message's header
        body_text = body_bytes.decode('cp437', errors='replace')
        self.assertIn('@REPLY: 1:114/30 abc123', body_text)

    def test_qwk_hub_ftp_no_reply_id_means_no_kludge_injected(self):
        from anetbbs.echomail.qwk_hub_ftp import _build_messages_dat

        class _FakeMsg:
            from_name = 'Alice'
            to_name = 'Bob'
            subject = 'New topic'
            body = 'Starting something new.'
            reply_id = None
            created_at = None

        data = _build_messages_dat({1: [_FakeMsg()]}, 'TESTNODE')
        body_bytes = data[128 + 128:]
        body_text = body_bytes.decode('cp437', errors='replace')
        self.assertNotIn('@REPLY:', body_text)

    def test_qwk_hub_injects_reply_kludge_into_body(self):
        from anetbbs.models import db, EchoArea, EchomailNetwork, QWKNode, QWKNodeLastSent, EchomailMessage

        with self.app.app_context():
            db.create_all()
            net = EchomailNetwork(name='ReplyThreadNet', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='1', name='General', is_active=True)
            db.session.add(area)
            node = QWKNode(packet_id='REPLYND', name='Reply Thread Node', password='x')
            db.session.add(node)
            db.session.commit()
            sub = QWKNodeLastSent(node_id=node.id, echo_area_id=area.id,
                                  conf_number=1, last_message_id=None)
            db.session.add(sub)
            msg = EchomailMessage(area_id=area.id, network_id=net.id,
                                  from_name='Alice', to_name='Bob',
                                  subject='Re: Test', body='A reply.',
                                  reply_id='1:114/30 xyz999')
            db.session.add(msg)
            db.session.commit()

            from anetbbs.web.qwk_hub import _build_qwk_hub_packet
            packet_bytes, _new_hwm, _total = _build_qwk_hub_packet(node)

            with zipfile.ZipFile(__import__('io').BytesIO(packet_bytes)) as zf:
                msgs_dat = zf.read('MESSAGES.DAT')
            body_bytes = msgs_dat[128 + 128:]
            body_text = body_bytes.decode('cp437', errors='replace')
            self.assertIn('@REPLY: 1:114/30 xyz999', body_text)
            db.drop_all()

    def test_upload_extracts_reply_kludge_and_strips_it_from_visible_body(self):
        """qwk_user.py's upload() -- the one importer that used to skip
        reply-threading entirely (raw positional parse, no
        _clean_body() call)."""
        import io as _io
        import struct as _struct
        import zipfile as _zipfile
        from anetbbs.models import db, User, EchoArea, EchomailNetwork, EchomailMessage

        with self.app.app_context():
            db.create_all()
            user = User(username='replyuploadtest', email='rut@example.com',
                       password_hash='x', access_level=100)
            db.session.add(user)
            net = EchomailNetwork(name='ReplyUploadNet', network_type='qwk')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='REPLYUP', name='Reply Upload Test',
                            is_active=True, min_access_level=0)
            db.session.add(area)
            db.session.commit()
            areas = EchoArea.query.filter_by(is_active=True).all()
            conf_num = next(i for i, a in enumerate(areas, 1) if a.id == area.id)
            user_id = user.id

        # Build a minimal REP-format MESSAGES.DAT with an @REPLY: line
        # leading the body, matching what a real vanilla QWK reader
        # (that reads this convention rather than native ref_num) sends.
        BLOCK = 128
        body = '@REPLY: 1:114/30 uploadtest1\nActual reply text here.'
        body_bytes = body.encode('latin-1')
        remainder = len(body_bytes) % BLOCK
        if remainder:
            body_bytes += b' ' * (BLOCK - remainder)
        num_blocks = 1 + len(body_bytes) // BLOCK
        header = bytearray(b' ' * BLOCK)
        header[21:46] = b'All'.ljust(25)
        header[71:96] = b'Test Reply'.ljust(25)
        header[116:122] = f'{num_blocks:<6d}'.encode()[:6]
        _struct.pack_into('<H', header, 123, conf_num)
        msgs_dat = b' ' * BLOCK + bytes(header) + body_bytes

        zbuf = _io.BytesIO()
        with _zipfile.ZipFile(zbuf, 'w', _zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('REPLYUP.MSG', msgs_dat)
        rep_bytes = zbuf.getvalue()

        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        resp = client.post('/qwk/upload', data={
            'rep': (_io.BytesIO(rep_bytes), 'REPLYUP.rep'),
        }, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            saved = EchomailMessage.query.filter_by(subject='Test Reply').first()
            self.assertIsNotNone(saved)
            self.assertEqual(saved.reply_id, '1:114/30 uploadtest1')
            self.assertNotIn('@REPLY:', saved.body,
                            'the kludge line must be stripped from the '
                            'visible body, not left as junk text')
            self.assertIn('Actual reply text here.', saved.body)


if __name__ == '__main__':
    unittest.main()
