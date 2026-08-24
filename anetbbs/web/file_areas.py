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
import fcntl
import hmac
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
from ..features.access_control import evaluate_access
from ..features.rate_limit import rate_limit, _user_or_ip
from .list_pagination import ListPagination


_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
FILES_PER_PAGE = 50


def _is_image(name):
    return os.path.splitext(name.lower())[1] in _IMAGE_EXTS

file_areas_bp = Blueprint('file_areas', __name__, url_prefix='/file-areas')


def _oversized_upload(upload):
    """True if `upload` (a Werkzeug FileStorage) declares more bytes
    than UPLOAD_MAX_SIZE. Real gap found in a security/performance
    audit: unlike web/files.py's own gallery upload() (which already
    does this exact check), none of this module's three upload routes
    checked file size at all -- combined with no app-wide
    MAX_CONTENT_LENGTH existing either (also fixed this audit, see
    config.py), any authenticated user could fill the disk via any of
    these endpoints. Same seek-to-end technique as files.py's check."""
    upload.stream.seek(0, 2)
    size = upload.stream.tell()
    upload.stream.seek(0)
    max_size = current_app.config.get('UPLOAD_MAX_SIZE', 100 * 1024 * 1024)
    return size > max_size, max_size


def _visible_to(user, area):
    """Is this area browseable by this user?

    Scoped bug-fix (confirmed): this used to check only is_active +
    is_sysop_only, never area.min_access_level -- even though that column
    exists, defaults to 10, and is already enforced identically in the
    terminal client (bbs_ui.py). Left unfixed, the new file-search feature
    would inherit the same gap into new code.
    """
    if not area.is_active:
        return False
    return evaluate_access(user, area.min_access_level,
                           is_sysop_only=area.is_sysop_only,
                           bypass_admin=True)


def _hatch_if_network_area(area, dest_path, filename, description=None,
                            is_crash=False, is_hold=False):
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
        hatch_local_file(area, dest_path, filename, description or '',
                         is_crash=is_crash, is_hold=is_hold)
    except Exception:
        current_app.logger.exception(
            'hatch_local_file failed for %s in area %s', filename, area.tag)


def _read_json_sidecar(path):
    """Plain, unlocked read of a JSON sidecar cache -- used where a
    momentarily-stale read is harmless (a cache-hit check that just
    falls back to re-computing on a miss), unlike the read-modify-write
    sites below which use the locked _update_json_sidecar instead."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _update_json_sidecar(path, mutate):
    """Read-modify-write a JSON sidecar cache file under an exclusive
    OS file lock (fcntl.flock).

    Real gap found in a security/performance audit: every *_cache
    helper below used to be a bare load-then-save pair, called with
    the read and write as two separate, unlocked steps -- two
    concurrent requests touching the SAME area (two uploads, an upload
    racing a manage/desc edit, two admins editing descriptions at
    once; this app runs multi-threaded/multi-worker) could both load
    the same starting snapshot, each apply their own change to their
    own in-memory copy, then each overwrite the file with THEIR view
    in turn -- whichever wrote last silently discarded the other
    request's change. Wrapping the read AND write in one flock-held
    critical section, with `mutate` re-applied against whatever is
    CURRENTLY on disk (not a possibly-stale copy read earlier,
    unlocked), closes that: concurrent updates now serialize instead
    of racing, and no caller's change is silently lost, whether it's
    an add/update OR a delete (mutate can pop a key just as easily as
    set one -- unlike a naive "merge my old snapshot back in", which
    can't distinguish "never loaded this key" from "deliberately
    removed it").

    mutate(dict) -> dict is called with the current on-disk content
    (freshly read under the lock; {} if missing/corrupt) and must
    return the new content to persist. Returns the new content, or {}
    on any I/O failure (best-effort, matches every *_cache helper's
    existing silent-failure behavior -- a sidecar-cache problem must
    never block an upload/delete/description edit).

    Locks a SEPARATE ".lock" file, not the JSON data file itself, and
    does a plain read ('r') + truncating write ('w') rather than
    locking one 'a+' fd and seek(0)/truncate()/write()-ing on it in
    place. An earlier version of this function did exactly that and
    LOOKED correct (the read-modify-write is still fully inside the
    flock-held section) but was empirically still racy under real
    concurrency -- confirmed via a 30-thread stress test that
    consistently dropped a handful of entries roughly 1 run in 5.
    Root cause: a file opened with 'a'/'a+' has O_APPEND set at the OS
    level, which forces the kernel to reposition to end-of-file on
    every write() *at write time*, regardless of any prior seek()/
    truncate() -- an interaction with Python's buffered TextIOWrapper
    that doesn't reliably behave like "seek(0) really means position
    0" for the write. Locking a separate, always-freshly-opened lock
    file and doing the actual data read/write as two ordinary,
    non-append opens sidesteps that interaction entirely. Verified via
    the same 30-thread stress test run 10+ times back to back with
    zero dropped entries after this change (see
    tests/test_file_areas_sidecar_cache_race.py).
    """
    if not path:
        return {}
    lock_path = path + '.lock'
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        return {}
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            data = {}
            if os.path.isfile(path):
                try:
                    with open(path, 'r', encoding='utf-8') as fh:
                        raw = fh.read()
                    data = json.loads(raw) if raw.strip() else {}
                    if not isinstance(data, dict):
                        data = {}
                except (OSError, ValueError):
                    data = {}
            new_data = mutate(data)
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(new_data, fh)
            return new_data
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except OSError:
        return {}
    finally:
        os.close(lock_fd)


_DESC_CACHE_FILENAME = '.descriptions.json'


def _desc_cache_path(area):
    if not area.storage_path:
        return None
    return os.path.join(area.storage_path, _DESC_CACHE_FILENAME)


def _clear_desc_cache(area):
    path = _desc_cache_path(area)
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


# Duplicate-content detection for this blueprint's three upload routes.
# There's no per-file DB row here (files are listed by scanning the
# directory at request time, see the module docstring), so this mirrors
# the ..descriptions.json cache above exactly, just keyed hash->filename
# instead of filename->description.
_HASH_CACHE_FILENAME = '.hashes.json'


def _hash_cache_path(area):
    if not area.storage_path:
        return None
    return os.path.join(area.storage_path, _HASH_CACHE_FILENAME)


def _check_and_record_dupe(area, dest, filename):
    """Hash *dest* (already saved), check the area's hash cache for a
    match, record the new hash either way. Returns the filename of an
    existing duplicate, or None. Best-effort -- never raises, a dedup
    failure should never block an upload."""
    try:
        from ..features.file_dedup import hash_file
        h = hash_file(dest)
        # The read (cache.get(h)) and write (cache[h] = filename) must
        # happen atomically under the SAME lock -- see
        # _update_json_sidecar's own docstring -- otherwise two
        # uploads racing each other could both read "no match yet"
        # before either recorded its own hash.
        found = {}

        def _mutate(cache):
            found['existing'] = cache.get(h)
            cache[h] = filename
            return cache

        _update_json_sidecar(_hash_cache_path(area), _mutate)
        existing = found.get('existing')
        return existing if existing != filename else None
    except Exception:
        current_app.logger.exception('dedup hash failed for %s in area %s',
                                     filename, area.tag)
        return None


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

    cache = _read_json_sidecar(_desc_cache_path(area))
    # Newly-extracted entries only, tracked separately from `cache` --
    # merged into whatever's CURRENTLY on disk under lock at the end
    # (see _update_json_sidecar's own docstring), rather than blindly
    # overwriting with this possibly-stale, unlocked-read snapshot.
    # Safe as an add-only merge: this loop never removes cache entries.
    updates = {}

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
                    updates[name] = {
                        'mtime': int(st.st_mtime),
                        'size': st.st_size,
                        'description': description,
                    }

            out.append({
                'name': name,
                'size': st.st_size,
                'mtime': datetime.datetime.fromtimestamp(st.st_mtime),
                'description': description,
                'is_image': _is_image(name),
            })
    except OSError:
        pass

    if updates:
        def _mutate(cache):
            cache.update(updates)
            return cache
        _update_json_sidecar(_desc_cache_path(area), _mutate)
    return out


def _area_file_stats(area):
    """Cheap (count, total_size) summary scan for the area-index page.

    Real perf bug found live: index() used to call the full
    _scan_area() per visible area just to get these two numbers for the
    summary row -- paying for a TicFile DB query, the description-cache
    JSON load/save, and (for anything not already cached) an archive
    extraction to pull FILE_ID.DIZ, on every area, on every load of the
    area list. None of that is needed for a count and a total size, so
    this does a plain directory scan (still real disk I/O -- can't avoid
    that for a live count without a DB-side cached counter, a bigger
    change than this fix -- but skips everything else _scan_area() does).
    """
    if not area.storage_path or not os.path.isdir(area.storage_path):
        return 0, 0
    count = 0
    total_size = 0
    try:
        with os.scandir(area.storage_path) as it:
            for entry in it:
                if entry.name.startswith('.'):
                    continue
                try:
                    if not entry.is_file():
                        continue
                    total_size += entry.stat().st_size
                    count += 1
                except OSError:
                    continue
    except OSError:
        pass
    return count, total_size


@file_areas_bp.route('/')
@login_required
def index():
    areas = (FileArea.query.filter_by(is_active=True)
             .order_by(FileArea.tag).all())
    visible = [a for a in areas if _visible_to(current_user, a)]
    # Annotate with file count for the listing page.
    rows = []
    for a in visible:
        count, total_size = _area_file_stats(a)
        rows.append({'area': a, 'count': count, 'total_size': total_size})
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
            # Real gap found in a full file-areas audit: this fails
            # open (same convention as the virus-scan/archive-integrity
            # blocks below, deliberate there) but, unlike those, was
            # completely silent -- a real bug in the FileRatio lookup
            # would disable ratio enforcement with zero trace anywhere.
            current_app.logger.exception(
                'ratio check crashed; allowing download (fail-open)')

    # Daily download quota (FR: Firehawke, 2026-07-24) -- see
    # features/file_quota.py's module docstring for the tier-resolution
    # rule. Checked before the counter bump below so a rejected
    # download doesn't get counted.
    from ..features.file_quota import check_quota, consume_quota
    file_size = os.path.getsize(full)
    ok, quota_msg = check_quota(current_user, file_size)
    if not ok:
        flash(quota_msg, 'danger')
        return redirect(url_for('file_areas.view_area', area_id=area.id))

    # Bump download counter (best-effort).
    try:
        from ..models import FileRatio, db as _db
        r = FileRatio.query.filter_by(user_id=current_user.id).first()
        if r is None:
            r = FileRatio(user_id=current_user.id)
            _db.session.add(r)
        r.bytes_downloaded = (r.bytes_downloaded or 0) + file_size
        r.files_downloaded = (r.files_downloaded or 0) + 1
        _db.session.commit()
    except Exception:
        pass
    consume_quota(current_user, file_size)

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
@rate_limit('file_area_upload', limit=20, window=300, key_fn=_user_or_ip)
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

    too_big, max_size = _oversized_upload(upload)
    if too_big:
        flash(f'File too large. Maximum size is {max_size // 1024 // 1024}MB.', 'danger')
        return redirect(url_for('file_areas.view_area', area_id=area.id))

    # Optional area password. Real gap found in a security/performance
    # audit: plain != is a timing side-channel on an attacker-supplied
    # value -- same bug class already fixed for BinkP/AreaFix/FileFix/
    # TIC elsewhere this audit.
    if area.password:
        if not hmac.compare_digest(
                (request.form.get('password') or '').encode('utf-8', errors='replace'),
                area.password.encode('utf-8', errors='replace')):
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
        # Compared as a string, not bool(): this is a no-restart-required
        # setting (admin.py's EDITABLE_SETTINGS), so the admin Settings
        # page writes the raw string 'true'/'false' straight into
        # current_app.config on save -- bool('false') is True in Python,
        # the same gotcha admin.py's settings() route already special-
        # cases for GAMES_INTERBBS_ENABLED. str(...).lower() == 'true'
        # handles both that string form and the real bool Config sets
        # at boot (str(False).lower() == 'false', correctly falsy).
        queue_on = str(current_app.config.get('FILE_MOD_QUEUE_ENABLED', False)).lower() == 'true'
        if queue_on and not getattr(current_user, 'is_admin', False):
            from ..models import FileQueueEntry
            qdir = os.path.join(current_app.config.get('DATA_DIR', 'data'),
                                'file-queue')
            os.makedirs(qdir, exist_ok=True)
            # Real gap found in a full file-areas audit: a bare
            # int(time.time()) prefix only has 1-second resolution --
            # two uploads of the same-named file within the same second
            # collided in the quarantine dir, silently overwriting one
            # (no FileQueueEntry.id exists yet at this point to
            # disambiguate the way approve()'s own dest-collision
            # handling does). secrets.token_hex is unique regardless of
            # timing.
            import secrets as _secrets
            qpath = os.path.join(qdir, f'{_secrets.token_hex(8)}-{safe_name}')
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

        # Archive integrity test (optional — silently skipped for formats
        # with no test API, or a missing optional library).
        try:
            from ..features.archive_meta import test_archive_integrity
            result = test_archive_integrity(dest)
            if not result.ok:
                try:
                    os.remove(dest)
                except OSError:
                    pass
                flash(f'Upload rejected: corrupt archive ({result.message}).',
                      'danger')
                return redirect(url_for('file_areas.view_area', area_id=area.id))
        except Exception:
            current_app.logger.exception('archive integrity test crashed; allowing upload')

        # Duplicate-content notice (not a block -- see _check_and_record_dupe).
        dupe_of = _check_and_record_dupe(area, dest, safe_name)

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
                               request.form.get('description'),
                               is_crash=bool(request.form.get('hatch_crash')),
                               is_hold=bool(request.form.get('hatch_hold')))
        if dupe_of:
            flash(f'Uploaded {safe_name} (note: identical to existing "{dupe_of}").',
                  'warning')
        else:
            flash(f'Uploaded {safe_name}.', 'success')
    except OSError as exc:
        flash(f'Save failed: {exc}', 'danger')
    return redirect(url_for('file_areas.view_area', area_id=area.id))


# ---------------------------------------------------------------------------
# Shared file links (peer-to-peer / anonymous-access shares)
# ---------------------------------------------------------------------------

def _client_ip():
    # Real gap found in a full auth-security audit: this trusted a
    # client-supplied X-Forwarded-For header unconditionally, with no
    # trusted-proxy boundary -- anyone could pollute last_accessed_ip's
    # audit trail with an arbitrary fake address. web_app.py's ProxyFix
    # (opt-in via TRUST_PROXY_HEADERS) is now the only place XFF is
    # trusted, rewriting request.remote_addr itself when the sysop has
    # confirmed Flask sits behind their own trusted proxy -- see
    # web/auth.py's _client_ip() for the full writeup of this same fix.
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
    if not area.storage_path or not os.path.isdir(area.storage_path):
        flash('File not found.', 'danger')
        return redirect(url_for('file_areas.view_area', area_id=area.id))

    # Real gap found in a full application-wide access-control audit:
    # this route never applied the same normpath+realpath traversal
    # guard download()/thumbnail() (above) already use -- a crafted
    # `filename` (e.g. `../../../etc/passwd`) made the existence check
    # an oracle (success/failure reveals whether an arbitrary path
    # exists on the server) and would have persisted a SharedFileLink
    # pointing outside the area's storage directory either way.
    full = os.path.normpath(os.path.join(area.storage_path, filename))
    if not full.startswith(os.path.realpath(area.storage_path) + os.sep):
        flash('File not found.', 'danger')
        return redirect(url_for('file_areas.view_area', area_id=area.id))
    if not os.path.isfile(full):
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
    if not area or not area.storage_path or not os.path.isdir(area.storage_path):
        abort(404)
    # Defense-in-depth: create_share() now blocks a traversal filename
    # from ever being stored, but re-check here too in case a
    # SharedFileLink row predates that fix.
    fpath = os.path.normpath(os.path.join(area.storage_path, link.filename))
    if not fpath.startswith(os.path.realpath(area.storage_path) + os.sep):
        abort(404)
    if not os.path.isfile(fpath):
        abort(404)
    # Daily download quota: this endpoint has no logged-in requester to
    # charge, so attribute it to the share's creator instead -- without
    # this, any user could self-issue a share link for a file they can
    # already see and re-fetch it anonymously to route unlimited
    # downloads around their own quota. Real gap found in a pre-release
    # audit.
    from ..features.file_quota import check_quota, consume_quota
    from ..models import User as _User
    creator = _User.query.get(link.created_by_id)
    if creator is not None:
        file_size = os.path.getsize(fpath)
        ok, _quota_msg = check_quota(creator, file_size)
        if not ok:
            abort(429)
    # Update audit fields BEFORE serving so the count is right even if the
    # user aborts the download mid-stream.
    link.download_count = (link.download_count or 0) + 1
    link.last_accessed_at = datetime.datetime.utcnow()
    link.last_accessed_ip = _client_ip()
    db.session.commit()
    if creator is not None:
        consume_quota(creator, file_size)
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

        def _mutate(cache):
            cache.pop(filename, None)
            return cache
        _update_json_sidecar(_desc_cache_path(area), _mutate)
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
    # Real gap found in a full file-areas audit: every sibling route that
    # touches a file by name in this module (manage_delete, download,
    # create_share, fetch_shared, thumbnail) does this normpath+realpath
    # confinement check before touching the filesystem -- this one only
    # had the os.sep/leading-dot check above, not the full guard. Not
    # currently exploitable on this Linux-only deployment (no os.sep
    # means no way to leave the directory), but kept consistent with the
    # established pattern rather than relying on that.
    full = os.path.normpath(os.path.join(area.storage_path, filename))
    if not full.startswith(os.path.realpath(area.storage_path) + os.sep):
        flash('Invalid path.', 'danger')
        return redirect(url_for('file_areas.manage_files', area_id=area.id))
    if not os.path.isfile(full):
        flash(f'{filename} not found.', 'danger')
        return redirect(url_for('file_areas.manage_files', area_id=area.id))
    try:
        st = os.stat(full)
        entry = {
            'mtime': int(st.st_mtime),
            'size': st.st_size,
            'description': description,
        }

        def _mutate(cache, entry=entry):
            cache[filename] = entry
            return cache
        _update_json_sidecar(_desc_cache_path(area), _mutate)
        flash(f'Description updated for {filename}.', 'success')
    except OSError as exc:
        flash(f'Failed: {exc}', 'danger')
    return redirect(url_for('file_areas.manage_files', area_id=area.id))


@file_areas_bp.route('/<int:area_id>/manage/upload', methods=['POST'])
@login_required
@rate_limit('file_area_upload', limit=20, window=300, key_fn=_user_or_ip)
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
    too_big, max_size = _oversized_upload(f)
    if too_big:
        flash(f'File too large. Maximum size is {max_size // 1024 // 1024}MB.', 'danger')
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

        # This route had neither a virus scan nor an archive-integrity
        # test before -- adding both together while touching it, same
        # reject pattern used by upload()/smart_upload() above.
        try:
            from ..features.virus_scan import scan_path
            vr = scan_path(dest)
            if vr.infected:
                try:
                    os.remove(dest)
                except OSError:
                    pass
                flash(f'Upload rejected: virus detected ({vr.signature}).', 'danger')
                return redirect(url_for('file_areas.manage_files', area_id=area.id))
        except Exception:
            current_app.logger.exception('virus scan crashed; allowing upload')
        try:
            from ..features.archive_meta import test_archive_integrity
            ar = test_archive_integrity(dest)
            if not ar.ok:
                try:
                    os.remove(dest)
                except OSError:
                    pass
                flash(f'Upload rejected: corrupt archive ({ar.message}).', 'danger')
                return redirect(url_for('file_areas.manage_files', area_id=area.id))
        except Exception:
            current_app.logger.exception('archive integrity test crashed; allowing upload')

        dupe_of = _check_and_record_dupe(area, dest, safe_name)

        if description:
            st = os.stat(dest)
            entry = {
                'mtime': int(st.st_mtime),
                'size': st.st_size,
                'description': description,
            }

            def _mutate(cache, entry=entry):
                cache[safe_name] = entry
                return cache
            _update_json_sidecar(_desc_cache_path(area), _mutate)
        _hatch_if_network_area(area, dest, safe_name, description,
                               is_crash=bool(request.form.get('hatch_crash')),
                               is_hold=bool(request.form.get('hatch_hold')))
        if dupe_of:
            flash(f'Uploaded {safe_name} (note: identical to existing "{dupe_of}").',
                  'warning')
        else:
            flash(f'Uploaded {safe_name}.', 'success')
    except OSError as exc:
        flash(f'Upload failed: {exc}', 'danger')
    return redirect(url_for('file_areas.manage_files', area_id=area.id))


# ---------------------------------------------------------------------------
# Smart upload — pick (or auto-detect) the target file area by tag
# ---------------------------------------------------------------------------

@file_areas_bp.route('/smart-upload', methods=['GET', 'POST'])
@login_required
@rate_limit('file_area_upload', limit=20, window=300, key_fn=_user_or_ip)
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
        too_big, max_size = _oversized_upload(upload)
        if too_big:
            flash(f'File too large. Maximum size is {max_size // 1024 // 1024}MB.', 'danger')
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
            safe = os.path.basename(upload.filename)
            if not safe or safe.startswith('.'):
                flash('Invalid filename.', 'danger')
                return redirect(url_for('file_areas.smart_upload'))

            # Real gap found in a full file-areas audit: the per-area
            # upload() route (above) checks FILE_MOD_QUEUE_ENABLED and
            # routes non-admin uploads into FileQueueEntry quarantine
            # before anything reaches area.storage_path or gets TIC-
            # hatched to the network -- this alternate upload entry
            # point (auto-detects/lets a user pick the target area by
            # tag) never checked it at all, saving straight to disk and
            # hatching immediately. Any user with upload_permission
            # ('users' on the target area, already correctly checked
            # above) could use this route instead of the per-area form
            # to get a file live and out to network peers with ZERO
            # sysop review, even with moderation explicitly turned on.
            # Compared as a string, not bool() -- see upload()'s own
            # matching comment above for why.
            queue_on = str(current_app.config.get(
                'FILE_MOD_QUEUE_ENABLED', False)).lower() == 'true'
            if queue_on and not getattr(current_user, 'is_admin', False):
                from ..models import FileQueueEntry
                import secrets as _secrets
                qdir = os.path.join(current_app.config.get('DATA_DIR', 'data'),
                                    'file-queue')
                os.makedirs(qdir, exist_ok=True)
                # See upload()'s own matching comment -- unique token
                # instead of a 1-second-resolution timestamp avoids a
                # same-second same-filename collision in quarantine.
                qpath = os.path.join(qdir, f'{_secrets.token_hex(8)}-{safe}')
                upload.save(qpath)
                entry = FileQueueEntry(
                    file_area_id=target.id,
                    user_id=current_user.id,
                    filename=safe,
                    quarantine_path=qpath,
                    description=(request.form.get('description') or '').strip() or None,
                    size_bytes=os.path.getsize(qpath),
                    status='pending',
                )
                db.session.add(entry)
                db.session.commit()
                flash(f'{safe} uploaded to {target.tag} — pending sysop approval.',
                      'info')
                return redirect(url_for('file_areas.view_area', area_id=target.id))

            os.makedirs(target.storage_path, exist_ok=True)
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
            # Optional archive integrity test
            try:
                from ..features.archive_meta import test_archive_integrity
                ar = test_archive_integrity(dest)
                if not ar.ok:
                    try: os.remove(dest)
                    except OSError: pass
                    flash(f'Rejected: corrupt archive ({ar.message}).', 'danger')
                    return redirect(url_for('file_areas.smart_upload'))
            except Exception:
                pass
            dupe_of = _check_and_record_dupe(target, dest, safe)
            _hatch_if_network_area(target, dest, safe,
                                   request.form.get('description'),
                                   is_crash=bool(request.form.get('hatch_crash')),
                                   is_hold=bool(request.form.get('hatch_hold')))
            if dupe_of:
                flash(f'Uploaded {safe} to {target.tag} '
                      f'(note: identical to existing "{dupe_of}").', 'warning')
            else:
                flash(f'Uploaded {safe} to {target.tag}.', 'success')
            return redirect(url_for('file_areas.view_area', area_id=target.id))
        except OSError as exc:
            flash(f'Save failed: {exc}', 'danger')

    return render_template('file_areas/smart_upload.html', areas=visible_areas)
