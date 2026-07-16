"""Regression test for MRC wire-format fields, verified directly against
the OFFICIAL MRC protocol specification (bbswiki.bottomlessabyss.net,
MRCDoc:MRC_Protocol -- the actual developer documentation, not inferred
client behavior).

This corrects two earlier revisions of this test file that had chased a
live "I still have to /identify every single time" report by copying
individual reference clients' own implementation shortcuts (uMRC's
sendCmdPacket hardcodes toRoom empty for every command; Synchronet's JS
connector empties both fromRoom and toRoom for LOGOFF specifically) --
neither actually matches the documented spec, which is authoritative
over any one client's own choices:

  - Most "Client session context" commands (MOTD, WHOON, BANNERS, etc.)
    are documented with a POPULATED toRoom:
        "user~bbs~room~SERVER~msgext~room~MOTD~"
  - Only IDENTIFY/REGISTER/UPDATE ("MRC Trust" verbs) are documented
    with an empty toRoom:
        "user~bbs~room~SERVER~msgext~~IDENTIFY password~"
  - LOGOFF is documented with BOTH fromRoom and toRoom populated:
        "user~bbs~room~SERVER~msgext~room~LOGOFF~"
  - USERIP is documented with fromRoom empty (unlike the generic
    command path):
        "user~bbs~~SERVER~msgext~~USERIP:ipaddress~"
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.mrc_protocol import MRCProtocol


class ServerCommandToRoomTests(unittest.TestCase):
    def test_generic_command_toroom_is_populated(self):
        for command in ('MOTD', 'WHOON', 'BANNERS', 'CHATTERS', 'ROOMS',
                         'TOPICS', 'CHANNEL', 'USERS', 'LASTSEEN',
                         'AFK', 'BACK', 'TOPIC', 'TRUST INFO'):
            pkt = MRCProtocol.create_server_command('StingRay', 'TestBBS', 'lobby', command)
            parsed = MRCProtocol.parse_packet(pkt)
            self.assertEqual(parsed['to_room'], 'lobby',
                             f'{command}: toRoom must be populated with the '
                             'room name per the documented spec template')

    def test_identify_register_update_toroom_is_empty(self):
        for command in ('IDENTIFY secret', 'REGISTER secret em@ex.com', 'UPDATE password x'):
            pkt = MRCProtocol.create_server_command('StingRay', 'TestBBS', 'lobby', command)
            parsed = MRCProtocol.parse_packet(pkt)
            self.assertEqual(parsed['to_room'], '',
                             f'{command}: toRoom must be empty per spec '
                             '("user~bbs~room~SERVER~msgext~~IDENTIFY password~")')

    def test_explicit_to_room_override_still_honored(self):
        pkt = MRCProtocol.create_server_command(
            'StingRay', 'TestBBS', 'lobby', 'SOMECMD', to_room='ctcp_echo_channel')
        parsed = MRCProtocol.parse_packet(pkt)
        self.assertEqual(parsed['to_room'], 'ctcp_echo_channel')

    def test_from_room_always_populated(self):
        pkt = MRCProtocol.create_server_command('StingRay', 'TestBBS', 'lobby', 'MOTD')
        parsed = MRCProtocol.parse_packet(pkt)
        self.assertEqual(parsed['from_room'], 'lobby')


class LogoffFieldsTests(unittest.TestCase):
    def test_logoff_toroom_is_populated(self):
        pkt = MRCProtocol.create_logoff('StingRay', 'TestBBS', 'lobby')
        parsed = MRCProtocol.parse_packet(pkt)
        self.assertEqual(parsed['to_room'], 'lobby',
                         'LOGOFF must send toRoom populated per the '
                         'documented spec template '
                         '("user~bbs~room~SERVER~msgext~room~LOGOFF~")')

    def test_logoff_from_room_is_populated(self):
        pkt = MRCProtocol.create_logoff('StingRay', 'TestBBS', 'lobby')
        parsed = MRCProtocol.parse_packet(pkt)
        self.assertEqual(parsed['from_room'], 'lobby')

    def test_logoff_command_word_and_target(self):
        pkt = MRCProtocol.create_logoff('StingRay', 'TestBBS', 'lobby')
        parsed = MRCProtocol.parse_packet(pkt)
        self.assertEqual(parsed['to_user'], 'SERVER')
        self.assertEqual(parsed['message'], 'LOGOFF')


class UserIpFromRoomTests(unittest.TestCase):
    def test_userip_from_room_is_empty(self):
        pkt = MRCProtocol.create_packet(
            'StingRay', 'TestBBS', '', 'SERVER', '', '', 'USERIP:203.0.113.7')
        parsed = MRCProtocol.parse_packet(pkt)
        self.assertEqual(parsed['from_room'], '',
                         'USERIP must send fromRoom empty per the documented '
                         'spec template ("user~bbs~~SERVER~msgext~~USERIP:'
                         'ipaddress~"), unlike the generic command path')
        self.assertEqual(parsed['to_room'], '')


if __name__ == '__main__':
    unittest.main()
