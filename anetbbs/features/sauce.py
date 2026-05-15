# anetbbs/features/sauce.py
"""
SAUCE metadata reader.

SAUCE = Standard Architecture for Universal Comment Extensions — the
trailing 128-byte block ANSI art tools (Pablo, Moebius, ACiDDraw, ...)
append to .ans files to record author, group, dimensions, etc.

Layout:
    EOF byte 0x1A
    SAUCE record (128 bytes)
        ID         5 bytes  "SAUCE"
        Version    2 bytes  ASCII "00"
        Title     35 bytes  CP437
        Author    20 bytes  CP437
        Group     20 bytes  CP437
        Date       8 bytes  ASCII YYYYMMDD
        FileSize   4 bytes  little-endian u32
        DataType   1 byte
        FileType   1 byte
        TInfo1     2 bytes  (e.g. width)
        TInfo2     2 bytes  (e.g. height)
        TInfo3     2 bytes
        TInfo4     2 bytes
        Comments   1 byte   number of 64-byte comment lines
        TFlags     1 byte
        TInfoS    22 bytes
"""
import struct
from datetime import datetime


SAUCE_SIZE = 128
SAUCE_ID = b'SAUCE'


def parse(data):
    """Return a dict of SAUCE fields, or None if no SAUCE record found.

    `data` is the raw bytes of the file.
    """
    if not data or len(data) < SAUCE_SIZE:
        return None
    sauce = data[-SAUCE_SIZE:]
    if not sauce.startswith(SAUCE_ID):
        return None
    try:
        title = sauce[7:42].decode('cp437', errors='replace').rstrip()
        author = sauce[42:62].decode('cp437', errors='replace').rstrip()
        group = sauce[62:82].decode('cp437', errors='replace').rstrip()
        date_str = sauce[82:90].decode('ascii', errors='replace').strip()
        try:
            date = datetime.strptime(date_str, '%Y%m%d')
        except ValueError:
            date = None
        file_size = struct.unpack('<I', sauce[90:94])[0]
        datatype = sauce[94]
        filetype = sauce[95]
        tinfo1 = struct.unpack('<H', sauce[96:98])[0]
        tinfo2 = struct.unpack('<H', sauce[98:100])[0]
        comments = sauce[124]
    except Exception:
        return None
    return {
        'title': title or None,
        'author': author or None,
        'group': group or None,
        'date': date,
        'file_size': file_size,
        'datatype': datatype,
        'filetype': filetype,
        'width': tinfo1 or None,
        'height': tinfo2 or None,
        'comments_count': comments,
    }


# Datatype enum -> human label
DATATYPE_LABEL = {
    0: 'None',
    1: 'Character',
    2: 'Bitmap',
    3: 'Vector',
    4: 'Audio',
    5: 'BinaryText',
    6: 'XBin',
    7: 'Archive',
    8: 'Executable',
}
