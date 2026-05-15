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
import shutil

from ..models import db, TicFile, FileArea, FileEchoSubscription, HatchQueue


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
            result['crc'] = val.lower().strip()
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

    bin_path = os.path.join(inbound_dir, parsed['file'])
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
    # to /var/lib/anetbbs/file_areas/<TAG>/ and persist that for next time.
    if not area.storage_path:
        area.storage_path = os.path.join('/var/lib/anetbbs/file_areas',
                                         area.tag.replace('/', '_'))
    try:
        os.makedirs(area.storage_path, exist_ok=True)
        dest = os.path.join(area.storage_path, parsed['file'])
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
            db.session.rollback()
            tic.error_message = (
                (tic.error_message or '')
                + f' [nodelist auto-import failed: {exc}]'
            ).strip()
            db.session.commit()

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
        # Skip if we already processed this TIC (match on raw filename)
        existing = TicFile.query.filter_by(
            filename=os.path.basename(fn)).first()
        if existing and existing.status == 'filed':
            continue
        process_tic(tic_path, inbound_dir)
        processed += 1
    return processed
