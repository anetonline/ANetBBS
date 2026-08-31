# anetbbs/web/nodelist.py
"""
Nodelist browser blueprint.

Routes:
    GET  /nodelist/                 search/browse imported nodelists
    POST /nodelist/admin/upload     sysop uploads a new nodelist file
"""
import os
import tempfile
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort, current_app)
from flask_login import login_required, current_user

from ..models import db, Nodelist, NodelistEntry
from ..echomail.nodelist import import_from_path, search

nodelist_bp = Blueprint('nodelist', __name__, url_prefix='/nodelist')


@nodelist_bp.route('/')
@login_required
def browse():
    q = (request.args.get('q') or '').strip()
    domain = request.args.get('domain') or None
    entries = []
    if q:
        entries = search(q, domain=domain, limit=300)
    nodelists = Nodelist.query.order_by(Nodelist.imported_at.desc()).all()
    return render_template('nodelist/browse.html',
                           q=q, domain=domain,
                           entries=entries, nodelists=nodelists)


@nodelist_bp.route('/<int:entry_id>')
@login_required
def view_entry(entry_id):
    entry = NodelistEntry.query.get_or_404(entry_id)
    import json as _json
    try:
        flags = _json.loads(entry.flags or '{}')
    except (ValueError, TypeError):
        flags = {}
    return render_template('nodelist/entry.html', entry=entry, flags=flags)


@nodelist_bp.route('/admin/upload', methods=['GET', 'POST'])
@login_required
def admin_upload():
    if not getattr(current_user, 'is_admin', False):
        abort(403)

    if request.method == 'POST':
        upload = request.files.get('nodelist_file')
        domain = (request.form.get('domain') or 'fidonet').strip().lower()
        if not upload or not upload.filename:
            flash('No file uploaded.', 'danger')
            return redirect(url_for('nodelist.admin_upload'))

        # Save to a tempfile so the parser can read it. Use the uploaded
        # filename's extension so import_from_path's ZIP detection works.
        suffix = os.path.splitext(upload.filename)[1] or '.txt'
        fd, tmp = tempfile.mkstemp(prefix='nodelist_', suffix=suffix)
        try:
            with os.fdopen(fd, 'wb') as fh:
                upload.save(fh)
            nl = import_from_path(tmp, domain=domain)
            flash(
                f'Imported {nl.entry_count} entries '
                f'(day {nl.day_of_year}, domain {domain}).',
                'success')
        except Exception as exc:
            db.session.rollback()
            flash(f'Import failed: {exc}', 'danger')
            current_app.logger.exception('Nodelist import failed')
        finally:
            try: os.unlink(tmp)
            except OSError: pass
        return redirect(url_for('nodelist.browse'))

    return render_template('nodelist/admin_upload.html')


@nodelist_bp.route('/admin/bulk-import', methods=['GET', 'POST'])
@login_required
def admin_bulk_import():
    """Scan a directory for nodelist files / archives and import the newest
    one per (extension-grouped) network. Inspired by Codefenix's NetLister
    for Synchronet, which auto-picks the freshest NODELIST.NNN in a path.

    The sysop maps directory entries to network domains via the form. We
    don't try to autodetect the domain — file naming is too varied
    (e.g. `tqwnet.z46` vs `FSXNET.318` vs `Z1DAILY.ZIP`). The form lists
    every plausible nodelist file in the configured scan dir; sysop ticks
    the boxes and selects a domain per file.
    """
    if not getattr(current_user, 'is_admin', False):
        abort(403)

    default_dir = current_app.config.get(
        'NODELIST_SCAN_DIR',
        os.path.join(current_app.config.get('DATA_DIR', ''), 'nodelists'))
    scan_dir = (request.values.get('scan_dir') or default_dir).strip()

    if request.method == 'POST' and request.form.get('action') == 'import':
        imported = 0
        errors = []
        for key in request.form:
            if not key.startswith('domain__'):
                continue
            filename = key[len('domain__'):]
            domain = (request.form.get(key) or '').strip().lower()
            if not domain:
                continue
            # Real gap found in a security/performance audit: `filename`
            # (a raw POST form-field-key suffix) and `scan_dir` were
            # joined with no traversal check at all -- a filename of
            # "../../../etc/passwd" (or any path containing a separator)
            # made `path` resolve outside scan_dir entirely, and
            # imported entries become visible to EVERY logged-in user
            # via /nodelist/ afterward, not just this admin. This route
            # already requires is_admin, but "admin-only" isn't a
            # reason to skip validating admin-supplied input -- an
            # admin session compromised via XSS/CSRF could otherwise be
            # used to exfiltrate arbitrary server file contents into a
            # public listing. Reject anything that isn't a bare
            # filename, and confirm the resolved path is still really
            # inside scan_dir as defense in depth.
            if filename != os.path.basename(filename) or filename in ('', '.', '..'):
                errors.append(f'{filename!r}: invalid filename')
                continue
            scan_dir_real = os.path.realpath(scan_dir)
            path = os.path.realpath(os.path.join(scan_dir, filename))
            if os.path.dirname(path) != scan_dir_real:
                errors.append(f'{filename!r}: invalid filename')
                continue
            if not os.path.isfile(path):
                errors.append(f'{filename}: file not found')
                continue
            try:
                nl = import_from_path(path, domain=domain)
                imported += 1
                flash(
                    f'{filename}: imported {nl.entry_count} entries '
                    f'(day {nl.day_of_year}, domain {domain})',
                    'success')
            except Exception as exc:
                db.session.rollback()
                errors.append(f'{filename}: {exc}')
                current_app.logger.exception('Bulk nodelist import failed')
        for e in errors:
            flash(e, 'danger')
        return redirect(url_for('nodelist.admin_bulk_import',
                                scan_dir=scan_dir))

    # GET — enumerate candidates
    candidates = []
    if os.path.isdir(scan_dir):
        from ..echomail.nodelist import _looks_like_nodelist, _is_zip
        for name in sorted(os.listdir(scan_dir)):
            full = os.path.join(scan_dir, name)
            if not os.path.isfile(full):
                continue
            # Anything that looks like a nodelist OR is a ZIP archive
            # (which might wrap one) is offered to the sysop.
            if _looks_like_nodelist(name) or _is_zip(full) or \
                    name.lower().endswith('.zip'):
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                candidates.append({'name': name, 'size': size})

    return render_template('nodelist/admin_bulk_import.html',
                           scan_dir=scan_dir,
                           candidates=candidates)
