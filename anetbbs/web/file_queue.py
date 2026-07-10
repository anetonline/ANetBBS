# anetbbs/web/file_queue.py
"""
Sysop file moderation queue.

When ``FILE_MOD_QUEUE_ENABLED`` is set on the app config, regular-user
uploads to file areas go into ``data/file-queue/`` and a FileQueueEntry
row is created. Sysops review at ``/admin/file-queue/`` and approve
(move file into the area's storage_path) or reject (delete from
quarantine).
"""
import os
from datetime import datetime

from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, current_app)
from flask_login import login_required, current_user

from ..models import db, FileQueueEntry, FileArea
from .access_control import require_admin_or_403 as _require_admin


queue_bp = Blueprint('file_queue', __name__, url_prefix='/admin/file-queue')


@queue_bp.route('/')
@login_required
def index():
    _require_admin()
    status = (request.args.get('status') or 'pending').lower()
    q = FileQueueEntry.query
    if status in ('pending', 'approved', 'rejected'):
        q = q.filter_by(status=status)
    entries = q.order_by(FileQueueEntry.created_at.desc()).limit(200).all()
    counts = {
        'pending': FileQueueEntry.query.filter_by(status='pending').count(),
        'approved': FileQueueEntry.query.filter_by(status='approved').count(),
        'rejected': FileQueueEntry.query.filter_by(status='rejected').count(),
    }
    return render_template('admin/file_queue.html',
                           entries=entries, status=status, counts=counts)


@queue_bp.route('/<int:entry_id>/approve', methods=['POST'])
@login_required
def approve(entry_id):
    _require_admin()
    entry = FileQueueEntry.query.get_or_404(entry_id)
    if entry.status != 'pending':
        flash('Entry already reviewed.', 'warning')
        return redirect(url_for('file_queue.index'))

    area = FileArea.query.get(entry.file_area_id)
    if not area or not area.storage_path:
        flash('Target area has no storage path.', 'danger')
        return redirect(url_for('file_queue.index'))

    try:
        os.makedirs(area.storage_path, exist_ok=True)
        safe_name = os.path.basename(entry.filename)
        dest = os.path.join(area.storage_path, safe_name)
        if os.path.exists(dest):
            # Don't clobber existing files
            base, ext = os.path.splitext(safe_name)
            dest = os.path.join(area.storage_path,
                                f'{base}-{entry.id}{ext}')
        os.rename(entry.quarantine_path, dest)
    except OSError as exc:
        flash(f'Approve failed: {exc}', 'danger')
        return redirect(url_for('file_queue.index'))

    entry.status = 'approved'
    entry.reviewed_by_id = current_user.id
    entry.reviewed_at = datetime.utcnow()
    db.session.commit()
    if area.network_id is not None:
        try:
            from ..echomail.tic import hatch_local_file
            hatch_local_file(area, dest, safe_name, entry.description or '')
        except Exception:
            current_app.logger.exception(
                'hatch_local_file failed for %s in area %s',
                safe_name, area.tag)
    flash(f'Approved {entry.filename}.', 'success')
    return redirect(url_for('file_queue.index'))


@queue_bp.route('/<int:entry_id>/reject', methods=['POST'])
@login_required
def reject(entry_id):
    _require_admin()
    entry = FileQueueEntry.query.get_or_404(entry_id)
    if entry.status != 'pending':
        flash('Entry already reviewed.', 'warning')
        return redirect(url_for('file_queue.index'))
    reason = (request.form.get('reason') or '').strip() or None
    try:
        if entry.quarantine_path and os.path.isfile(entry.quarantine_path):
            os.remove(entry.quarantine_path)
    except OSError:
        current_app.logger.exception('Failed to delete quarantine file')
    entry.status = 'rejected'
    entry.rejection_reason = reason
    entry.reviewed_by_id = current_user.id
    entry.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash(f'Rejected {entry.filename}.', 'success')
    return redirect(url_for('file_queue.index'))


@queue_bp.route('/<int:entry_id>/delete', methods=['POST'])
@login_required
def delete(entry_id):
    _require_admin()
    entry = FileQueueEntry.query.get_or_404(entry_id)
    try:
        if entry.quarantine_path and os.path.isfile(entry.quarantine_path):
            os.remove(entry.quarantine_path)
    except OSError:
        pass
    db.session.delete(entry)
    db.session.commit()
    flash('Queue entry deleted.', 'success')
    return redirect(url_for('file_queue.index'))
