"""Shared "download a zip from a configured URL" helper for admin-
triggered add-on installers -- anetbbs/web/ansi_editor.py's TheDraw
font pack installer and anetbbs/web/addons.py's generalized Add-ons
page both call this instead of each keeping their own copy of the
same streaming-download-with-size-cap logic.
"""
import os
import tempfile

import requests


class DownloadError(Exception):
    """A download-time failure -- callers map this to a 502 (unreachable
    host, non-2xx status, a network-level error, or the size cap)."""


def download_zip_to_tempfile(url, max_bytes=100 * 1024 * 1024,
                              user_agent='ANetBBS/addon-installer'):
    """Streams `url` to a temp file, never buffering the whole response
    in memory, capped at max_bytes (a generous sanity limit -- this is
    meant for a multi-MB add-on package, not an open-ended fetch).
    Returns the temp file path; the caller owns cleanup (os.remove)
    once done with it. Raises DownloadError on any network failure,
    non-2xx status, or if the cap is exceeded (the partial temp file is
    removed first)."""
    try:
        resp = requests.get(url, timeout=(10, 120), stream=True,
                            headers={'User-Agent': user_agent})
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise DownloadError(f'download failed: {exc.__class__.__name__}') from exc

    fd, tmp_path = tempfile.mkstemp(suffix='.zip')
    try:
        total = 0
        with os.fdopen(fd, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=262144):
                total += len(chunk)
                if total > max_bytes:
                    raise DownloadError('download exceeded the size limit')
                f.write(chunk)
    except DownloadError:
        os.remove(tmp_path)
        raise
    return tmp_path
