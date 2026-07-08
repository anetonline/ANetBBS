#!/usr/bin/env python3
"""
One-shot cleanup for QWK messages duplicated by the missing-msg_id dedup
bug (see anetbbs/echomail/qwk.py): inbound messages from a hub that
doesn't tunnel @MSGID: kludges got no msg_id at all, so the dedup check
in poller.py:_import_message never fired -- every poll that received any
overlapping content from the hub re-imported the same real messages as
brand new, uncapped.

Safe to run any time after upgrading past the fix -- it wipes ALL
messages in QWK-network echo areas (keeping the areas + your
subscriptions) so the next poll repopulates them cleanly. Inbound QWK
messages now always get a msg_id (real or a deterministic content-hash
fallback), so re-polls won't re-duplicate going forward.

Usage:
    cd /opt/anetbbs   # (or wherever anetbbs-rebuilt is installed)
    python -m tools.dedupe_qwk_messages                        # dry-run, all QWK networks
    python -m tools.dedupe_qwk_messages --apply                # actually wipe + reset counters
    python -m tools.dedupe_qwk_messages --network-id 3 --apply # limit to one network
"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true',
                        help='Actually delete (default is dry-run).')
    parser.add_argument('--network-id', type=int, default=None,
                        help='Limit to one EchomailNetwork id (default: all QWK networks).')
    args = parser.parse_args()

    from anetbbs.web_app import create_app
    from anetbbs.models import db, EchomailNetwork, EchoArea, EchomailMessage

    app = create_app()
    with app.app_context():
        networks_q = EchomailNetwork.query.filter_by(network_type='qwk')
        if args.network_id is not None:
            networks_q = networks_q.filter_by(id=args.network_id)
        networks = networks_q.all()
        if not networks:
            print("No matching QWK networks found.")
            return 0

        rows = []
        total_msgs = 0
        print("=== QWK areas to be reset ===")
        for network in networks:
            for area in EchoArea.query.filter_by(network_id=network.id).all():
                count = EchomailMessage.query.filter_by(area_id=area.id).count()
                if count == 0:
                    continue
                rows.append((network, area, count))
                total_msgs += count
                print(f"  network={network.name!r:<20} area={area.tag:<14} "
                      f"name={area.name!r:<32} messages={count}")

        if not rows:
            print("  none -- nothing to clean up.")
            return 0

        print(f"\nTotal: {total_msgs} message(s) across {len(rows)} area(s).")

        if not args.apply:
            print("\nDry-run only. Re-run with --apply to actually delete.")
            print("After applying, the next poll will repopulate these areas cleanly.")
            return 0

        for network, area, count in rows:
            EchomailMessage.query.filter_by(area_id=area.id).delete()
            area.total_messages = 0
            area.last_message_at = None
        db.session.commit()
        print(f"\nDeleted {total_msgs} message(s) from {len(rows)} area(s).")
        print("Next poll will repopulate with clean, deduped messages.")
        return 0


if __name__ == '__main__':
    sys.exit(main())
