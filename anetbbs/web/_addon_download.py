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


class AddonRefused(Exception):
    """The configured URL or pin is unacceptable (callers answer 400), or
    the download doesn't match its pin (502)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def pinned_download(url, url_config_key, config, user_agent):
    """Download an add-on with the integrity rules (audit L-3).

    The optional pin lives in <url_config_key minus _URL>_SHA256 (config,
    then environment). https is required unless a pin is set or the host
    is this machine. Returns the temp file path; raises AddonRefused or
    DownloadError."""
    import hashlib
    import hmac
    import re
    from urllib.parse import urlsplit
    pin_key = url_config_key.rsplit('_URL', 1)[0] + '_SHA256'
    pin = (config.get(pin_key) or os.environ.get(pin_key) or '').strip().lower()
    if pin and not re.fullmatch(r'[0-9a-f]{64}', pin):
        raise AddonRefused(f'{pin_key} must be a 64-character hex SHA-256')
    parts = urlsplit(url)
    if not (parts.scheme == 'https' or (parts.scheme == 'http' and (
            pin or parts.hostname in ('127.0.0.1', '::1', 'localhost')))):
        raise AddonRefused(f'{url_config_key} must be an https:// URL '
                           f'(or http with {pin_key} set)')
    tmp_path = download_zip_to_tempfile(url, user_agent=user_agent)
    if pin:
        h = hashlib.sha256()
        with open(tmp_path, 'rb') as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b''):
                h.update(chunk)
        if not hmac.compare_digest(h.hexdigest(), pin):
            os.remove(tmp_path)
            raise AddonRefused(f'download does not match {pin_key}', status=502)
    return tmp_path
