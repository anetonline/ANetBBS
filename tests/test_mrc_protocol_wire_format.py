"""Regression test for a real wire-format bug found investigating a live
"I still have to /identify every single time, even leaving and
immediately rejoining" report.

Verified directly against the reference client's actual C source
(umrc-client/main.c):

  - sendCmdPacket() -- backs every generic /command (MOTD, WHOON,
    BANNERS, CHATTERS, USERIP, etc.) -- hardcodes toRoom empty for
    EVERY command it sends, unconditionally:
        createPacket(chatterName, fromSite, gRoom, "SERVER", "", "", cmdstr)
                                                          ^^ toRoom always ""

  - LOGOFF (sent via sendMsgPacket on /quit) also sends toRoom empty:
        sendMsgPacket(&mrcSock, "SERVER", "", "", "LOGOFF")
                                            ^^ toRoom = ""

ANetBBS's create_server_command() only emptied toRoom for
IDENTIFY/REGISTER/UPDATE (an earlier, narrower investigation had
confirmed those three specifically matched the reference and stopped
there) -- every OTHER generic command, and create_logoff() separately,
populated toRoom with the actual room name instead, a real deviation
from the reference on literally every command exchange and on the
exact packet sent each time a user leaves.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.mrc_protocol import MRCProtocol


class ServerCommandToRoomTests(unittest.TestCase):
    def test_generic_command_toroom_is_empty(self):
        for command in ('MOTD', 'WHOON', 'BANNERS', 'CHATTERS', 'ROOMS',
                         'TOPICS', 'CHANNEL', 'USERS', 'STATS', 'LASTSEEN',
                         'AFK', 'BACK', 'TOPIC', 'USERIP:203.0.113.7'):
            pkt = MRCProtocol.create_server_command('StingRay', 'TestBBS', 'lobby', command)
            parsed = MRCProtocol.parse_packet(pkt)
            self.assertEqual(parsed['to_room'], '',
                             f'{command}: toRoom must be empty, matching the '
                             'reference client\'s sendCmdPacket (always empty)')

    def test_identify_register_update_toroom_still_empty(self):
        for command in ('IDENTIFY secret', 'REGISTER secret em@ex.com', 'UPDATE password x'):
            pkt = MRCProtocol.create_server_command('StingRay', 'TestBBS', 'lobby', command)
            parsed = MRCProtocol.parse_packet(pkt)
            self.assertEqual(parsed['to_room'], '')

    def test_explicit_to_room_override_still_honored(self):
        pkt = MRCProtocol.create_server_command(
            'StingRay', 'TestBBS', 'lobby', 'SOMECMD', to_room='ctcp_echo_channel')
        parsed = MRCProtocol.parse_packet(pkt)
        self.assertEqual(parsed['to_room'], 'ctcp_echo_channel')

    def test_from_room_still_populated(self):
        pkt = MRCProtocol.create_server_command('StingRay', 'TestBBS', 'lobby', 'MOTD')
        parsed = MRCProtocol.parse_packet(pkt)
        self.assertEqual(parsed['from_room'], 'lobby')


class LogoffToRoomTests(unittest.TestCase):
    def test_logoff_toroom_is_empty(self):
        pkt = MRCProtocol.create_logoff('StingRay', 'TestBBS', 'lobby')
        parsed = MRCProtocol.parse_packet(pkt)
        self.assertEqual(parsed['to_room'], '',
                         'LOGOFF must send an empty toRoom, matching the '
                         'reference client -- a populated toRoom here is '
                         'the real bug behind repeated forced re-identify')

    def test_logoff_from_room_still_populated(self):
        pkt = MRCProtocol.create_logoff('StingRay', 'TestBBS', 'lobby')
        parsed = MRCProtocol.parse_packet(pkt)
        self.assertEqual(parsed['from_room'], 'lobby')

    def test_logoff_command_word_and_target(self):
        pkt = MRCProtocol.create_logoff('StingRay', 'TestBBS', 'lobby')
        parsed = MRCProtocol.parse_packet(pkt)
        self.assertEqual(parsed['to_user'], 'SERVER')
        self.assertEqual(parsed['message'], 'LOGOFF')


if __name__ == '__main__':
    unittest.main()
