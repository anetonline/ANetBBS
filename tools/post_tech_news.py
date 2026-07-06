#!/usr/bin/env python3
"""
Post the top tech news stories from an RSS feed into ANotherNetwork's
ANN.TECH echo area, once a day.

This is intentionally a standalone script, not a general ANetBBS
feature — it's meant to run on bbs.a-net.fyi specifically (the
ANotherNetwork hub), via your own crontab. It ships with the codebase
(version-controlled, testable) but does nothing unless you invoke it;
no other install runs this automatically.

ANN.TECH exists as two separate EchoArea rows (one for the BinkP
network entry, one for QWK — see anetbbs/web_app.py's ANotherNetwork
seeding) that share messages via the hub tosser. This posts to both
rows so the story shows up for BinkP and QWK subscribers alike.

Messages are queued as normal outbound EchomailMessage rows — the
existing poller/tosser picks them up and distributes them on its next
cycle, same as anything a human sysop composes through the terminal.

Usage:
    cd /opt/anetbbs   # (or wherever this install lives)
    venv/bin/python -m tools.post_tech_news                    # post today's stories
    venv/bin/python -m tools.post_tech_news --dry-run           # show what would post, don't write
    venv/bin/python -m tools.post_tech_news --max-stories 3
    venv/bin/python -m tools.post_tech_news --feed-url https://example.com/feed

Crontab (once daily at 8am):
    0 8 * * * cd /opt/anetbbs && venv/bin/python -m tools.post_tech_news >> logs/tech_news.log 2>&1
"""
import argparse
import re
import sys

DEFAULT_FEED_URL = 'https://feeds.arstechnica.com/arstechnica/index'
DEFAULT_MAX_STORIES = 5
AREA_TAG = 'ANN.TECH'
FROM_NAME = 'Tech News Bot'
TO_NAME = 'All'


def _strip_html(text):
    """RSS summaries are frequently HTML fragments — strip tags for a
    plain-text echomail body."""
    text = re.sub(r'<[^<]+?>', '', text or '')
    return re.sub(r'\s+', ' ', text).strip()


def _truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(' ', 1)[0] + '...'


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true',
                        help="Show what would be posted without writing to the DB.")
    parser.add_argument('--max-stories', type=int, default=DEFAULT_MAX_STORIES,
                        help=f"Max stories to post per run (default {DEFAULT_MAX_STORIES}).")
    parser.add_argument('--feed-url', type=str, default=DEFAULT_FEED_URL,
                        help=f"RSS feed URL (default: {DEFAULT_FEED_URL}).")
    args = parser.parse_args()

    import feedparser
    from anetbbs.web_app import create_app
    from anetbbs.models import db, EchoArea, EchomailMessage

    app = create_app()
    with app.app_context():
        areas = EchoArea.query.filter_by(tag=AREA_TAG).all()
        if not areas:
            print(f"No {AREA_TAG} areas found — is ANotherNetwork seeded on this install?")
            sys.exit(1)

        feed = feedparser.parse(args.feed_url)
        if getattr(feed, 'bozo', False):
            print(f"Warning: feed parse issue: {feed.bozo_exception}")
        entries = feed.entries[:args.max_stories]
        if not entries:
            print("No entries found in feed — nothing to post.")
            return

        feed_title = feed.feed.get('title', args.feed_url) if hasattr(feed, 'feed') else args.feed_url

        posted = 0
        for entry in entries:
            link = getattr(entry, 'link', '').strip()
            title = _truncate(getattr(entry, 'title', '(no title)').strip(), 190)
            if not link:
                continue

            # Dedup: skip a story we've already posted (matched by link,
            # which RSS feeds treat as a stable per-story identifier).
            already_posted = EchomailMessage.query.filter(
                EchomailMessage.from_name == FROM_NAME,
                EchomailMessage.body.like(f'%{link}%'),
            ).first()
            if already_posted:
                continue

            summary = _strip_html(
                getattr(entry, 'summary', '') or getattr(entry, 'description', ''))
            summary = _truncate(summary, 400)

            body = f"{summary}\n\nRead more: {link}\n\n-- via {feed_title}"

            if args.dry_run:
                print(f"[dry-run] Would post: {title}\n  {link}\n")
                posted += 1
                continue

            for area in areas:
                db.session.add(EchomailMessage(
                    area_id=area.id,
                    network_id=area.network_id,
                    from_name=FROM_NAME,
                    to_name=TO_NAME,
                    subject=title,
                    body=body,
                    direction='outbound',
                ))
            posted += 1

        if not args.dry_run:
            db.session.commit()

        verb = 'Would post' if args.dry_run else 'Posted'
        print(f"{verb} {posted} new stor{'y' if posted == 1 else 'ies'} "
              f"to {len(areas)} {AREA_TAG} row(s).")


if __name__ == '__main__':
    main()
