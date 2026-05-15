# anetbbs/web/feeds.py
"""
RSS / Atom feeds for shouts, board posts, bulletins, and echomail.

Anonymous-readable so external aggregators (Slack RSS, Inoreader, etc.)
can subscribe."""
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from flask import Blueprint, Response, request

from ..models import db, ShoutboxPost, Post, Message, EchomailMessage


feeds_bp = Blueprint('feeds', __name__, url_prefix='/feed')


def _rss(title, link, description, items):
    """Render an RSS 2.0 feed. `items` is a list of dicts."""
    out = ['<?xml version="1.0" encoding="UTF-8" ?>',
           '<rss version="2.0"><channel>',
           f'<title>{escape(title)}</title>',
           f'<link>{escape(link)}</link>',
           f'<description>{escape(description)}</description>',
           f'<lastBuildDate>{datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>']
    for it in items:
        out.append('<item>')
        out.append(f'<title>{escape(it.get("title") or "")}</title>')
        if it.get('link'):
            out.append(f'<link>{escape(it["link"])}</link>')
            out.append(f'<guid>{escape(it["link"])}</guid>')
        if it.get('description'):
            out.append(f'<description>{escape(it["description"])}</description>')
        if it.get('pub_date'):
            out.append(
                f'<pubDate>{it["pub_date"].strftime("%a, %d %b %Y %H:%M:%S +0000")}</pubDate>')
        if it.get('author'):
            out.append(f'<author>{escape(it["author"])}</author>')
        out.append('</item>')
    out.append('</channel></rss>')
    return Response('\n'.join(out), mimetype='application/rss+xml')


@feeds_bp.route('/shouts.xml')
def shouts_rss():
    base = request.host_url.rstrip('/')
    items = []
    try:
        rows = (ShoutboxPost.query.filter_by(is_hidden=False)
                .order_by(ShoutboxPost.created_at.desc())
                .limit(50).all())
        for s in rows:
            items.append({
                'title': f'{s.user.username if s.user else "?"}: {s.text[:60]}',
                'description': s.text,
                'pub_date': s.created_at,
                'author': s.user.username if s.user else '',
                'link': f'{base}/shoutbox/#{s.id}',
            })
    except Exception:
        db.session.rollback()
    return _rss('Shoutbox — ANetBBS', base + '/shoutbox/',
                'Recent shouts.', items)


@feeds_bp.route('/posts.xml')
def posts_rss():
    base = request.host_url.rstrip('/')
    items = []
    try:
        rows = (Post.query.order_by(Post.created_at.desc()).limit(50).all())
        for p in rows:
            items.append({
                'title': p.subject or (p.content or '')[:60],
                'description': p.content or '',
                'pub_date': p.created_at,
                'author': p.author.username if p.author else '',
                'link': f'{base}/boards/post/{p.id}',
            })
    except Exception:
        db.session.rollback()
    return _rss('Board Posts — ANetBBS', base + '/boards/',
                'Recent message-board posts.', items)


@feeds_bp.route('/bulletins.xml')
def bulletins_rss():
    base = request.host_url.rstrip('/')
    items = []
    try:
        rows = (Message.query.order_by(Message.created_at.desc())
                .limit(20).all())
        for b in rows:
            items.append({
                'title': b.title or '(bulletin)',
                'description': b.content or '',
                'pub_date': b.created_at,
                'link': f'{base}/bulletins/{b.id}',
            })
    except Exception:
        db.session.rollback()
    return _rss('Bulletins — ANetBBS', base + '/bulletins/',
                'Sysop bulletins.', items)


@feeds_bp.route('/echomail.xml')
def echomail_rss():
    base = request.host_url.rstrip('/')
    items = []
    try:
        rows = (EchomailMessage.query
                .order_by(EchomailMessage.created_at.desc())
                .limit(50).all())
        for e in rows:
            items.append({
                'title': f'[{(e.area.tag if e.area else "?")}] {e.subject or "(no subject)"}',
                'description': e.body or '',
                'pub_date': e.created_at,
                'author': e.from_name or '',
                'link': f'{base}/echomail/{e.area_id}/{e.id}',
            })
    except Exception:
        db.session.rollback()
    return _rss('Echomail — ANetBBS', base + '/echomail/',
                'Recent echomail across subscribed areas.', items)
