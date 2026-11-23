"""Read-only audit of account names and private-message ownership.

Run it against a COPY of the database (for example the newest backup
from update.sh) before upgrading. It opens the file read-only, changes
nothing, and works on databases from before and after this upgrade.

It reports:
  1. Name collisions: display or real names that match another account's
     username, display name or real name; names with % or _ in them;
     generic role names used as display or real names.
  2. What the upgrade will do with each older private message (QWK
     NETMAIL-area rows and FTN netmail): link it, or leave it admin-only
     and queue it for sysop review, and why. The same rules as the
     upgrade itself (anetbbs/messaging/recipients.py).
  3. Existing FTN netmail links that the upgrade will keep or remove.

Only names, ids, dates and reasons are printed. Message subjects and
bodies are never read.

Usage:
    python -m tools.audit_private_message_ownership --db data/backups/<dir>/anetbbs.db.bak
    python -m tools.audit_private_message_ownership --db <file> --json > audit.json
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from types import SimpleNamespace


def _ts(value):
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace('Z', ''))
    except ValueError:
        return None


def _columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}  # nosec B608


def collect(db_path):
    from anetbbs.messaging.recipients import (
        ASSIGNED, RESERVED_NAMES, AccountSnapshot, fold, plan_history_row)

    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    users = [(r['id'], r['username'], r['display_name'], r['real_name'],
              _ts(r['created_at']))
             for r in conn.execute('SELECT id, username, display_name, real_name, '
                                   'created_at FROM users')]
    admins = [r[0] for r in conn.execute('SELECT id FROM users WHERE is_admin = 1')]
    akas = []
    if 'user_akas' in {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}:
        akas = [(r['user_id'], r['address'], _ts(r['created_at']))
                for r in conn.execute('SELECT user_id, address, created_at FROM user_akas')]
    snap = AccountSnapshot(users, akas, admins)
    names = {u[0]: u[1] for u in users}

    # 1. name collisions
    collisions = []
    owners = {}
    for uid, username, display_name, real_name, _c in users:
        for field, value in (('username', username), ('display_name', display_name),
                             ('real_name', real_name)):
            if fold(value):
                owners.setdefault(fold(value), []).append((uid, field))
    for uid, username, display_name, real_name, _c in users:
        for field, value in (('display_name', display_name), ('real_name', real_name)):
            f = fold(value)
            if not f:
                continue
            others = sorted({names[o] for o, _fld in owners.get(f, []) if o != uid})
            issues = []
            if others:
                issues.append('same as another account: ' + ', '.join(others))
            if '%' in value or '_' in value:
                issues.append('contains % or _')
            if f in RESERVED_NAMES:
                issues.append('generic role name')
            if issues:
                collisions.append({'user': username, 'field': field, 'value': value,
                                   'issues': issues})
        if '_' in (username or '') or '%' in (username or ''):
            collisions.append({'user': username, 'field': 'username', 'value': username,
                               'issues': ['contains % or _ (review only; often legitimate)']})

    # 2 and 3. what the upgrade will do
    private_ids = [r[0] for r in conn.execute(
        "SELECT id FROM echo_areas WHERE upper(trim(tag)) = 'NETMAIL'")]
    plan = []
    for kind, table in (('qwk', 'echomail_messages'), ('netmail', 'netmail_messages')):
        cols = _columns(conn, table)
        wanted = ['id', 'direction', 'from_name', 'to_name', 'from_address',
                  'to_address', 'created_at', 'imported_at', 'received_at',
                  'from_user_id', 'to_user_id']
        select = ', '.join(c if c in cols else f'NULL AS {c}' for c in wanted)
        where = []
        if kind == 'qwk':
            if not private_ids:
                continue
            where.append('area_id IN (%s)' % ','.join(str(int(i)) for i in private_ids))
        if 'owner_resolved' in cols:
            where.append('owner_resolved IS NULL')
        sql = f'SELECT {select} FROM {table}' + (' WHERE ' + ' AND '.join(where) if where else '')  # nosec B608
        for r in conn.execute(sql):
            m = SimpleNamespace(**{k: r[k] for k in wanted})
            for k in ('created_at', 'imported_at', 'received_at'):
                setattr(m, k, _ts(getattr(m, k)))
            if kind == 'qwk':
                m.from_user_id = m.to_user_id = None
            stored = m.imported_at or m.created_at or m.received_at
            for party, uid, reason, sug, prev in plan_history_row(snap, kind, m):
                plan.append({
                    'kind': kind, 'id': m.id, 'party': party,
                    'name': m.to_name if party == 'to' else m.from_name,
                    'stored': str(stored)[:16] if stored else '',
                    'action': 'link' if reason in ASSIGNED else
                              ('none' if reason == 'unresolved' else 'review'),
                    'reason': reason,
                    'user': names.get(uid) if uid else None,
                    'suggested': names.get(sug) if sug else None,
                    'previous': names.get(prev, f'#{prev} (missing)') if prev else None,
                })
    conn.close()
    return {'users': len(users), 'collisions': collisions, 'plan': plan,
            'summary': dict(Counter((p['kind'], p['action'], p['reason']) for p in plan))}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--db', required=True, help='Path to a COPY of the SQLite database.')
    ap.add_argument('--json', action='store_true', help='Print JSON instead of text.')
    args = ap.parse_args(argv)
    result = collect(args.db)
    if args.json:
        result['summary'] = [{'kind': k, 'action': a, 'reason': r, 'count': n}
                             for (k, a, r), n in sorted(result['summary'].items())]
        print(json.dumps(result, indent=2, default=str))
        return 0
    print(f'Read-only audit of {args.db} ({result["users"]} accounts). Nothing was changed.\n')
    print(f'1. Name collisions: {len(result["collisions"])}')
    for c in result['collisions']:
        print(f'   {c["user"]:<20} {c["field"]:<12} {c["value"]!r}: {"; ".join(c["issues"])}')
    print('\n2. What the upgrade will do with older private messages:')
    if not result['summary']:
        print('   nothing to do')
    for (kind, action, reason), n in sorted(result['summary'].items()):
        print(f'   {kind:<8} {action:<7} {reason:<18} {n}')
    review = [p for p in result['plan'] if p['action'] == 'review']
    print(f'\n3. Messages that will be admin-only and queued for review: {len(review)}')
    for p in review:
        print(f'   {p["kind"]:<8} #{p["id"]:<7} {p["party"]:<4} {p["stored"]:<16} '
              f'{(p["name"] or "")[:30]!r:<32} {p["reason"]:<15} '
              f'suggested={p["suggested"] or "-"} previous={p["previous"] or "-"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
