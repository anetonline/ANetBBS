#!/usr/bin/env python3
"""
One-shot admin tool: hard-delete a user AND every row in every table
that references them.

Context: User has 50+ NOT NULL foreign keys pointing at it across the
schema, and only two relationships (posts, messages) have
cascade='all, delete-orphan' configured -- deleting a user with any
real activity (game sessions, PMs, chat messages, etc.) normally fails
with an IntegrityError (see the 2026-09-01 delete-user fix, which
catches that and shows a graceful "use Ban/Deactivate instead"
message rather than a raw 500). This tool is for when you genuinely
want the account and all its data gone -- e.g. a test/duplicate
account colliding with real-name matching in echomail -- not just
deactivated.

Every FK-to-users.id column is discovered by introspecting the actual
database schema (not a hardcoded table list), so this stays correct
even as new tables get added later.

Usage:
    cd /opt/anetbbs   # (or wherever anetbbs-rebuilt is installed)
    python -m tools.delete_user_and_related_data someusername              # dry-run
    python -m tools.delete_user_and_related_data someusername --apply       # actually delete
"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('username', help='Username of the account to delete.')
    parser.add_argument('--apply', action='store_true',
                        help='Actually delete everything (default is dry-run).')
    args = parser.parse_args()

    from anetbbs.web_app import create_app
    from anetbbs.models import db, User
    import sqlalchemy as sa

    app = create_app()
    with app.app_context():
        user = User.query.filter_by(username=args.username).first()
        if user is None:
            print(f'No user named {args.username!r} found.')
            return 1
        uid = user.id
        print(f'Found user {user.username!r} (id={uid}, email={user.email!r})')
        print()

        inspector = sa.inspect(db.engine)
        to_clean = []  # (table_name, column_name)
        for table_name in inspector.get_table_names():
            if table_name == 'users':
                continue
            for fk in inspector.get_foreign_keys(table_name):
                if fk.get('referred_table') != 'users':
                    continue
                for local_col, remote_col in zip(
                        fk['constrained_columns'], fk['referred_columns']):
                    if remote_col == 'id':
                        to_clean.append((table_name, local_col))

        total = 0
        found = []
        for table_name, col in to_clean:
            count = db.session.execute(
                sa.text(f'SELECT COUNT(*) FROM "{table_name}" WHERE "{col}" = :uid'),
                {'uid': uid}
            ).scalar()
            if count:
                found.append((table_name, col, count))
                total += count

        if not found:
            print('No related rows found anywhere -- this account should '
                 'already be deletable normally from Admin -> Users.')
        else:
            print(f'Related rows found ({total} total across {len(found)} table(s)):')
            for table_name, col, count in found:
                print(f'  {table_name}.{col}: {count} row(s)')

        if not args.apply:
            print(f'\nDry-run only. Re-run with --apply to permanently delete '
                 f'{args.username!r} and all {total} related row(s) above.')
            return 0

        for table_name, col, _count in found:
            db.session.execute(
                sa.text(f'DELETE FROM "{table_name}" WHERE "{col}" = :uid'),
                {'uid': uid})
        db.session.delete(user)
        db.session.commit()
        print(f'\nDeleted user {args.username!r} and {total} related row(s).')
        return 0


if __name__ == '__main__':
    sys.exit(main())
