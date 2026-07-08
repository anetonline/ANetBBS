"""Regression test for a live-caught BinkP interop bug with real binkd.

Jerry relayed a real session transcript from Firehawke: every single
connect attempt to a real binkd hub (binkp.pharcyde.org) failed with
`BSY: Secure AKA 1:114/30@fidonet busy` immediately after ANetBBS sent
its password response -- regardless of whether the configured password
was correct (confirmed not blank/wrong by the peer sysop).

Root cause, traced into binkd's own source (protocol.c, ADR() handler):
`_handshake()` sent our own address TWICE in one M_ADR line -- once
qualified (`addr@domain`), once bare (`addr`) -- as a "cover whichever
form the peer keys on" compat measure. binkd's ADR() handler calls
bsy_add() (its busy-lock acquire function) once per space-separated
TOKEN in the line, with no de-duplication. For a "secure" (password-
protected) link, the FIRST token successfully acquires binkd's busy
lock for that AKA; the SECOND token -- the same numeric address, just
unqualified -- immediately fails to acquire the same lock a split
second later in the same loop, and binkd sends `Secure AKA <addr>
busy` and drops the session BEFORE password verification ever runs.

Fixed by sending only ONE address form per M_ADR line. A spec-
compliant peer (including real binkd, confirmed via its own
parse_ftnaddress()) parses the domain-qualified form fine on its own,
so there's no need to duplicate it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class BinkPSingleAdrFormTests(unittest.TestCase):
    def _run_handshake_capture_adr(self, domain):
        """Drives _handshake() with _send_cmd/_recv_frame_logged
        monkeypatched (no real socket needed) -- captures every command
        we send, and ends the loop immediately after ADR by handing back
        a canned CMD_OK frame."""
        from anetbbs.echomail.binkp import BinkPClient, CMD_OK

        client = BinkPClient(host='x', port=1, our_address='1:114/30',
                             hub_address='1:114/0', password='secret',
                             domain=domain)

        sent = []

        def _fake_send_cmd(cmd, text=''):
            sent.append((cmd, text))

        recv_calls = {'n': 0}

        def _fake_recv_frame_logged():
            recv_calls['n'] += 1
            # First call: pretend we got the remote's OK immediately --
            # we only care about what WE sent, not a full simulated peer.
            return True, bytes([CMD_OK])

        client._send_cmd = _fake_send_cmd
        client._recv_frame_logged = _fake_recv_frame_logged

        client._handshake()
        return sent

    def test_qualified_address_sent_only_once_not_duplicated(self):
        from anetbbs.echomail.binkp import CMD_ADR
        sent = self._run_handshake_capture_adr(domain='fidonet')

        adr_sends = [text for cmd, text in sent if cmd == CMD_ADR]
        self.assertEqual(len(adr_sends), 1, 'expected exactly one M_ADR command')

        # THE actual regression check: the address must appear as a
        # SINGLE token, not space-separated duplicate tokens for the
        # same underlying address (the exact pattern that made real
        # binkd self-collide on its own busy-lock).
        tokens = adr_sends[0].split()
        self.assertEqual(len(tokens), 1,
                         f'M_ADR must carry exactly one address token, got: {adr_sends[0]!r}')
        self.assertEqual(tokens[0], '1:114/30@fidonet')

    def test_bare_address_when_no_domain_configured(self):
        from anetbbs.echomail.binkp import CMD_ADR
        sent = self._run_handshake_capture_adr(domain=None)

        adr_sends = [text for cmd, text in sent if cmd == CMD_ADR]
        self.assertEqual(len(adr_sends), 1)
        tokens = adr_sends[0].split()
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0], '1:114/30')


if __name__ == '__main__':
    unittest.main()
