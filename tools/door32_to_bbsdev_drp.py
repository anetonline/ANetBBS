#!/usr/bin/env python3
"""
Converts a DOOR32.SYS drop file to BBSDEV.DRP (RealDeuce's newer drop
file spec, https://github.com/RealDeuce/bbsdev.drp) -- so a door that
only understands BBSDEV.DRP (RDQ3 and ANetCHESS both gained a real
reader for it) can be launched from a BBS that only ever writes
DOOR32.SYS, without that BBS needing to know the new format exists.

Deliberately self-contained: no `anetbbs` package import, no third-
party dependency, nothing beyond the Python 3 standard library --
built to be copied as a single file to a DIFFERENT box (e.g. a
Synchronet install, which writes DOOR32.SYS natively) and run there
with whatever system python3 is already installed, not to require an
ANetBBS checkout alongside it.

Field mapping is intentionally conservative where DOOR32.SYS simply
doesn't carry a concept BBSDEV.DRP wants:
  - Screen width/height: DOOR32.SYS has no such fields; 80x24 is used.
  - CTerm version / encoding-detection / language: not detectable from
    DOOR32.SYS; encoding is set to IBM437 (this whole BBS-door
    ecosystem's universal convention) and language to en-US.
  - Board name (line 15) vs BBS software name+version (line 14):
    DOOR32.SYS conflates these into one field; that value is used for
    the software-name line, and --board-name (falling back to the
    same value) fills the board-name line.
  - Logoff deadline (line 11): DOOR32.SYS only carries a MINUTES-
    REMAINING duration, not an absolute deadline. Rather than leaving
    this blank (spec-legal, but throws away real information), this
    converter computes now + minutes-remaining and emits it as the
    RFC 3339 UTC deadline BBSDEV.DRP expects -- preserving the same
    effective time limit instead of silently dropping it.

Usage:
    python3 door32_to_bbsdev_drp.py DOOR32.SYS BBSDEV.DRP
    python3 door32_to_bbsdev_drp.py DOOR32.SYS BBSDEV.DRP \\
        --board-name "My Synchronet BBS" --sysop-name StingRay
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

# DOOR32.SYS field indices (0-based), matching the exact 11-line layout
# this whole project's OpenDoors reader (third_party/OpenDoors/src/
# ODInEx1.c's FOUND_DOOR32_SYS branch) parses -- verified against that
# real consumer, not guessed.
_COMM_TYPE = 0
_COMM_HANDLE = 1
_BAUD = 2
_SOFTWARE_NAME = 3
_USER_NUM = 4
_REAL_NAME = 5
_ALIAS = 6
_SECURITY = 7
_MINUTES_LEFT = 8
_TERMINAL_TYPE = 9
_NODE = 10
_DOOR32_LINE_COUNT = 11


class Door32ConversionError(ValueError):
    """Raised when the input isn't a well-formed DOOR32.SYS file."""


def parse_door32_sys(text):
    """Parses DOOR32.SYS text into a plain dict of its 11 fields.
    Accepts CRLF or bare LF (DOOR32.SYS producers vary), matching this
    project's own tolerance convention for legacy drop files."""
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    # Strip a single trailing blank element from a final newline, same
    # as every dropfile.py generator's own '\r\n'.join(...) + '\r\n'
    # round-trips.
    if lines and lines[-1] == '':
        lines = lines[:-1]
    if len(lines) < _DOOR32_LINE_COUNT:
        raise Door32ConversionError(
            f'expected {_DOOR32_LINE_COUNT} lines, got {len(lines)}')

    try:
        comm_type = int(lines[_COMM_TYPE].strip())
        comm_handle = lines[_COMM_HANDLE].strip()
        security = int(lines[_SECURITY].strip() or '0')
        minutes_left = int(lines[_MINUTES_LEFT].strip() or '0')
        terminal_type = int(lines[_TERMINAL_TYPE].strip() or '0')
        node = int(lines[_NODE].strip() or '1')
        user_num = lines[_USER_NUM].strip()
    except ValueError as exc:
        raise Door32ConversionError(f'malformed numeric field: {exc}') from exc

    return {
        'comm_type': comm_type,
        'comm_handle': comm_handle,
        'software_name': lines[_SOFTWARE_NAME].strip(),
        'user_num': user_num,
        'real_name': lines[_REAL_NAME].strip(),
        'alias': lines[_ALIAS].strip(),
        'security': security,
        'minutes_left': minutes_left,
        'terminal_type': terminal_type,
        'node': node,
    }


def build_bbsdev_drp(door32, board_name=None, sysop_name=None, now=None):
    """Builds BBSDEV.DRP content (str, CRLF-terminated) from a parsed
    DOOR32.SYS dict (parse_door32_sys()'s return value)."""
    now = now or datetime.now(timezone.utc)

    # Line 2/3: communications type/parameters. DOOR32.SYS type 2 is
    # "telnet" -- an inherited socket, the same contract BBSDEV.DRP's
    # own "socket" type describes. Type 0 (local) maps directly. Type
    # 1 (serial) has no equivalent inherited-descriptor concept in
    # DOOR32.SYS (its handle field there is a raw COM port index, not
    # an open fd) -- "local" is used as the closest safe fallback
    # rather than fabricating a socket/serial value BBSDEV.DRP would
    # then hand a door that doesn't actually have.
    if door32['comm_type'] == 2:
        comm_type_line = 'socket'
        comm_param_line = door32['comm_handle'] or '0'
    else:
        comm_type_line = 'local'
        comm_param_line = ''

    alias = door32['alias'] or door32['real_name'] or 'User'
    user_key = door32['user_num'] or '0'

    # Terminal type: 0=ASCII, 1=ANSI, 2=AVATAR, 3=RIP (matches
    # ODInEx1.c's own switch on this exact field). ANSI/AVATAR/RIP all
    # imply a graphics-capable terminal for line 8; only RIP (3) also
    # sets line 9.
    ansi = 'N' if door32['terminal_type'] == 0 else 'Y'
    rip = 'Y' if door32['terminal_type'] == 3 else 'N'

    software_name = door32['software_name'] or 'Unknown BBS'
    board = board_name or software_name
    sysop = sysop_name or 'Sysop'

    deadline = now + timedelta(minutes=max(0, door32['minutes_left']))
    deadline_line = deadline.strftime('%Y-%m-%dT%H:%M:%SZ')

    lines = [
        '1.0',
        comm_type_line,
        comm_param_line,
        alias,
        user_key,
        '80',
        '24',
        ansi,
        rip,
        '',                     # CTerm version -- not detectable
        deadline_line,
        'IBM437',
        'en-US',
        software_name,
        board,
        sysop,
        str(door32['security']),
        str(door32['node']),
        'N',
    ]
    return '\r\n'.join(lines) + '\r\n'


def convert_door32_to_bbsdev_drp(door32_path, output_path, board_name=None,
                                  sysop_name=None):
    """Reads `door32_path`, writes the converted BBSDEV.DRP to
    `output_path`. Returns the written content."""
    with open(door32_path, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    door32 = parse_door32_sys(text)
    content = build_bbsdev_drp(door32, board_name=board_name, sysop_name=sysop_name)
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        f.write(content)
    return content


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('door32_sys', help='Path to the input DOOR32.SYS file.')
    parser.add_argument('bbsdev_drp', help='Path to write the output BBSDEV.DRP file.')
    parser.add_argument('--board-name', default=None,
                        help='Board name (line 15). Defaults to the DOOR32.SYS '
                             'software-name field, since that format has no '
                             'separate concept of the two.')
    parser.add_argument('--sysop-name', default=None,
                        help="Sysop's alias (line 16). Defaults to \"Sysop\" -- "
                             'DOOR32.SYS carries no sysop-name field at all.')
    args = parser.parse_args(argv)

    try:
        convert_door32_to_bbsdev_drp(
            args.door32_sys, args.bbsdev_drp,
            board_name=args.board_name, sysop_name=args.sysop_name)
    except (OSError, Door32ConversionError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1

    print(f'Wrote {args.bbsdev_drp}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
