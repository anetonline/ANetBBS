# anetbbs/games/dropfile.py
"""
Drop File Generator for ANetBBS Game Center

Generates the major drop file formats (DOOR.SYS, DORINFO1.DEF, DOOR32.SYS)
from a user session so that classic BBS door games can be launched.
"""
import os
from datetime import datetime


def _u(user, field, default=None):
    """Read `field` from a user that may be a SQLAlchemy model OR a plain dict
    (telnet/SSH/rlogin sessions store user as a dict)."""
    if user is None:
        return default
    if isinstance(user, dict):
        return user.get(field, default)
    return getattr(user, field, default)


def generate_door_sys(user, node_number, minutes_remaining=60, bbs_name='ANetBBS',
                      output_path=None):
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
    now = datetime.utcnow()
    last_login = _u(user, 'last_login')
    last_call = last_login.strftime('%m/%d/%Y') if last_login else '01/01/2000'
    seconds_remaining = minutes_remaining * 60

    # Split username into first/last for compatibility
    parts = (_u(user, 'username') or 'User').split(None, 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else 'User'
    full_name = f'{first_name} {last_name}'

    security_level = 255 if _u(user, 'is_admin') else 50
    login_count = _u(user, 'login_count') or 1

    # NOTE: COM1: + 38400 (NOT COM0: + 0) — telling LORD/TradeWars/etc.
    # that the user is a remote BBS caller on a FOSSIL-driven COM1, NOT
    # a local-console user. With "COM0:" + "0", many doors (LORD especially)
    # silently switch to BIOS-only screen output even when their own config
    # is set to FOSSIL — and the BBS's TCP-nullmodem bridge then sees no
    # bytes. LORD's local-detection logic looks at lines 1+2 of DOOR.SYS
    # and short-circuits the FOSSIL path if either is "local".
    lines = [
        'COM1:',          # Line 1: COM port (COM1 = use FOSSIL on COM1)
        '38400',          # Line 2: Baud rate (any non-zero = non-local)
        '8',              # Line 3: Data bits
        str(node_number), # Line 4: Node number
        '38400',          # Line 5: DTE rate (match line 2)
        'Y',              # Line 6: Screen display (Y=on)
        'Y',              # Line 7: Printer (Y=on)
        'Y',              # Line 8: Page bell
        'Y',              # Line 9: Caller alarm
        full_name,        # Line 10: User's full name
        'Unknown',        # Line 11: Location/city
        '000-000-0000',   # Line 12: Home phone
        '000-000-0000',   # Line 13: Business phone
        'PASSWORD',       # Line 14: Password placeholder
        str(security_level),  # Line 15: Security level
        str(login_count),     # Line 16: Times called
        last_call,            # Line 17: Last call date
        str(seconds_remaining),  # Line 18: Seconds remaining
        str(minutes_remaining),  # Line 19: Minutes remaining
        'GR',                    # Line 20: Graphics (GR=ANSI)
        '25',                    # Line 21: Page length
        'N',                     # Line 22: Expert mode
        '',                      # Line 23: Conference joined
        '',                      # Line 24: Conference scanned
        '01/01/2099',            # Line 25: Expiration date
        str(_u(user, 'id') or 0), # Line 26: User file record position
        'ANSI',                  # Line 27: Default protocol (ANSI)
        '0',                     # Line 28: Total uploads
        '0',                     # Line 29: Total downloads
        '0',                     # Line 30: Daily download total (KB)
        '9999999',               # Line 31: Daily download limit (KB)
        now.strftime('%m/%d/%Y'),  # Line 32: Today's date
        now.strftime('%H:%M'),     # Line 33: Current time
        bbs_name,                  # Line 34: BBS name
        'Sysop',                   # Line 35: SysOp name
        str(node_number),          # Line 36: Node number (again)
        '38400',                   # Line 37: Max baud rate
        'Y',                       # Line 38: FOSSIL driver
        _u(user, 'username') or '',# Line 39: User's handle
        '0',                       # Line 40: Total uploads (KB)
        '0',                       # Line 41: Total downloads (KB)
        _u(user, 'email') or '',   # Line 42: User's email
        '0',                       # Line 43: Number of messages posted
        '0',                       # Line 44: Number of messages read
        '0',                       # Line 45: Page length in lines
        'Y',                       # Line 46: ANSI graphics
        'Y',                       # Line 47: Record locking
        '1',                       # Line 48: Default transfer protocol
        'N',                       # Line 49: Door registration flag
        str(security_level),       # Line 50: Network access level
        'N',                       # Line 51: Screen blanking
        '0',                       # Line 52: Number of calls today
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
    last_name = parts[1] if len(parts) > 1 else 'User'

    sysop_parts = 'Sysop User'.split()
    security_level = 255 if _u(user, 'is_admin') else 50

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
    last_name = parts[1] if len(parts) > 1 else 'User'
    full_name = f'{first_name} {last_name}'

    security_level = 255 if _u(user, 'is_admin') else 50

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


def write_drop_file(user, game, node_number, minutes_remaining=60,
                    bbs_name='ANetBBS', token_ctx=None):
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
        # Fall back to the legacy hardcoded base if BASE_DIR isn't accessible
        # (which only happens in unit tests with no app context).
        try:
            from ..config import get_config
            base = str(getattr(get_config(), 'BASE_DIR', '/home/stingray/anetbbs'))
        except Exception:
            base = '/home/stingray/anetbbs'
        output_path = os.path.normpath(os.path.join(base, output_path))

    # If they gave us a DIRECTORY (common — "put the drop file inside this
    # folder"), append the standard filename for the type.
    if output_path.endswith('/') or os.path.isdir(output_path):
        filename_for_type = {
            'door.sys': 'DOOR.SYS',
            'dorinfo': 'DORINFO1.DEF',
            'door32.sys': 'DOOR32.SYS',
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

    return output_path
