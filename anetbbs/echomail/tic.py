# anetbbs/echomail/tic.py
"""
TIC file processor — FidoNet file echo distribution (FSC-0028).

A TIC file is a small text manifest distributed alongside a binary file in a
FidoNet "file echo". Layout (simplified):

    File foo.zip
    Area FILEAREA.TAG
    Desc Short description
    Ldesc Longer line 1
    Ldesc Longer line 2
    Crc 0a1b2c3d
    Size 12345
    Replaces foo*.zip
    Origin 1:234/5
    From 1:234/5
    To 1:234/567
    Pw secret
    Path 1:234/5 1739000000 Sun Apr 27 12:00:00 2025 EDT
    Seenby 1:234/5
    Seenby 1:234/567

When BinkP transports the TIC bundle into our inbound dir, we:
  1. Parse the .tic file
  2. Locate the matching binary (Path-relative to the TIC file)
  3. Validate CRC and size
  4. Insert a FileUpload row in the corresponding area
  5. Record the TIC in tic_files for audit
  6. Optionally re-distribute (hatch) to downstream peers
"""
import os
import re
import json
import zlib
import datetime
import logging
import shutil

from ..models import db, TicFile, FileArea, FileEchoSubscription, HatchQueue

logger = logging.getLogger(__name__)


# HatchQueue.retry_count/error_message/status ("pending / sent / failed")
# were part of the model from the start (see its own docstring: "Failures
# bump retry_count; we cap at retry_max and mark failed") but neither
# delivery path (binkp.py's _send_hatch, binkp_server.py's
# _send_hatch_items) ever actually wrote to them -- a failed attempt was
# indistinguishable from one never even tried, and the hub admin page's
# "Failed" counter was permanently 0. Real sysop report: "need a tic out
# log for hub management" surfaced this while looking for exactly this
# information. HATCH_RETRY_MAX caps retries so a permanently-broken
# subscription (deleted peer, bad address) eventually stops being
# retried forever and surfaces as 'failed' in the log instead of sitting
# in 'pending' indefinitely with no visible sign anything is wrong.
HATCH_RETRY_MAX = 20


def record_hatch_attempt_failure(item, message):
    """Record one failed outbound delivery attempt on a HatchQueue row.

    Bumps retry_count and stores the reason; flips status to 'failed'
    once HATCH_RETRY_MAX is exceeded so it stops being retried forever.
    Caller is responsible for committing."""
    item.retry_count = (item.retry_count or 0) + 1
    item.error_message = message
    if item.retry_count >= HATCH_RETRY_MAX:
        item.status = 'failed'


_LINE_RE = re.compile(r'^\s*(\w+)\s+(.*?)\s*$')


def parse_tic(content):
    """Parse a TIC file's text into a dict.

    Multi-line fields like Ldesc, Path, Seenby get aggregated into lists.
    Other fields are strings (last value wins on duplicates)."""
    result = {
        'file': '',
        'area': '',
        'desc': '',
        'ldesc': [],
        'crc': '',
        'size': 0,
        'replaces': '',
        'origin': '',
        'from': '',
        'to': '',
        'pw': '',
        'path': [],
        'seenby': [],
    }
    for line in content.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        key = m.group(1).lower()
        val = m.group(2)
        if key == 'file':
            result['file'] = val
        elif key == 'area':
            result['area'] = val.upper()
        elif key == 'desc':
            result['desc'] = val
        elif key == 'ldesc':
            result['ldesc'].append(val)
        elif key == 'crc':
            # Real gap found live: some TIC generators (older DOS-era
            # tools especially) write the CRC32 without zero-padding to
            # 8 hex digits (e.g. "a1b2c3" instead of "00a1b2c3") --
            # _crc32_file() below always formats its own result as
            # exactly 8 digits, so an unpadded manifest value failed
            # the equality check every single time regardless of
            # whether the file was actually intact, permanently
            # blocking that TIC from ever filing (retried and failed
            # identically on every subsequent scan). Zero-pad here so
            # both sides compare on equal footing; also strip a stray
            # "0x" prefix some tools emit.
            crc_val = val.lower().strip()
            if crc_val.startswith('0x'):
                crc_val = crc_val[2:]
            result['crc'] = crc_val.zfill(8) if crc_val else ''
        elif key == 'size':
            try:
                result['size'] = int(val)
            except ValueError:
                pass
        elif key in ('replaces', 'origin', 'from', 'to', 'pw'):
            result[key] = val
        elif key == 'path':
            result['path'].append(val)
        elif key == 'seenby':
            result['seenby'].append(val)
    return result


def _crc32_file(path, blocksize=65536):
    """Compute CRC32 of a file as an 8-char lowercase hex string."""
    h = 0
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(blocksize)
            if not chunk:
                break
            h = zlib.crc32(chunk, h)
    return f'{h & 0xffffffff:08x}'


def _dos83_truncate(name):
    """Return the classic DOS 8.3 form of a filename: the base name
    (everything before the last '.') truncated to 8 characters, plus
    the extension truncated to 3. Used as a fallback when a TIC
    manifest's File: field is itself 8.3-truncated but the actual
    attached binary kept its real, longer filename."""
    base, ext = os.path.splitext(name)
    ext = ext[1:4] if ext.startswith('.') else ext[:3]
    return f'{base[:8]}.{ext}' if ext else base[:8]


def process_tic(tic_path, inbound_dir):
    """Process a single .tic file. Returns the TicFile DB row.

    `tic_path`  — full path to the .tic file
    `inbound_dir` — dir to look in for the referenced binary

    Side effects:
      - parses + saves a TicFile row
      - if the file exists and CRC matches, copies it to the matching
        FileArea's storage and records a FileUpload row
      - sets TicFile.status to 'filed', 'error', or 'skipped'
    """
    try:
        with open(tic_path, 'r', encoding='cp437', errors='replace') as f:
            content = f.read()
    except OSError as exc:
        # Can't even read the TIC — record an error row anyway.
        tic = TicFile(filename=os.path.basename(tic_path),
                      raw_content='', status='error',
                      error_message=str(exc))
        db.session.add(tic)
        db.session.commit()
        return tic

    parsed = parse_tic(content)
    tic = TicFile(
        area_tag=parsed['area'],
        filename=parsed['file'],
        size_bytes=parsed['size'],
        crc32=parsed['crc'],
        description=parsed['desc'] + (
            ('\n' + '\n'.join(parsed['ldesc'])) if parsed['ldesc'] else ''),
        origin=parsed['origin'],
        from_address=parsed['from'],
        seenby=json.dumps(parsed['seenby']),
        path=json.dumps(parsed['path']),
        raw_content=content,
        status='pending',
    )
    db.session.add(tic)
    db.session.commit()

    # Locate the binary
    if not parsed['file']:
        tic.status = 'error'
        tic.error_message = 'TIC missing File: header'
        db.session.commit()
        return tic

    # Real gap found in a full echomail-subsystem audit: the manifest's
    # File: field is peer-supplied text, used unsanitized in TWO
    # os.path.join() calls below (locating the binary, and staging it
    # into the file area's storage) -- the exact same traversal bug
    # already fixed for the raw wire-level CMD_FILE filename in
    # binkp.py's _sanitize_inbound_filename() (an absolute path like
    # '/etc/cron.d/x' silently discards inbound_dir/storage_path
    # entirely per os.path.join()'s own documented semantics, and
    # '../../x' escapes it). Reusing that same sanitizer here means a
    # crafted "File ../../x" manifest can no longer read or write
    # outside inbound_dir/area.storage_path. binkp.py already applies
    # this sanitizer when it stashes the file it received on the wire,
    # so a legitimate File: field (matching the real basename) still
    # resolves to the exact same on-disk name here.
    from .binkp import _sanitize_inbound_filename
    safe_filename = _sanitize_inbound_filename(parsed['file'])

    bin_path = os.path.join(inbound_dir, safe_filename)
    if not os.path.isfile(bin_path):
        # Real gap found live: some FTN mailers uppercase (or otherwise
        # re-case) filenames on the wire regardless of the case the
        # TIC's own File: field uses -- an exact-case lookup on a
        # case-sensitive Linux filesystem then never finds a binary
        # that's genuinely sitting right there under a different case,
        # permanently blocking that TIC (retried and failed identically
        # on every subsequent scan; the binary just accumulates in
        # inbound_dir forever alongside every other stuck one). Fall
        # back to a case-insensitive match before giving up.
        try:
            actual_name = next(
                (f for f in os.listdir(inbound_dir)
                 if f.lower() == safe_filename.lower()), None)
        except OSError:
            actual_name = None
        if actual_name:
            bin_path = os.path.join(inbound_dir, actual_name)
    if not os.path.isfile(bin_path):
        # Second real gap found live: some TIC generators write a
        # classic DOS 8.3-truncated name in the File: field (first 8
        # chars of the base name + the extension) even though the
        # actual attached/hatched binary keeps its real, longer
        # filename on the wire -- e.g. manifest says "white_pa.zip"
        # but the file that actually arrived is "white_paper_3.0.zip".
        # Confirmed live: six files in one batch all matched this
        # pattern exactly, each with an identical byte size to its
        # long-named counterpart already sitting in inbound_dir. Fall
        # back to comparing each candidate's own 8.3-truncated form
        # against the manifest name. Safe even if this ever matches
        # the wrong file by coincidence -- the size/CRC checks right
        # below still reject it before anything gets filed.
        try:
            actual_name = next(
                (f for f in os.listdir(inbound_dir)
                 if _dos83_truncate(f).lower() == safe_filename.lower()),
                None)
        except OSError:
            actual_name = None
        if actual_name:
            bin_path = os.path.join(inbound_dir, actual_name)
    if not os.path.isfile(bin_path):
        tic.status = 'error'
        tic.error_message = f'binary not found at {bin_path!r}'
        db.session.commit()
        return tic

    # Validate
    if parsed['size'] and os.path.getsize(bin_path) != parsed['size']:
        tic.status = 'error'
        tic.error_message = (
            f'size mismatch — TIC says {parsed["size"]} but file is '
            f'{os.path.getsize(bin_path)}')
        db.session.commit()
        return tic
    if parsed['crc']:
        actual = _crc32_file(bin_path)
        if actual != parsed['crc']:
            tic.status = 'error'
            tic.error_message = (
                f'CRC mismatch — TIC says {parsed["crc"]} but file is {actual}')
            db.session.commit()
            return tic

    # File-area integration: look up a FileArea by tag and copy the binary
    # into its storage directory.
    area = (FileArea.query.filter_by(tag=parsed['area']).first()
            if parsed['area'] else None)

    # Real gap found in the same audit: FileArea.password ("optional
    # area password", models.py) exists specifically to gate TIC
    # filing the same way network.areafix_password gates AreaFix --
    # but nothing here ever compared it against the manifest's own Pw:
    # field. Any authenticated BinkP peer able to deliver a TIC could
    # file into a password-protected area regardless. Only enforced
    # when the area actually has a password set (it's opt-in).
    if area is not None and area.password and parsed['pw'] != area.password:
        tic.status = 'error'
        tic.error_message = 'TIC password does not match area password'
        db.session.commit()
        return tic
    if area is None:
        # Auto-create the area on first sight so an unknown TIC doesn't get
        # silently dropped. The sysop can later edit storage_path / name.
        area = FileArea(
            tag=parsed['area'] or 'UNKNOWN',
            name=parsed['area'] or 'Unknown File Area',
            description='Auto-created from inbound TIC',
            is_active=True,
            is_subscribed=True,
        )
        db.session.add(area)
        db.session.flush()
        tic.error_message = (tic.error_message or '') + ' [auto-created area]'

    # Resolve a storage destination. If the area has no storage_path, default
    # to {DATA_DIR}/file_areas/<TAG>/ and persist that for next time -- same
    # convention every other on-disk default in this app uses (uploads/,
    # avatars/, echomail/, etc. all live under DATA_DIR). Real bug found
    # live: this used to default to /var/lib/anetbbs/file_areas/<TAG>,
    # which the service (running as an unprivileged user in every real
    # install, e.g. /home/stingray/anetbbs) has no permission to create --
    # every auto-created area's first TIC delivery failed with EACCES and
    # only ever recovered if a sysop manually pre-built that path as root.
    # Confirmed live: a legitimate tqwinfo.zip TIC retried and failed 4
    # times (permission denied one directory level deeper each retry, as
    # a human manually built the path by hand) before finally filing.
    if not area.storage_path:
        try:
            from flask import current_app
            data_dir = current_app.config.get('DATA_DIR') or 'data'
        except RuntimeError:
            data_dir = 'data'
        area.storage_path = os.path.join(data_dir, 'file_areas',
                                         area.tag.replace('/', '_'))
    try:
        os.makedirs(area.storage_path, exist_ok=True)
        dest = os.path.join(area.storage_path, safe_filename)
        # Don't clobber an existing file with the same name — re-distributed
        # TICs could legitimately replace, but we treat that as the sysop's
        # choice and currently skip if the same name already exists.
        if not os.path.exists(dest):
            shutil.copy2(bin_path, dest)
        tic.stored_path = dest
    except OSError as exc:
        tic.status = 'error'
        tic.error_message = (
            f'Could not stage binary into area storage: {exc}')
        db.session.commit()
        return tic

    tic.file_area_id = area.id
    tic.processed_at = datetime.datetime.utcnow()
    tic.status = 'filed'
    db.session.commit()

    # Real gap found live ("files piled up in inbound"): the binary was
    # only ever COPIED into area.storage_path -- the original .tic
    # manifest and the binary itself were never removed from
    # inbound_dir on success, so every TIC ever received (successfully
    # filed or not) accumulated there forever with no way to tell, from
    # a directory listing alone, which ones were actually stuck vs.
    # already filed fine and just never swept. Move both out to a
    # processed/ subdir (never delete outright -- matches this
    # project's usual "keep it recoverable" posture elsewhere) so
    # inbound_dir only ever holds things still awaiting or failing
    # processing. Best-effort: a cleanup failure here must never
    # downgrade a TIC that already successfully filed.
    try:
        processed_dir = os.path.join(inbound_dir, 'processed')
        os.makedirs(processed_dir, exist_ok=True)
        for src in (bin_path, tic_path):
            if src and os.path.isfile(src):
                dst = os.path.join(processed_dir, os.path.basename(src))
                if os.path.exists(dst):
                    os.remove(src)
                else:
                    shutil.move(src, dst)
    except OSError:
        logger.exception('TIC %s: could not clean up inbound after filing',
                         tic.filename)

    # Auto-import nodelist if this file area is a nodelist distribution echo
    # (e.g. Z1DAILY for FidoNet, tqwinfo for TQWnet). The nodelist text might
    # be the bare file or wrapped in a ZIP — `import_from_path` handles both.
    if getattr(area, 'is_nodelist_source', False) and area.nodelist_domain:
        from . import nodelist as _nodelist_importer
        try:
            nl = _nodelist_importer.import_from_path(
                tic.stored_path, domain=area.nodelist_domain)
            tic.error_message = (
                (tic.error_message or '')
                + f' [nodelist auto-imported: {nl.entry_count} entries, '
                + f'domain {area.nodelist_domain}]'
            ).strip()
            db.session.commit()
        except Exception as exc:
            # The TIC transfer itself already succeeded (status='filed',
            # committed above) -- this is a secondary, best-effort "also
            # try to import this as a nodelist" step. A nodelist-flagged
            # area can legitimately receive ordinary (non-nodelist) files
            # too (mixed-use tag, or just a day with no nodelist release),
            # so a file here not matching the nodelist-shape heuristic
            # isn't a real transfer error. Previously this was appended to
            # tic.error_message, which reads exactly like a failed
            # transfer in the TIC log — a real sysop report ("the tic log
            # shows an error... on files that are not a nodelist") traced
            # to exactly this. Log it instead of flagging the TIC record.
            db.session.rollback()
            logger.debug(
                'TIC %s: nodelist auto-import skipped for area %s '
                '(domain %s) — %s', tic.filename, area.tag,
                area.nodelist_domain, exc)

    # Hatch-out: queue the TIC for any downstream peer subscribed to this
    # area, EXCEPT peers who are already in the SEEN-BY (avoid loops).
    seenby_addrs = set(parsed['seenby'] or [])
    # Inbound peer is implicitly seen.
    if parsed['from']:
        seenby_addrs.add(parsed['from'])
    new_seenby = list(seenby_addrs)
    new_path = list(parsed['path'] or [])

    subs = (FileEchoSubscription.query
            .filter_by(file_area_id=area.id, is_active=True).all())
    queued = 0
    for sub in subs:
        if sub.peer_address in seenby_addrs:
            continue
        db.session.add(HatchQueue(
            file_area_id=area.id,
            peer_address=sub.peer_address,
            binary_path=tic.stored_path,
            filename=parsed['file'],
            description=tic.description,
            crc32=parsed['crc'],
            size_bytes=parsed['size'],
            seenby=json.dumps(new_seenby),
            path=json.dumps(new_path),
            status='pending',
        ))
        queued += 1
    if queued:
        db.session.commit()
    return tic


def hatch_local_file(area, stored_path, filename, description=''):
    """Queue a locally-originated file (uploaded by a sysop/user directly,
    not received via an inbound TIC) for outbound distribution to every
    peer subscribed to this file echo.

    Mirrors the fan-out in process_tic()'s hatch-out step (above), but
    for a file that originates here -- there's no prior SEEN-BY/PATH to
    inherit since this IS the origin hop; build_tic_text() fills those in
    when the queued item is actually transmitted.

    Returns the number of peers queued (0 if nobody is subscribed, or if
    the area has no active subscriptions at all).
    """
    crc = _crc32_file(stored_path)
    size = os.path.getsize(stored_path)

    subs = (FileEchoSubscription.query
            .filter_by(file_area_id=area.id, is_active=True).all())
    queued = 0
    for sub in subs:
        db.session.add(HatchQueue(
            file_area_id=area.id,
            peer_address=sub.peer_address,
            binary_path=stored_path,
            filename=filename,
            description=description or '',
            crc32=crc,
            size_bytes=size,
            seenby=json.dumps([]),
            path=json.dumps([]),
            status='pending',
        ))
        queued += 1
    if queued:
        db.session.commit()
    return queued


def build_tic_text(item, our_address):
    """Render a HatchQueue row back to a TIC file body for outbound transmit.

    Used by the BinkP client when shipping queued TICs. Accumulates this
    node's SEEN-BY / PATH so the manifest is valid down the chain.
    """
    seenby = json.loads(item.seenby) if item.seenby else []
    path = json.loads(item.path) if item.path else []
    if our_address and our_address not in seenby:
        seenby = seenby + [our_address]
    # Append our hop to the PATH with a timestamp.
    import time as _time
    new_path_entry = (
        f'{our_address} {int(_time.time())} '
        + datetime.datetime.utcnow().strftime('%a %b %d %H:%M:%S %Y') + ' UTC')
    path = path + [new_path_entry]

    lines = [
        f'File {item.filename}',
        f'Area {item.file_area.tag if item.file_area else "UNKNOWN"}',
    ]
    if item.description:
        for line in item.description.splitlines():
            lines.append(f'Desc {line}'
                         if not line.startswith('Desc ') else line)
    if item.crc32:
        lines.append(f'Crc {item.crc32}')
    if item.size_bytes:
        lines.append(f'Size {item.size_bytes}')
    lines.append(f'Origin {our_address}')
    lines.append(f'From {our_address}')
    lines.append(f'To {item.peer_address}')
    for p in path:
        lines.append(f'Path {p}')
    for s in seenby:
        lines.append(f'Seenby {s}')
    return '\r\n'.join(lines) + '\r\n'


def scan_inbound(inbound_dir):
    """Scan an inbound dir for unprocessed .tic files and process them.

    Returns the number of TICs processed (any status — including errors)."""
    if not os.path.isdir(inbound_dir):
        return 0
    processed = 0
    for fn in sorted(os.listdir(inbound_dir)):
        if not fn.lower().endswith('.tic'):
            continue
        tic_path = os.path.join(inbound_dir, fn)
        # Skip TICs whose binary was already successfully filed.
        # TicFile.filename stores the binary name (e.g. "tqwinfo.zip"), not
        # the .tic manifest name — so peek at the content to get it.
        try:
            with open(tic_path, 'r', encoding='cp437', errors='replace') as _f:
                _peeked = parse_tic(_f.read())
            bin_name = _peeked.get('file', '')
        except Exception:
            bin_name = ''
        if bin_name:
            existing = TicFile.query.filter_by(
                filename=bin_name, status='filed').first()
            if existing:
                continue
        process_tic(tic_path, inbound_dir)
        processed += 1
    return processed
