"""Regression test: the inbound BinkP listener's M_ADR announcement
never consulted EchomailNetwork.ftn_domain, only ever deriving the
qualified-address domain suffix from the network's (potentially long)
display `name` -- e.g. "ANotherNetwork" always truncated to "anothern"
here, even for a sysop who had already set ftn_domain='anet' via the
network admin form specifically to avoid that. The outbound side
(poller.py's BinkPClient(domain=...)) already preferred ftn_domain
correctly; this inbound path (the M_ADR line _handle_connection sends
right after connect, before auth) did not.

Reuses test_binkp_multi_hub_identity.py's harness/fakes, subclassing
_FakeEchomailNetwork locally (rather than editing the shared file) to
add the two fields this test needs that no existing test there uses:
is_active (required by the AKA-announcement lookup's own
filter_by(network_type='binkp', is_active=True)) and ftn_domain.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_binkp_multi_hub_identity import (
    _BinkpHandlerHarness, _FakeEchomailNetwork, _decode_sent_commands,
)
from anetbbs.echomail.binkp_server import CMD_ADR


class _ActiveNetwork(_FakeEchomailNetwork):
    def __init__(self, *args, is_active=True, ftn_domain=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_active = is_active
        self.ftn_domain = ftn_domain


class InboundFtnDomainTests(unittest.TestCase, _BinkpHandlerHarness):

    def _sent_adr_line(self, writer):
        commands = _decode_sent_commands(writer.sent)
        adr_lines = [text for cmd, text in commands if cmd == CMD_ADR]
        self.assertEqual(len(adr_lines), 1, 'expected exactly one M_ADR sent')
        return adr_lines[0]

    def test_ftn_domain_override_used_when_set(self):
        network = _ActiveNetwork(
            id=5, hub_address='1200:1/1', our_address='1200:1/1',
            binkp_password='secret', name='ANotherNetwork',
            ftn_domain='anet')
        writer, _ = self._run(networks=[network], nodes=[],
                              remote_addr='1200:1/1', remote_pwd='secret',
                              our_address='1200:1/1')
        adr = self._sent_adr_line(writer)
        self.assertIn('@anet', adr)
        self.assertNotIn('@anothern', adr)

    def test_falls_back_to_name_when_ftn_domain_blank(self):
        network = _ActiveNetwork(
            id=5, hub_address='1200:1/1', our_address='1200:1/1',
            binkp_password='secret', name='ANotherNetwork',
            ftn_domain=None)
        writer, _ = self._run(networks=[network], nodes=[],
                              remote_addr='1200:1/1', remote_pwd='secret',
                              our_address='1200:1/1')
        adr = self._sent_adr_line(writer)
        self.assertIn('@anothern', adr)


if __name__ == '__main__':
    unittest.main()
