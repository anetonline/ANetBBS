"""Regression test for a real Low-severity finding from a security/
performance audit (2026-08-31): both poller.py's _import_message() (the
outbound-poll import path) and binkp_server.py's _import_pkt_payload()
(the inbound-listener path, a peer connecting IN to deliver mail) bumped
EchoArea.total_messages/last_message_at with a Python-side read-then-
write (`area.total_messages = (area.total_messages or 0) + 1`) -- the
same lost-update-race shape already fixed this audit round for
FileRatio/file_quota counters. Unlike a purely single-threaded, single-
call race, this ONE is reachable from two genuinely concurrent code
paths hitting the SAME EchoArea row at once: an inbound BinkP session
(a peer dialing in) and an outbound poll of that same network (scheduled
or manually triggered) can run at the same time, in different
threads/processes. Cosmetic display stat only (Messages/Last Activity
columns), not data loss -- but fixed with the same atomic SQL UPDATE
pattern already established elsewhere this audit round.

Deterministic (source-inspection) test, not a real-thread race, per
this project's own established lesson (see test_file_quota.py's
ConsumeQuotaAtomicIncrementTests docstring for why a real-thread stress
test doesn't reliably discriminate against SQLite/GIL scheduling in
this sandbox for this exact bug shape) -- the actual bug is a code-
shape problem (atomic SQL UPDATE vs. Python round-trip), not a
timing-dependent one.
"""
import ast
import inspect
import os
import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


def _body_source_without_docstring(func):
    raw = inspect.getsource(func)
    tree = ast.parse(textwrap.dedent(raw))
    func_node = tree.body[0]
    body_nodes = func_node.body
    if (body_nodes and isinstance(body_nodes[0], ast.Expr)
            and isinstance(getattr(body_nodes[0], 'value', None), ast.Constant)
            and isinstance(body_nodes[0].value.value, str)):
        body_nodes = body_nodes[1:]
    source = '\n'.join(
        ast.get_source_segment(textwrap.dedent(raw), node) or ''
        for node in body_nodes)
    # ast.get_source_segment() preserves inline "# ..." comments as part
    # of whatever statement they're attached to -- this function's own
    # fix comments deliberately quote the OLD buggy pattern as prose
    # explaining what changed, which would otherwise false-positive the
    # regex checks below against comment text rather than real code
    # (none of the real code here has a literal '#' inside a string, so
    # a plain per-line strip is safe for this specific check).
    return '\n'.join(line.split('#', 1)[0] for line in source.splitlines())


class EchoAreaCounterIsAtomicStructuralTests(unittest.TestCase):
    """Deterministic structural guard: both bump sites must issue the
    increment via a single atomic SQL UPDATE, not a Python-side
    row.attr = row.attr + n assignment."""

    def test_poller_import_message_uses_atomic_sql_update(self):
        from anetbbs.echomail import poller
        code = _body_source_without_docstring(poller._import_message)
        self.assertIn(
            'EchoArea.total_messages + 1', code,
            '_import_message() must reference the column atomically '
            '(total_messages = total_messages + 1) rather than a '
            'Python-computed literal')
        self.assertNotRegex(
            code, r'\.total_messages\s*=\s*\(',
            'must not contain a Python-side read-then-write assignment '
            "(e.g. `area.total_messages = (area.total_messages or 0) "
            '+ 1`) -- that shape always has a lost-update race window '
            'no matter how it is timed')

    def test_binkp_server_import_pkt_payload_uses_atomic_sql_update(self):
        from anetbbs.echomail import binkp_server
        code = _body_source_without_docstring(binkp_server._import_pkt_payload)
        self.assertIn(
            'EchoArea.total_messages + 1', code,
            '_import_pkt_payload() must reference the column atomically '
            '(total_messages = total_messages + 1) rather than a '
            'Python-computed literal')
        self.assertNotRegex(
            code, r'\.total_messages\s*=\s*\(',
            'must not contain a Python-side read-then-write assignment '
            '-- that shape always has a lost-update race window no '
            'matter how it is timed')

    def test_qwk_hub_import_rep_packet_uses_atomic_sql_update(self):
        """Third sibling of the same bug, found and fixed in a follow-up
        pass: web/qwk_hub.py's import_rep_packet() (the QWK-over-HTTP
        hub REP importer) bumped this same counter the same
        Python-side read-then-write way -- reachable concurrently with
        either of the two paths above, since a QWK hub upload can land
        while a BinkP session is also importing into the same
        EchoArea."""
        from anetbbs.web import qwk_hub
        code = _body_source_without_docstring(qwk_hub.import_rep_packet)
        self.assertIn(
            'EchoArea.total_messages + 1', code,
            'import_rep_packet() must reference the column atomically '
            '(total_messages = total_messages + 1) rather than a '
            'Python-computed literal')
        self.assertNotRegex(
            code, r'\.total_messages\s*=\s*\(',
            'must not contain a Python-side read-then-write assignment '
            '-- that shape always has a lost-update race window no '
            'matter how it is timed')


class EchoAreaCounterFunctionalTests(unittest.TestCase):
    """Confirms the atomic UPDATE still produces the correct end result
    for normal, sequential (non-racing) use -- the discriminator above
    guards the mechanism, this guards actual behavior."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.echo_area_counter_test.db')
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

    def test_poller_import_message_increments_counter_correctly(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        from anetbbs.echomail.poller import _import_message

        with self.app.app_context():
            net = EchomailNetwork(name='CounterTestNet', network_type='binkp')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='COUNTER.TEST', name='Counter Test',
                            is_subscribed=True, is_active=True, total_messages=5)
            db.session.add(area)
            db.session.commit()
            area_id = area.id

            for i in range(3):
                _import_message(net, {
                    'area_tag': 'COUNTER.TEST',
                    'from_name': f'Node{i}',
                    'subject': f'Test {i}',
                    'body': 'x',
                    'msg_id': f'<counter-test-{i}@test>',
                })

            # The atomic UPDATE runs with synchronize_session=False, so
            # the identity-mapped `area`/`net` objects still in this
            # session never get told about it -- a query for the same
            # PK would otherwise return that same stale cached Python
            # object instead of genuinely re-reading the row. expire_all()
            # forces a real re-fetch, confirming what actually landed in
            # the database rather than what's cached in memory.
            db.session.expire_all()
            refreshed = EchoArea.query.filter_by(id=area_id).first()
            self.assertEqual(refreshed.total_messages, 8,
                             'sequential imports must still accumulate '
                             'correctly (5 existing + 3 new)')

    def test_binkp_listener_import_increments_counter_correctly(self):
        from anetbbs.echomail.binkp import _build_ftn_packet
        from anetbbs.echomail import binkp_server
        from anetbbs.models import db, EchomailNetwork, EchoArea

        class _FakeMsg:
            def __init__(self, *, area, subject='Test', msg_id=None):
                self.area = area
                self.from_name = 'Tester'
                self.to_name = 'All'
                self.subject = subject
                self.body = 'Hello'
                self.tear_line = None
                self.origin_line = None
                self.kludges = None
                self.seenby = None
                self.path = None
                self.chrs = 'CP437 2'
                self.msg_id = msg_id
                self.reply_id = None
                self.to_address = ''
                self.from_address = ''

        class _FakeArea:
            def __init__(self, tag):
                self.tag = tag

        with self.app.app_context():
            net = EchomailNetwork(name='ListenerCounterNet', network_type='binkp',
                                  our_address='1:114/30', hub_address='1:114/0')
            db.session.add(net)
            db.session.commit()
            area = EchoArea(network_id=net.id, tag='LISTENER.COUNTER',
                            name='Listener Counter', is_active=True,
                            is_subscribed=True, total_messages=10)
            db.session.add(area)
            db.session.commit()
            area_id = area.id
            net_id = net.id

            pkt = _build_ftn_packet(
                [_FakeMsg(area=_FakeArea('LISTENER.COUNTER'),
                         msg_id='1:114/30@fidonet u5001')],
                '1:114/30', '1:114/0')
            binkp_server._import_pkt_payload(pkt, net_id, 'counter.pkt')

            # Same identity-map staleness reasoning as the poller test
            # above -- force a real re-fetch from the database.
            db.session.expire_all()
            refreshed = EchoArea.query.filter_by(id=area_id).first()
            self.assertEqual(refreshed.total_messages, 11,
                             'single import must accumulate correctly '
                             '(10 existing + 1 new)')

    def test_qwk_hub_import_rep_packet_increments_counter_correctly(self):
        import io
        import struct
        import zipfile
        from anetbbs.models import (db, QWKNode, EchomailNetwork, EchoArea,
                                    QWKNodeLastSent)
        from anetbbs.web.qwk_hub import import_rep_packet, resolve_hub_id

        def _build_rep_zip(hub_id, messages):
            """messages: list of (to_name, subject, body, conf_num)."""
            BLOCK = 128
            buf = io.BytesIO()
            buf.write(b' ' * BLOCK)
            for to_name, subject, body, conf_num in messages:
                body_bytes = body.encode('latin-1', errors='replace')
                remainder = len(body_bytes) % BLOCK
                if remainder:
                    body_bytes += b' ' * (BLOCK - remainder)
                num_blocks = 1 + len(body_bytes) // BLOCK
                header = bytearray(b' ' * BLOCK)
                header[8:21] = b'01-01-2026'.ljust(13)[:13]
                header[21:46] = to_name.encode('cp437')[:25].ljust(25)
                header[46:71] = b'Tester'.ljust(25)[:25]
                header[71:96] = subject.encode('cp437')[:25].ljust(25)
                header[116:122] = f'{num_blocks:<6d}'.encode()[:6]
                struct.pack_into('<H', header, 123, conf_num)
                buf.write(bytes(header))
                buf.write(body_bytes)
            zbuf = io.BytesIO()
            with zipfile.ZipFile(zbuf, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(f'{hub_id}.MSG', buf.getvalue())
            return zbuf.getvalue()

        with self.app.app_context():
            net = EchomailNetwork(name='QwkHubCounterNet', network_type='qwk')
            db.session.add(net)
            db.session.flush()
            area = EchoArea(network_id=net.id, tag='QWKHUB.COUNTER',
                            name='QWK Hub Counter', is_active=True,
                            total_messages=20)
            db.session.add(area)
            db.session.flush()
            node = QWKNode(packet_id='CNTRNODE', name='CounterNode',
                           password='x', is_active=True)
            db.session.add(node)
            db.session.flush()
            db.session.add(QWKNodeLastSent(node_id=node.id, echo_area_id=area.id,
                                           conf_number=1))
            db.session.commit()
            area_id = area.id

            hub_id = resolve_hub_id(node)
            rep_bytes = _build_rep_zip(hub_id, [
                ('All', 'Counter test', 'Some body text', 1)])
            imported = import_rep_packet(node, rep_bytes)

            self.assertEqual(imported, 1)
            db.session.expire_all()
            refreshed = EchoArea.query.filter_by(id=area_id).first()
            self.assertEqual(refreshed.total_messages, 21,
                             'single REP import must accumulate correctly '
                             '(20 existing + 1 new)')


if __name__ == '__main__':
    unittest.main()
