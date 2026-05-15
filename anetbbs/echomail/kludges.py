# anetbbs/echomail/kludges.py
"""
FidoNet kludge line parsing and serialization.

Kludges are control lines embedded in netmail/echomail message bodies, each
prefixed with a Ctrl-A character (0x01 / SOH). They carry routing, threading,
encoding and other metadata that's invisible to the user but critical for
correct FTN message handling.

Common kludges (FTS-0001, FTS-4001, FTS-5005, FTN-0009):
    @MSGID  zone:net/node[.point]@domain serialnum
    @REPLY  zone:net/node[.point]@domain serialnum
    @INTL   destaddr origaddr            (zone-zone destination)
    @CHRS   charset level                ('CP437 2', 'UTF-8 4', 'LATIN-1 2')
    @CHARSET utf-8                       (legacy)
    @PID    Software-name version
    @TID    Tosser-name version
    @TZUTC  +0500                        (sender's TZ)
    @FMPT   <pointnum>                   (FROM point)
    @TOPT   <pointnum>                   (TO point)
    @ENC    encoding kludge

This module:
  - parse_message(body) splits a message body into (kludges, body, tear,
    origin, seenby, path) — each as plain strings/lists.
  - build_message(...) takes back-end fields and assembles a wire-format
    message ready for packing into a packet.
"""
import json
import re

SOH = '\x01'

_TEAR_RE = re.compile(r'^---', re.MULTILINE)
_ORIGIN_RE = re.compile(r'^\s*\* Origin:.*$', re.MULTILINE)
_SEENBY_RE = re.compile(r'^SEEN-BY:\s*(.+)$', re.MULTILINE)
# PATH lines (echomail) are kludges in some implementations but appear
# uncrushed in others — handle both cases.
_KLUDGE_PATH_RE = re.compile(r'^\x01PATH:\s*(.+)$', re.MULTILINE)


def parse_message(body):
    """Split a raw FTN message body into structured fields.

    Returns a dict with keys:
        kludges:   list of kludge strings (without the leading SOH)
        body:      message text with kludges/tear/origin/seenby/path stripped
        tear:      tear line (single string starting with "---") or ''
        origin:    origin line (one beginning with "* Origin:") or ''
        seenby:    list of SEEN-BY entries (each a string of comma'd nodes)
        path:      list of PATH entries
    """
    if not body:
        return {'kludges': [], 'body': '', 'tear': '',
                'origin': '', 'seenby': [], 'path': []}

    kludges = []
    seenby = []
    path = []
    body_lines = []

    for line in body.splitlines():
        if not line:
            body_lines.append(line)
            continue
        if line.startswith(SOH):
            kl = line[1:]
            # PATH kludge gets pulled out separately
            if kl.startswith('PATH:'):
                path.append(kl[5:].strip())
            else:
                kludges.append(kl)
            continue
        if line.startswith('SEEN-BY:'):
            seenby.append(line[len('SEEN-BY:'):].strip())
            continue
        body_lines.append(line)

    # Tear and origin: keep them in body for display, but expose the lines
    body_text = '\n'.join(body_lines).rstrip()
    tear = ''
    origin = ''
    tm = _TEAR_RE.search(body_text)
    if tm:
        # Get the entire tear line
        end = body_text.find('\n', tm.start())
        tear = body_text[tm.start(): end if end >= 0 else None].strip()
    om = _ORIGIN_RE.search(body_text)
    if om:
        origin = om.group(0).strip()

    return {
        'kludges': kludges,
        'body': body_text,
        'tear': tear,
        'origin': origin,
        'seenby': seenby,
        'path': path,
    }


def build_message(body, kludges=None, tear=None, origin=None,
                  seenby=None, path=None):
    """Assemble a complete FTN message body from structured fields.

    Layout (FTS-0001 §4) — lines are terminated with **bare CR**.
    Compliant writers must NOT emit LF; strict readers (notably Mystic
    AreaFix) split on \r only and treat a stray \n as junk on the next
    line, breaking ^A kludge detection and password-line parsing.

        <kludge lines, each ^A prefixed>\r
        \r                                  (blank line separator)
        <body text>\r
        --- <tearline>\r
         * Origin: ... (addr)\r
        SEEN-BY: <list>\r
        ^APATH: <list>\r
    """
    parts = []
    for k in (kludges or []):
        parts.append(SOH + k.lstrip(SOH))
    if parts:
        parts.append('')   # blank line separator before body
    if body:
        parts.append(body.rstrip())
    if tear:
        parts.append(tear if tear.startswith('---') else '--- ' + tear)
    if origin:
        parts.append(origin if origin.startswith('* Origin:') else ' * Origin: ' + origin)
    for s in (seenby or []):
        parts.append('SEEN-BY: ' + s)
    for p in (path or []):
        parts.append(SOH + 'PATH: ' + p)
    return '\r'.join(parts) + '\r'


def kludges_to_json(kludges):
    """Serialize a list of kludge strings to JSON for DB storage."""
    return json.dumps(list(kludges or []))


def kludges_from_json(s):
    """Inverse of kludges_to_json — returns [] on missing/bad input."""
    if not s:
        return []
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def find_kludge(kludges, name):
    """Return the value (everything after the kludge name + space) of the
    first kludge whose name matches (case-insensitive). Returns '' if absent.
    Examples: find_kludge(['MSGID 1:234/5 abc'], 'MSGID') -> '1:234/5 abc'."""
    name = name.upper()
    for k in (kludges or []):
        if k.upper().startswith(name):
            rest = k[len(name):]
            if not rest or rest[0] in (' ', ':', '\t'):
                return rest.lstrip(' :\t').rstrip()
    return ''


def make_msgid(our_address, serial_hex=None):
    """Generate an MSGID kludge value (FTS-0009).

    `serial_hex` should be a unique-per-system hex serial — typically the
    epoch seconds in hex, optionally padded. If omitted we use the current
    timestamp."""
    import time
    if serial_hex is None:
        serial_hex = format(int(time.time()), '08x')
    return f'{our_address} {serial_hex}'
