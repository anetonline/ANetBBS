# anetbbs/games/dropfile.py
"""
Drop File Generator for ANetBBS Game Center

Generates the major drop file formats (DOOR.SYS, DORINFO1.DEF, DOOR32.SYS,
CHAIN.TXT, SFDOORS.DAT, BBSDEV.DRP) from a user session so that classic
BBS door games can be launched.
"""
import os
from datetime import datetime
from pathlib import Path

from .. import __version__ as _ANETBBS_VERSION


def _u(user, field, default=None):
    """Read `field` from a user that may be a SQLAlchemy model OR a plain dict
    (telnet/SSH/rlogin sessions store user as a dict).

    Real field-injection gap found in a full access-control audit: these
    fixed-position, newline-delimited drop-file formats trust every field
    verbatim -- a username/email containing an embedded CR/LF injected
    extra physical lines, shifting every subsequent positional field
    (including DOOR.SYS's security-level line) out from under the door
    game reading it. Stripping CR/LF here, the one place every field in
    this module funnels through, closes it for all three formats.
    """
    if user is None:
        return default
    if isinstance(user, dict):
        val = user.get(field, default)
    else:
        val = getattr(user, field, default)
    if isinstance(val, str):
        val = val.replace('\r', '').replace('\n', '')
    return val


def generate_door_sys(user, node_number, minutes_remaining=60, bbs_name='ANetBBS',
                      output_path=None, tw2002_compat=False):
    """
    Generate a DOOR.SYS drop file (52-line format).

    Args:
        user: User model instance
        node_number: Integer node number
        minutes_remaining: Session time remaining in minutes
        bbs_name: Name of the BBS
        output_path: Full path to write the file (optional)

    Returns:
        String content of the drop file
    """
    now = datetime.now()  # local time — must match dosemu2's DOS clock
    last_login = _u(user, 'last_login')
    last_call = last_login.strftime('%m/%d/%Y') if last_login else '01/01/2000'
    seconds_remaining = minutes_remaining * 60

    # Split username into first/last for standard DOOR.SYS lines 7/8
    parts = (_u(user, 'username') or 'User').split(None, 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ''

    security_level = 200 if _u(user, 'is_admin') else 50
    # Pascal BYTE holds 0-255; many DOS doors store times-called in a BYTE.
    # login_count above 255 causes a range-check error (Turbo Pascal error 201)
    # in games that read DOOR.SYS line 14 into a BYTE variable.
    login_count = min(_u(user, 'login_count') or 1, 255)

    # NOTE: COM1: + 38400 (NOT COM0: + 0) — telling LORD/TradeWars/etc.
    # that the user is a remote BBS caller on a FOSSIL-driven COM1, NOT
    # a local-console user. With "COM0:" + "0", many doors (LORD especially)
    # silently switch to BIOS-only screen output even when their own config
    # is set to FOSSIL — and the BBS's TCP-nullmodem bridge then sees no
    # bytes. LORD's local-detection logic looks at lines 1+2 of DOOR.SYS
    # and short-circuits the FOSSIL path if either is "local".
    #
    # TW2002 DOOR.SYS layout (confirmed from TW2002 validation warnings):
    #   Line 7-9:  first name / last name / location
    #   Line 13:   caller security level
    #   Line 14:   times called
    #   Line 15:   sysop security level  (numeric — NOT last call date)
    #   Line 16:   seconds remaining
    #   Line 17:   time on today (numeric)
    #   Line 18:   time left today in minutes  (numeric "Time Left")
    #   Line 19:   GR/NG/7E  ← TW2002 reads ANSI type from HERE, not line 18
    #   Line 20:   expert mode
    #   Line 21:   page length  (numeric)
    lines = [
        'COM1:',          # Line 1:  COM port (COM1 = use FOSSIL on COM1)
        '38400',          # Line 2:  Baud rate (any non-zero = non-local)
        '8',              # Line 3:  Data bits
        str(node_number), # Line 4:  Node number
        '38400',          # Line 5:  DTE rate (match line 2)
        'Y',              # Line 6:  Screen clear (Y=on)
        first_name,       # Line 7:  First name
        last_name,        # Line 8:  Last name
        'Unknown',        # Line 9:  Location/city
        '000-000-0000',   # Line 10: Home phone
        '000-000-0000',   # Line 11: Business phone
        'PASSWORD',       # Line 12: Password placeholder
        str(security_level),     # Line 13: Caller security level
        str(login_count),        # Line 14: Times called
        # Line 15: Standard PCBoard format = last date on (MM/DD/YYYY).
        # TW2002 is the exception — it reads line 15 as sysop security level
        # (numeric). LORD and all other doors expect a date string; passing
        # a number causes date parsing to fail → array[0] → error 201.
        str(security_level) if tw2002_compat else last_call,  # Line 15
        str(seconds_remaining),  # Line 16: Seconds remaining today
        '0',                     # Line 17: Time on today
        str(seconds_remaining),  # Line 18: TW2002 reads this as SECONDS (not minutes)
        'GR',                    # Line 19: Graphics (GR=ANSI) ← TW2002 reads from HERE
        'N',                     # Line 20: Expert mode
        '25',                    # Line 21: Page length (TW2002 validates)
        '',                      # Line 22: Conference scanned
        '01/01/2099',            # Line 23: Expiration date
        str(_u(user, 'id') or 0), # Line 24: User file record position
        'ANSI',                  # Line 25: Default protocol (ANSI)
        '0',                     # Line 26: Total uploads
        '0',                     # Line 27: Total downloads
        '0',                     # Line 28: Daily download total (KB)
        '9999999',               # Line 29: Daily download limit (KB)
        now.strftime('%m/%d/%Y'),  # Line 30: Today's date
        now.strftime('%H:%M'),     # Line 31: Current time
        bbs_name,                  # Line 32: BBS name
        'Sysop',                   # Line 33: SysOp name
        str(node_number),          # Line 34: Node number (again)
        '38400',                   # Line 35: Max baud rate
        'Y',                       # Line 36: FOSSIL driver
        _u(user, 'username') or '',# Line 37: User's handle
        '0',                       # Line 38: Total uploads (KB)
        '0',                       # Line 39: Total downloads (KB)
        _u(user, 'email') or '',   # Line 40: User's email
        '0',                       # Line 41: Number of messages posted
        '0',                       # Line 42: Number of messages read
        '0',                       # Line 43: Page length in lines
        'Y',                       # Line 44: ANSI graphics
        'Y',                       # Line 45: Record locking
        '1',                       # Line 46: Default transfer protocol
        'N',                       # Line 47: Door registration flag
        str(security_level),       # Line 48: Network access level
        'N',                       # Line 49: Screen blanking
        '0',                       # Line 50: Number of calls today
        'N',                       # Line 51:
        '0',                       # Line 52:
    ]

    content = '\r\n'.join(lines) + '\r\n'

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(content)

    return content


def generate_dorinfo(user, node_number, minutes_remaining=60, bbs_name='ANetBBS',
                     output_path=None):
    """
    Generate a DORINFO1.DEF drop file.

    Args:
        user: User model instance
        node_number: Integer node number
        minutes_remaining: Session time remaining in minutes
        bbs_name: Name of the BBS
        output_path: Full path to write the file (optional)

    Returns:
        String content of the drop file
    """
    parts = (_u(user, 'username') or 'User').split(None, 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ''

    sysop_parts = 'Sysop User'.split()
    security_level = 200 if _u(user, 'is_admin') else 50

    # COM1 + 38400 baud — same FOSSIL/non-local rationale as DOOR.SYS above.
    lines = [
        bbs_name,           # BBS name
        sysop_parts[0],     # Sysop first name
        sysop_parts[1],     # Sysop last name
        'COM1',             # COM port (COM0 = local; doors fall back to BIOS)
        '38400 BAUD,8,N,1', # Baud/data bits
        str(node_number),   # Node number
        first_name,         # User first name
        last_name,          # User last name
        'Unknown',          # Location
        '1',                # ANSI flag (1=ANSI, 0=ASCII)
        str(security_level),    # Security level
        str(minutes_remaining), # Minutes remaining
        '-1',               # Subscription days remaining (-1=unlimited)
    ]

    content = '\r\n'.join(lines) + '\r\n'

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(content)

    return content


def generate_door32(user, node_number, minutes_remaining=60, bbs_name='ANetBBS',
                    output_path=None, comm_handle=None):
    """
    Generate a DOOR32.SYS drop file (modern 32-bit format).

    Args:
        user: User model instance
        node_number: Integer node number
        minutes_remaining: Session time remaining in minutes
        bbs_name: Name of the BBS
        output_path: Full path to write the file (optional)
        comm_handle: Override for the comm handle field.
            None / not supplied → type 1 / handle 0 (FOSSIL, for DOS doors).
            -1   → type 2 / handle -1 (Mystic Linux STDIO convention).
                   RMDoor/DDPlus and most modern FPC door kits treat
                   ComNum=-1 on UNIX as a signal to use Write(StdOut)/ReadKey
                   (PTY-safe) instead of fpSend/fpRecv, which fail with
                   ENOTSOCK on a PTY and produce an instant carrier-drop exit.
            >= 0 → type 2 / handle N (real inherited socket fd).

    Returns:
        String content of the drop file
    """
    parts = (_u(user, 'username') or 'User').split(None, 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ''
    full_name = f'{first_name} {last_name}'.strip()

    security_level = 200 if _u(user, 'is_admin') else 50

    # Comm type and handle selection:
    # DOS doors (door_dos) → type 1 / handle 0: FOSSIL on COM1 via TCP nullmodem.
    # Native Linux PTY doors (door_native) → type 2 / handle -1: Mystic STDIO
    # convention. fpSend/fpRecv fail with ENOTSOCK on a PTY fd, so we signal
    # the door kit to use stdin/stdout directly instead.
    if comm_handle is None:
        comm_type = 1
        comm_handle = 0
    elif comm_handle == -1:
        comm_type = 2
    else:
        comm_type = 2

    lines = [
        str(comm_type),        # Comm type
        str(comm_handle),      # Comm or socket handle
        '38400',               # Baud rate
        bbs_name,          # BBS name
        str(_u(user, 'id') or 0), # User ID
        full_name,                 # User's real name
        _u(user, 'username') or '',# User's handle/alias
        str(security_level),    # Security level
        str(minutes_remaining), # Minutes remaining this session
        '1',               # ANSI emulation (1=yes)
        str(node_number),  # Node number
    ]

    content = '\r\n'.join(lines) + '\r\n'

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(content)

    return content


def generate_chain_txt(user, node_number, minutes_remaining=60, bbs_name='ANetBBS',
                       output_path=None):
    """
    Generate a CHAIN.TXT drop file (WWIV-style, 30-line format).

    Field layout confirmed directly against OpenDoors' own parser
    (third_party/OpenDoors/ODInEx1.c, the `FOUND_CHAIN_TXT` branch of
    its dropfile auto-detector) rather than guessed from a spec —
    the same verify-against-the-real-consumer discipline used
    throughout this project for dropfile formats. Lines OpenDoors
    reads but doesn't interpret into any od_control field are left
    blank; every line OpenDoors DOES populate a real field from is
    filled with a real, working value. Note line 16 (time remaining)
    is read in SECONDS here, unlike DOOR.SYS/DOOR32.SYS's minutes —
    OpenDoors divides it by 60 immediately after reading.

    Args:
        user: User model instance
        node_number: Integer node number
        minutes_remaining: Session time remaining in minutes
        bbs_name: Name of the BBS (unused by this format directly,
            kept for signature consistency with the other generators)
        output_path: Full path to write the file (optional)

    Returns:
        String content of the drop file
    """
    del bbs_name  # not part of CHAIN.TXT's own field set
    parts = (_u(user, 'username') or 'User').split(None, 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ''

    security_level = 200 if _u(user, 'is_admin') else 50
    seconds_remaining = minutes_remaining * 60

    lines = [
        str(_u(user, 'id') or 0),      # 1: User number
        _u(user, 'username') or '',    # 2: User handle
        f'{first_name} {last_name}'.strip(),  # 3: User name
        '',                            # 4: Callsign (ham radio field, N/A)
        '',                            # 5: unused
        'U',                           # 6: Sex (unknown)
        '',                            # 7: unused
        '01/01/00',                    # 8: Last date on
        '80',                          # 9: Screen width
        '24',                          # 10: Screen length
        str(security_level),           # 11: Security level
        '1' if _u(user, 'is_admin') else '0',  # 12: Is sysop
        '0',                           # 13: Is co-sysop
        '1',                           # 14: ANSI (1=yes)
        '1',                           # 15: Non-zero if remote (always true here)
        str(seconds_remaining),        # 16: Time remaining, in SECONDS
        '',                            # 17: unused
        '',                            # 18: unused
        '',                            # 19: unused
        '38400',                       # 20: Baud rate
        str(node_number),              # 21: Port number
        '',                            # 22: unused
        '',                            # 23: Password (left blank -- never echo a real one here)
        '',                            # 24: unused
        '',                            # 25: unused
        '',                            # 26: unused
        '',                            # 27: unused
        '',                            # 28: unused
        '',                            # 29: unused
        '',                            # 30: unused
    ]

    content = '\r\n'.join(lines) + '\r\n'

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(content)

    return content


def generate_sfdoors_dat(user, node_number, minutes_remaining=60, bbs_name='ANetBBS',
                         output_path=None):
    """
    Generate an SFDOORS.DAT drop file (Spitfire-style format).

    Field layout confirmed directly against OpenDoors' own parser
    (third_party/OpenDoors/ODInEx1.c's `ODInitReadSFDoorsDAT()`), which
    documents every line with its own inline comment -- the clearest
    of the four formats this module supports to verify, since there
    was no need to infer meaning from surrounding logic the way
    CHAIN.TXT required. Only lines 1-33 are actually required by the
    parser (it returns failure if any of those are missing); lines
    34+ are read best-effort, so this generator stops at 35 rather
    than also emitting the DOS-serial-hardware-specific IRQ/port-
    address lines that follow, which have no meaning for a modern
    telnet/SSH/PTY-based install.

    Args:
        user: User model instance
        node_number: Integer node number
        minutes_remaining: Session time remaining in minutes
        bbs_name: Name of the BBS (unused by this format directly,
            kept for signature consistency with the other generators)
        output_path: Full path to write the file (optional)

    Returns:
        String content of the drop file
    """
    del bbs_name  # not part of SFDOORS.DAT's own field set
    security_level = 200 if _u(user, 'is_admin') else 50

    lines = [
        str(_u(user, 'id') or 0),      # 1: User number
        _u(user, 'username') or 'User',  # 2: User name
        '',                            # 3: Password (left blank)
        '',                            # 4: unused
        '38400',                       # 5: Baud rate
        str(node_number),              # 6: Serial port number
        str(minutes_remaining),        # 7: Time remaining, in MINUTES
        '',                            # 8: unused
        '',                            # 9: unused
        'T',                           # 10: ANSI mode (T=true)
        str(security_level),           # 11: Security level
        '0',                           # 12: Upload count
        '0',                           # 13: Download count
        '',                            # 14: unused
        '0',                           # 15: Login time (minutes since midnight)
        '',                            # 16: unused
        'T' if _u(user, 'is_admin') else 'F',  # 17: Sysop-next flag
        '',                            # 18: unused
        '',                            # 19: unused
        '',                            # 20: unused
        'T',                           # 21: Error-free connection (telnet/SSH always is)
        '0',                           # 22: Current message area
        '0',                           # 23: Current file area
        str(node_number),              # 24: Current node number
        '',                            # 25: unused
        '',                            # 26: unused
        '',                            # 27: unused
        '0',                           # 28: Kilobytes downloaded today
        '0',                           # 29: Kilobytes uploaded, total
        '0',                           # 30: Kilobytes downloaded, total
        '000-000-0000',                # 31: Home phone
        'Unknown',                     # 32: Home location
        '',                            # 33: unused (last REQUIRED line)
        'F',                           # 34: RIP mode (optional)
        'F',                           # 35: Wants chat (optional)
    ]

    content = '\r\n'.join(lines) + '\r\n'

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(content)

    return content


def generate_bbsdev_drp(user, node_number, minutes_remaining=60, bbs_name='ANetBBS',
                        output_path=None, sysop_name=None):
    """
    Generate a BBSDEV.DRP drop file (RealDeuce's bbsdev.drp spec,
    https://github.com/RealDeuce/bbsdev.drp) -- a newer, UTF-8/CRLF-
    strict, 19-line format. Field layout confirmed directly against the
    spec's own ABNF grammar and reference examples, same
    verify-against-the-real-consumer discipline used throughout this
    module for the other formats (and against the reader added to
    OpenDoors itself for RDQ3/ANetCHESS, third_party/OpenDoors's own
    ODInEx1.c FOUND_BBSDEV_DRP branch).

    Only the "stdio" communications type is emitted here -- ANetBBS
    launches every door_native game (RDQ3, ANetCHESS) over a PTY with
    fd 0/1/2 as the real channel, and BBSDEV.DRP's "stdio" type is a
    clean, dedicated way to say that. This sidesteps the fragile
    Comm-Type=2/Handle=-1 "use stdio" SENTINEL DOOR32.SYS callers have
    to use instead (see generate_door32()'s own comm_handle docstring,
    and door32.c's WriteFixedDoor32Sys() on the RDQ3/ANetCHESS side,
    which exists ONLY to work around that sentinel not being understood
    generically) -- BBSDEV.DRP has no equivalent hack to work around.
    door_dos/door_dosemu games are DOS binaries under FOSSIL emulation,
    a communications type this generator (and the OpenDoors reader
    it's paired with) doesn't support, so they keep using DOOR32.SYS/
    DOOR.SYS instead; write_drop_file() only calls this generator for
    games a sysop has explicitly configured with drop_file_type
    'bbsdev.drp'.

    Per spec, a producer MUST also announce the file via the
    BBSDEV_DRP environment variable in the door's own environment --
    that's the door_runner.py fork's job (see its BBSDEV_DRP env var
    assignment, set from this function's return value), not this
    generator's.

    Args:
        user: User model instance
        node_number: Integer node number
        minutes_remaining: Session time remaining in minutes
        bbs_name: Name of the BBS (used for both line 14's software
            name and line 15's board name -- ANetBBS doesn't track
            those as two separate concepts the way the spec allows)
        output_path: Full path to write the file (optional)
        sysop_name: Sysop's alias for line 16. Defaults to 'Sysop',
            matching generate_dorinfo()'s own hardcoded fallback, when
            the caller (write_drop_file(), via door_runner.py) doesn't
            have a real configured value to pass.

    Returns:
        String content of the drop file
    """
    user_id = _u(user, 'id') or 0
    username = _u(user, 'username') or 'User'
    security_level = 100 if _u(user, 'is_admin') else 50

    lines = [
        '1.0',                          # 1: Format version
        'stdio',                        # 2: Communications type
        '',                             # 3: Communications parameters (empty for stdio)
        username,                       # 4: User alias
        str(user_id),                   # 5: Unique user key (opaque; the DB user id)
        '80',                           # 6: Screen width
        '24',                           # 7: Screen height
        'Y',                            # 8: ANSI
        'N',                            # 9: RIP
        '',                             # 10: CTerm version (not detected)
        '',                             # 11: Time of logoff (no forced deadline)
        'IBM437',                       # 12: Encoding
        'en-US',                        # 13: Language
        f'ANetBBS {_ANETBBS_VERSION}',  # 14: BBS software name and version
        bbs_name,                       # 15: Board name
        sysop_name or 'Sysop',          # 16: Sysop alias
        # 17: Access level -- the "sysop" token is more portable/
        # expressive than a magic security number, and BBSDEV.DRP is
        # the one format here that actually defines it; every other
        # generator in this module has no equivalent to reach for.
        'sysop' if _u(user, 'is_admin') else str(security_level),
        str(node_number),               # 18: Node number
        'N',                            # 19: Show local display
    ]

    content = '\r\n'.join(lines) + '\r\n'

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            f.write(content)

    return content


def write_drop_file(user, game, node_number, minutes_remaining=60,
                    bbs_name='ANetBBS', token_ctx=None, sysop_name=None):
    """
    Write the appropriate drop file for a game.

    Args:
        user: User model instance
        game: Game model instance
        node_number: Allocated node number
        minutes_remaining: Session time remaining
        bbs_name: BBS name string
        token_ctx: Optional Synchronet/Mystic %-token context. When supplied,
                   `Game.drop_file_path` is %-expanded — so a sysop can write
                   `%Pdoor32.sys` instead of `data/temp/node{node}/door32.sys`.
        sysop_name: Sysop alias, used only by the bbsdev.drp generator
                   (the only format here with a dedicated field for it).

    Returns:
        Path to the written drop file, or None if drop_file_type is 'none'
    """
    import os
    drop_type = (game.drop_file_type or 'none').lower()
    if drop_type == 'none' or not game.drop_file_path:
        return None

    output_path = game.drop_file_path

    # Apply the BBS token vocabulary if we have a context. This is the
    # primary supported substitution form; `{node}` below is legacy.
    if token_ctx is not None:
        from .node_paths import expand_tokens
        output_path = expand_tokens(output_path, token_ctx)

    # Legacy {node} placeholder support — kept for installs configured
    # before %-tokens existed. New games should use %P (Mystic) or %f
    # (Synchronet) instead.
    output_path = output_path.replace('{node}', str(node_number))

    # Resolve relative paths to absolute against the install dir.
    if not os.path.isabs(output_path):
        # Fall back to deriving the install dir from this file's own
        # location if BASE_DIR isn't accessible (which only happens in
        # unit tests with no app context) -- works on any install,
        # regardless of where the sysop put it.
        try:
            from ..config import get_config
            base = str(getattr(get_config(), 'BASE_DIR', None) or
                       Path(__file__).resolve().parents[2])
        except Exception:
            base = str(Path(__file__).resolve().parents[2])
        output_path = os.path.normpath(os.path.join(base, output_path))

    # If they gave us a DIRECTORY (common — "put the drop file inside this
    # folder"), append the standard filename for the type.
    if output_path.endswith('/') or os.path.isdir(output_path):
        filename_for_type = {
            'door.sys': 'DOOR.SYS',
            'dorinfo': 'DORINFO1.DEF',
            'door32.sys': 'DOOR32.SYS',
            'chain.txt': 'CHAIN.TXT',
            'sfdoors.dat': 'SFDOORS.DAT',
            'bbsdev.drp': 'BBSDEV.DRP',
        }.get(drop_type, 'DOOR.SYS')
        output_path = os.path.join(output_path.rstrip('/'), filename_for_type)

    # Make sure parent dir exists
    parent = os.path.dirname(output_path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass

    if drop_type == 'door.sys':
        generate_door_sys(user, node_number, minutes_remaining, bbs_name, output_path)
    elif drop_type == 'dorinfo':
        generate_dorinfo(user, node_number, minutes_remaining, bbs_name, output_path)
    elif drop_type == 'door32.sys':
        # Native Linux PTY doors need comm_handle=-1 (Mystic STDIO convention)
        # so the door kit uses stdin/stdout instead of fpSend/fpRecv on a PTY fd.
        game_type = getattr(game, 'game_type', '') or ''
        ch = -1 if game_type == 'door_native' else None
        generate_door32(user, node_number, minutes_remaining, bbs_name, output_path,
                        comm_handle=ch)
    elif drop_type == 'chain.txt':
        generate_chain_txt(user, node_number, minutes_remaining, bbs_name, output_path)
    elif drop_type == 'sfdoors.dat':
        generate_sfdoors_dat(user, node_number, minutes_remaining, bbs_name, output_path)
    elif drop_type == 'bbsdev.drp':
        generate_bbsdev_drp(user, node_number, minutes_remaining, bbs_name, output_path,
                            sysop_name=sysop_name)

    return output_path
