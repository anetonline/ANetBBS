# anetbbs/web/file_areas.py
"""
File-area browser — list FidoNet/local file areas, browse contents, download.

File areas store their binaries on disk under `area.storage_path` (set
manually by sysop or auto-defaulted by the TIC processor). We list directory
contents at view time so newly-hatched files show up without needing a DB
row per file.

Routes:
    /file-areas/                    list all areas (filtered by visibility)
    /file-areas/<id>                list files in one area
    /file-areas/<id>/<filename>     stream-download a file
    /file-areas/<id>/upload         POST file (where permission allows)
"""
import io
import json
import os
import secrets
import datetime
from flask import (Blueprint, render_template, abort, send_from_directory,
                   redirect, url_for, flash, request, current_app, send_file)
from flask_login import login_required, current_user

from ..models import db, FileArea, TicFile, SharedFileLink
from ..features.archive_meta import extract_archive_description
from .list_pagination import ListPagination


_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
FILES_PER_PAGE = 50


def _is_image(name):
    return os.path.splitext(name.lower())[1] in _IMAGE_EXTS

file_areas_bp = Blueprint('file_areas', __name__, url_prefix='/file-areas')


def _visible_to(user, area):
    """Is this area browseable by this user?"""
    if not area.is_active:
        return False
    if area.is_sysop_only and not getattr(user, 'is_admin', False):
        return False
    return True


def _hatch_if_network_area(area, dest_path, filename, description=None):
    """Queue a freshly-saved file for outbound TIC distribution if its
    area belongs to an echomail network (e.g. ANN.FILES.*) -- purely
    local file areas (network_id is None) have no peers to hatch to.

    Best-effort: logs and swallows any error so a hatch failure never
    blocks the upload itself from succeeding.
    """
    if area.network_id is None:
        return
    try:
        from ..echomail.tic import hatch_local_file
        hatch_local_file(area, dest_path, filename, description or '')
    except Exception:
        current_app.logger.exception(
            'hatch_local_file failed for %s in area %s', filename, area.tag)


_DESC_CACHE_FILENAME = '.descriptions.json'


def _desc_cache_path(area):
    if not area.storage_path:
        return None
    return os.path.join(area.storage_path, _DESC_CACHE_FILENAME)


def _load_desc_cache(area):
    path = _desc_cache_path(area)
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_desc_cache(area, cache):
    path = _desc_cache_path(area)
    if not path:
        return
    try:
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(cache, fh)
    except OSError:
        pass


def _clear_desc_cache(area):
    path = _desc_cache_path(area)
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _scan_area(area):
    """List files on disk under area.storage_path. Returns list of dicts.

    Skips dotfiles and the directory itself. Descriptions resolve in this
    order, first hit wins:
        1. TIC sidecar (tic_files row for this area + filename)
        2. Cached FILE_ID.DIZ / README.* extracted from the archive
        3. Fresh extraction from the archive (then cached on disk)
    """
    if not area.storage_path or not os.path.isdir(area.storage_path):
        return []

    tic_descs = {}
    for t in (TicFile.query
              .filter_by(file_area_id=area.id, status='filed')
              .all()):
        tic_descs[t.filename] = t.description or ''

    cache = _load_desc_cache(area)
    cache_dirty = False

    out = []
    try:
        for name in sorted(os.listdir(area.storage_path)):
            if name.startswith('.'):
                continue
            full = os.path.join(area.storage_path, name)
            if not os.path.isfile(full):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue

            description = tic_descs.get(name, '') or ''
            if not description:
                entry = cache.get(name)
                if (isinstance(entry, dict)
                        and entry.get('mtime') == int(st.st_mtime)
                        and entry.get('size') == st.st_size):
                    description = entry.get('description', '') or ''
                else:
                    try:
                        description = extract_archive_description(full) or ''
                    except Exception:
                        description = ''
                    cache[name] = {
                        'mtime': int(st.st_mtime),
                        'size': st.st_size,
                        'description': description,
                    }
                    cache_dirty = True

            out.append({
                'name': name,
                'size': st.st_size,
                'mtime': datetime.datetime.fromtimestamp(st.st_mtime),
                'description': description,
                'is_image': _is_image(name),
            })
    except OSError:
        pass

    if cache_dirty:
        _save_desc_cache(area, cache)
    return out


@file_areas_bp.route('/')
@login_required
def index():
    areas = (FileArea.query.filter_by(is_active=True)
             .order_by(FileArea.tag).all())
    visible = [a for a in areas if _visible_to(current_user, a)]
    # Annotate with file count for the listing page.
    rows = []
    for a in visible:
        files = _scan_area(a)
        rows.append({
            'area': a, 'count': len(files),
            'total_size': sum(f['size'] for f in files),
        })
    return render_template('file_areas/index.html', rows=rows)


@file_areas_bp.route('/<int:area_id>')
@login_required
def view_area(area_id):
    area = FileArea.query.get_or_404(area_id)
    if not _visible_to(current_user, area):
        abort(403)
    files = _scan_area(area)
    sort = request.args.get('sort', '-date')
    if sort not in ('name', '-name', 'date', '-date', 'size', '-size'):
        sort = '-date'
    reverse = sort.startswith('-')
    key = sort.lstrip('-')
    key_fns = {
        'name': lambda f: f['name'].lower(),
        'date': lambda f: f['mtime'],
        'size': lambda f: f['size'],
    }
    files = sorted(files, key=key_fns.get(key, key_fns['date']), reverse=reverse)

    page = request.args.get('page', 1, type=int)
    pagination = ListPagination(page, FILES_PER_PAGE, len(files))
    page_files = pagination.slice(files)

    return render_template('file_areas/area.html', area=area, files=page_files,
                           sort=sort, pagination=pagination)


@file_areas_bp.route('/<int:area_id>/rescan-descriptions', methods=['POST'])
@login_required
def rescan_descriptions(area_id):
    """Sysop-only: drop the cached FILE_ID.DIZ descriptions for this area
    and re-extract from disk. Useful after dropping files in directly (no
    TIC, no web upload) or after replacing an archive."""
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    area = FileArea.query.get_or_404(area_id)
    _clear_desc_cache(area)
    files = _scan_area(area)
    flash(f'Re-scanned descriptions for {len(files)} file(s) in '
          f'{area.name or area.tag}.', 'success')
    return redirect(url_for('file_areas.view_area', area_id=area.id))


@file_areas_bp.route('/<int:area_id>/<path:filename>')
@login_required
def download(area_id, filename):
    area = FileArea.query.get_or_404(area_id)
    if not _visible_to(current_user, area):
        abort(403)
    if not area.storage_path or not os.path.isdir(area.storage_path):
        abort(404)

    full = os.path.normpath(os.path.join(area.storage_path, filename))
    if not full.startswith(os.path.realpath(area.storage_path) + os.sep):
        abort(404)
    if not os.path.isfile(full):
        abort(404)

    # Optional download ratio enforcement (Synchronet/Mystic style).
    # Sysop turns it on by setting RATIO_MIN (e.g. 0.05 means user must
    # have uploaded at least 5% of what they've downloaded). Sysops are
    # always exempt.
    try:
        ratio_min_raw = current_app.config.get('RATIO_MIN') or \
                        os.environ.get('RATIO_MIN', '')
        ratio_min = float(ratio_min_raw) if ratio_min_raw else 0.0
    except (TypeError, ValueError):
        ratio_min = 0.0
    if (ratio_min > 0 and not getattr(current_user, 'is_admin', False)):
        try:
            from ..models import FileRatio
            r = FileRatio.query.filter_by(user_id=current_user.id).first()
            ul = (r.bytes_uploaded if r else 0) or 0
            dl = (r.bytes_downloaded if r else 0) or 0
            file_size = os.path.getsize(full)
            projected_dl = dl + file_size
            if projected_dl > 0 and (ul / projected_dl) < ratio_min:
                flash(
                    f'Your upload/download ratio is below the required '
                    f'{ratio_min:.2f}. Upload some files first to unlock '
                    f'downloads.', 'danger')
                return redirect(url_for('file_areas.view_area',
                                        area_id=area.id))
        except Exception:
            pass

    # Bump download counter (best-effort).
    try:
        from ..models import FileRatio, db as _db
        r = FileRatio.query.filter_by(user_id=current_user.id).first()
        if r is None:
            r = FileRatio(user_id=current_user.id)
            _db.session.add(r)
        r.bytes_downloaded = (r.bytes_downloaded or 0) + os.path.getsize(full)
        r.files_downloaded = (r.files_downloaded or 0) + 1
        _db.session.commit()
    except Exception:
        pass

    # send_from_directory handles directory traversal safely.
    return send_from_directory(area.storage_path, filename, as_attachment=True)


@file_areas_bp.route('/<int:area_id>/thumb/<path:filename>')
@login_required
def thumbnail(area_id, filename):
    """Serve a 256-px thumbnail for an image file in this area.

    Lazy-generated and cached on disk under storage_path/.thumbs/. Falls
    back to redirecting to the full file if Pillow is missing or the
    source isn't a recognized image."""
    area = FileArea.query.get_or_404(area_id)
    if not _visible_to(current_user, area):
        abort(403)
    if not area.storage_path or not os.path.isdir(area.storage_path):
        abort(404)
    if not _is_image(filename):
        abort(404)

    src = os.path.normpath(os.path.join(area.storage_path, filename))
    # Defense against path traversal — confirm src is inside storage_path.
    if not src.startswith(os.path.realpath(area.storage_path) + os.sep):
        abort(404)
    if not os.path.isfile(src):
        abort(404)

    thumb_dir = os.path.join(area.storage_path, '.thumbs')
    thumb_path = os.path.join(thumb_dir, os.path.basename(filename) + '.png')

    # Regenerate if missing or stale.
    try:
        src_mtime = os.path.getmtime(src)
        thumb_mtime = os.path.getmtime(thumb_path) if os.path.exists(thumb_path) else 0
        if thumb_mtime < src_mtime:
            try:
                from PIL import Image
            except ImportError:
                # Pillow not available — fall back to the raw file
                return send_from_directory(area.storage_path, filename)
            os.makedirs(thumb_dir, exist_ok=True)
            with Image.open(src) as im:
                im.thumbnail((256, 256))
                if im.mode in ('RGBA', 'LA', 'P'):
                    im = im.convert('RGBA')
                else:
                    im = im.convert('RGB')
                buf = io.BytesIO()
                im.save(buf, format='PNG', optimize=True)
                with open(thumb_path, 'wb') as f:
                    f.write(buf.getvalue())
    except Exception:
        # Any failure: serve the original
        return send_from_directory(area.storage_path, filename)
    return send_file(thumb_path, mimetype='image/png',
                     max_age=86400)


@file_areas_bp.route('/<int:area_id>/upload', methods=['POST'])
@login_required
def upload(area_id):
    area = FileArea.query.get_or_404(area_id)
    if not _visible_to(current_user, area):
        abort(403)

    perm = (area.upload_permission or 'users').lower()
    if perm == 'none':
        abort(403)
    if perm == 'sysop' and not getattr(current_user, 'is_admin', False):
        abort(403)

    upload = request.files.get('file')
    if not upload or not upload.filename:
        flash('No file selected.', 'danger')
        return redirect(url_for('file_areas.view_area', area_id=area.id))

    # Optional area password
    if area.password:
        if (request.form.get('password') or '') != area.password:
            flash('Incorrect area password.', 'danger')
            return redirect(url_for('file_areas.view_area', area_id=area.id))

    try:
        os.makedirs(area.storage_path, exist_ok=True)
        # Sanitize the basename
        safe_name = os.path.basename(upload.filename)
        if not safe_name or safe_name.startswith('.'):
            flash('Invalid filename.', 'danger')
            return redirect(url_for('file_areas.view_area', area_id=area.id))

        # Sysop moderation queue — non-admin uploads go to quarantine first
        # when FILE_MOD_QUEUE_ENABLED is set.
        queue_on = bool(current_app.config.get('FILE_MOD_QUEUE_ENABLED', False))
        if queue_on and not getattr(current_user, 'is_admin', False):
            from ..models import FileQueueEntry
            qdir = os.path.join(current_app.config.get('DATA_DIR', 'data'),
                                'file-queue')
            os.makedirs(qdir, exist_ok=True)
            qpath = os.path.join(qdir, f'{int(__import__("time").time())}-{safe_name}')
            upload.save(qpath)
            entry = FileQueueEntry(
                file_area_id=area.id,
                user_id=current_user.id,
                filename=safe_name,
                quarantine_path=qpath,
                description=(request.form.get('description') or '').strip() or None,
                size_bytes=os.path.getsize(qpath),
                status='pending',
            )
            db.session.add(entry); db.session.commit()
            flash(f'{safe_name} uploaded — pending sysop approval.', 'info')
            return redirect(url_for('file_areas.view_area', area_id=area.id))

        dest = os.path.join(area.storage_path, safe_name)
        upload.save(dest)

        # Virus scan (optional — silently skipped if clamscan isn't installed).
        try:
            from ..features.virus_scan import scan_path
            result = scan_path(dest)
            if result.infected:
                try:
                    os.remove(dest)
                except OSError:
                    pass
                flash(f'Upload rejected: virus detected ({result.signature}).',
                      'danger')
                return redirect(url_for('file_areas.view_area', area_id=area.id))
        except Exception:
            current_app.logger.exception('virus scan crashed; allowing upload')

        # Bump upload ratio counter.
        try:
            from ..models import FileRatio
            r = FileRatio.query.filter_by(user_id=current_user.id).first()
            if r is None:
                r = FileRatio(user_id=current_user.id)
                db.session.add(r)
            r.bytes_uploaded = (r.bytes_uploaded or 0) + os.path.getsize(dest)
            r.files_uploaded = (r.files_uploaded or 0) + 1
            db.session.commit()
        except Exception:
            db.session.rollback()
        _hatch_if_network_area(area, dest, safe_name,
                               request.form.get('description'))
        flash(f'Uploaded {safe_name}.', 'success')
    except OSError as exc:
        flash(f'Save failed: {exc}', 'danger')
    return redirect(url_for('file_areas.view_area', area_id=area.id))


# ---------------------------------------------------------------------------
# Shared file links (peer-to-peer / anonymous-access shares)
# ---------------------------------------------------------------------------

def _client_ip():
    fwd = request.headers.get('X-Forwarded-For', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.remote_addr or ''


@file_areas_bp.route('/<int:area_id>/share/<path:filename>', methods=['POST'])
@login_required
def create_share(area_id, filename):
    """Mint a SharedFileLink token for one file in this area.

    Form fields (all optional): note, hours_valid, max_downloads.
    """
    area = FileArea.query.get_or_404(area_id)
    if not _visible_to(current_user, area):
        abort(403)
    if not area.storage_path or not os.path.isfile(
            os.path.join(area.storage_path, filename)):
        flash('File not found.', 'danger')
        return redirect(url_for('file_areas.view_area', area_id=area.id))

    note = (request.form.get('note') or '').strip()[:200]
    hours = request.form.get('hours_valid', type=int)
    max_dl = request.form.get('max_downloads', type=int)
    expires = (datetime.datetime.utcnow() + datetime.timedelta(hours=hours)
               if hours else None)

    token = secrets.token_urlsafe(16)
    link = SharedFileLink(
        token=token,
        created_by_id=current_user.id,
        file_area_id=area.id,
        filename=filename,
        note=note or None,
        expires_at=expires,
        max_downloads=max_dl,
    )
    db.session.add(link)
    db.session.commit()
    share_url = url_for('file_areas.fetch_shared', token=token, _external=True)
    flash(f'Share link created: {share_url}', 'success')
    return redirect(url_for('file_areas.view_area', area_id=area.id))


@file_areas_bp.route('/shared/<token>')
def fetch_shared(token):
    """Anonymous download endpoint — no login_required."""
    link = SharedFileLink.query.filter_by(token=token).first()
    if link is None or not link.is_valid:
        abort(404)
    area = link.file_area
    if not area or not area.storage_path:
        abort(404)
    fpath = os.path.join(area.storage_path, link.filename)
    if not os.path.isfile(fpath):
        abort(404)
    # Update audit fields BEFORE serving so the count is right even if the
    # user aborts the download mid-stream.
    link.download_count = (link.download_count or 0) + 1
    link.last_accessed_at = datetime.datetime.utcnow()
    link.last_accessed_ip = _client_ip()
    db.session.commit()
    return send_from_directory(area.storage_path, link.filename,
                               as_attachment=True)


@file_areas_bp.route('/shares')
@login_required
def my_shares():
    """User's own active and past shares."""
    rows = (SharedFileLink.query
            .filter_by(created_by_id=current_user.id)
            .order_by(SharedFileLink.created_at.desc()).all())
    return render_template('file_areas/my_shares.html', rows=rows)


@file_areas_bp.route('/shares/<int:link_id>/revoke', methods=['POST'])
@login_required
def revoke_share(link_id):
    link = SharedFileLink.query.get_or_404(link_id)
    if link.created_by_id != current_user.id and \
            not getattr(current_user, 'is_admin', False):
        abort(403)
    link.is_revoked = True
    db.session.commit()
    flash('Share link revoked.', 'success')
    return redirect(url_for('file_areas.my_shares'))


# ---------------------------------------------------------------------------
# Sysop file management (list / delete / edit desc / upload inside an area)
# ---------------------------------------------------------------------------

@file_areas_bp.route('/<int:area_id>/manage')
@login_required
def manage_files(area_id):
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    area = FileArea.query.get_or_404(area_id)
    files = _scan_area(area)
    return render_template('file_areas/manage.html', area=area, files=files)


@file_areas_bp.route('/<int:area_id>/manage/delete', methods=['POST'])
@login_required
def manage_delete(area_id):
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    area = FileArea.query.get_or_404(area_id)
    filename = request.form.get('filename', '')
    if not filename or filename.startswith('.') or os.sep in filename:
        flash('Invalid filename.', 'danger')
        return redirect(url_for('file_areas.manage_files', area_id=area.id))
    if not area.storage_path:
        flash('No storage path set for this area.', 'danger')
        return redirect(url_for('file_areas.manage_files', area_id=area.id))
    full = os.path.normpath(os.path.join(area.storage_path, filename))
    if not full.startswith(os.path.realpath(area.storage_path) + os.sep):
        flash('Invalid path.', 'danger')
        return redirect(url_for('file_areas.manage_files', area_id=area.id))
    if not os.path.isfile(full):
        flash(f'{filename} not found.', 'danger')
        return redirect(url_for('file_areas.manage_files', area_id=area.id))
    try:
        os.remove(full)
        cache = _load_desc_cache(area)
        if filename in cache:
            del cache[filename]
            _save_desc_cache(area, cache)
        flash(f'Deleted {filename}.', 'success')
    except OSError as exc:
        flash(f'Delete failed: {exc}', 'danger')
    return redirect(url_for('file_areas.manage_files', area_id=area.id))


@file_areas_bp.route('/<int:area_id>/manage/desc', methods=['POST'])
@login_required
def manage_desc(area_id):
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    area = FileArea.query.get_or_404(area_id)
    filename = request.form.get('filename', '')
    description = (request.form.get('description') or '').strip()
    if not filename or filename.startswith('.') or os.sep in filename:
        flash('Invalid filename.', 'danger')
        return redirect(url_for('file_areas.manage_files', area_id=area.id))
    if not area.storage_path:
        flash('No storage path set.', 'danger')
        return redirect(url_for('file_areas.manage_files', area_id=area.id))
    full = os.path.join(area.storage_path, filename)
    if not os.path.isfile(full):
        flash(f'{filename} not found.', 'danger')
        return redirect(url_for('file_areas.manage_files', area_id=area.id))
    try:
        st = os.stat(full)
        cache = _load_desc_cache(area)
        cache[filename] = {
            'mtime': int(st.st_mtime),
            'size': st.st_size,
            'description': description,
        }
        _save_desc_cache(area, cache)
        flash(f'Description updated for {filename}.', 'success')
    except OSError as exc:
        flash(f'Failed: {exc}', 'danger')
    return redirect(url_for('file_areas.manage_files', area_id=area.id))


@file_areas_bp.route('/<int:area_id>/manage/upload', methods=['POST'])
@login_required
def manage_upload(area_id):
    if not getattr(current_user, 'is_admin', False):
        abort(403)
    area = FileArea.query.get_or_404(area_id)
    if not area.storage_path:
        flash('No storage path configured for this area.', 'danger')
        return redirect(url_for('file_areas.manage_files', area_id=area.id))
    f = request.files.get('file')
    if not f or not f.filename:
        flash('No file selected.', 'danger')
        return redirect(url_for('file_areas.manage_files', area_id=area.id))
    safe_name = os.path.basename(f.filename)
    if not safe_name or safe_name.startswith('.'):
        flash('Invalid filename.', 'danger')
        return redirect(url_for('file_areas.manage_files', area_id=area.id))
    description = (request.form.get('description') or '').strip()
    try:
        os.makedirs(area.storage_path, exist_ok=True)
        dest = os.path.join(area.storage_path, safe_name)
        f.save(dest)
        if description:
            st = os.stat(dest)
            cache = _load_desc_cache(area)
            cache[safe_name] = {
                'mtime': int(st.st_mtime),
                'size': st.st_size,
                'description': description,
            }
            _save_desc_cache(area, cache)
        _hatch_if_network_area(area, dest, safe_name, description)
        flash(f'Uploaded {safe_name}.', 'success')
    except OSError as exc:
        flash(f'Upload failed: {exc}', 'danger')
    return redirect(url_for('file_areas.manage_files', area_id=area.id))


# ---------------------------------------------------------------------------
# Smart upload — pick (or auto-detect) the target file area by tag
# ---------------------------------------------------------------------------

@file_areas_bp.route('/smart-upload', methods=['GET', 'POST'])
@login_required
def smart_upload():
    """Upload a file and route to a chosen area, or auto-detect by tag prefix.

    Auto-detect heuristic: if the filename starts with `<TAG>_` or contains
    `[TAG]`, we try to find a FileArea with that tag. Otherwise the user
    picks from a dropdown."""
    visible_areas = [a for a in
                     FileArea.query.filter(FileArea.is_active.is_(True)).all()
                     if _visible_to(current_user, a)]

    if request.method == 'POST':
        upload = request.files.get('file')
        if not upload or not upload.filename:
            flash('No file selected.', 'danger')
            return redirect(url_for('file_areas.smart_upload'))
        manual_area_id = request.form.get('area_id', type=int)

        target = None
        if manual_area_id:
            target = next((a for a in visible_areas if a.id == manual_area_id),
                          None)
        else:
            # Heuristic auto-detection
            fname = upload.filename.upper()
            for a in visible_areas:
                tag = (a.tag or '').upper()
                if tag and (fname.startswith(tag + '_')
                            or f'[{tag}]' in fname
                            or f'.{tag}.' in fname):
                    target = a
                    break

        if target is None:
            flash('No matching file area — pick one manually.', 'danger')
            return redirect(url_for('file_areas.smart_upload'))

        perm = (target.upload_permission or 'users').lower()
        if perm == 'none':
            flash('Uploads are disabled for that area.', 'danger')
            return redirect(url_for('file_areas.smart_upload'))
        if perm == 'sysop' and not getattr(current_user, 'is_admin', False):
            flash('Only sysops can upload to that area.', 'danger')
            return redirect(url_for('file_areas.smart_upload'))

        try:
            os.makedirs(target.storage_path, exist_ok=True)
            safe = os.path.basename(upload.filename)
            dest = os.path.join(target.storage_path, safe)
            upload.save(dest)
            # Optional virus scan
            try:
                from ..features.virus_scan import scan_path
                r = scan_path(dest)
                if r.infected:
                    try: os.remove(dest)
                    except OSError: pass
                    flash(f'Rejected: virus ({r.signature}).', 'danger')
                    return redirect(url_for('file_areas.smart_upload'))
            except Exception:
                pass
            _hatch_if_network_area(target, dest, safe,
                                   request.form.get('description'))
            flash(f'Uploaded {safe} to {target.tag}.', 'success')
            return redirect(url_for('file_areas.view_area', area_id=target.id))
        except OSError as exc:
            flash(f'Save failed: {exc}', 'danger')

    return render_template('file_areas/smart_upload.html', areas=visible_areas)
