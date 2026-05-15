# anetbbs/web/site_pages.py
"""
Sysop-editable static pages — currently used for the BBS history page,
extensible to any other slug-keyed content.
"""
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from ..models import db, SitePage

site_pages_bp = Blueprint('site_pages', __name__, url_prefix='/page')


_DEFAULT_HISTORY_MD = """\
# About this BBS

(This page is editable by the sysop — visit `/page/history/edit` to update.)

Add the story of your BBS here:

* When was it started?
* Who's been a sysop / co-sysop?
* Notable milestones, outages, callsigns?
* Networks you're a member of?
* Why you keep running it?
"""


def _get_or_default(slug, default_title, default_md):
    p = SitePage.query.filter_by(slug=slug).first()
    if p is None:
        p = SitePage(slug=slug, title=default_title, content_md=default_md)
        db.session.add(p)
        db.session.commit()
    return p


@site_pages_bp.route('/<slug>')
def view(slug):
    p = SitePage.query.filter_by(slug=slug).first()
    if p is None:
        if slug == 'history':
            p = _get_or_default('history', 'BBS History', _DEFAULT_HISTORY_MD)
        else:
            abort(404)
    return render_template('site_pages/view.html', page=p)


@site_pages_bp.route('/<slug>/edit', methods=['GET', 'POST'])
@login_required
def edit(slug):
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    p = SitePage.query.filter_by(slug=slug).first()
    if p is None:
        if slug == 'history':
            p = _get_or_default('history', 'BBS History', _DEFAULT_HISTORY_MD)
        else:
            # Allow creating arbitrary new slug-pages from the edit URL.
            p = SitePage(slug=slug, title=slug.replace('-', ' ').title(),
                         content_md='')
            db.session.add(p)
            db.session.commit()

    if request.method == 'POST':
        p.title = (request.form.get('title') or p.title).strip()
        p.content_md = request.form.get('content_md') or ''
        p.updated_by_id = current_user.id
        p.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Page saved.', 'success')
        return redirect(url_for('site_pages.view', slug=slug))

    return render_template('site_pages/edit.html', page=p)
