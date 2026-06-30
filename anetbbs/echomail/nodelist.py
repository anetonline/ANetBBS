# anetbbs/echomail/nodelist.py
"""
FidoNet nodelist parser + importer.

A nodelist is a flat-file directory of every system on a FidoNet-compatible
network. The format is defined by FTS-5000 — comma-separated rows with an
optional KEYWORD prefix that indicates the row's role in the hierarchy:

    Zone,1,FidoNet,World,...                # zone start
    Region,10,RegionalNet_10,...            # region within zone
    Host,234,SomeNet,SomeCity,...           # net hub
    ,567,LocalBBS,Boise_ID,Sysop_Name,...   # plain node within current net
    Hub,500,SomeHub,...                     # area hub (still in current net)
    Pvt,42,PrivateBBS,...
    Hold,99,RetiredBBS,...
    Down,15,DyingBBS,...

Header line carries the day-of-year and CRC checksum; subsequent ;-prefixed
lines are comments. We track currentZone/currentNet across rows so plain
nodes inherit the right zone:net/node coordinates.

Reference: binkterm-php's NodelistParser.php ported to Python.
"""
import json
import os
import re
import tempfile
import datetime

from ..models import db, Nodelist, NodelistEntry


_HEADER_RE = re.compile(
    r';A\s+(\w+)\s+nodelist\s+for\s+(\w+\s+\d+,\s+\d+)\s+--\s+Day\s+(\d+)\s+:\s+(\d+)\s*$'
)


def parse_header(content):
    """Pull metadata out of the nodelist's first ;-comment line.

    Returns a dict with keys: filename, day_of_year, release_date (date),
    crc_checksum. Falls back to today's date / day=1 if header is malformed."""
    first_line = (content.splitlines()[0] if content else '').strip()
    if not first_line.startswith(';'):
        return {
            'filename': 'NODELIST',
            'day_of_year': 1,
            'release_date': datetime.date.today(),
            'crc_checksum': '',
        }

    m = _HEADER_RE.match(first_line)
    if not m:
        # Try to grab just the trailing CRC at minimum
        crc_m = re.search(r'(\d{3,5})\s*$', first_line)
        return {
            'filename': 'NODELIST',
            'day_of_year': 1,
            'release_date': datetime.date.today(),
            'crc_checksum': crc_m.group(1) if crc_m else '',
        }

    name, date_str, day, crc = m.groups()
    try:
        release_date = datetime.datetime.strptime(date_str.strip(), '%B %d, %Y').date()
    except ValueError:
        release_date = datetime.date.today()
    return {
        'filename': name,
        'day_of_year': int(day),
        'release_date': release_date,
        'crc_checksum': crc,
    }


def _clean(field):
    """Nodelist fields use underscores in place of spaces (per FTS-5000)."""
    return (field or '').strip().replace('_', ' ')


def parse_flags(flag_string):
    """Flags are comma-separated tokens, optionally `KEY:VALUE`.

    We return a dict so JSON encoding is straightforward. `IBN:24554` becomes
    {'IBN': '24554'}; `CM` (no value) becomes {'CM': True}."""
    flags = {}
    for raw in (flag_string or '').split(','):
        raw = raw.strip()
        if not raw:
            continue
        if ':' in raw:
            k, v = raw.split(':', 1)
            flags[k.strip()] = v.strip()
        else:
            flags[raw] = True
    return flags


class _ParserState:
    def __init__(self):
        self.current_zone = 1
        self.current_net = 0


def parse_line(line, state):
    """Parse a single non-comment nodelist line into a dict for DB insertion.

    Returns None if the line is malformed or empty. Mutates state to track
    the current zone/net across rows (Zone/Region/Net/Host markers update it)."""
    line = line.strip()
    if not line or line.startswith(';'):
        return None

    fields = line.split(',')
    if len(fields) < 6:
        return None

    keyword_type = fields[0].strip()
    raw_node = fields[1].strip()
    system_name = _clean(fields[2] if len(fields) > 2 else '')
    location    = _clean(fields[3] if len(fields) > 3 else '')
    sysop_name  = _clean(fields[4] if len(fields) > 4 else '')
    phone       = (fields[5] if len(fields) > 5 else '').strip()
    try:
        baud_rate = int((fields[6] if len(fields) > 6 else '0').strip() or 0)
    except ValueError:
        baud_rate = 0
    flag_part = ','.join(fields[7:]) if len(fields) > 7 else ''
    flags = parse_flags(flag_part)

    kw = keyword_type.lower()
    try:
        node_number = int(raw_node)
    except ValueError:
        return None

    if kw == 'zone':
        state.current_zone = node_number
        node_number = 0           # zone coordinator is always node 0
    elif kw in ('net', 'host', 'region'):
        state.current_net = node_number
        node_number = 0           # net coordinator is always node 0
    elif kw in ('hub', 'pvt', 'hold', 'down', ''):
        # plain node row (or a sub-classification of one) — keep node_number
        pass
    else:
        # Unknown keyword — fall back to treating field 1 as the node number
        if raw_node.isdigit():
            node_number = int(raw_node)
            keyword_type = ''

    # Point address support (e.g. "12.5" — node 12, point 5)
    point_number = 0
    if isinstance(raw_node, str) and '.' in raw_node:
        try:
            n_str, p_str = raw_node.split('.', 1)
            node_number = int(n_str)
            point_number = int(p_str)
        except ValueError:
            pass

    return {
        'zone': state.current_zone,
        'net': state.current_net,
        'node': node_number,
        'point': point_number,
        'keyword_type': keyword_type or None,
        'system_name': system_name,
        'location': location,
        'sysop_name': sysop_name,
        'phone': phone,
        'baud_rate': baud_rate,
        'flags': json.dumps(flags),
    }


def parse_nodelist(content):
    """Parse a full nodelist file's text content. Returns (metadata, entries).

    `entries` is a list of dicts ready to be passed to NodelistEntry(**entry).
    """
    metadata = parse_header(content)
    state = _ParserState()
    entries = []
    for line in content.splitlines():
        try:
            entry = parse_line(line, state)
            if entry is not None:
                entries.append(entry)
        except Exception:
            # Skip malformed lines silently — nodelists from the wild often
            # have stray bytes; better to import the rest than fail entirely.
            continue
    return metadata, entries


def _looks_like_nodelist(name):
    """Heuristic: does this filename look like an FTN nodelist?

    Matches `NODELIST.NNN` (day-of-year suffix) and the common shorthand
    `*.NNN` / `*.zNN` (Mystic / tqwnet style — e.g. `tqwnet.z46`). Plain
    `.txt` is also accepted on the assumption the sysop knows what they
    packed. Returns a sort key (newest first) so callers can pick the
    best file out of an archive's manifest.
    """
    name_l = name.lower()
    # NODELIST.NNN (FidoNet convention) — highest priority
    m = re.match(r'^(.*?)\.(\d{3})$', name_l)
    if m:
        return (3, int(m.group(2)))
    # *.zNN / *.aNN — weekly zipped variants. Take the week number.
    m = re.match(r'^.*\.[zal](\d{2,3})$', name_l)
    if m:
        return (2, int(m.group(1)))
    # *.txt — lowest priority but accepted
    if name_l.endswith('.txt') or 'nodelist' in name_l:
        return (1, 0)
    return None


def _extract_nodelist_from_archive(archive_path):
    """Open a ZIP archive and return (extracted_text_path, member_name) for
    the file inside that most looks like a nodelist. Caller is responsible
    for deleting the returned path.

    Returns (None, None) if no plausible member is found. Only ZIP is
    supported — ARJ/LZH require external tools and aren't standard in
    today's FTN file echoes anyway."""
    import zipfile
    if not zipfile.is_zipfile(archive_path):
        return None, None
    candidates = []
    with zipfile.ZipFile(archive_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            key = _looks_like_nodelist(os.path.basename(info.filename))
            if key:
                candidates.append((key, info))
        if not candidates:
            return None, None
        candidates.sort(key=lambda c: c[0], reverse=True)
        chosen = candidates[0][1]
        fd, out_path = tempfile.mkstemp(prefix='nodelist_', suffix='.txt')
        with os.fdopen(fd, 'wb') as out_fh:
            out_fh.write(zf.read(chosen.filename))
    return out_path, chosen.filename


def import_from_path(filepath, domain='fidonet'):
    """Sysop-friendly wrapper: import a nodelist whether it's plain text
    or wrapped in a ZIP. Used by the TIC auto-import hook and the bulk
    importer. Returns the Nodelist row (or raises)."""
    if filepath.lower().endswith(('.zip', '.z01', '.z02', '.z03', '.z04',
                                   '.z05', '.z06', '.z07', '.z08', '.z09')) \
            or _is_zip(filepath):
        extracted, member = _extract_nodelist_from_archive(filepath)
        if not extracted:
            raise ValueError(f'No nodelist-shaped file inside {filepath}')
        try:
            return import_nodelist(extracted, domain=domain)
        finally:
            try: os.unlink(extracted)
            except OSError: pass
    return import_nodelist(filepath, domain=domain)


def _is_zip(path):
    """Quick magic-byte check — extensions like `.z46` / `.za7` are ZIPs
    in FTN nodelist distributions but won't match a simple suffix list."""
    try:
        with open(path, 'rb') as fh:
            return fh.read(4)[:2] == b'PK'
    except OSError:
        return False


def import_nodelist(filepath, domain='fidonet'):
    """Read a nodelist file and import its entries into the database.

    Replaces any prior nodelist with the same domain (one nodelist per
    domain at a time; old entries are cascaded out via the relationship).
    Returns the new Nodelist row.
    """
    with open(filepath, 'r', encoding='cp437', errors='replace') as fh:
        content = fh.read()

    metadata, entries = parse_nodelist(content)

    # Drop the previous nodelist for this domain (cascades to entries).
    old = Nodelist.query.filter_by(domain=domain).all()
    for o in old:
        db.session.delete(o)
    db.session.commit()

    nl = Nodelist(
        domain=domain,
        filename=metadata['filename'],
        day_of_year=metadata['day_of_year'],
        release_date=metadata['release_date'],
        crc_checksum=metadata['crc_checksum'],
        entry_count=len(entries),
    )
    db.session.add(nl)
    db.session.flush()  # assign nl.id

    for e in entries:
        db.session.add(NodelistEntry(nodelist_id=nl.id, **e))
    db.session.commit()
    return nl


def search(query, domain=None, limit=200):
    """Search the most recent nodelist for systems matching `query`.

    Matches against system_name, sysop_name, and address forms (zone, zone:net,
    zone:net/node). Returns up to `limit` NodelistEntry rows."""
    q = NodelistEntry.query.join(Nodelist)
    if domain:
        q = q.filter(Nodelist.domain == domain)
    if query:
        like = f'%{query}%'
        # Try to parse "1:234/567" address forms as exact filter
        addr_m = re.match(r'^(\d+)(?::(\d+)(?:/(\d+)(?:\.(\d+))?)?)?$', query.strip())
        if addr_m:
            z, n, no, p = addr_m.groups()
            q = q.filter(NodelistEntry.zone == int(z))
            if n:
                q = q.filter(NodelistEntry.net == int(n))
            if no:
                q = q.filter(NodelistEntry.node == int(no))
            if p:
                q = q.filter(NodelistEntry.point == int(p))
        else:
            q = q.filter(
                db.or_(
                    NodelistEntry.system_name.ilike(like),
                    NodelistEntry.sysop_name.ilike(like),
                    NodelistEntry.location.ilike(like),
                )
            )
    return q.order_by(NodelistEntry.zone, NodelistEntry.net,
                      NodelistEntry.node, NodelistEntry.point).limit(limit).all()


# ---------------------------------------------------------------------------
# Nodelist generator — produce a standard FidoNet NODELIST for ANotherNetwork
# ---------------------------------------------------------------------------

def generate_nodelist(zone: int, net: int, hub_node: int,
                      hub_name: str, hub_location: str,
                      hub_sysop: str, hub_phone: str = '-Unpublished-',
                      hub_speed: int = 115200) -> str:
    """Generate a NODELIST.XXX text file for the hub's network.

    Includes the hub itself (as Host) and all registered BinkPNode downstream
    nodes.  Returns the nodelist as a string (UTF-8, CR+LF line endings).

    zone:net/hub_node is the hub's FTN address (e.g. 99:1/1 for ANotherNetwork).
    """
    from ..models import BinkPNode

    today = datetime.date.today()
    day_of_year = today.timetuple().tm_yday
    network_name = hub_name.replace(' ', '_')

    lines = [
        f';A NODELIST.{day_of_year:03d} for ANotherNetwork -- '
        f'{today.strftime("%B %d, %Y")} -- Day {day_of_year} : 0000',
        f';',
        f'; ANotherNetwork Nodelist — generated by ANetBBS',
        f'; {today.isoformat()}',
        f';',
    ]

    # Zone header.
    lines.append(
        f'Zone,{zone},{network_name},{hub_location},{hub_sysop},'
        f'{hub_phone},IBN:24554,CM,XX,V34'
    )

    # Net / Host line for the hub.
    lines.append(
        f'Host,{net},{network_name},{hub_location},{hub_sysop},'
        f'{hub_phone},IBN:24554,CM,XX,V34'
    )

    # Hub node entry (zone:net/hub_node).
    safe_name = (hub_name or 'ANetBBS Hub').replace(' ', '_').replace(',', '_')
    lines.append(
        f'Hub,{hub_node},{safe_name},{hub_location},{hub_sysop},'
        f'{hub_phone},IBN:24554,CM,XX,V34'
    )

    # Downstream nodes registered with us.
    nodes = BinkPNode.query.filter_by(is_active=True).order_by(BinkPNode.ftn_address).all()
    for node in nodes:
        # Parse node number from ftn_address (zone:net/node).
        try:
            node_part = node.ftn_address.split('@', 1)[0]
            node_num = int(node_part.split('/', 1)[1].split('.')[0])
        except Exception:
            continue
        safe_sys = (node.system_name or node.name or 'Unknown').replace(' ', '_').replace(',', '_')
        safe_loc = (node.location or 'Unknown').replace(' ', '_').replace(',', '_')
        safe_sop = (node.sysop or 'Sysop').replace(' ', '_').replace(',', '_')
        phone = (node.phone or '-Unpublished-').replace(' ', '_').replace(',', '_')
        lines.append(
            f',{node_num},{safe_sys},{safe_loc},{safe_sop},{phone},IBN:24554,CM'
        )

    return '\r\n'.join(lines) + '\r\n'
