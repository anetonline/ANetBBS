"""Regression tests for a real gap found in this round's security/
performance audit: QWK's own network download (both the HTTP path in
QWKClient.poll() and the FTP/QNET-FTP path in QWKClient._ftp_download())
buffered the hub's response fully into memory with NO size limit at
all -- unlike BinkP's own inbound file receive (binkp.py/binkp_server.py's
MAX_INBOUND_FILE_SIZE), which has had exactly this kind of cap for a
while. A compromised, misconfigured, or simply misbehaving QWK hub could
return an arbitrarily large response and exhaust the poller process's
memory.

Fixed with two small helpers in anetbbs/echomail/qwk.py:
  - _read_capped(fileobj, max_size, source_desc): bounded chunked read
    for the HTTP (urlopen) path.
  - _capped_ftp_writer(buf, max_size, source_desc): a bounded write
    callback for ftplib's retrbinary(), which has no size-limit
    parameter of its own.
Both raise ValueError (surfaced to the caller as a clean
ConnectionError from QWKClient.poll(), same as any other download
failure) the moment the running total crosses MAX_QWK_PACKET_SIZE,
rather than after buffering an unbounded response in full.
"""
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anetbbs.echomail import qwk as qwk_mod


class ReadCappedTests(unittest.TestCase):
    def test_reads_data_under_cap_fine(self):
        data = b'x' * 1000
        result = qwk_mod._read_capped(io.BytesIO(data), 10_000, 'test-source')
        self.assertEqual(result, data)

    def test_raises_once_over_cap(self):
        data = b'x' * 10_000
        with self.assertRaises(ValueError) as ctx:
            qwk_mod._read_capped(io.BytesIO(data), 5_000, 'test-source')
        self.assertIn('exceeds', str(ctx.exception))

    def test_does_not_buffer_past_the_cap(self):
        # A source that would keep yielding data forever must still stop
        # (raise) shortly after crossing the cap, not only after a final
        # unbounded read() call -- confirms the size check happens
        # per-chunk while reading, not after the fact.
        class _InfiniteStream:
            def read(self, n):
                return b'a' * n

        with self.assertRaises(ValueError):
            qwk_mod._read_capped(_InfiniteStream(), 200_000, 'infinite-source')


class CappedFtpWriterTests(unittest.TestCase):
    def test_writes_under_cap_fine(self):
        buf = io.BytesIO()
        writer = qwk_mod._capped_ftp_writer(buf, 10_000, 'test-host')
        writer(b'x' * 1000)
        writer(b'y' * 1000)
        self.assertEqual(buf.getvalue(), b'x' * 1000 + b'y' * 1000)

    def test_raises_once_over_cap(self):
        buf = io.BytesIO()
        writer = qwk_mod._capped_ftp_writer(buf, 5_000, 'test-host')
        writer(b'x' * 4000)
        with self.assertRaises(ValueError) as ctx:
            writer(b'y' * 4000)
        self.assertIn('exceeds', str(ctx.exception))


class QwkClientHttpDownloadCapIntegrationTests(unittest.TestCase):
    """End-to-end through QWKClient.poll()'s HTTP path -- confirms an
    oversized/misbehaving hub response is rejected as a clean
    ConnectionError instead of being buffered without limit."""

    def test_oversized_http_download_is_refused(self):
        client = qwk_mod.QWKClient(host='qwktest.example', port=80,
                                   username='u', password='p')

        class _FakeResp:
            def read(self, n):
                # Keep yielding data well past any small test cap --
                # simulates a hub that never stops sending.
                return b'\x00' * n

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        orig_cap = qwk_mod.MAX_QWK_PACKET_SIZE
        qwk_mod.MAX_QWK_PACKET_SIZE = 100_000
        import urllib.request as _ur
        orig_urlopen = _ur.urlopen
        _ur.urlopen = lambda *a, **kw: _FakeResp()
        try:
            with self.assertRaises(ConnectionError) as ctx:
                client.poll(data_dir='/tmp')
            self.assertIn('exceeds', str(ctx.exception))
        finally:
            _ur.urlopen = orig_urlopen
            qwk_mod.MAX_QWK_PACKET_SIZE = orig_cap

    def test_normal_sized_http_download_is_not_affected(self):
        client = qwk_mod.QWKClient(host='qwktest.example', port=80,
                                   username='u', password='p')
        small_payload = b'not a real zip'

        class _FakeResp:
            def __init__(self):
                self._buf = io.BytesIO(small_payload)

            def read(self, n):
                return self._buf.read(n)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        import urllib.request as _ur
        orig_urlopen = _ur.urlopen
        _ur.urlopen = lambda *a, **kw: _FakeResp()
        try:
            # Not a real QWK zip, so it parses to zero messages -- this
            # only confirms the size cap itself doesn't reject a normal,
            # small download.
            result = client.poll(data_dir='/tmp')
            self.assertEqual(result['received'], [])
        finally:
            _ur.urlopen = orig_urlopen


if __name__ == '__main__':
    unittest.main()
