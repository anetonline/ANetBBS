"""Regression tests for MRC bridge debug-log password redaction --
real gap found in a security/performance audit: MRC_BRIDGE_LOG_LEVEL=
DEBUG's raw wire-packet trace ("MRC RAW OUT/IN" in main.py, "MYSTIC RAW
OUT/IN" in mystic_connection.py) logged every packet completely
unredacted. IDENTIFY/REGISTER/UPDATE/ROOMPASS carry their password as
plain text inside the packet's own command field (per the real MRC
protocol spec template, e.g. "user~bbs~room~SERVER~msgext~~IDENTIFY
password~"), so enabling DEBUG tracing for any other diagnostic reason
put every login password a user typed into the bridge's own log file
in plaintext, permanently. Fixed via MRCProtocol.redact_packet_for_logs(),
now called at every raw-trace log site in both main.py and
mystic_connection.py.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.mrc_protocol import MRCProtocol


class RedactCommandForLogsTests(unittest.TestCase):
    def test_identify_password_is_redacted(self):
        self.assertEqual(MRCProtocol.redact_command_for_logs('IDENTIFY hunter2'),
                         'IDENTIFY ********')

    def test_register_password_is_redacted(self):
        self.assertEqual(MRCProtocol.redact_command_for_logs('REGISTER hunter2'),
                         'REGISTER ********')

    def test_update_password_is_redacted(self):
        self.assertEqual(MRCProtocol.redact_command_for_logs('UPDATE newpass123'),
                         'UPDATE ********')

    def test_roompass_is_redacted(self):
        self.assertEqual(MRCProtocol.redact_command_for_logs('ROOMPASS secretroom'),
                         'ROOMPASS ********')

    def test_case_insensitive_match(self):
        # Matching is case-insensitive; the redacted marker itself is
        # always uppercase regardless of the input's original casing --
        # redaction doesn't need to preserve it, only hide the secret.
        self.assertEqual(MRCProtocol.redact_command_for_logs('identify hunter2'),
                         'IDENTIFY ********')

    def test_ordinary_command_is_untouched(self):
        self.assertEqual(MRCProtocol.redact_command_for_logs('MOTD'), 'MOTD')

    def test_ordinary_chat_message_is_untouched(self):
        self.assertEqual(MRCProtocol.redact_command_for_logs('hey everyone!'),
                         'hey everyone!')

    def test_empty_and_none_are_handled(self):
        self.assertEqual(MRCProtocol.redact_command_for_logs(''), '')
        self.assertEqual(MRCProtocol.redact_command_for_logs(None), '')


class RedactPacketForLogsTests(unittest.TestCase):
    def test_identify_password_removed_from_full_packet(self):
        packet = 'alice~bbs1~lobby~SERVER~~~IDENTIFY hunter2~'
        redacted = MRCProtocol.redact_packet_for_logs(packet)
        self.assertNotIn('hunter2', redacted)
        self.assertIn('IDENTIFY ********', redacted)
        # Everything else about the packet must still be visible for
        # real debugging -- only the password itself is masked.
        self.assertIn('alice', redacted)
        self.assertIn('bbs1', redacted)
        self.assertIn('lobby', redacted)

    def test_register_password_removed_from_full_packet(self):
        packet = 'bob~bbs1~lobby~SERVER~~~REGISTER newpass123~'
        redacted = MRCProtocol.redact_packet_for_logs(packet)
        self.assertNotIn('newpass123', redacted)
        self.assertIn('REGISTER ********', redacted)

    def test_ordinary_chat_packet_is_unaffected(self):
        packet = 'alice~bbs1~lobby~~~lobby~just chatting~'
        redacted = MRCProtocol.redact_packet_for_logs(packet)
        self.assertEqual(redacted, packet)

    def test_field_count_matches_original_after_redaction(self):
        packet = 'alice~bbs1~lobby~SERVER~~~IDENTIFY hunter2~'
        redacted = MRCProtocol.redact_packet_for_logs(packet)
        self.assertEqual(redacted.count('~'), packet.count('~'))

    def test_too_short_to_be_a_real_packet_is_returned_unchanged(self):
        garbage = 'not~a~real~packet'
        self.assertEqual(MRCProtocol.redact_packet_for_logs(garbage), garbage)

    def test_empty_and_none_are_handled(self):
        self.assertEqual(MRCProtocol.redact_packet_for_logs(''), '')
        self.assertIsNone(MRCProtocol.redact_packet_for_logs(None))


class RawTraceLogSitesUseRedactionTests(unittest.TestCase):
    """Guards against the exact regression this audit found: confirms
    every raw-packet DEBUG log call site actually routes through
    redact_packet_for_logs(), not just that the helper itself works in
    isolation -- a future refactor could easily reintroduce a bare
    logger.debug("...RAW...", packet) call without it."""

    def test_main_py_raw_trace_sites_call_the_redaction_helper(self):
        import inspect
        from mrc.bridge import main as bridge_main
        source = inspect.getsource(bridge_main)
        for marker in ('logger.debug("MRC RAW OUT', 'logger.debug("MRC RAW IN'):
            idx = source.index(marker)
            line_end = source.index('\n', idx)
            line = source[idx:line_end]
            self.assertIn('redact_packet_for_logs', line,
                          f'{marker} log line must redact via redact_packet_for_logs()')

    def test_mystic_connection_py_raw_trace_sites_call_the_redaction_helper(self):
        import inspect
        from mrc.bridge import mystic_connection
        source = inspect.getsource(mystic_connection)
        for marker in ('logger.debug("MYSTIC RAW OUT', 'logger.debug("MYSTIC RAW IN'):
            idx = source.index(marker)
            line_end = source.index('\n', idx)
            line = source[idx:line_end]
            self.assertIn('redact_packet_for_logs', line,
                          f'{marker} log line must redact via redact_packet_for_logs()')


if __name__ == '__main__':
    unittest.main()
