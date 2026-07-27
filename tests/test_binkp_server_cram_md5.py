"""Regression test for a real production bug: the inbound BinkP
listener (anetbbs/echomail/binkp_server.py) never advertised or
verified CRAM-MD5 (FTS-1027) as the answering side -- only the
outbound client (binkp.py) had it, and only for the calling role.

Reported live by Nicholas Boel (pharcyde.org, real binkd peer) polling
IN to an ANetBBS install: "CRAM-MD5 is not supported by remote". Root
cause confirmed by reading the code directly: the listener's M_NUL
preamble never sent an `OPT CRAM-MD5-<hex-challenge>` line, so a
calling binkd had no challenge to respond to and correctly reported
this. Fixed by generating a random challenge, advertising it in the
preamble, and verifying a CRAM-MD5 response against it in
_verify_binkp_password() -- which also still accepts a legacy
plain-text M_PWD, so older peers aren't broken.

See tests/test_binkp_server_single_adr_fix.py for why this file avoids
creating a real Flask app: _handle_connection() creates its own
internal one, and a second coexisting app/engine in the same process
corrupts unrelated test files' own create_app() calls later in the
same pytest run.
"""
import asyncio
import hashlib
import hmac
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _FakeWriter:
    def __init__(self):
        self.sent = []

    def get_extra_info(self, key):
        return ('127.0.0.1', 12345) if key == 'peername' else None

    def write(self, data):
        self.sent.append(data)

    async def drain(self):
        pass

    def close(self):
        pass


class _FakeReader:
    """See test_binkp_server_single_adr_fix.py -- lets the preamble get
    sent, then _handle_connection bails out cleanly on the first read."""
    async def readexactly(self, n):
        raise asyncio.IncompleteReadError(partial=b'', expected=n)


class _FakeQuery:
    def filter_by(self, **kwargs):
        return self

    def all(self):
        return []


def _decode_sent_commands(raw_frames):
    out = []
    for frame in raw_frames:
        word = struct.unpack('>H', frame[0:2])[0]
        length = word & 0x7FFF
        payload = frame[2:2 + length]
        cmd = payload[0]
        text = payload[1:].decode('latin-1', errors='replace')
        out.append((cmd, text))
    return out


class VerifyBinkpPasswordTests(unittest.TestCase):
    """Pure unit tests for _verify_binkp_password() -- no Flask/DB needed."""

    def test_plaintext_match_accepted(self):
        from anetbbs.echomail.binkp_server import _verify_binkp_password
        self.assertTrue(_verify_binkp_password('secret123', 'secret123', b'x' * 32))

    def test_plaintext_mismatch_rejected(self):
        from anetbbs.echomail.binkp_server import _verify_binkp_password
        self.assertFalse(_verify_binkp_password('secret123', 'wrongpass', b'x' * 32))

    def test_cram_md5_correct_digest_accepted(self):
        from anetbbs.echomail.binkp_server import _verify_binkp_password
        challenge = b'\x01\x02\x03' * 8
        digest = hmac.new('secret123'.encode('latin-1'), challenge,
                          hashlib.md5).hexdigest()
        self.assertTrue(_verify_binkp_password(
            'secret123', f'CRAM-MD5-{digest}', challenge))

    def test_cram_md5_wrong_password_rejected(self):
        from anetbbs.echomail.binkp_server import _verify_binkp_password
        challenge = b'\x01\x02\x03' * 8
        digest = hmac.new('wrongpassword'.encode('latin-1'), challenge,
                          hashlib.md5).hexdigest()
        self.assertFalse(_verify_binkp_password(
            'secret123', f'CRAM-MD5-{digest}', challenge))

    def test_cram_md5_digest_against_wrong_challenge_rejected(self):
        """The single biggest way this class of bug could hide: verifying
        against a stale/mismatched challenge would silently accept the
        wrong session's digest."""
        from anetbbs.echomail.binkp_server import _verify_binkp_password
        real_challenge = b'\x01\x02\x03' * 8
        other_challenge = b'\x04\x05\x06' * 8
        digest = hmac.new('secret123'.encode('latin-1'), real_challenge,
                          hashlib.md5).hexdigest()
        self.assertFalse(_verify_binkp_password(
            'secret123', f'CRAM-MD5-{digest}', other_challenge))

    def test_cram_md5_case_insensitive_prefix_and_digest(self):
        from anetbbs.echomail.binkp_server import _verify_binkp_password
        challenge = b'\xAA\xBB\xCC' * 8
        digest = hmac.new('secret123'.encode('latin-1'), challenge,
                          hashlib.md5).hexdigest()
        self.assertTrue(_verify_binkp_password(
            'secret123', f'cram-md5-{digest.upper()}', challenge))

    def test_blank_stored_password_does_not_match_arbitrary_cram_response(self):
        from anetbbs.echomail.binkp_server import _verify_binkp_password
        challenge = b'\x01\x02\x03' * 8
        self.assertFalse(_verify_binkp_password(
            '', 'CRAM-MD5-deadbeef', challenge))

    def test_no_password_configured_accepts_binkp_clients_dash_sentinel(self):
        """binkp.py's own outbound client sends the literal placeholder
        "-" for M_PWD when it has no password configured (a real BinkP
        convention -- M_PWD historically couldn't carry an empty value).
        Two unsecured links (e.g. two ANetBBS installs both intentionally
        configured with no BinkP password) must actually authenticate,
        not silently fail because our blank stored password only ever
        matched a literal empty M_PWD, never our own client's "-"."""
        from anetbbs.echomail.binkp_server import _verify_binkp_password
        self.assertTrue(_verify_binkp_password('', '-', b'x' * 32))

    def test_no_password_configured_still_accepts_literal_empty_pwd(self):
        from anetbbs.echomail.binkp_server import _verify_binkp_password
        self.assertTrue(_verify_binkp_password('', '', b'x' * 32))

    def test_password_configured_rejects_dash_sentinel(self):
        """The "-" fallback must only apply when WE have no password
        configured -- a peer sending "-" against a network that DOES
        require a password must still be rejected."""
        from anetbbs.echomail.binkp_server import _verify_binkp_password
        self.assertFalse(_verify_binkp_password('realpassword', '-', b'x' * 32))


class InboundPollTypeTests(unittest.TestCase):
    """Pure unit tests for _inbound_poll_type() -- part of the fix for a
    real gap: EchomailPollLog was ONLY ever written by the outbound
    poller (anetbbs/echomail/poller.py); an inbound-initiated session
    (a remote hub calling IN and delivering mail -- how tqwnet/sp00knet
    actually work) never wrote a poll log row at all, so real inbound
    mail showed up in message areas but as 0 in the poll log."""

    def test_both_when_received_and_sent(self):
        from anetbbs.echomail.binkp_server import _inbound_poll_type
        self.assertEqual(_inbound_poll_type(5, 3), 'both')

    def test_receive_when_only_received(self):
        from anetbbs.echomail.binkp_server import _inbound_poll_type
        self.assertEqual(_inbound_poll_type(5, 0), 'receive')

    def test_send_when_only_sent(self):
        from anetbbs.echomail.binkp_server import _inbound_poll_type
        self.assertEqual(_inbound_poll_type(0, 5), 'send')

    def test_receive_when_neither(self):
        from anetbbs.echomail.binkp_server import _inbound_poll_type
        self.assertEqual(_inbound_poll_type(0, 0), 'receive')


class BinkPServerAdvertisesCramMd5Tests(unittest.TestCase):
    """Confirms the answering side actually sends a usable challenge in
    its preamble -- the missing half of the real bug (verification
    logic alone is useless if the challenge is never offered)."""

    def _run(self):
        from anetbbs.echomail.binkp_server import _handle_connection, CMD_NUL
        from anetbbs.models import EchomailNetwork

        reader = _FakeReader()
        writer = _FakeWriter()
        EchomailNetwork.query = _FakeQuery()
        try:
            asyncio.run(_handle_connection(reader, writer, '1:114/30', 'ANetBBS'))
        finally:
            del EchomailNetwork.query

        commands = _decode_sent_commands(writer.sent)
        return [text for cmd, text in commands if cmd == CMD_NUL]

    def test_opt_cram_md5_line_sent_with_valid_hex_challenge(self):
        nul_lines = self._run()
        opt_lines = [t for t in nul_lines if t.upper().startswith('OPT ')]
        self.assertEqual(len(opt_lines), 1,
                         f'expected exactly one OPT line, got: {opt_lines}')
        opt = opt_lines[0]
        self.assertTrue(opt.upper().startswith('OPT CRAM-MD5-'),
                        f'OPT line not CRAM-MD5: {opt!r}')
        hex_part = opt.split('CRAM-MD5-', 1)[1].strip()
        # Must be valid, non-trivial hex -- a real caller hex-decodes
        # this to build the HMAC challenge bytes.
        self.assertGreaterEqual(len(hex_part), 32)
        bytes.fromhex(hex_part)  # raises ValueError if not valid hex

    def test_opt_line_sent_before_adr_line(self):
        from anetbbs.echomail.binkp_server import _handle_connection, CMD_NUL, CMD_ADR
        from anetbbs.models import EchomailNetwork

        reader = _FakeReader()
        writer = _FakeWriter()
        EchomailNetwork.query = _FakeQuery()
        try:
            asyncio.run(_handle_connection(reader, writer, '1:114/30', 'ANetBBS'))
        finally:
            del EchomailNetwork.query

        commands = _decode_sent_commands(writer.sent)
        opt_idx = next(i for i, (cmd, text) in enumerate(commands)
                       if cmd == CMD_NUL and text.upper().startswith('OPT '))
        adr_idx = next(i for i, (cmd, text) in enumerate(commands) if cmd == CMD_ADR)
        self.assertLess(opt_idx, adr_idx,
                        'OPT CRAM-MD5 challenge must be sent before M_ADR, '
                        'matching the outbound client convention in binkp.py')


if __name__ == '__main__':
    unittest.main()
