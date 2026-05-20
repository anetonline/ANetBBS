#!/usr/bin/env python3
"""
anetbbs-import-users — migrate user accounts into ANetBBS.

Supported source BBSes
  synchronet   Synchronet SBBS 3.17+ (data/user/user.dat, 1024-byte records)
               Also accepts older 512-byte records (auto-detected).
  mystic       Mystic BBS A47–A50 (data/users.dat, 2048-byte records)

Passwords are NEVER migrated — both systems use incompatible hashing.
Each imported account gets a random 16-char temporary password.  The
tool writes a CSV report (--report, default: import_report.csv) with
every user's handle and temp password so the sysop can notify them.

Usage examples
  # Dry-run — show what would be imported, touch nothing
  python tools/import_users.py --dry-run synchronet /sbbs/data/user/user.dat

  # Live import, auto-generate placeholder emails from mybbs.net domain
  python tools/import_users.py synchronet /sbbs/data/user/user.dat \\
      --email-domain mybbs.net

  # Mystic import, skip users whose username already exists
  python tools/import_users.py mystic /mystic/data/users.dat \\
      --on-conflict skip

  # Overwrite existing accounts with imported data (keeps ANetBBS password)
  python tools/import_users.py mystic /mystic/data/users.dat \\
      --on-conflict update

DATABASE_URL is read from the environment (same as the BBS itself).
Falls back to sqlite:///data/anetbbs.db if not set.

Exit codes: 0 = success, 1 = fatal error, 2 = partial (some users skipped).
"""
import argparse
import csv
import os
import secrets
import struct
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Iterator, Optional

# ---------------------------------------------------------------------------
# Colour helpers (no deps)
# ---------------------------------------------------------------------------
_TTY = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f'\x1b[{code}m{text}\x1b[0m' if _TTY else text

def ok(s):   print(_c('92', f'  ✓ {s}'))
def warn(s): print(_c('93', f'  ! {s}'))
def err(s):  print(_c('91', f'  ✗ {s}'), file=sys.stderr)
def info(s): print(_c('90', f'    {s}'))
def head(s): print(_c('96;1', f'\n{s}'))


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _get_db_uri() -> str:
    uri = os.environ.get('DATABASE_URL')
    if uri:
        return uri
    try:
        from anetbbs.config import get_config
        cfg = get_config(os.environ.get('FLASK_ENV', 'production'))
        return cfg.SQLALCHEMY_DATABASE_URI
    except Exception:
        return 'sqlite:///data/anetbbs.db'


def _get_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(_get_db_uri(), future=True)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    return Session()


# ---------------------------------------------------------------------------
# Shared data structure
# ---------------------------------------------------------------------------

class ImportedUser:
    """Normalised user record produced by any importer."""
    __slots__ = (
        'handle', 'real_name', 'email', 'location', 'access_level',
        'login_count', 'created_at', 'last_login', 'source',
    )

    def __init__(self, *, handle: str, email: str = '',
                 real_name: str = '', location: str = '',
                 access_level: int = 10, login_count: int = 0,
                 created_at: Optional[datetime] = None,
                 last_login: Optional[datetime] = None,
                 source: str = ''):
        self.handle       = handle.strip()
        self.email        = email.strip()
        self.real_name    = real_name.strip()
        self.location     = location.strip()
        self.access_level = max(0, min(255, access_level))
        self.login_count  = login_count
        self.created_at   = created_at
        self.last_login   = last_login
        self.source       = source

    def __repr__(self):
        return f'<ImportedUser {self.handle!r} level={self.access_level}>'


# ---------------------------------------------------------------------------
# Utility: decode null-terminated C string bytes
# ---------------------------------------------------------------------------

def _cstr(raw: bytes) -> str:
    """Decode a null-terminated C string from a bytes slice."""
    nul = raw.find(b'\x00')
    if nul >= 0:
        raw = raw[:nul]
    return raw.decode('cp437', errors='replace').strip()


def _pascal_str(raw: bytes) -> str:
    """Decode a Pascal string (first byte = length)."""
    if not raw:
        return ''
    length = raw[0]
    return raw[1:1 + length].decode('cp437', errors='replace').strip()


def _unixts(ts: int) -> Optional[datetime]:
    """Convert a 32-bit unix timestamp to datetime; None if zero/invalid."""
    if ts <= 0:
        return None
    try:
        return datetime.utcfromtimestamp(ts)
    except (OSError, OverflowError, ValueError):
        return None


def _mystic_date(raw: bytes) -> Optional[datetime]:
    """Decode a Mystic TRecDate (Year:Word, Month:Byte, Day:Byte, Hour:Byte, Min:Byte)."""
    if len(raw) < 6:
        return None
    year, month, day, hour, minute = struct.unpack_from('<H4B', raw)
    if year == 0:
        return None
    try:
        return datetime(year, max(1, month), max(1, day), hour, minute)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Synchronet importer
# Targets SBBS 3.17+ (1024-byte records).  512-byte records (older) are
# auto-detected; field offsets for those are a subset of the same layout.
#
# Layout source: SBBS src/smblib/smbdefs.h
#   LEN_ALIAS=25  LEN_NAME=25  LEN_HANDLE=25  LEN_NOTE=40  LEN_COMP=40
#   LEN_COMMENT=40  LEN_NETMAIL=60  LEN_PHONE=13  LEN_BIRTH=8
#   LEN_ADDRESS=25  LEN_LOCATION=25  LEN_ZIPCODE=10  LEN_MODEM=20
#
# Numeric section (after offset 370) layout — SBBS 3.17+:
#   370  firston  uint32  (first login, unix ts)
#   374  laston   uint32  (last login, unix ts)
#   378  expire   uint32
#   382  ns_time  uint32
#   386  logons   uint32  (total logons → login_count)
#   390  ltoday   uint32
#   394  ttoday   uint16
#   396  tlast    uint16
#   398  textra   uint16
#   400  min      uint16
#   402  cdt      uint32
#   406  freecdt  uint32
#   410  dlb      uint16
#   412  tpd      uint16
#   414  posts    uint16
#   416  emails   uint16
#   418  fbacks   uint16
#   420  uploaded uint32
#   424  downloaded uint32
#   428  ufiles   uint16
#   430  dfiles   uint16
#   432  level    uint8   ← security level (0–99)
# ---------------------------------------------------------------------------

SBBS_REC_V3   = 1024
SBBS_REC_V2   = 512

# Text-section struct (370 bytes total)
_SBBS_TEXT_FMT = struct.Struct(
    '<'
    '26s'   # 0:   alias  (handle)
    '26s'   # 26:  name   (real name)
    '26s'   # 52:  handle (IRC — ignored)
    '41s'   # 78:  note
    '41s'   # 119: comp
    '41s'   # 160: comment
    '61s'   # 201: netmail (email)
    '14s'   # 262: phone
    '9s'    # 276: birth  (YYYYMMDD)
    'B'     # 285: sex
    '26s'   # 286: address
    '26s'   # 312: location
    '11s'   # 338: zipcode
    '21s'   # 349: modem (obsolete padding)
)
assert _SBBS_TEXT_FMT.size == 370

# Numeric-section struct (offset 370+), only the fields we use
_SBBS_NUM_FMT = struct.Struct('<4I I')  # firston, laston, expire, ns_time, logons
assert _SBBS_NUM_FMT.size == 20

_SBBS_LEVEL_OFF = 432   # uint8 security level within record


def _sbbs_is_deleted(alias_raw: bytes) -> bool:
    """True if the record is an empty/deleted slot."""
    nul = alias_raw.find(b'\x00')
    actual = alias_raw[:nul] if nul >= 0 else alias_raw
    return len(actual.strip()) == 0 or actual[0:1] in (b'\xff', b'\xfe', b'\x00')


def _parse_sbbs_record(data: bytes, rec_num: int) -> Optional[ImportedUser]:
    """Parse one Synchronet user record. Returns None for deleted/empty slots."""
    if len(data) < _SBBS_TEXT_FMT.size:
        return None

    (alias_raw, name_raw, _handle_raw, _note, _comp, _comment,
     netmail_raw, _phone, _birth, _sex,
     _address, location_raw, _zip, _modem) = _SBBS_TEXT_FMT.unpack_from(data)

    if _sbbs_is_deleted(alias_raw):
        return None

    handle   = _cstr(alias_raw)
    real_name = _cstr(name_raw)
    email     = _cstr(netmail_raw)
    location  = _cstr(location_raw)

    if not handle:
        return None

    # Numeric fields
    login_count = 0
    created_at  = None
    last_login  = None
    access_level = 10

    if len(data) >= _SBBS_NUM_FMT.size + 370:
        firston, laston, _exp, _ns, logons = _SBBS_NUM_FMT.unpack_from(data, 370)
        created_at   = _unixts(firston)
        last_login   = _unixts(laston)
        login_count  = logons

    if len(data) > _SBBS_LEVEL_OFF:
        raw_level = data[_SBBS_LEVEL_OFF]
        # Synchronet levels 0–99 → scale proportionally into ANetBBS 0–100 range
        # (level 99 = sysop in SBBS) → ANetBBS 100; level 0 = 0; others linear.
        if raw_level >= 99:
            access_level = 100
        else:
            access_level = max(10, int(raw_level * 100 / 99))

    return ImportedUser(
        handle=handle,
        real_name=real_name,
        email=email,
        location=location,
        access_level=access_level,
        login_count=login_count,
        created_at=created_at,
        last_login=last_login,
        source=f'synchronet:rec{rec_num}',
    )


def import_synchronet(path: Path) -> Iterator[ImportedUser]:
    """Yield ImportedUser records from a Synchronet user.dat file."""
    data = path.read_bytes()
    size = len(data)

    if size % SBBS_REC_V3 == 0:
        rec_size = SBBS_REC_V3
    elif size % SBBS_REC_V2 == 0:
        rec_size = SBBS_REC_V2
        warn(f'Detected older 512-byte SBBS records; numeric fields may be less accurate.')
    else:
        # Guess v3 and proceed anyway
        rec_size = SBBS_REC_V3
        warn(f'File size {size} not a clean multiple of {SBBS_REC_V3}; proceeding anyway.')

    num_records = size // rec_size
    info(f'Record size: {rec_size} bytes  ·  Slots: {num_records}')

    for i in range(num_records):
        chunk = data[i * rec_size:(i + 1) * rec_size]
        user = _parse_sbbs_record(chunk, i + 1)
        if user:
            yield user


# ---------------------------------------------------------------------------
# Mystic BBS importer
# Targets Mystic A47–A50 (2048-byte records).
#
# Layout source: MysticBBS Pascal source (TUserRec packed record)
#
#   Off   Size  Field         Notes
#   0     31    Handle        pascal string[30]  ← username
#   31    21    Password      pascal string[20]  (ignored — hash incompatible)
#   52    6     RegDate       TRecDate (Year:W, Month:B, Day:B, Hour:B, Min:B)
#   58    6     LastDate      TRecDate
#   64    6     LastTime      pascal string[5]
#   70    4     Calls         LongInt            ← login_count
#   74    4     Posts         LongInt
#   78    4     Emails        LongInt
#   82    4     ULFiles       LongInt
#   86    8     ULBytes       Int64
#   94    4     DLFiles       LongInt
#   98    8     DLBytes       Int64
#   106   2     TimeLeft      Integer
#   108   2     FileRatio     Integer
#   110   2     ByteRatio     Integer
#   112   1     Gender        Char
#   113   6     BirthDate     TRecDate
#   119   61    UserInfo      pascal string[60]  ← city/state/location
#   180   41    Comment       pascal string[40]  (sysop note — ignored)
#   221   31    Address       pascal string[30]
#   252   11    ZipCode       pascal string[10]
#   263   16    DataPhone     pascal string[15]
#   279   16    HomePhone     pascal string[15]
#   295   61    NetAddress    pascal string[60]  ← email
#   356   31    RealName      pascal string[30]
#   387   2     Security      Word               ← access level (0–255)
#   389   4     Flags         LongInt
#   393   4     Exemptions    LongInt
#   397   4     Restrictions  LongInt
# ---------------------------------------------------------------------------

MYS_REC_SIZE = 2048

_MYS_HDR_FMT = struct.Struct(
    '<'
    '31s'   # 0:   Handle
    '21s'   # 31:  Password (ignored)
    '6s'    # 52:  RegDate
    '6s'    # 58:  LastDate
    '6s'    # 64:  LastTime (ignored)
    'i'     # 70:  Calls (login_count)
    'i'     # 74:  Posts
    'i'     # 78:  Emails
    'i'     # 82:  ULFiles
    'q'     # 86:  ULBytes
    'i'     # 94:  DLFiles
    'q'     # 98:  DLBytes
    'h'     # 106: TimeLeft
    'h'     # 108: FileRatio
    'h'     # 110: ByteRatio
    'c'     # 112: Gender
    '6s'    # 113: BirthDate
    '61s'   # 119: UserInfo (location)
    '41s'   # 180: Comment
    '31s'   # 221: Address
    '11s'   # 252: ZipCode
    '16s'   # 263: DataPhone
    '16s'   # 279: HomePhone
    '61s'   # 295: NetAddress (email)
    '31s'   # 356: RealName
    'H'     # 387: Security (access level)
)
assert _MYS_HDR_FMT.size == 389


def _parse_mystic_record(data: bytes, rec_num: int) -> Optional[ImportedUser]:
    """Parse one Mystic BBS user record."""
    if len(data) < _MYS_HDR_FMT.size:
        return None

    fields = _MYS_HDR_FMT.unpack_from(data)
    (handle_raw, _pass_raw,
     regdate_raw, lastdate_raw, _lasttime,
     calls, _posts, _emails, _ulfiles, _ulbytes,
     _dlfiles, _dlbytes,
     _timeleft, _fileratio, _byteratio,
     _gender, _birthdate,
     userinfo_raw, _comment,
     _address, _zipcode, _dataphone, _homephone,
     netaddress_raw, realname_raw,
     security) = fields

    handle = _pascal_str(handle_raw)
    if not handle:
        return None
    # Deleted records in Mystic often have a handle starting with a null
    if handle_raw[1:2] in (b'\x00', b'\xff'):
        return None

    email     = _pascal_str(netaddress_raw)
    real_name = _pascal_str(realname_raw)
    location  = _pascal_str(userinfo_raw)
    created_at = _mystic_date(regdate_raw)
    last_login = _mystic_date(lastdate_raw)

    # Mystic security level 0–255.
    # Common convention: 10=newuser 50=regular 100=sysop 200=cosysop 255=sysop+
    # ANetBBS uses the same 0–255 scale, so we map 1:1 but floor at 10.
    if security >= 200:
        access_level = 100  # treat high Mystic levels as ANetBBS sysop
    elif security >= 100:
        access_level = 50
    else:
        access_level = max(10, security)

    return ImportedUser(
        handle=handle,
        real_name=real_name,
        email=email,
        location=location,
        access_level=access_level,
        login_count=max(0, calls),
        created_at=created_at,
        last_login=last_login,
        source=f'mystic:rec{rec_num}',
    )


def import_mystic(path: Path) -> Iterator[ImportedUser]:
    """Yield ImportedUser records from a Mystic users.dat file."""
    data = path.read_bytes()
    size = len(data)

    if size % MYS_REC_SIZE != 0:
        warn(f'File size {size} not a clean multiple of {MYS_REC_SIZE}; some records may be corrupt.')

    num_records = size // MYS_REC_SIZE
    info(f'Record size: {MYS_REC_SIZE} bytes  ·  Slots: {num_records}')

    for i in range(num_records):
        chunk = data[i * MYS_REC_SIZE:(i + 1) * MYS_REC_SIZE]
        user = _parse_mystic_record(chunk, i + 1)
        if user:
            yield user


# ---------------------------------------------------------------------------
# Core import logic
# ---------------------------------------------------------------------------

def _make_temp_password() -> str:
    """Generate a secure 16-char alphanumeric temporary password."""
    alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'
    return ''.join(secrets.choice(alphabet) for _ in range(16))


def _sanitise_username(handle: str) -> str:
    """Keep only characters ANetBBS accepts in a username (alnum + _ - .)."""
    import re
    cleaned = re.sub(r'[^\w.\-]', '_', handle, flags=re.ASCII)
    return cleaned[:80] or 'user'


def _make_email(handle: str, email: str, domain: str) -> str:
    """Return a valid-looking email; fabricate one from domain if source is blank."""
    if email and '@' in email:
        return email
    slug = re.sub(r'[^\w.\-]', '', handle.lower()) or 'user'
    return f'{slug}@{domain}'


import re  # needed by helpers above — import at module level is cleaner


def run_import(users: Iterator[ImportedUser], *,
               dry_run: bool,
               on_conflict: str,
               email_domain: str,
               require_verify: bool,
               report_path: Path) -> int:
    """
    Write users into the ANetBBS database.

    on_conflict: 'skip' | 'update' | 'rename'
    Returns exit code (0=ok, 2=partial).
    """
    from sqlalchemy import select
    from werkzeug.security import generate_password_hash

    if not dry_run:
        session = _get_session()
        from anetbbs.models import User

    head('Importing users')

    imported_count = 0
    skipped_count  = 0
    conflict_count = 0
    report_rows    = []

    for u in users:
        handle = _sanitise_username(u.handle)
        email  = _make_email(handle, u.email, email_domain)
        temp_pw = _make_temp_password()

        if dry_run:
            print(f'  [DRY] {handle:<25}  email={email:<40}  level={u.access_level}')
            imported_count += 1
            report_rows.append({'handle': handle, 'email': email,
                                 'temp_password': temp_pw,
                                 'real_name': u.real_name,
                                 'location': u.location,
                                 'access_level': u.access_level,
                                 'source': u.source})
            continue

        # --- check for existing username ---
        existing_by_name = session.execute(
            select(User).where(User.username == handle)
        ).scalar_one_or_none()

        # --- check for existing email ---
        existing_by_email = session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()

        if existing_by_name or existing_by_email:
            conflict_count += 1
            if on_conflict == 'skip':
                warn(f'SKIP  {handle}  (already exists)')
                skipped_count += 1
                continue
            elif on_conflict == 'rename':
                handle = f'{handle}_imported'
                email  = _make_email(handle, '', email_domain)
                # re-check
                if session.execute(select(User).where(User.username == handle)).scalar_one_or_none():
                    warn(f'SKIP  {handle}  (renamed handle also taken)')
                    skipped_count += 1
                    continue
            elif on_conflict == 'update':
                target = existing_by_name or existing_by_email
                target.display_name = u.real_name or target.display_name
                target.location     = u.location or target.location
                target.access_level = u.access_level
                if u.login_count > (target.login_count or 0):
                    target.login_count = u.login_count
                if u.last_login and (not target.last_login or u.last_login > target.last_login):
                    target.last_login = u.last_login
                session.commit()
                ok(f'UPDATE {handle}')
                imported_count += 1
                report_rows.append({'handle': handle, 'email': target.email,
                                     'temp_password': '(existing account — password unchanged)',
                                     'real_name': u.real_name,
                                     'location': u.location,
                                     'access_level': u.access_level,
                                     'source': u.source})
                continue

        user = User(
            username      = handle,
            email         = email,
            password_hash = generate_password_hash(temp_pw),
            display_name  = u.real_name or handle,
            location      = u.location,
            access_level  = u.access_level,
            login_count   = u.login_count,
            created_at    = u.created_at or datetime.utcnow(),
            last_login    = u.last_login,
            is_active     = True,
            is_admin      = u.access_level >= 100,
            is_verified   = not require_verify,
        )
        try:
            session.add(user)
            session.commit()
            ok(f'ADD   {handle:<25}  level={u.access_level}')
            imported_count += 1
            report_rows.append({'handle': handle, 'email': email,
                                 'temp_password': temp_pw,
                                 'real_name': u.real_name,
                                 'location': u.location,
                                 'access_level': u.access_level,
                                 'source': u.source})
        except Exception as exc:
            session.rollback()
            warn(f'ERR   {handle}: {exc}')
            skipped_count += 1

    if not dry_run:
        session.close()

    # Write report CSV
    if report_rows:
        with open(report_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'handle', 'email', 'temp_password', 'real_name',
                'location', 'access_level', 'source'])
            writer.writeheader()
            writer.writerows(report_rows)

    head('Summary')
    print(f'  {"[DRY RUN] " if dry_run else ""}Imported : {imported_count}')
    if skipped_count:
        print(f'  Skipped  : {skipped_count}')
    if conflict_count:
        print(f'  Conflicts: {conflict_count}  (mode: {on_conflict})')
    if report_rows:
        print(f'  Report   : {report_path}')
    if not dry_run and imported_count:
        warn('Temporary passwords written to the report CSV. '
             'Notify users and ask them to change their password on first login.')

    return 2 if skipped_count else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='anetbbs-import-users',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument('source', choices=['synchronet', 'mystic'],
                   help='Source BBS software')
    p.add_argument('userdb', type=Path,
                   help='Path to the source user database file '
                        '(e.g. sbbs/data/user/user.dat or mystic/data/users.dat)')
    p.add_argument('--dry-run', action='store_true',
                   help='Parse and display users without writing to the database')
    p.add_argument('--on-conflict', choices=['skip', 'update', 'rename'],
                   default='skip',
                   help='What to do when a username or email already exists '
                        '(default: skip)')
    p.add_argument('--email-domain', default='localhost',
                   metavar='DOMAIN',
                   help='Domain used to fabricate emails for users who have '
                        'no internet address on file (default: localhost)')
    p.add_argument('--require-verify', action='store_true',
                   help='Mark imported accounts is_verified=False so the sysop '
                        'must approve each one before they can log in')
    p.add_argument('--report', type=Path, default=Path('import_report.csv'),
                   metavar='FILE',
                   help='Path for the CSV report of imported users + temp passwords '
                        '(default: import_report.csv)')
    return p


def main():
    parser = build_parser()
    args   = parser.parse_args()

    path: Path = args.userdb
    if not path.exists():
        err(f'File not found: {path}')
        sys.exit(1)

    head(f'ANetBBS User Import  ·  source={args.source}  ·  file={path}')
    info(f'DB : {_get_db_uri()}')
    if args.dry_run:
        info('DRY RUN — nothing will be written to the database.')

    if args.source == 'synchronet':
        users = import_synchronet(path)
    else:
        users = import_mystic(path)

    exit_code = run_import(
        users,
        dry_run       = args.dry_run,
        on_conflict   = args.on_conflict,
        email_domain  = args.email_domain,
        require_verify= args.require_verify,
        report_path   = args.report,
    )
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
