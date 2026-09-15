#!/usr/bin/env python3
"""
anetbbs-export-users — export ANetBBS user accounts to a portable file.

Writes username, email, display name, access level, admin/active/locked
flags, profile fields (bio/location/website/signature/tagline),
account-history fields (created_at/last_login/login_count), and language
preference for every user (or every active user, by default).

Passwords are NEVER exported — password_hash is a one-way hash anyway,
and no other BBS software would understand ANetBBS's hashing scheme,
same reasoning tools/import_users.py already documents for its own
"passwords never migrate" rule in the opposite direction. A sysop
migrating away from ANetBBS gets everything else needed to recreate
accounts elsewhere; users get new passwords on the destination system,
same as any cross-platform migration.

Usage examples
  # Active users only, CSV to users_export.csv (defaults)
  python tools/export_users.py

  # Every account, including inactive/locked ones, as JSON
  python tools/export_users.py --format json --all -o everyone.json

DATABASE_URL is read from the environment, same convention as every
other tools/*.py script and the BBS itself.

Exit codes: 0 = success, 1 = fatal error.
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Colour helpers (no deps) — same shape as tools/import_users.py
# ---------------------------------------------------------------------------
_TTY = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f'\x1b[{code}m{text}\x1b[0m' if _TTY else text

def ok(s):   print(_c('92', f'  ✓ {s}'))
def err(s):  print(_c('91', f'  ✗ {s}'), file=sys.stderr)
def info(s): print(_c('90', f'    {s}'))
def head(s): print(_c('96;1', f'\n{s}'))


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


# The exported field set — deliberately excludes password_hash,
# avatar_upload/avatar_url (local file paths, meaningless elsewhere),
# theme_id/notify_prefs (ANetBBS-internal foreign keys/JSON blobs with
# no portable meaning on another system).
FIELDS = [
    'username', 'email', 'display_name', 'access_level',
    'is_admin', 'is_active', 'is_locked', 'bio', 'location',
    'website', 'signature', 'tagline', 'show_email', 'language',
    'codepage', 'date_of_birth', 'created_at', 'last_login', 'login_count',
]


def _iso(value):
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def export_users(session, include_inactive: bool):
    from anetbbs.models import User
    q = session.query(User)
    if not include_inactive:
        q = q.filter(User.is_active.is_(True))
    return q.order_by(User.username).all()


def write_csv(users, path: Path):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(FIELDS)
        for u in users:
            writer.writerow([_iso(getattr(u, field)) for field in FIELDS])


def write_json(users, path: Path):
    rows = []
    for u in users:
        row = {}
        for field in FIELDS:
            value = getattr(u, field)
            row[field] = _iso(value) if isinstance(value, datetime) else value
        rows.append(row)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(rows, f, indent=2, default=str)
        f.write('\n')


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='anetbbs-export-users',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument('--format', choices=['csv', 'json'], default='csv',
                   help='Output file format (default: csv)')
    p.add_argument('-o', '--output', type=Path, default=None,
                   metavar='FILE',
                   help='Output path (default: users_export.csv or '
                        'users_export.json, matching --format)')
    p.add_argument('--all', action='store_true', dest='include_inactive',
                   help='Include inactive and locked accounts too '
                        '(default: active accounts only)')
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    output = args.output or Path(f'users_export.{args.format}')

    head(f'ANetBBS User Export  ·  format={args.format}  ·  file={output}')
    info(f'DB : {_get_db_uri()}')

    try:
        session = _get_session()
        users = export_users(session, args.include_inactive)
    except Exception as exc:
        err(f'Could not read users: {exc}')
        return 1

    if args.format == 'csv':
        write_csv(users, output)
    else:
        write_json(users, output)

    ok(f'Exported {len(users)} user(s) to {output}')
    if not args.include_inactive:
        info('Inactive/locked accounts were skipped — pass --all to include them.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
