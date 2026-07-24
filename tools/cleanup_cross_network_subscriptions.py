#!/usr/bin/env python3
"""
One-shot cleanup for subscription rows created by the pre-fix AreaFix/
FileFix cross-network bug: hub-side +ALL/+TAG handling never scoped the
available-area list to the requesting downstream node's own network_id,
so any node could +ALL its way into every echo/file network this hub
relays, not just the one it's actually a member of.

Finds (and, with --apply, deletes) two kinds of bad rows:

  EchoAreaNode        -- node.network_id set, but the subscribed
                         EchoArea belongs to a DIFFERENT network.
  FileEchoSubscription -- same check, resolving peer_address to a
                         BinkPNode first (FileEchoSubscription has no
                         node_id FK -- see filefix.py's own docstring),
                         PLUS any subscription to a local-only file area
                         (FileArea.network_id IS NULL -- these were never
                         meant to be hatched to any downstream node at
                         all, and the pre-fix bug swept them in too).

BinkPNode rows with network_id unset (legacy, pre-dates that column) are
reported separately and never touched -- there's no way to tell a
legitimate subscription from a leaked one without knowing which network
the node actually belongs to.

Usage:
    cd /opt/anetbbs   # (or wherever anetbbs-rebuilt is installed)
    python -m tools.cleanup_cross_network_subscriptions               # dry-run
    python -m tools.cleanup_cross_network_subscriptions --apply       # delete
    python -m tools.cleanup_cross_network_subscriptions --node 1200:1/4 --apply
"""
import argparse
import sys


def _resolve_node_for_peer_address(peer_address, nodes_by_address):
    """Same candidate-matching logic as filefix.handle_filefix_netmail:
    try the exact peer_address, then its bare (no @domain) form."""
    if peer_address in nodes_by_address:
        return nodes_by_address[peer_address]
    bare = peer_address.split('@', 1)[0].strip()
    return nodes_by_address.get(bare)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true',
                        help='Actually delete (default is dry-run).')
    parser.add_argument('--node', type=str, default='',
                        help='Only consider this one node (by ftn_address). '
                             'Default: check every node.')
    args = parser.parse_args()

    from anetbbs.web_app import create_app
    from anetbbs.models import (db, BinkPNode, EchoArea, EchoAreaNode,
                                FileArea, FileEchoSubscription, EchomailNetwork)

    app = create_app()
    with app.app_context():
        all_nodes = BinkPNode.query.all()
        nodes_by_address = {}
        for n in all_nodes:
            if n.ftn_address:
                nodes_by_address[n.ftn_address] = n
                bare = n.ftn_address.split('@', 1)[0].strip()
                nodes_by_address.setdefault(bare, n)
        net_names = {n.id: n.name for n in EchomailNetwork.query.all()}

        if args.node:
            all_nodes = [n for n in all_nodes if n.ftn_address == args.node]
            if not all_nodes:
                print(f"No BinkPNode found with ftn_address={args.node!r}")
                return 1

        unverifiable_nodes = [n for n in all_nodes if n.network_id is None]

        bad_echo_rows = []   # (EchoAreaNode row, node, area)
        bad_file_rows = []   # (FileEchoSubscription row, node_or_None, area)
        unresolvable_file_rows = []  # rows whose peer_address matches no node

        for n in all_nodes:
            if n.network_id is None:
                continue
            subs = EchoAreaNode.query.filter_by(node_id=n.id).all()
            for row in subs:
                area = EchoArea.query.get(row.echo_area_id)
                if area is not None and area.network_id != n.network_id:
                    bad_echo_rows.append((row, n, area))

        file_subs = FileEchoSubscription.query.all()
        node_addr_filter = args.node
        for row in file_subs:
            node = _resolve_node_for_peer_address(row.peer_address, nodes_by_address)
            if node_addr_filter and (node is None or node.ftn_address != node_addr_filter):
                continue
            if node is None:
                unresolvable_file_rows.append(row)
                continue
            if node.network_id is None:
                continue
            area = FileArea.query.get(row.file_area_id)
            if area is not None and area.network_id != node.network_id:
                bad_file_rows.append((row, node, area))

        print("=== BinkPNode rows with no network_id set (skipped -- can't verify) ===")
        if not unverifiable_nodes:
            print("  none")
        for n in unverifiable_nodes:
            print(f"  - id={n.id:<5} ftn_address={n.ftn_address}")

        print("\n=== EchoAreaNode rows subscribed to a DIFFERENT network's area ===")
        if not bad_echo_rows:
            print("  none")
        for row, n, area in bad_echo_rows:
            print(f"  - node {n.ftn_address} (network={net_names.get(n.network_id, n.network_id)})"
                  f"  ->  area {area.tag!r} (network={net_names.get(area.network_id, area.network_id)})")

        print("\n=== FileEchoSubscription rows subscribed to a DIFFERENT network's "
              "(or local-only) file area ===")
        if not bad_file_rows:
            print("  none")
        for row, n, area in bad_file_rows:
            net_label = net_names.get(area.network_id, area.network_id) if area.network_id else 'LOCAL-ONLY'
            print(f"  - node {n.ftn_address} (network={net_names.get(n.network_id, n.network_id)})"
                  f"  ->  file area {area.tag!r} (network={net_label})")

        if unresolvable_file_rows:
            print("\n=== FileEchoSubscription rows whose peer_address matches no "
                  "known BinkPNode (skipped -- can't verify) ===")
            for row in unresolvable_file_rows:
                print(f"  - peer_address={row.peer_address}  file_area_id={row.file_area_id}")

        if not args.apply:
            print("\nDry-run only. Re-run with --apply to actually delete the "
                  "rows listed above.")
            return 0

        for row, _n, _area in bad_echo_rows:
            db.session.delete(row)
        for row, _n, _area in bad_file_rows:
            db.session.delete(row)
        db.session.commit()
        print(f"\nDeleted {len(bad_echo_rows)} EchoAreaNode row(s) and "
              f"{len(bad_file_rows)} FileEchoSubscription row(s).")
        return 0


if __name__ == '__main__':
    sys.exit(main())
