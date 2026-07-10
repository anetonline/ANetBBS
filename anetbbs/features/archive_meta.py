"""
Extract a human-readable description from an uploaded archive.

Pulls FILE_ID.DIZ if present (the BBS-standard short description, max
~10 lines × 45 chars), then falls back to README.* / DESCRIPT.ION /
.txt files in the root of the archive.

Supports zip, tar (gzipped, bzipped, xz), 7z, rar, lha/lzh — but only
when the corresponding library or external tool is available. Returns
an empty string if nothing useful was found, never raises.
"""
import logging
import os
import tarfile
import zipfile
from collections import namedtuple

logger = logging.getLogger(__name__)

# Mirrors virus_scan.py's ScanResult shape.
ArchiveTestResult = namedtuple('ArchiveTestResult', ['ok', 'message'])

# Filenames searched in priority order. Case-insensitive.
DESCRIPTION_CANDIDATES = (
    'FILE_ID.DIZ',
    'file_id.diz',
    'FILEID.DIZ',
    'README.MD',
    'README.md',
    'README.TXT',
    'README.txt',
    'README',
    'readme.txt',
    'readme.md',
    'readme',
    'DESCRIPT.ION',
    'descript.ion',
    'INFO.TXT',
    'info.txt',
)

# Cap the extracted text — descriptions over a few KB aren't really
# descriptions, they're docs. The BBS file gallery wants a short blurb.
MAX_DESCRIPTION_BYTES = 4096


def extract_archive_description(path):
    """Return a description string pulled from the archive at *path*.

    Returns '' if the file isn't a recognized archive, or if no
    description-style file is present, or on any extraction error.
    """
    if not os.path.isfile(path):
        return ''
    name_lower = path.lower()
    try:
        if zipfile.is_zipfile(path):
            return _extract_from_zip(path)
        if tarfile.is_tarfile(path) or name_lower.endswith(
                ('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2',
                 '.tar.xz', '.txz')):
            return _extract_from_tar(path)
        if name_lower.endswith('.7z'):
            return _extract_from_7z(path)
        if name_lower.endswith('.rar'):
            return _extract_from_rar(path)
        if name_lower.endswith(('.lzh', '.lha')):
            return _extract_from_lha(path)
    except Exception:
        logger.exception('archive description extraction failed for %s', path)
    return ''


def test_archive_integrity(path):
    """Test whether the archive at *path* is internally consistent.

    Returns an ArchiveTestResult. `ok=True` covers three distinct cases a
    caller should treat the same way (let the upload through): the
    archive tested clean, the format has no test API to run at all (lha),
    or the format's optional library isn't installed (7z/rar) -- fail
    open on anything we can't actually check, same philosophy as
    virus_scan.py ("better to allow than to silently swallow uploads on
    a misconfig"). Only a *confirmed* bad archive gets `ok=False`.
    """
    if not os.path.isfile(path):
        return ArchiveTestResult(True, 'file not found')
    name_lower = path.lower()
    try:
        if zipfile.is_zipfile(path):
            return _test_zip(path)
        if tarfile.is_tarfile(path) or name_lower.endswith(
                ('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2',
                 '.tar.xz', '.txz')):
            return _test_tar(path)
        if name_lower.endswith('.7z'):
            return _test_7z(path)
        if name_lower.endswith('.rar'):
            return _test_rar(path)
        if name_lower.endswith(('.lzh', '.lha')):
            return ArchiveTestResult(True, 'not tested (lha has no test API)')
    except Exception as exc:
        logger.exception('archive integrity test crashed for %s', path)
        return ArchiveTestResult(True, f'test crashed, allowing through: {exc}')
    return ArchiveTestResult(True, 'not a recognized archive format')


def _test_zip(path):
    with zipfile.ZipFile(path, 'r') as zf:
        bad = zf.testzip()
    if bad is not None:
        return ArchiveTestResult(False, f'corrupt member: {bad}')
    return ArchiveTestResult(True, 'clean')


def _test_tar(path):
    """tar has no CRC/testzip()-equivalent -- best-effort by reading
    every regular-file member through to completion."""
    with tarfile.open(path, 'r:*') as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            fh = tf.extractfile(member)
            if fh is None:
                continue
            try:
                while fh.read(65536):
                    pass
            except (tarfile.TarError, OSError) as exc:
                return ArchiveTestResult(False, f'corrupt member {member.name}: {exc}')
    return ArchiveTestResult(True, 'clean (best-effort read-through, tar has no CRC test)')


def _test_7z(path):
    try:
        import py7zr
    except ImportError:
        return ArchiveTestResult(True, 'not tested (py7zr not installed)')
    try:
        with py7zr.SevenZipFile(path, 'r') as zf:
            zf.test()
    except Exception as exc:
        return ArchiveTestResult(False, f'corrupt: {exc}')
    return ArchiveTestResult(True, 'clean')


def _test_rar(path):
    try:
        import rarfile
    except ImportError:
        return ArchiveTestResult(True, 'not tested (rarfile not installed)')
    with rarfile.RarFile(path) as rf:
        bad = rf.testrar()
    if bad is not None:
        return ArchiveTestResult(False, f'corrupt member: {bad}')
    return ArchiveTestResult(True, 'clean')


# ---------------------------------------------------------------------------
# Format-specific extractors
# ---------------------------------------------------------------------------

def _extract_from_zip(path):
    with zipfile.ZipFile(path, 'r') as zf:
        names = zf.namelist()
        match = _find_match(names)
        if not match:
            return ''
        with zf.open(match) as fh:
            data = fh.read(MAX_DESCRIPTION_BYTES + 1)
    return _decode(data, match)


def _extract_from_tar(path):
    with tarfile.open(path, 'r:*') as tf:
        names = tf.getnames()
        match = _find_match(names)
        if not match:
            return ''
        member = tf.getmember(match)
        if not member.isfile():
            return ''
        fh = tf.extractfile(member)
        if fh is None:
            return ''
        data = fh.read(MAX_DESCRIPTION_BYTES + 1)
    return _decode(data, match)


def _extract_from_7z(path):
    try:
        import py7zr
    except ImportError:
        logger.debug('py7zr not installed; skipping 7z description extract')
        return ''
    with py7zr.SevenZipFile(path, 'r') as zf:
        names = zf.getnames()
        match = _find_match(names)
        if not match:
            return ''
        extracted = zf.read(targets=[match])
        bio = extracted.get(match)
        if bio is None:
            return ''
        data = bio.read(MAX_DESCRIPTION_BYTES + 1)
    return _decode(data, match)


def _extract_from_rar(path):
    try:
        import rarfile
    except ImportError:
        logger.debug('rarfile not installed; skipping rar description extract')
        return ''
    with rarfile.RarFile(path) as rf:
        names = rf.namelist()
        match = _find_match(names)
        if not match:
            return ''
        with rf.open(match) as fh:
            data = fh.read(MAX_DESCRIPTION_BYTES + 1)
    return _decode(data, match)


def _extract_from_lha(path):
    """LHA (.lzh) needs the external 'lha' tool — call it via subprocess
    rather than pulling in a hard dependency."""
    import shutil
    import subprocess
    if not shutil.which('lha'):
        return ''
    try:
        listing = subprocess.run(['lha', 'lq', path],
                                  capture_output=True, text=True, timeout=5)
        names = [l.strip().split()[-1] for l in listing.stdout.splitlines() if l.strip()]
        match = _find_match(names)
        if not match:
            return ''
        result = subprocess.run(['lha', 'pq', path, match],
                                  capture_output=True, timeout=5)
        return _decode(result.stdout[:MAX_DESCRIPTION_BYTES + 1], match)
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_match(names):
    """Find the highest-priority candidate file among *names*.

    *names* is the list of filenames inside the archive (may include
    directory paths). We match the basename, case-insensitive, and prefer
    files at the archive root over deeply nested ones.
    """
    by_priority = {n.lower(): i for i, n in enumerate(DESCRIPTION_CANDIDATES)}

    best = None
    best_pri = len(DESCRIPTION_CANDIDATES) + 1
    best_depth = 999

    for n in names:
        base = os.path.basename(n).lower()
        if base not in by_priority:
            continue
        pri = by_priority[base]
        depth = n.count('/') + n.count('\\')
        # Prefer lower priority number first, then shallower depth
        if (pri < best_pri) or (pri == best_pri and depth < best_depth):
            best = n
            best_pri = pri
            best_depth = depth
    return best


def _decode(data, source_name):
    """Decode archive contents (CP437 by default — BBS files predate UTF-8;
    fall back to UTF-8/latin-1 if it's clearly not CP437). Trim and cap."""
    if not data:
        return ''
    # Newer files (README.md from a github archive) are usually utf-8
    if source_name.lower().endswith('.md'):
        try:
            text = data.decode('utf-8', errors='replace')
        except Exception:
            text = data.decode('cp437', errors='replace')
    else:
        try:
            text = data.decode('cp437', errors='replace')
        except Exception:
            text = data.decode('latin-1', errors='replace')

    # Strip trailing nulls / whitespace
    text = text.rstrip('\x00 \r\n\t')
    if len(text) > MAX_DESCRIPTION_BYTES:
        text = text[:MAX_DESCRIPTION_BYTES].rstrip()
    return text
