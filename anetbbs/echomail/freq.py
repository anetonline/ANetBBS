# anetbbs/echomail/freq.py
"""
WaZOO-style file requests ("FREQ") over BinkP -- FTS-0006's "WaZOO File
Requests" section, verified against the real, published FTSC document
(ftsc.org/docs/fts-0006.002), not guessed from memory or from binkp's
own casual "M_GET = request specific file" description (M_GET is
actually only a resume/re-offset mechanism per the binkp spec itself,
ritlabs.com/binkp -- a completely different, older mechanism handles
genuine FREQ).

A WaZOO file request is an ordinary file transfer: the requester sends
a small text file named `NNNNnnnn.REQ` (the ADDRESSEE's net/node as
two 4-digit hex numbers -- e.g. requesting something from 12/2 names
the file 000C0002.REQ) containing one wanted-file line per line:

    <filename>[<space>!<password>][<space><+/-><unixtime>]

`filename` may use `*`/`?` wildcards and must not contain a path.
`!password` is an optional area-level password. `+time`/`-time` asks
for only files newer/older-or-equal than a given UNIX timestamp (an
"update" FREQ) -- honored here, but a requester that omits it just
gets every match unconditionally, same as any other FREQ.

Design choice made here, worth knowing about: unlike a same-session
Multiple-Batch-mode reply (the fancier variant FTS-0006/binkp/1.1
allow), ANetBBS answers a FREQ by queuing matched files as normal
HatchQueue rows -- the SAME delivery mechanism TIC-push already uses
-- rather than replying within the same TCP connection. The requester
gets their file(s) on their *next* poll/connection, not instantly.
This reuses the existing, already-correct, already-tested hatch/TIC
delivery path instead of building a new same-session batch-restart
state machine (real complexity/fragility for a feature with few, if
any, real modern peers to test interop against), and matches FTS-0006's
own explicit statement that the answering side has no obligation to
respond immediately: "There is no accounting or responsibilities on
the part of the remote system."

Only FileArea rows with freq_enabled=True are ever matched -- opt-in,
same posture as every other file-area access-control gate in this
codebase.
"""
import fnmatch
import re
from datetime import datetime

from .routing import parse_address
from .areafix import _passwords_match
from ..models import db, FileArea, FileUpload, HatchQueue

# Per FTS-0006: "netnode.REQ", the addressee's net/node as two 4-digit
# hex numbers.
_REQ_FILENAME_RE = re.compile(r'^([0-9A-Fa-f]{4})([0-9A-Fa-f]{4})\.req$',
                              re.IGNORECASE)

# One action line: filename [!password] [<+/-><time>]. Password is
# "6 or fewer characters" per spec; not enforced here (rejecting an
# odd password just means it never matches, which is safe).
_REQ_LINE_RE = re.compile(
    r'^\s*(?P<filename>\S+)'
    r'(?:\s+!(?P<password>\S+))?'
    r'(?:\s+(?P<sign>[+-])(?P<time>\d+))?\s*$')

# Real-world safety bound, not part of the spec: an unqualified wildcard
# line (e.g. a bare "*") in a freq-enabled area could otherwise match
# every file ever uploaded there. Caps how many HatchQueue rows one
# inbound .REQ can create in total, across every line in the file.
MAX_FILES_PER_REQUEST = 100


def req_filename_for_address(ftn_address):
    """Build the NNNNnnnn.REQ filename addressed to `ftn_address` (the
    system this .REQ is being SENT to -- see module docstring). Returns
    None if the address doesn't parse."""
    parsed = parse_address(ftn_address)
    if parsed is None:
        return None
    _zone, net, node, _point = parsed
    return f'{net & 0xFFFF:04X}{node & 0xFFFF:04X}.req'


def is_req_filename(filename):
    """True if `filename` matches the WaZOO NNNNnnnn.REQ convention."""
    return bool(_REQ_FILENAME_RE.match((filename or '').strip()))


def parse_req_lines(content):
    """Parse a .REQ file's raw bytes into a list of dicts, one per
    recognized action line: {'filename', 'password', 'sign', 'time'}.
    `password`/`sign`/`time` are None when not present on that line.
    Blank lines and lines that don't match the action-line grammar are
    silently skipped (a malformed .REQ shouldn't crash the session --
    same defensive posture as this codebase's other inbound-file
    parsers, e.g. _parse_ftn_packet's per-message try/except)."""
    text = content.decode('latin-1', errors='replace') if isinstance(content, bytes) else content
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _REQ_LINE_RE.match(line)
        if not m:
            continue
        out.append({
            'filename': m.group('filename'),
            'password': m.group('password'),
            'sign': m.group('sign'),
            'time': int(m.group('time')) if m.group('time') else None,
        })
    return out


def build_req_content(wanted):
    """Build a .REQ file's text content (bytes) from `wanted`, a list of
    either plain filename strings or (filename, password) tuples. Real
    inverse of parse_req_lines() -- used when ANetBBS itself sends a
    FREQ to a peer."""
    lines = []
    for item in wanted:
        if isinstance(item, (tuple, list)):
            filename, password = item[0], (item[1] if len(item) > 1 else None)
        else:
            filename, password = item, None
        line = filename
        if password:
            line += f' !{password}'
        lines.append(line)
    return ('\r\n'.join(lines) + '\r\n').encode('latin-1', errors='replace')


def _line_matches_upload(req_line, upload):
    """Does one parsed .REQ line match this FileUpload row? Wildcards
    per fnmatch (`*`/`?`), case-insensitive (matches the DOS-8.3-era
    convention this whole mechanism originates from). Tries both the
    original (human-facing) filename and the stored/sanitized one,
    since a remote requester only ever knows the former but the area's
    own on-disk name is the latter."""
    pattern = req_line['filename'].lower()
    candidates = {(upload.original_filename or '').lower(),
                  (upload.filename or '').lower()}
    if not any(fnmatch.fnmatch(c, pattern) for c in candidates if c):
        return False
    sign, when = req_line['sign'], req_line['time']
    if sign and when is not None:
        mtime = upload.created_at
        if mtime is None:
            return True
        upload_ts = mtime.replace(tzinfo=None)
        cutoff = datetime.utcfromtimestamp(when)
        if sign == '+' and not (upload_ts > cutoff):
            return False
        if sign == '-' and not (upload_ts <= cutoff):
            return False
    return True


def process_inbound_req(content, requester_address, our_address=''):
    """Parse an inbound .REQ file and queue matching files as HatchQueue
    rows addressed to `requester_address`. Returns the list of newly
    created HatchQueue ids (empty if nothing matched, the request was
    empty, or no freq-enabled area had a match).

    Deliberately does NOT commit here when the caller might want to
    batch this with other session bookkeeping -- but since this is the
    only DB write in the common inbound-file-received path that calls
    it, it commits its own work to keep the queued rows durable even if
    something later in that same session raises. Matches
    hatch_local_file()'s own commit-immediately behavior in tic.py.
    """
    req_lines = parse_req_lines(content)
    if not req_lines:
        return []

    areas = FileArea.query.filter_by(freq_enabled=True, is_active=True).all()
    queued_rows = []
    seen_upload_ids = set()
    for req_line in req_lines:
        if len(queued_rows) >= MAX_FILES_PER_REQUEST:
            break
        for area in areas:
            # Real gap found in a security/performance audit: this
            # compared the attacker-supplied FREQ password with plain
            # != -- a timing side-channel letting an attacker who can
            # time repeated FREQ attempts statistically recover a
            # password-protected area's password byte-by-byte. Same
            # bug class already fixed for BinkP's own M_PWD/CRAM-MD5
            # check and this exact netmail-based robot-auth module's
            # own Areafix password (see areafix.py's _passwords_match,
            # reused here rather than duplicated).
            if area.freq_password and not _passwords_match(
                    area.freq_password, req_line['password'] or ''):
                continue
            uploads = (FileUpload.query
                      .filter_by(file_area_id=area.id)
                      .order_by(FileUpload.created_at.desc())
                      .limit(500).all())
            for upload in uploads:
                if len(queued_rows) >= MAX_FILES_PER_REQUEST:
                    break
                if upload.id in seen_upload_ids:
                    continue
                if not _line_matches_upload(req_line, upload):
                    continue
                import os as _os
                if not upload.file_path or not _os.path.isfile(upload.file_path):
                    continue
                from .tic import _crc32_file
                row = HatchQueue(
                    file_area_id=area.id,
                    peer_address=requester_address,
                    binary_path=upload.file_path,
                    filename=upload.original_filename or upload.filename,
                    description=upload.description or '',
                    crc32=_crc32_file(upload.file_path),
                    size_bytes=upload.file_size or _os.path.getsize(upload.file_path),
                    seenby='[]',
                    path='[]',
                    status='pending',
                )
                db.session.add(row)
                seen_upload_ids.add(upload.id)
                queued_rows.append(row)
    if not queued_rows:
        return []
    db.session.commit()
    return [row.id for row in queued_rows]
