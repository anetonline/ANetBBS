# anetbbs/web/files.py
"""
File gallery blueprint
"""
import os
import uuid
import mimetypes
from flask import Blueprint, render_template, redirect, url_for, flash, send_from_directory, abort, current_app
from flask_login import login_required, current_user
from wtforms import TextAreaField, SubmitField, SelectField
from wtforms.validators import Optional
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from werkzeug.utils import secure_filename

from ..models import db, FileUpload, FileArea
from ..features.archive_meta import extract_archive_description

files_bp = Blueprint('files', __name__, url_prefix='/files')


class UploadForm(FlaskForm):
    file_area_id = SelectField('File Area', coerce=int, validators=[Optional()])
    description = TextAreaField('Description', validators=[Optional()])
    file = FileField('File', validators=[FileRequired()])
    submit = SubmitField('Upload')


def allowed_file(filename):
    """Check if file extension is allowed"""
    allowed = current_app.config.get('ALLOWED_EXTENSIONS', {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf', 'txt'})
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed


@files_bp.route('/')
def list_files():
    """List all uploaded files"""
    uploads = FileUpload.query.order_by(FileUpload.created_at.desc()).all()
    return render_template('files/list.html', uploads=uploads)


@files_bp.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    """Upload a file"""
    form = UploadForm()
    # Build the file-area picker. (0, '— Top-level —') means no area scoping.
    areas = FileArea.query.filter_by(is_active=True).order_by(FileArea.name).all()
    form.file_area_id.choices = [(0, '— Top-level (no area) —')] + [
        (a.id, f'{a.name or a.tag}') for a in areas]

    if form.validate_on_submit():
        f = form.file.data
        if not f or not f.filename:
            flash('No file selected.', 'danger')
            return render_template('files/upload.html', form=form)

        # Per-area access control. upload_permission values:
        #   'users' = any logged-in user
        #   'sysop' = admin only
        #   'none'  = uploads disabled to this area
        chosen_area = None
        if form.file_area_id.data:
            chosen_area = FileArea.query.get(form.file_area_id.data)
            if chosen_area is None or not chosen_area.is_active:
                flash('That file area no longer exists.', 'danger')
                return render_template('files/upload.html', form=form)
            perm = (chosen_area.upload_permission or 'users').lower()
            if perm == 'none':
                flash(f'Uploads to "{chosen_area.name}" are disabled.', 'danger')
                return render_template('files/upload.html', form=form)
            if perm == 'sysop' and not current_user.is_admin:
                flash(f'"{chosen_area.name}" accepts uploads from the sysop only.',
                      'danger')
                return render_template('files/upload.html', form=form)

        if not allowed_file(f.filename):
            flash('File type not allowed.', 'danger')
            return render_template('files/upload.html', form=form)

        # Check file size
        f.seek(0, 2)
        size = f.tell()
        f.seek(0)
        max_size = current_app.config.get('UPLOAD_MAX_SIZE', 5 * 1024 * 1024)
        if size > max_size:
            flash(f'File too large. Maximum size is {max_size // 1024 // 1024}MB.', 'danger')
            return render_template('files/upload.html', form=form)

        # Save file with unique name
        ext = secure_filename(f.filename).rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        unique_name = f"{uuid.uuid4().hex}.{ext}" if ext else uuid.uuid4().hex

        uploads_dir = current_app.config.get('UPLOADS_DIR', os.path.join(current_app.config['DATA_DIR'], 'uploads'))
        os.makedirs(uploads_dir, exist_ok=True)

        filepath = os.path.join(uploads_dir, unique_name)
        f.save(filepath)

        # Optional virus scan. If clamscan is installed and detects an
        # infection we delete the file and reject the upload.
        try:
            from ..features.virus_scan import scan_path
            result = scan_path(filepath)
            if result.infected:
                try:
                    os.remove(filepath)
                except OSError:
                    pass
                flash(f'Upload rejected: virus detected ({result.signature}).',
                      'danger')
                return redirect(url_for('files.upload'))
        except Exception:
            current_app.logger.exception(
                'virus scan crashed during user upload; allowing file')

        mime_type = mimetypes.guess_type(f.filename)[0] or 'application/octet-stream'

        # Auto-pull a description from the archive if the user didn't type one.
        # Tries FILE_ID.DIZ first (the BBS standard), then README.* fallbacks.
        description = (form.description.data or '').strip()
        if not description:
            try:
                description = extract_archive_description(filepath) or ''
            except Exception:
                current_app.logger.exception(
                    'description-extract failed; continuing without it')

        upload_obj = FileUpload(
            uploader_id=current_user.id,
            filename=unique_name,
            original_filename=secure_filename(f.filename),
            file_path=filepath,
            file_size=size,
            mime_type=mime_type,
            description=description,
            file_area_id=chosen_area.id if chosen_area else None,
        )
        db.session.add(upload_obj)
        db.session.commit()

        flash('File uploaded successfully!', 'success')
        return redirect(url_for('files.list_files'))

    return render_template('files/upload.html', form=form)


@files_bp.route('/download/<int:file_id>')
def download(file_id):
    """Download a file"""
    upload_obj = FileUpload.query.get_or_404(file_id)

    uploads_dir = current_app.config.get('UPLOADS_DIR', os.path.join(current_app.config['DATA_DIR'], 'uploads'))

    # Increment download count
    upload_obj.download_count = (upload_obj.download_count or 0) + 1
    db.session.commit()

    return send_from_directory(
        uploads_dir,
        upload_obj.filename,
        as_attachment=True,
        download_name=upload_obj.original_filename
    )


@files_bp.route('/delete/<int:file_id>', methods=['POST'])
@login_required
def delete_file(file_id):
    """Delete a file"""
    upload_obj = FileUpload.query.get_or_404(file_id)

    if upload_obj.uploader_id != current_user.id and not current_user.is_admin:
        abort(403)

    uploads_dir = current_app.config.get('UPLOADS_DIR', os.path.join(current_app.config['DATA_DIR'], 'uploads'))
    filepath = os.path.join(uploads_dir, upload_obj.filename)

    if os.path.exists(filepath):
        os.remove(filepath)

    db.session.delete(upload_obj)
    db.session.commit()

    flash('File deleted.', 'success')
    return redirect(url_for('files.list_files'))
