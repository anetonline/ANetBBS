#!/usr/bin/env python3
"""
List / remove QWK node join requests (QWKNodeRequest rows) -- for
clearing out a stale application that's stuck showing "pending" in
Admin -> Echomail -> Hub -> QWK Node Requests, e.g. one submitted
before a feature was finished, or where the applicant's own network
config has since changed and the request is no longer relevant.

Approving/denying through the normal admin web UI is still the right
path for a real, current application -- this is for a request that's
just stuck/stale and needs to go away outright (a hard delete, not a
"denied" status -- denied requests stay visible in the reviewed-history
list, which isn't what you want for something that was never a real
decision to reject).

Safe to run any time -- QWKNodeRequest has exactly one foreign key
(node_id, only ever set on approval), so deleting a pending/unapproved
row has no cascading effects on anything else.

Usage:
    cd /opt/anetbbs   # (or wherever anetbbs-rebuilt is installed)
    python -m tools.manage_qwk_requests --list                    # pending only
    python -m tools.manage_qwk_requests --list --all               # every status
    python -m tools.manage_qwk_requests --remove 7                 # dry-run
    python -m tools.manage_qwk_requests --remove 7 --apply         # actually delete
    python -m tools.manage_qwk_requests --remove-packet-id FIREHK  # match by packet_id instead of row id
"""
import argparse
import sys


def _print_row(req):
    print(f"  id={req.id:<5} packet_id={req.packet_id:<10} bbs_name={req.bbs_name!r:<28} "
          f"status={req.status:<8} applied_via={req.applied_via:<10} "
          f"created_at={req.created_at}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--list', action='store_true', help='List requests.')
    parser.add_argument('--all', action='store_true',
                        help='With --list, show every status, not just pending.')
    parser.add_argument('--remove', type=int, metavar='ID',
                        help='Delete the request with this row id.')
    parser.add_argument('--remove-packet-id', type=str, metavar='PACKET_ID',
                        help='Delete request(s) matching this packet_id instead of a row id.')
    parser.add_argument('--apply', action='store_true',
                        help='Actually delete (default is dry-run for --remove*).')
    args = parser.parse_args()

    if not (args.list or args.remove or args.remove_packet_id):
        args.list = True  # default: show what's there

    from anetbbs.web_app import create_app
    from anetbbs.models import db, QWKNodeRequest

    app = create_app()
    with app.app_context():
        if args.list:
            q = QWKNodeRequest.query
            if not args.all:
                q = q.filter_by(status='pending')
            rows = q.order_by(QWKNodeRequest.created_at.asc()).all()
            label = 'all' if args.all else 'pending'
            print(f"=== QWK node requests ({label}) ===")
            if not rows:
                print("  none")
            for req in rows:
                _print_row(req)
            if not (args.remove or args.remove_packet_id):
                return 0

        targets = []
        if args.remove:
            req = QWKNodeRequest.query.get(args.remove)
            if req is None:
                print(f"No request with id={args.remove} found.")
                return 1
            targets.append(req)
        if args.remove_packet_id:
            found = QWKNodeRequest.query.filter_by(packet_id=args.remove_packet_id).all()
            if not found:
                print(f"No request(s) with packet_id={args.remove_packet_id!r} found.")
                return 1
            targets.extend(found)

        if not targets:
            return 0

        print("\n=== Requests to remove ===")
        for req in targets:
            _print_row(req)

        if not args.apply:
            print("\nDry-run only. Re-run with --apply to actually delete.")
            return 0

        for req in targets:
            db.session.delete(req)
        db.session.commit()
        print(f"\nDeleted {len(targets)} request(s).")
        return 0


if __name__ == '__main__':
    sys.exit(main())
