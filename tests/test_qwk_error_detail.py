"""Regression test: a QWK download failure with an empty str(exc) (e.g.
a bare socket timeout) used to produce a completely uninformative
"QWK: failed to download packet:" error with nothing after the colon --
live-caught trying to diagnose a real poll failure. Fixed to always
include the exception type name too.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class QwkErrorDetailTests(unittest.TestCase):
    def test_empty_exception_message_still_shows_exception_type(self):
        from anetbbs.echomail.qwk import QWKClient

        client = QWKClient(host='bbs.example.com', port=21, username='X',
                           password='Y', packet_id='X')

        with patch.object(client, '_resolve_download_url',
                          return_value='ftp://bbs.example.com/X.qwk'), \
             patch.object(client, '_ftp_download',
                          side_effect=TimeoutError()):
            with self.assertRaises(ConnectionError) as ctx:
                client.poll()

        msg = str(ctx.exception)
        self.assertIn('TimeoutError', msg)
        self.assertNotIn('packet: \n', msg)
        self.assertFalse(msg.rstrip().endswith('packet:'))

    def test_non_empty_exception_message_still_included(self):
        from anetbbs.echomail.qwk import QWKClient

        client = QWKClient(host='bbs.example.com', port=21, username='X',
                           password='Y', packet_id='X')

        with patch.object(client, '_resolve_download_url',
                          return_value='ftp://bbs.example.com/X.qwk'), \
             patch.object(client, '_ftp_download',
                          side_effect=OSError('Connection refused')):
            with self.assertRaises(ConnectionError) as ctx:
                client.poll()

        msg = str(ctx.exception)
        self.assertIn('OSError', msg)
        self.assertIn('Connection refused', msg)


if __name__ == '__main__':
    unittest.main()
