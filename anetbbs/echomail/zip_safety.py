# anetbbs/echomail/zip_safety.py
"""Shared safe-ZIP-extraction helper.

Real gap found in a security/performance audit: every ZIP extraction
site in this package read decompressed bytes via ZipFile.read() with
no check on the declared uncompressed size, letting a small, highly-
compressed archive (a "zip bomb") expand to gigabytes in memory the
instant it's read -- confirmed empirically: a ~51KB crafted all-zero
DEFLATE archive expands to 50MB+ in well under a second. The
worst-exposed call site (binkp_server.py's inbound file-receive path)
requires no authentication at all -- FTN convention deliberately
accepts an unrecognized peer for anonymous crashmail delivery, so this
is reachable by anyone who can open a TCP connection to the BinkP
port, not just an authenticated peer.

ZipInfo.file_size (the DECLARED uncompressed size, read from the local
file header) is available with zero decompression cost -- checking it
before calling zf.read() is what actually prevents the bomb from ever
being decompressed, unlike a check applied only after the fact.
"""

# Real FTS-0001 packets and file-echo bundles are small (typically well
# under a few MB even for a busy day's mail); these caps are generous
# headroom while still making a bomb's ~1000:1+ DEFLATE ratio
# impractical to exploit meaningfully against this daemon.
MAX_MEMBER_UNCOMPRESSED = 200 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED = 500 * 1024 * 1024


class ZipBombError(Exception):
    """Raised when a ZIP member's declared (or cumulative archive)
    uncompressed size exceeds the safety cap -- callers should catch
    this the same way they already catch zipfile.BadZipFile."""


def iter_safe_members(zf, max_member=MAX_MEMBER_UNCOMPRESSED,
                       max_total=MAX_ARCHIVE_UNCOMPRESSED):
    """Yield (ZipInfo, bytes) for every non-directory member in `zf`,
    refusing -- via ZipBombError, BEFORE decompressing -- any member
    whose declared uncompressed size alone would push either the
    per-member or the whole-archive cumulative declared total over its
    cap. Drop-in replacement for `for info in zf.infolist(): ...
    zf.read(info)`.
    """
    total = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        if info.file_size > max_member:
            raise ZipBombError(
                f'{info.filename!r} declares {info.file_size} bytes '
                f'uncompressed (per-member cap {max_member}) -- refusing '
                'to extract')
        total += info.file_size
        if total > max_total:
            raise ZipBombError(
                f'cumulative declared uncompressed size ({total} bytes) '
                f'exceeds archive cap ({max_total}) at member '
                f'{info.filename!r} -- refusing to extract remaining '
                'members')
        yield info, zf.read(info)
