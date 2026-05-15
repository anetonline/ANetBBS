#!/usr/bin/env python3
"""
One-shot cleanup for QWK echomail data corrupted by the pre-v129 parser
bug (num_chunks read as binary instead of ASCII, causing misaligned reads
that produced single huge garbage messages and bogus conference areas).

Two cleanup modes — combine as needed:

  --bogus-areas   Drop any QWK_<n> area whose <n> isn't a known DOVE-Net
                  conference (default 2001-2030). These were created by
                  the misaligned parser landing on random body bytes.

  --reset-msgs    Delete ALL messages from VALID QWK areas, keeping the
                  area + your subscription. Next poll will repopulate
                  with clean messages parsed by the v129 fix.

Usage:
    cd /opt/anetbbs   # (or wherever anetbbs-rebuilt is installed)
    python -m tools.purge_bad_qwk_areas                              # dry-run all
    python -m tools.purge_bad_qwk_areas --bogus-areas --apply        # delete bogus
    python -m tools.purge_bad_qwk_areas --reset-msgs --apply         # wipe msgs in valid
    python -m tools.purge_bad_qwk_areas --bogus-areas --reset-msgs --apply   # both

Override the valid-conf set if you carry QWK networks other than DOVE-Net:
    python -m tools.purge_bad_qwk_areas --valid-confs 100,101,200 --bogus-areas --apply
"""
import argparse
import sys

# Known DOVE-Net conference numbers (Synchronet hub, 2026 catalog).
DOVENET_VALID_CONFS = {
    2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010,
    2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020,
    2021, 2022, 2030,
}


def _conf_num(area):
    if not area.tag.startswith('QWK_'):
        return None
    try:
        return int(area.tag.split('_', 1)[1])
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true',
                        help='Actually delete (default is dry-run).')
    parser.add_argument('--bogus-areas', action='store_true',
                        help='Drop QWK areas whose conf# is not in the valid set.')
    parser.add_argument('--reset-msgs', action='store_true',
                        help='Delete all messages from valid QWK areas '
                             '(area + subscription kept).')
    parser.add_argument('--valid-confs', type=str, default='',
                        help='Comma-separated list of valid conf numbers '
                             '(overrides DOVE-Net default).')
    args = parser.parse_args()

    if not (args.bogus_areas or args.reset_msgs):
        # Default: show everything in dry-run mode.
        args.bogus_areas = True
        args.reset_msgs = True

    valid = (set(int(x) for x in args.valid_confs.split(',') if x.strip())
             if args.valid_confs else DOVENET_VALID_CONFS)

    from anetbbs.web_app import create_app
    from anetbbs.models import db, EchoArea, EchomailMessage

    app = create_app()
    with app.app_context():
        bogus = []
        valid_areas = []
        for area in EchoArea.query.filter(EchoArea.tag.like('QWK_%')).all():
            num = _conf_num(area)
            if num is None:
                continue
            msgs = EchomailMessage.query.filter_by(area_id=area.id).count()
            if num not in valid:
                bogus.append((area, num, msgs))
            else:
                valid_areas.append((area, num, msgs))

        if args.bogus_areas:
            print(f"\n=== Bogus QWK areas (conf# not in valid set) ===")
            if not bogus:
                print("  none")
            for area, num, msgs in bogus:
                print(f"  - id={area.id:<5}  tag={area.tag:<14}  "
                      f"name={area.name!r:<32}  messages={msgs}")

        if args.reset_msgs:
            print(f"\n=== Valid QWK areas (messages will be wiped) ===")
            if not valid_areas:
                print("  none")
            for area, num, msgs in valid_areas:
                print(f"  - id={area.id:<5}  tag={area.tag:<14}  "
                      f"name={area.name!r:<32}  messages={msgs}")

        if not args.apply:
            print("\nDry-run only. Re-run with --apply to actually delete.")
            return 0

        n_areas = 0
        n_msgs = 0
        if args.bogus_areas:
            for area, _, _ in bogus:
                n_msgs += EchomailMessage.query.filter_by(area_id=area.id).delete()
                db.session.delete(area)
                n_areas += 1
        if args.reset_msgs:
            for area, _, _ in valid_areas:
                deleted = EchomailMessage.query.filter_by(area_id=area.id).delete()
                n_msgs += deleted
                # Reset the area's per-area counters too
                area.total_messages = 0
                area.last_message_at = None
        db.session.commit()
        print(f"\nDeleted {n_areas} bogus area(s) and {n_msgs} message(s).")
        print("Next poll will repopulate valid areas with clean v129-parsed messages.")
        return 0


if __name__ == '__main__':
    sys.exit(main())
