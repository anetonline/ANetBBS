# anetbbs/features/virus_scan.py
"""
Optional ClamAV virus scan for uploads.

Set CLAMSCAN_PATH (default 'clamscan' on PATH) to enable. If the binary
is missing or the scan fails for transient reasons, files pass through
(better to allow than to silently swallow uploads on a misconfig).

Usage:
    from anetbbs.features.virus_scan import scan_path
    result = scan_path('/path/to/file')
    if result.infected:
        os.remove(...)
        return f'Rejected: {result.signature}'
"""
import logging
import os
import subprocess
from collections import namedtuple


logger = logging.getLogger(__name__)

ScanResult = namedtuple(
    'ScanResult',
    ['infected', 'signature', 'message', 'scanner_available'])


def scan_path(filepath, timeout=None):
    """Scan a file with the system clamscan (or whatever CLAMSCAN_PATH points to).

    timeout defaults to the sysop-configurable CLAMSCAN_TIMEOUT setting
    (Admin -> Settings, env var CLAMSCAN_TIMEOUT, default 60s) when
    called from inside a Flask app context; falls back to a plain 60 if
    called from somewhere without one (e.g. a standalone script).

    Returns a ScanResult with:
        infected           = True if a signature was matched
        signature          = name of the matched signature (or '')
        message            = human-readable status text
        scanner_available  = False if clamscan isn't installed
    """
    if timeout is None:
        try:
            from flask import current_app
            timeout = current_app.config.get('CLAMSCAN_TIMEOUT', 60)
        except Exception:
            timeout = 60

    scanner = os.environ.get('CLAMSCAN_PATH', 'clamscan')
    if not os.path.isfile(filepath):
        return ScanResult(False, '', 'file not found', True)

    try:
        proc = subprocess.run(
            [scanner, '--no-summary', '--infected', '--stdout', filepath],
            capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        # ClamAV not installed — skip silently. Sysop can enable later.
        return ScanResult(False, '', 'scanner not installed',
                          scanner_available=False)
    except subprocess.TimeoutExpired:
        logger.warning('clamscan timed out on %s', filepath)
        return ScanResult(False, '', 'scan timed out', True)
    except OSError as exc:
        logger.warning('clamscan failed: %s', exc)
        return ScanResult(False, '', f'scanner error: {exc}', True)

    # clamscan exit codes: 0 = clean, 1 = infected, 2 = error.
    if proc.returncode == 0:
        return ScanResult(False, '', 'clean', True)
    if proc.returncode == 1:
        # stdout has lines like "/path: SignatureName FOUND"
        sig = ''
        for line in (proc.stdout or '').splitlines():
            if 'FOUND' in line:
                # "/path: SigName FOUND"
                parts = line.rsplit(':', 1)
                if len(parts) == 2:
                    sig = parts[1].strip().rsplit(' FOUND', 1)[0].strip()
                break
        return ScanResult(True, sig, f'infected: {sig}', True)
    return ScanResult(False, '',
                      f'clamscan returned {proc.returncode}', True)
