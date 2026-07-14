"""Tests for MRCConnection.send_info_fields() -- broadcasting this BBS's
own description/telnet/ssh/website/sysop so other clients on the
network can look up "BBS info" for it.

Verified against the real reference C client
(anetmrc_v1.3.9/src/helper_protocol.c's mrc_send_info_fields): ANetBBS's
bridge never sent any of INFODSC/INFOTEL/INFOSSH/INFOWEB/INFOSYS at all,
so this was always empty for anyone looking ANetBBS up from another
client (e.g. the Mystic Python client, which surfaces it) -- reported
live by the sysop as "the BBS info that is not shown with ANetBBS but
is with the mystic mrc client."
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import MRCConnection
from mrc.bridge.mrc_protocol import MRCProtocol


def _run(coro):
    return asyncio.run(coro)


class _FakeWriter:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data.decode())

    async def drain(self):
        pass


def _make_conn(**bbs_fields):
    config = {
        "mrc_host": "example.invalid",
        "mrc_port": 5000,
        "bridge_bbs": "TestBBS",
        **bbs_fields,
    }
    conn = MRCConnection(config)
    conn.connected = True
    conn.writer = _FakeWriter()
    return conn


class SendInfoFieldsTests(unittest.TestCase):
    def test_all_fields_configured_sends_all_five_packets(self):
        conn = _make_conn(
            bbs_description="A friendly BBS",
            bbs_telnet="bbs.example.com:23",
            bbs_ssh="bbs.example.com:22",
            bbs_website="https://bbs.example.com",
            bbs_sysop="StingRay",
        )
        _run(conn.send_info_fields())
        joined = "".join(conn.writer.written)
        self.assertIn("INFODSC: A friendly BBS", joined)
        self.assertIn("INFOTEL: bbs.example.com:23", joined)
        self.assertIn("INFOSSH: bbs.example.com:22", joined)
        self.assertIn("INFOWEB: https://bbs.example.com", joined)
        self.assertIn("INFOSYS: StingRay", joined)

    def test_unconfigured_fields_are_omitted_not_sent_blank(self):
        conn = _make_conn(bbs_description="Only a description")
        _run(conn.send_info_fields())
        joined = "".join(conn.writer.written)
        self.assertIn("INFODSC: Only a description", joined)
        self.assertNotIn("INFOTEL", joined)
        self.assertNotIn("INFOSSH", joined)
        self.assertNotIn("INFOWEB", joined)
        self.assertNotIn("INFOSYS", joined)

    def test_no_fields_configured_sends_nothing(self):
        conn = _make_conn()
        _run(conn.send_info_fields())
        self.assertEqual(conn.writer.written, [])

    def test_whitespace_only_field_treated_as_unconfigured(self):
        conn = _make_conn(bbs_telnet="   ")
        _run(conn.send_info_fields())
        self.assertEqual(conn.writer.written, [])

    def test_packet_wire_format_matches_reference_client_byte_order(self):
        # Verified byte-for-byte against the real reference C client
        # (helper_protocol.c's mrc_send_info_fields:
        # mrc_send_packet_fields("CLIENT", bbs_name, "", "SERVER", "ALL",
        # msgext, body)) -- checked positionally, not via
        # MRCProtocol.parse_packet's semantic labels, since this
        # specific broadcast-to-everyone packet type puts "ALL" and the
        # timestamp in the OPPOSITE positions from every other message
        # in this protocol (room-scoped sends put msg_ext before
        # to_room; this puts "ALL" before the timestamp) -- confirmed
        # live: sending them in the "logically correct" order instead
        # put a garbage timestamp where the hub expects the literal
        # "ALL", and it silently discarded every packet.
        conn = _make_conn(bbs_website="https://bbs.example.com")
        _run(conn.send_info_fields())
        raw = conn.writer.written[0]
        fields = raw.rstrip('\n').rstrip('~').split('~')
        self.assertEqual(fields[0], "CLIENT")       # from_user
        self.assertEqual(fields[1], "TestBBS")       # from_site (bbs)
        self.assertEqual(fields[2], "")              # from_room
        self.assertEqual(fields[3], "SERVER")        # to_user
        self.assertEqual(fields[4], "ALL")           # position 5 -- "ALL"
        self.assertTrue(fields[5])                   # position 6 -- timestamp
        self.assertEqual(fields[6], "INFOWEB: https://bbs.example.com")

    def test_pipe_color_codes_sent_through_untouched(self):
        # Confirmed with the sysop (2 years running this exact hub/
        # protocol before ANetBBS existed): pipe-color codes in these
        # fields are expected and supported, same as the BBS name
        # itself was never stripped either. An earlier version of this
        # method stripped them defensively, reasoning only from the
        # reference client's own plain-text example -- that was wrong,
        # corrected after the sysop pushed back live.
        conn = _make_conn(
            bbs_telnet="|15bbs.example.com:2233",
            bbs_sysop="|04Sy|01so|04pN|01ame",
        )
        _run(conn.send_info_fields())
        joined = "".join(conn.writer.written)
        self.assertIn("INFOTEL: |15bbs.example.com:2233", joined)
        self.assertIn("INFOSYS: |04Sy|01so|04pN|01ame", joined)


class SendBbsMetaTests(unittest.TestCase):
    """ANetBBS never sent BBSMETA at all -- the reference client sends
    it right before INFO fields (helper_protocol.c's connect sequence).
    Added alongside the send_info_fields field-order fix since both
    were live-suspected contributors to the BBS directory staying blank
    and couldn't be isolated without another live round-trip."""

    def test_sends_seclevel_and_sysop(self):
        conn = _make_conn(bbs_sysop="StingRay")
        _run(conn.send_bbsmeta())
        joined = "".join(conn.writer.written)
        self.assertIn("BBSMETA: SecLevel(100) Sysop(StingRay)", joined)

    def test_defaults_sysop_when_unconfigured(self):
        conn = _make_conn()
        _run(conn.send_bbsmeta())
        joined = "".join(conn.writer.written)
        self.assertIn("Sysop(SysOp)", joined)

    def test_pipe_codes_in_sysop_sent_through_untouched(self):
        # See test_pipe_color_codes_sent_through_untouched -- the sysop
        # confirmed colored values are expected/supported here.
        conn = _make_conn(bbs_sysop="|04Sti|01ng|04Ra|01y")
        _run(conn.send_bbsmeta())
        joined = "".join(conn.writer.written)
        self.assertIn("Sysop(|04Sti|01ng|04Ra|01y)", joined)

    def test_field_order_matches_reference_client(self):
        # BBSMETA's own field order in the reference client IS
        # internally consistent with the standard msg_ext-before-
        # to_room layout (unlike INFO fields, above) -- no swap needed
        # here.
        conn = _make_conn(bbs_sysop="StingRay")
        _run(conn.send_bbsmeta())
        raw = conn.writer.written[0]
        fields = raw.rstrip('\n').rstrip('~').split('~')
        self.assertEqual(fields[0], "CLIENT")
        self.assertEqual(fields[1], "TestBBS")
        self.assertEqual(fields[3], "SERVER")
        self.assertTrue(fields[4])   # timestamp
        self.assertEqual(fields[5], "")  # to_room blank
        self.assertEqual(fields[6], "BBSMETA: SecLevel(100) Sysop(StingRay)")


if __name__ == '__main__':
    unittest.main()
