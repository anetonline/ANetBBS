"""
BinkP/1.1 inbound listener for ANetBBS — accepts incoming sessions from
remote echomail nodes that want to deliver mail to us (or pull mail FROM us).

Pairs with the existing outbound binkp.py client. Together they make this BBS
a full peer node, not just a leaf-client.

Listens on 0.0.0.0:24554 by default (configurable via BINKP_LISTEN_PORT env).
Per connection:
  1. Send M_NUL info + our M_ADR
  2. Receive client M_NUL/M_ADR/M_PWD
  3. Look up the remote address in EchomailNetwork (matching hub_address)
  4. Validate password against EchomailNetwork.binkp_password
  5. Send M_OK on success, M_ERR on failure
  6. Receive their .pkt file frames → parse → import as inbound EchomailMessages
  7. Send our queued outbound EchomailMessages for that node, mark sent_at
  8. Exchange M_EOB and close

Usage:
    python -m anetbbs.echomail.binkp_server
"""
import os
import re
import io
import logging
import asyncio
import struct
import hashlib
import hmac
import secrets
import zipfile
from datetime import datetime, timedelta

from .binkp import (
    CMD_NUL, CMD_ADR, CMD_PWD, CMD_FILE, CMD_OK, CMD_EOB,
    CMD_GOT, CMD_ERR, CMD_SKIP, CMD_NAMES,
    _build_cmd, _build_data,
    _build_ftn_packet, _parse_ftn_packet,
    _sanitize_inbound_filename,
    resolve_outbound_dir, list_outbound_dir_files, archive_sent_outbound_file,
)

logger = logging.getLogger(__name__)

# Real vulnerability found in a security audit: the size field in an
# incoming CMD_FILE header is fully attacker-controlled and was never
# checked before this fix -- state['buf'] would grow to match whatever
# size a peer claimed. Worse, this file-receive path is reachable with
# ZERO authentication at all: an unrecognized peer is deliberately
# accepted as "anonymous crashmail" (see _handle_connection below,
# matching real FTN nodelist convention) and still proceeds to file
# receive. Any host on the internet could connect, complete the
# trivial handshake, and declare an arbitrarily large file size to
# exhaust this daemon's memory. 100MB comfortably covers real echomail/
# file-echo traffic (packets and file-echo distributions are routinely
# well under this) while bounding the worst case; configurable via env
# for anyone who legitimately needs larger file-echo distributions.
MAX_INBOUND_FILE_SIZE = int(os.environ.get('BINKP_MAX_FILE_SIZE', 100 * 1024 * 1024))

# Defense in depth alongside the per-file cap above: bounds how many
# BinkP sessions can be mid-receive at once, so even many separate
# connections each offering a just-under-the-cap file can't add up to
# unbounded total memory.
MAX_CONCURRENT_SESSIONS = int(os.environ.get('BINKP_MAX_CONCURRENT_SESSIONS', 20))


# FidoNet hubs deliver mail in several wrappers:
#
#   * Raw FTS-0001 type-2 packet — bytes 18-19 == 02 00 / 02 01.
#     Extension typically .pkt, but Mystic ships point-targeted variants:
#       .pkt              standard
#       .cut .crt         crash mail
#       .dut .drt         direct mail
#       .iut .irt         immediate mail
#       .hut .hrt         hold mail
#       .t<flavor><hex>   point-targeted (.th5 = hold for point .5,
#                          .we3 = weekly echomail for point .3, etc.)
#
#   * ZIP-compressed mail bundle (FTS-5003 / standard FidoNet). Magic
#     bytes 50 4B 03 04 = "PK..".  Inside is one or more .pkt files.
#     Mystic and binkd default to ZIP for echomail to save bandwidth;
#     if we don't extract these the entire feed is silently dropped.
#
# Strategy: detect by content (magic bytes), extract any .pkt files
# inside ZIPs, then import each. Fall back to the extension regex.
#
# Mail-file extension acceptance. Several FTN conventions coexist:
#   .pkt                — raw FTS-0001 packet (any tosser)
#   .[cdih]ut/.[cdih]rt — Mystic point flavors (crash/direct/
#                         immediate/hold, 'ut' = unzipped, 'rt' = ARC)
#   .t[a-z][0-9a-z]     — point-targeted bundles per Mystic naming
#   .(mo|tu|we|th|fr|sa|su)[0-9a-z]
#                       — FTS-5003 day-of-week bundled mail for nodes,
#                         where the prefix is the local day at the
#                         sending hub and the trailing char is a
#                         per-file sequence (0..9, then a..z = 36 per day).
# The OLD regex only matched `we<hex>` (Wednesday only) which made
# Friday-bundled mail (`.frk`, `.frl`, …) get silently filed as
# non-mail and dropped. Mystic hubs delivering to a node use these
# by default. Pattern below now covers all 7 days and 36-char
# sequence space.
#
# Module-level (not a per-connection closure) so it's directly
# testable and isn't recompiled/redefined on every single inbound
# session -- previously lived inline inside _handle_connection().
# Second real gap found live in the day-of-week branch below: it
# originally allowed ANY alphanumeric point character ([0-9a-z]), not
# just a digit -- meaning `.mod` (a music tracker module, extremely
# common inside ANSI-art/demoscene zips, exactly what ANN.FILES.ANSIART
# carries) collided with 'mo' (Monday) + 'd'. Confirmed live: a 46MB
# TIC-distributed zip with a .mod member inside got entirely misrouted
# as a "mail packet" (one bogus member "imported" as 0 messages) and
# the whole zip was silently never written to inbound_dir at all --
# same failure shape as the .tic collision above, just triggered by a
# different, much more common extension. Real Mystic point-bundle
# numbers are a small decimal count, never a letter, so the point
# character is restricted to a plain digit here.
#
# Third gap, found on inspection (not yet live-caught) while auditing
# for more instances of the same mistake: the 't[cdih][0-9a-f]' branch
# below claimed (in the comment on _looks_like_mail_bundle_ext, and in
# THIS comment above) to already be digit-only, but the regex itself
# still used the hex range [0-9a-f] -- so 't' + 'i'/'c'/'d'/'h' + any
# of a-f still matched. That let extremely common, unrelated
# extensions collide: '.tif' (TIFF images -- t+i+f) and '.tie'/'.tid'
# (t+i+e/d). Narrowed to plain digits, matching what the comments
# already claimed. '.crt' (TLS certificates) has the same collision
# risk via the '[cdih]rt' branch -- that one's a fixed 4-letter flavor
# set, not an overly-broad character class, so it's excluded by name
# below instead (same mechanism as '.tic'), keeping '.cut' (an
# already-tested, intentional match) working.
_PKT_EXT_RE = re.compile(
    r'^\.(?:'
    r'pkt'
    r'|[cdih]ut|[cdih]rt'
    r'|t[cdih][0-9]'
    r'|(?:mo|tu|we|th|fr|sa|su)[0-9]'
    r')$',
    re.IGNORECASE)

# Extensions that coincidentally match _PKT_EXT_RE above but must never
# be treated as mail, regardless -- either a universal FTN filename
# ('.tic') or a common real-world format ('.crt') that happens to fit
# one of the Mystic bundle patterns by coincidence.
_MAIL_BUNDLE_EXT_EXCLUSIONS = frozenset({'.tic', '.crt'})


def _is_fts_packet(payload):
    return len(payload) >= 60 and payload[18:20] in (b'\x02\x00', b'\x02\x01')


def _is_zip(payload):
    return len(payload) >= 4 and payload[:4] == b'PK\x03\x04'


def _looks_like_mail_bundle_ext(name):
    """Real gap found live: the 't[cdih][0-9a-f]' branch above
    (Mystic point-targeted bundle names like .tc1/.td2/.th3)
    ALSO coincidentally matches the universal FTN '.tic' TIC-
    manifest extension itself -- 't' + 'i' (in [cdih]) + 'c'
    (a valid hex digit in [0-9a-f]). Every inbound .tic manifest
    was silently misrouted into this mail-packet extractor
    instead of ever reaching the "write to inbound for TIC
    processing" branch below -- and since a TIC manifest's
    plain text never parses as a real FTS-0001 packet, it just
    vanished with zero trace: not imported as mail (nothing to
    parse), not filed as a TIC (never wrote to inbound_dir),
    not even logged as an error. Confirmed live: a downstream
    Synchronet/binkd node's own sender log showed the .tic
    genuinely transmitted successfully right after its paired
    binary, but no matching receive ever appeared anywhere in
    our own logs. '.tic' is excluded here regardless of the
    coincidental regex match -- it must always fall through to
    the TIC scanner, never the mail importer.
    """
    ext = os.path.splitext(name)[1].lower()
    if ext in _MAIL_BUNDLE_EXT_EXCLUSIONS:
        return False
    return bool(_PKT_EXT_RE.match(ext))


def _extract_packets(name, payload):
    """Yield (inner_name, inner_bytes) for every FTS-0001 packet found
    in this file — directly, or after unzipping a mail bundle."""
    if _is_zip(payload):
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    try:
                        inner = zf.read(info.filename)
                    except Exception as exc:
                        logger.warning('Failed to read %s from %s: %s',
                                       info.filename, name, exc)
                        continue
                    if _is_fts_packet(inner) or _looks_like_mail_bundle_ext(
                            info.filename):
                        yield (info.filename, inner)
                    else:
                        logger.info('Skipping non-packet %s inside %s',
                                    info.filename, name)
        except zipfile.BadZipFile as exc:
            logger.warning('Bad ZIP %s: %s', name, exc)
    elif _is_fts_packet(payload) or _looks_like_mail_bundle_ext(name):
        yield (name, payload)


def _new_app():
    """Build a fresh, disposable Flask app for one connection's DB access.

    Each caller is responsible for calling _dispose_app_engine() (while
    still inside that app's app_context()) once done with it -- see the
    call sites in _handle_connection(). Every inbound connection
    creates its own app (twice, in fact: once for the early AKA-
    announcement lookup, again for the main network/node match +
    import), and each db.init_app() call binds a brand-new SQLAlchemy
    engine (its own SQLite connection pool) to that app. Without an
    explicit dispose(), those engines/connections were never reclaimed
    -- relying on GC for this is unreliable (SQLAlchemy's own docs warn
    against it, and Flask-SQLAlchemy's internal ref cycles can delay
    collection well past the point the local `app` variable goes out of
    scope). With 5 active BinkP networks polling in roughly every 10
    minutes, this steadily accumulated open sqlite3 connections between
    service restarts; under WAL mode those hold back checkpointing and
    add just enough intermittent lock contention to occasionally blow
    past a peer's own session timeout, even though our side finishes and
    logs the poll as a success.

    (A single cached, shared app was tried instead and reverted -- test
    fixtures across this module rely on getting a fresh, disposable app
    per call so they can freely monkeypatch db.init_app/db.session per
    test; a shared app carries stale bindings across tests and breaks
    that isolation. Explicit dispose() per call gets the leak fix
    without changing that architecture.)
    """
    from flask import Flask
    from anetbbs.config import get_config
    app = Flask(__name__)
    app.config.from_object(
        get_config(os.environ.get('FLASK_ENV', 'production')))
    return app


def _dispose_app_engine():
    """Best-effort db.engine.dispose() for the currently-active app
    context. Disposal is cleanup, not correctness -- tests that mock
    db.init_app() to a no-op (so unit tests don't need a real DB) leave
    no engine registered at all, which makes db.engine raise rather than
    just being a no-op; swallow that here instead of at every call site."""
    from anetbbs.models import db
    try:
        db.engine.dispose()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Async frame I/O (mirror of the sync helpers in binkp.py)
# ---------------------------------------------------------------------------

def _log_transcript(transcript, line: str):
    """Append one timestamped line to a caller-owned transcript list --
    mirrors binkp.py's BinkPClient._log_transcript(), just as a free
    function since this module has no equivalent per-session object.
    `transcript` may be None (most call sites during early handshake,
    before _handle_connection has built its own list) -- a no-op then.

    Real gap found while investigating why a sysop debugging an inbound
    session (a peer connecting TO this BBS -- e.g. the exact "hub
    delivers mail, we stall" scenario this session's BinkP audit was
    chasing) had NO frame-by-frame transcript available at all: outbound
    polls (poller.py dialing OUT) have saved one on EchomailPollLog since
    v1.0b2.47, but this inbound listener never captured one, on either
    the fix-shipping side or before it. A sysop diagnosing an INBOUND
    stall -- arguably the more useful direction to have logs for, since
    that's the side that can't just be re-run on demand -- had nothing
    to look at.
    """
    if transcript is None:
        return
    ts = datetime.utcnow().strftime('%H:%M:%S.%f')[:-3]
    transcript.append(f'{ts} {line}')


async def _recv_frame(reader: asyncio.StreamReader, transcript=None):
    """Read one BinkP frame. Returns (is_command, data_bytes)."""
    hdr = await reader.readexactly(2)
    word = struct.unpack('>H', hdr)[0]
    is_command = bool(word & 0x8000)
    length = word & 0x7FFF
    payload = await reader.readexactly(length) if length else b''
    if transcript is not None:
        if is_command:
            cmd = payload[0] if payload else None
            name = CMD_NAMES.get(cmd, str(cmd)) if cmd is not None else '?'
            text = payload[1:].decode('latin-1', errors='replace') if payload else ''
            _log_transcript(transcript, f'<< CMD {name}' + (f': {text}' if text else ''))
        else:
            _log_transcript(transcript, f'<< DATA: {len(payload)} bytes')
    return is_command, payload


async def _send_cmd(writer, cmd, text='', transcript=None):
    name = CMD_NAMES.get(cmd, str(cmd))
    _log_transcript(transcript, f'>> CMD {name}' + (f': {text}' if text else ''))
    writer.write(_build_cmd(cmd, text))
    await writer.drain()


async def _send_data(writer, data, transcript=None):
    _log_transcript(transcript, f'>> DATA: {len(data)} bytes')
    writer.write(_build_data(data))
    await writer.drain()


def _inbound_poll_type(imported_total, sent_count):
    """EchomailPollLog.poll_type for an inbound-initiated session, given
    how many messages were actually imported/sent during it."""
    if imported_total and sent_count:
        return 'both'
    if sent_count:
        return 'send'
    return 'receive'


def _flush_transcript_checkpoint(app, poll_log_id, transcript, peer):
    """Best-effort mid-session transcript save, called from a couple of
    natural checkpoints in _handle_connection (after sending our backlog,
    after receiving the peer's files) -- NOT on every frame. This exists
    because anetbbs-binkp.service is a SEPARATE OS process from
    anetbbs-web.service (confirmed: deploy/anetbbs-binkp.service runs its
    own `python -m anetbbs.echomail.binkp_server`), so the shared SQLite
    database is the only channel the admin UI has to see an inbound
    session's progress while it's still running -- an in-memory list in
    this process is otherwise invisible to it. Deliberately isolated from
    the actual protocol flow: any failure here is logged and swallowed,
    never raised, and this is only ever called between awaits (never
    itself blocking a socket read/write), so it cannot introduce the kind
    of timing regression this codebase's BinkP work has hit before.
    """
    if poll_log_id is None or not transcript:
        return
    try:
        with app.app_context():
            from ..models import db as _db, EchomailPollLog
            from .poller import _format_transcript
            log = EchomailPollLog.query.get(poll_log_id)
            if log is not None:
                log.transcript = _format_transcript(transcript)
                _db.session.commit()
    except Exception:
        logger.debug('BinkP %s: mid-session transcript checkpoint failed '
                     '(poll_log_id=%s)', peer, poll_log_id, exc_info=True)


def _verify_binkp_password(stored_password, remote_pwd, challenge_bytes):
    """Verify a caller's M_PWD against our stored password.

    Supports both legacy plaintext and CRAM-MD5 (FTS-1027) responses --
    we (the answering side) offer a CRAM-MD5 challenge in the M_NUL
    preamble (see _handle_connection), but still accept a plain-text
    M_PWD from callers that don't speak CRAM-MD5, so this doesn't break
    older peers. Before this, the listener only ever compared remote_pwd
    against the stored password as a literal string -- a caller that
    correctly responded to our challenge (or, before this fix, that
    never got a challenge to respond to at all, since we never sent one)
    had no way to authenticate via CRAM-MD5 at all. Real-world case: a
    binkd peer polling in reported "CRAM-MD5 is not supported by
    remote" because this listener never advertised the OPT CRAM-MD5-...
    line an answering side is supposed to send."""
    stored = stored_password or ''
    remote = remote_pwd or ''
    if remote.upper().startswith('CRAM-MD5-'):
        digest = remote[len('CRAM-MD5-'):]
        expected = hmac.new(stored.encode('latin-1'), challenge_bytes,
                            hashlib.md5).hexdigest()
        # Real gap found in a security/performance audit: a plain ==
        # on hex digests short-circuits on the first mismatched
        # character, leaking (via response timing) how many leading
        # hex digits an attacker's guess got right -- hmac.compare_digest
        # runs in constant time regardless of where the strings first
        # differ.
        return hmac.compare_digest(digest.lower(), expected.lower())
    # binkp.py's client (our own outbound-dial side) sends the literal
    # placeholder "-" for M_PWD when it has no password configured --
    # a real-world BinkP convention (M_PWD historically couldn't carry
    # an empty value). Two unsecured links (an ANetBBS install with no
    # password dialing another with no password configured, e.g. a
    # sysop testing a leaf link before adding real credentials) never
    # actually authenticated, because a blank `stored` password only
    # ever matched a literal empty M_PWD, never binkp.py's own "-"
    # sentinel for the exact same "no password" state.
    if not stored and remote in ('', '-'):
        return True
    # Same constant-time reasoning as the CRAM-MD5 branch above -- this
    # is the legacy plaintext-M_PWD path, still a real credential
    # comparison.
    return hmac.compare_digest(stored.encode('latin-1', errors='replace'),
                               remote.encode('latin-1', errors='replace'))


# ---------------------------------------------------------------------------
# Per-connection session
# ---------------------------------------------------------------------------

async def _handle_connection(reader, writer, our_address: str, system_name: str):
    peer = writer.get_extra_info('peername')
    logger.info('BinkP inbound from %s', peer)
    session_started_at = datetime.utcnow()
    # Frame-by-frame transcript for this session, saved to
    # EchomailPollLog.transcript at the end (see "9." below) -- mirrors
    # binkp.py's BinkPClient._transcript for outbound polls, which has
    # existed since v1.0b2.47. Real gap found while investigating this
    # session's own BinkP audit: outbound polls (us dialing OUT) always
    # got a saved transcript; inbound sessions (a peer dialing IN --
    # e.g. the exact "hub delivers mail, we stall" scenario this whole
    # audit was chasing) never did, on either the fix-shipping side or
    # before it. A sysop diagnosing an inbound stall had nothing to look
    # at, even though that's arguably the more valuable direction to
    # have logs for (it can't be re-run on demand the way a poll can).
    transcript = []

    # 1. Send our M_NUL info + M_ADR.  Match Synchronet binkit's
    # preamble (SYS / ZYZ / LOC / NDL / TIME / VER) so peers that key
    # routing decisions off these fields recognise us as a real BBS.
    sysop_name = os.environ.get('SYSOP_NAME', 'sysop')
    location = os.environ.get('BBS_LOCATION', 'Earth')
    from .. import __version__ as _anetbbs_version
    full_ver = f'ANetBBS/{_anetbbs_version} binkp/1.1'
    await _send_cmd(writer, CMD_NUL, f'SYS {system_name}', transcript=transcript)
    await _send_cmd(writer, CMD_NUL, f'ZYZ {sysop_name}', transcript=transcript)
    await _send_cmd(writer, CMD_NUL, f'LOC {location}', transcript=transcript)
    await _send_cmd(writer, CMD_NUL, 'NDL 115200,TCP,BINKP', transcript=transcript)
    await _send_cmd(writer, CMD_NUL, f'TIME {datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")}', transcript=transcript)
    await _send_cmd(writer, CMD_NUL, f'VER {full_ver}', transcript=transcript)

    # Advertise CRAM-MD5 support as the answering side (FTS-1027) -- a
    # random challenge callers can hash their password against instead
    # of sending it in plain text. Without this line, a caller that
    # expects/requires CRAM-MD5 (e.g. real-world binkd peers) sees no
    # challenge offered and reports "CRAM-MD5 is not supported by
    # remote" -- confirmed live against a real peer (Nicholas Boel /
    # pharcyde.org) polling in. See _verify_binkp_password() below for
    # the matching verification side; plain-text M_PWD is still
    # accepted for callers that don't speak CRAM-MD5.
    cram_challenge_bytes = secrets.token_bytes(32)
    await _send_cmd(writer, CMD_NUL, f'OPT CRAM-MD5-{cram_challenge_bytes.hex()}', transcript=transcript)

    akas = []
    try:
        from anetbbs.models import db, EchomailNetwork
        _app = _new_app()
        db.init_app(_app)
        with _app.app_context():
            try:
                rows = (EchomailNetwork.query
                        .filter_by(network_type='binkp', is_active=True)
                        .all())
                import re as _re
                for r in rows:
                    a = (r.our_address or '').strip()
                    if not a:
                        continue
                    # Advertise ONE form per address -- qualified
                    # (`addr@domain`) when a domain is derivable, bare
                    # otherwise. Previously sent BOTH forms for every
                    # address as a "cover whichever form the peer keys on"
                    # compat measure, but real binkd's ADR() handler calls
                    # bsy_add() (its busy-lock acquire) once per
                    # space-separated token with no de-duplication -- for a
                    # secure (password-protected) link the first token
                    # acquires the lock and the second, same address in the
                    # other form, immediately fails to acquire it and binkd
                    # drops the session with "Secure AKA busy", before
                    # password verification ever runs. Confirmed live in
                    # both directions: ANetBBS calling out to a real binkd
                    # hub, AND a real binkd hub calling in to an ANetBBS
                    # install (this code path). A spec-compliant peer
                    # parses the domain-qualified form fine on its own, so
                    # one form is sufficient. Domain is sanitised per
                    # FSP-1028 (a-z 0-9 _ - ~, ≤8 chars).
                    #
                    # Prefer EchomailNetwork.ftn_domain (an explicit override
                    # a sysop can set from the network's admin form) over
                    # deriving one from `name` -- poller.py's outbound side
                    # (BinkPClient's own `domain` kwarg) already does this;
                    # this inbound path never did, so a long display name
                    # (e.g. "ANotherNetwork") always truncated to an awkward
                    # "anothern" here regardless of any ftn_domain the sysop
                    # had already configured.
                    raw = ((getattr(r, 'ftn_domain', None) or r.name or '')
                           .strip().lower())
                    domain = _re.sub(r'[^a-z0-9_~-]+', '', raw)[:8]
                    one_form = f'{a}@{domain}' if domain else a
                    if one_form not in akas:
                        akas.append(one_form)
            finally:
                # Dispose AFTER reading every row's attributes, not
                # before -- real bug found live: disposing the engine
                # first (between the query and this loop) risked a
                # detached-instance access on `r.our_address`/`r.name`/
                # `r.ftn_domain` for any attribute SQLAlchemy hadn't
                # already fully materialized, which the broad except
                # below would silently swallow and fall back to a
                # single bare address -- exactly the "inbound session
                # only shows one AKA instead of all four" oddity a real
                # sysop noticed live (intermittent, not every session).
                _dispose_app_engine()
    except Exception as exc:
        logger.warning('BinkP listener: AKA lookup failed (%s) — using default', exc)

    # Fallback default address, only if its bare form isn't ALREADY
    # covered by an entry from the DB loop above (qualified or bare) --
    # a plain `not in akas` exact-string check missed this, since the
    # DB loop may have added the qualified form ("1:114/30@fidonet")
    # while this bare "1:114/30" is a different string, silently
    # re-introducing the exact same duplicate-token bug this whole fix
    # is for.
    if our_address and not any(
            existing == our_address or existing.split('@', 1)[0] == our_address
            for existing in akas):
        akas.append(our_address)

    # Sort AKAs so the most-FTN-looking ones come first. BinkP/1.1 has
    # ONE M_ADR per side and the argument is a space-separated list of
    # AKAs — but some peers (Mystic, older binkd builds) match only
    # against the FIRST AKA when deciding which outbound queue to
    # flush.  4D/point addresses (`zone:net/node.point`) are the most
    # specific FTN form, so put those first; then bare 3D; then any
    # non-FTN custom addresses last.
    def _aka_rank(a):
        # Lower rank sorts earlier.  Heuristics:
        #   0 = qualified 4D/point address (e.g. "1337:3/207.5@tqwnet")
        #   1 = bare 4D/point
        #   2 = qualified 3D
        #   3 = bare 3D
        #   4 = unparseable / unknown
        if not a or ':' not in a or '/' not in a:
            return (4, a)
        bare, _, _ = a.partition('@')
        has_domain = '@' in a
        zone_str = bare.split(':', 1)[0]
        try:
            int(zone_str)
        except ValueError:
            return (4, a)
        has_point = '.' in bare.split('/', 1)[1] if '/' in bare else False
        if has_point and has_domain: return (0, a)
        if has_point:               return (1, a)
        if has_domain:              return (2, a)
        return (3, a)
    akas.sort(key=_aka_rank)
    aka_line = ' '.join(akas) if akas else our_address
    logger.info('BinkP %s: announcing AKAs %s', peer, aka_line)
    await _send_cmd(writer, CMD_ADR, aka_line, transcript=transcript)

    # 2. Read client commands until we get their address + password
    remote_addr = None
    remote_akas = []
    remote_pwd = None
    while remote_addr is None or remote_pwd is None:
        try:
            is_cmd, payload = await asyncio.wait_for(_recv_frame(reader, transcript=transcript), timeout=30)
        except asyncio.TimeoutError:
            logger.warning('BinkP %s: handshake timed out', peer)
            return
        except (asyncio.IncompleteReadError, ConnectionResetError):
            return
        if not is_cmd or not payload:
            continue
        cmd, body = payload[0], payload[1:].decode('latin-1', errors='replace')
        if cmd == CMD_ADR:
            # M_ADR carries one or more whitespace-separated AKAs and may
            # be sent more than once. Many implementations (Mystic, BinkD,
            # Argus) suffix the address with @domain (e.g. "1337:3/207@tqwnet")
            # which has to be stripped for a 5D-style match.
            remote_akas = list(body.strip().split())
            if remote_akas and not remote_addr:
                remote_addr = remote_akas[0]
        elif cmd == CMD_PWD:
            remote_pwd = body.strip()
        elif cmd == CMD_NUL:
            logger.debug('BinkP %s NUL: %s', peer, body)

    # 3. Look up the caller — may be an upstream hub (EchomailNetwork) or a
    #    downstream node that registered with us as hub (BinkPNode).
    from anetbbs.models import db, EchomailNetwork, BinkPNode
    from .routing import self_hub_binkp_network

    app = _new_app()
    db.init_app(app)

    # Build the set of address candidates to check. Each AKA the peer
    # sent — and its bare 5D form without the @domain suffix — is a valid
    # match against hub_address (upstream) or ftn_address (downstream).
    candidates = []
    for a in (remote_akas or [remote_addr]) if remote_addr else []:
        if not a:
            continue
        if a not in candidates:
            candidates.append(a)
        bare = a.split('@', 1)[0]
        if bare and bare not in candidates:
            candidates.append(bare)

    net_id = None
    net_name = None
    downstream_node_id = None   # set when caller is a downstream BinkPNode
    # Per-network packet password + netmail flavor defaults, captured as
    # plain scalars (not the ORM object) when the network is matched below
    # -- see the capture site's own comment for why.
    net_packet_password = ''
    net_default_crash = False
    net_default_hold = False
    net_default_direct = False
    # HatchQueue.peer_address for this network's hub, captured as a plain
    # scalar for the same detached-instance reason as the fields above --
    # needed later (net_id branch, file-echo hatch-out) to look up any
    # pending TIC/file-echo items queued FOR our upstream hub.
    net_hub_address = ''
    # id of the EchomailPollLog row created at session start (upstream
    # network sessions only -- see its creation site for why). None means
    # either no such row was created (downstream-node session) or the
    # creation attempt itself failed; both are handled as "nothing to
    # update" at completion, matching pre-existing behavior.
    poll_log_id = None
    # Outbound FROM-address for THIS session's .pkt -- defaults to the
    # single process-wide address (BINKP_OUR_ADDRESS), same as before
    # multi-hub-identity support existed. Resolved to the matched
    # network's/node's own hub identity below when possible -- a peer
    # belonging to a non-default hub identity must get its outbound
    # mail stamped with THAT identity's AKA, not always the default's.
    # Fail-open by design (per the multi-hub-identity plan): identity
    # resolution can only IMPROVE this value from the fallback, never
    # block or reject an otherwise-valid, already-password-verified
    # connection.
    matched_our_address = our_address

    with app.app_context():
        # 3a. Check upstream networks first.
        network = (EchomailNetwork.query
                   .filter(EchomailNetwork.hub_address.in_(candidates))
                   .first()) if candidates else None
        if network and network.network_type == 'binkp':
            if not _verify_binkp_password(network.binkp_password, remote_pwd,
                                          cram_challenge_bytes):
                logger.warning('BinkP %s: bad password for upstream %s', peer, remote_addr)
                await _send_cmd(writer, CMD_ERR, 'bad password', transcript=transcript)
                writer.close()
                _dispose_app_engine()
                return
            net_id = network.id
            net_name = network.name
            # Capture these as plain scalars NOW, not the ORM object --
            # `network` was loaded inside this app_context, which closes
            # before the outbound .pkt gets built several hundred lines
            # below in a LATER, separate app_context (line ~524+). Holding
            # the object across that boundary and reading attributes off
            # it there is exactly the detached-instance hazard already
            # found and fixed once in this file (v1.0b2.152's AKA
            # dispose-ordering bug) -- same mistake, different fields.
            net_packet_password = getattr(network, 'packet_password', None) or ''
            net_default_crash = bool(getattr(network, 'default_crash', False))
            net_default_hold = bool(getattr(network, 'default_hold', False))
            net_default_direct = bool(getattr(network, 'default_direct', False))
            net_hub_address = network.hub_address or ''
            # Create the EchomailPollLog row NOW, at the start of the
            # session, not only after it completes (the prior behavior --
            # see step "9." far below). Real FR from a sysop: "no place to
            # see if a poll is going on... you can't tell if one is
            # ongoing currently or not" before deciding whether it's safe
            # to trigger a manual one. Capture only the new row's id as a
            # plain scalar -- same detached-instance reasoning as the
            # capture above, this survives into the LATER app_context
            # where the session actually gets updated/completed, an ORM
            # object reference would not.
            try:
                from ..models import EchomailPollLog as _EPL
                _new_log = _EPL(
                    network_id=net_id,
                    node_id=downstream_node_id,  # always None on this branch (3a, upstream hub calling us) -- set for symmetry with the 3b/downstream-node creation sites below
                    poll_type='both',  # not yet known; set for real at completion
                    started_at=session_started_at,
                    status='running',
                )
                db.session.add(_new_log)
                db.session.commit()
                poll_log_id = _new_log.id
            except Exception:
                logger.exception('BinkP %s: failed to create in-progress poll '
                                 'log row for network %s', peer, net_id)
                db.session.rollback()
            # The matched network row already carries its own AKA --
            # no identity lookup needed for this branch.
            if (network.our_address or '').strip():
                matched_our_address = network.our_address.strip()
        else:
            # 3b. Check downstream nodes registered with us as hub.
            node = (BinkPNode.query
                    .filter(BinkPNode.ftn_address.in_(candidates))
                    .filter_by(is_active=True)
                    .first()) if candidates else None
            if node:
                if not _verify_binkp_password(node.password, remote_pwd,
                                              cram_challenge_bytes):
                    logger.warning('BinkP %s: bad password for downstream node %s',
                                   peer, remote_addr)
                    await _send_cmd(writer, CMD_ERR, 'bad password', transcript=transcript)
                    writer.close()
                    _dispose_app_engine()
                    return
                downstream_node_id = node.id
                net_name = node.name
                # Update last-seen timestamp.
                node.last_seen_at = datetime.utcnow()
                db.session.commit()

                identity = node.hub_identity
                # Prefer the node's own explicit network_id when set -- the
                # only unambiguous source of truth (see BinkPNode.network_id).
                # A HubIdentity can own MULTIPLE binkp EchomailNetwork rows
                # (we're hub of one, leaf member of others under the same
                # identity), so falling back to hub_identity_id alone can't
                # tell which network a downstream node actually belongs to.
                identity_net = EchomailNetwork.query.get(node.network_id) \
                    if node.network_id else None
                if identity_net is None and identity is not None:
                    # Legacy row predating network_id (or never resolved at
                    # creation time) -- narrow hub_identity_id's candidates
                    # down to the one network under this identity where WE
                    # are the hub (our_address == hub_address). Plain
                    # hub_identity_id filtering + .first() used to pick
                    # whichever binkp network under this identity sorted
                    # first by id, completely unrelated to the node that
                    # actually connected, whenever an identity owned more
                    # than one binkp network -- a real bug found live
                    # (sysop is hub of one network and a leaf member of
                    # several others, all sharing one HubIdentity; inbound
                    # mail from a real downstream node landed silently under
                    # the wrong network's poll log/EchoArea).
                    identity_net = self_hub_binkp_network(identity.id)
                if identity is None:
                    logger.warning(
                        'BinkP %s: downstream node %s (id=%s) has no '
                        'resolvable hub identity -- outbound mail will be '
                        'stamped with the process-wide default address %s',
                        peer, remote_addr, node.id, our_address)
                elif identity_net:
                    # Real bug found live (multi-hub-identity install,
                    # sysop testing a second network): this used to
                    # only read identity_net.our_address for the
                    # OUTBOUND stamping address below and never
                    # captured identity_net.id anywhere -- net_id
                    # stayed None for every downstream-node session,
                    # every single time, regardless of how correctly
                    # everything else was configured. That None then
                    # got passed straight into the inbound packet
                    # importer, which hit EchoArea/EchomailMessage's
                    # NOT-NULL network_id constraint on flush -- an
                    # IntegrityError silently swallowed by the
                    # session's generic exception handler further
                    # down, with zero trace anywhere a sysop could
                    # see (BadAreaLog is a poller.py-only mechanism,
                    # never touched by this listener). BinkP itself
                    # looked completely successful (GOT sent back for
                    # the received .pkt file), but the message inside
                    # it never made it into any EchoArea.
                    net_id = identity_net.id
                    net_name = identity.name
                    if (identity_net.our_address or '').strip():
                        matched_our_address = identity_net.our_address.strip()
                elif identity.binkp_zone and identity.binkp_net:
                    matched_our_address = (
                        f'{identity.binkp_zone}:{identity.binkp_net}/'
                        f'{identity.binkp_hub_node or 1}')
                    logger.warning(
                        'BinkP %s: downstream node %s belongs to hub '
                        'identity %r, which has no resolvable BinkP '
                        'EchomailNetwork row (none set, or ambiguous which '
                        'of several under this identity) -- inbound mail '
                        'cannot be imported (no network to attach it to) '
                        'until node.network_id is set explicitly',
                        peer, remote_addr, identity.name)
                else:
                    logger.warning(
                        'BinkP %s: downstream node %s belongs to hub '
                        'identity %r, which has no BinkP network or '
                        'zone:net configured -- outbound mail will be '
                        'stamped with the process-wide default address '
                        '%s instead of that identity\'s own AKA',
                        peer, remote_addr, identity.name, our_address)
            else:
                # Unrecognized peer -- not a configured upstream hub or
                # downstream node. Standard FTN nodelist policy requires an
                # unflagged (non-Hold, non-Pvt) listed node to accept
                # CRASH-delivered netmail from ANY address, not just
                # pre-registered peers -- real report from a net's nodelist
                # coordinator (peer address 2:280/464, 2026-07-31):
                # rejecting every unlisted caller here made this node
                # non-compliant ("Try result: unknown address ...").
                # Accept the session as anonymous crashmail: net_id and
                # downstream_node_id both stay None, so the import step
                # below only stores NETMAIL (point-to-point, addressed to
                # a real local user/robot) and silently drops any echomail
                # content -- echo distribution still requires real network
                # membership, this is only about netmail deliverability.
                # No password check: an unlisted/uncoordinated peer has no
                # shared secret with us to verify in the first place, same
                # as real binkd/ifcico's handling of unlisted callers.
                logger.info('BinkP %s: unrecognized address %s (tried: %s) -- '
                           'accepting as anonymous crashmail session '
                           '(netmail only, no echomail)',
                           peer, remote_addr, ', '.join(candidates))
                net_name = f'unknown ({remote_addr})' if remote_addr else 'unknown'

    logger.info('BinkP %s: authenticated as %s (%s)', peer, remote_addr, net_name)

    # 4. M_OK
    await _send_cmd(writer, CMD_OK, 'secure', transcript=transcript)

    # 5. Send queued outbound messages FIRST, then our own M_EOB --
    #    moved ahead of receiving (this used to be step "6", after a
    #    full receive-to-completion). Real live report (a peer's own
    #    session log): after delivering its files, a real binkd peer
    #    sat in total silence waiting to hear ANYTHING back from us --
    #    apparently expecting our own EOB (or outbound offer) before it
    #    would send its own -- and our old code never sent a word until
    #    it had first fully drained the peer's inbound stream (waiting
    #    on the PEER's M_EOB, up to 120s). Neither side spoke first, so
    #    the peer's own (shorter) timeout fired, it closed the
    #    connection, and its bookkeeping marked the whole transfer
    #    failed even though every file had already been individually
    #    M_GOT-acknowledged -- so it kept resending the same backlog on
    #    every subsequent poll. Now we announce completion (our own
    #    EOB) as soon as we know what, if anything, we have to send --
    #    before waiting on anything further from the peer.
    #
    #    Shared receive state/results -- _send_pkt_file's own
    #    wait-for-GOT loop and the _receive_files() call below both
    #    feed into these, since a peer may interleave its own file
    #    offers while we're still sending ours.
    #    For upstream hub sessions: flush outbound EchomailMessages for that network.
    #    For downstream node sessions: flush the BinkPHoldQueue for that node.
    # 5b. Wrap the whole post-auth session in a try/except so a crash
    # partway through (a real exception in _send_pkt_file/_receive_files/
    # _finish_session/import, or a network error) still leaves a record --
    # previously, _import_and_log() below (step "9.") was the ONLY place
    # that ever wrote an EchomailPollLog row for an inbound session, and
    # it's the very LAST statement in this function: any exception before
    # it meant the session vanished with no trace in the sysop-facing Poll
    # Log at all, only a bare stack trace in the application log -- the
    # exact gap that left a real crashed session (peer report: our own
    # listener closed the connection ~60s in, mid-transfer, with none of
    # the peer's files ever GOT-acknowledged) with no ANetBBS-side
    # transcript to diagnose it from. Mirrors poller.py's _do_poll(),
    # which has always saved the transcript on failure via its own
    # try/except/finally.
    try:
        recv_state = {'name': None, 'size': 0, 'buf': bytearray()}
        inbound_files = []
        sent_count = 0
        with app.app_context():
            if downstream_node_id is not None:
                from .tosser import (
                    get_pending_for_node, mark_sent_for_node,
                    get_pending_netmail_for_node, mark_netmail_sent,
                )
                outbound_echo = get_pending_for_node(downstream_node_id)
                # Queued outbound NetmailMessage rows were NEVER flushed
                # via this inbound-listener path before -- see
                # get_pending_netmail_for_node()'s docstring for the full
                # story (confirmed live: a sysop's own netmail reply, and
                # a week-plus backlog of AreaFix auto-replies, both stuck
                # in status='queued' because this node only ever calls IN).
                outbound_nm = get_pending_netmail_for_node(node)

                from .poller import _NetmailAdapter
                outbound = list(outbound_echo) + [_NetmailAdapter(nm) for nm in outbound_nm]
                if outbound:
                    pkt_bytes = _build_ftn_packet(outbound, matched_our_address, remote_addr)
                    fname = f'{int(datetime.utcnow().timestamp()):08x}.pkt'
                    accepted = await _send_pkt_file(reader, writer, fname, pkt_bytes,
                                                    peer, recv_state, inbound_files,
                                                    transcript=transcript)
                    if accepted:
                        if outbound_echo:
                            mark_sent_for_node(downstream_node_id,
                                               [m.id for m in outbound_echo])
                        mark_netmail_sent(outbound_nm)
                        sent_count = len(outbound)
                    else:
                        logger.warning('Hold-queue .pkt not accepted by %s — '
                                       'leaving pending for retry', remote_addr)

                # Loose outbound-spool files (see binkp.py's
                # resolve_outbound_dir docstring) keyed the same way as
                # the HatchQueue lookup just below -- node.ftn_address,
                # not remote_addr, for the same reason.
                node_outbound_dir = resolve_outbound_dir(
                    app.config.get('DATA_DIR') or 'data', node.ftn_address or '')
                if os.path.isdir(node_outbound_dir):
                    dir_sent, dir_failures = await _send_outbound_dir_items(
                        reader, writer, node_outbound_dir, peer,
                        recv_state, inbound_files, transcript=transcript)
                    sent_count += dir_sent
                    if dir_failures:
                        logger.warning('Outbound dir: %d file(s) not delivered to %s',
                                       len(dir_failures), remote_addr)

                from ..models import HatchQueue
                # Keyed on node.ftn_address (the resolved DB row's own
                # address, matching what filefix.py's hub side stored
                # as FileEchoSubscription/HatchQueue.peer_address when
                # the node subscribed) -- NOT remote_addr, which is
                # only the first AKA the peer happened to claim during
                # auth and isn't guaranteed byte-identical to it.
                pending_hatch = (HatchQueue.query
                                 .filter(HatchQueue.peer_address == (node.ftn_address or ''),
                                         HatchQueue.status == 'pending')
                                 .order_by(HatchQueue.queued_at)
                                 .all())
                if pending_hatch:
                    hatched_ids, hatch_failures = await _send_hatch_items(
                        reader, writer, pending_hatch, matched_our_address,
                        peer, recv_state, inbound_files, transcript=transcript)
                    if hatched_ids or hatch_failures:
                        now = datetime.utcnow()
                        for hid in hatched_ids:
                            row = HatchQueue.query.get(hid)
                            if row is not None:
                                row.status = 'sent'
                                row.sent_at = now
                        from .tic import record_hatch_attempt_failure
                        for failed_item, message in hatch_failures:
                            record_hatch_attempt_failure(failed_item, message)
                        db.session.commit()
            elif net_id is not None:
                from ..models import EchomailMessage
                from .tosser import get_pending_netmail_for_network, mark_netmail_sent
                from .poller import _NetmailAdapter
                outbound_echo = EchomailMessage.query.filter_by(
                    network_id=net_id,
                    direction='outbound',
                    sent_at=None,
                ).all()
                # Same gap as the downstream-node branch above (see
                # get_pending_netmail_for_node()'s docstring for the full
                # story): queued outbound netmail was never flushed via
                # this inbound-listener path either, only via poller.py's
                # outbound dial. No per-node address to disambiguate here
                # -- this branch matches a whole EchomailNetwork (one
                # designated counterpart), same as poller.py's own
                # network_id-only netmail gather.
                outbound_nm = get_pending_netmail_for_network(net_id)
                outbound = list(outbound_echo) + [_NetmailAdapter(nm) for nm in outbound_nm]
                if outbound:
                    pkt_bytes = _build_ftn_packet(
                        outbound, matched_our_address, remote_addr,
                        packet_password=net_packet_password,
                        default_crash=net_default_crash,
                        default_hold=net_default_hold,
                        default_direct=net_default_direct)
                    fname = f'{int(datetime.utcnow().timestamp()):08x}.pkt'
                    accepted = await _send_pkt_file(reader, writer, fname, pkt_bytes,
                                                    peer, recv_state, inbound_files,
                                                    transcript=transcript)
                    if accepted:
                        now = datetime.utcnow()
                        for m in outbound_echo:
                            m.sent_at = now
                        mark_netmail_sent(outbound_nm)
                        db.session.commit()
                        sent_count = len(outbound)
                    else:
                        logger.warning('Outbound .pkt was not accepted by %s — '
                                       'leaving in queue for retry', remote_addr)

                # Loose outbound-spool files (see binkp.py's
                # resolve_outbound_dir docstring), keyed the same way as
                # the HatchQueue lookup just below -- net_hub_address,
                # our upstream hub calling us instead of us dialing out.
                net_outbound_dir = resolve_outbound_dir(
                    app.config.get('DATA_DIR') or 'data', net_hub_address or '')
                if net_hub_address and os.path.isdir(net_outbound_dir):
                    dir_sent, dir_failures = await _send_outbound_dir_items(
                        reader, writer, net_outbound_dir, peer,
                        recv_state, inbound_files, transcript=transcript)
                    sent_count += dir_sent
                    if dir_failures:
                        logger.warning('Outbound dir: %d file(s) not delivered to %s',
                                       len(dir_failures), remote_addr)

                from ..models import HatchQueue
                # Same gap as the downstream_node_id branch above (see
                # _send_hatch_items()'s own docstring for the full story),
                # just for the OTHER direction: our upstream hub dialing
                # IN to us, rather than a downstream leaf dialing in.
                # FileFix subscriptions targeting our hub queue HatchQueue
                # rows keyed by peer_address == network.hub_address (same
                # value poller.py's _run_client() already sends correctly
                # when WE dial OUT to the hub) -- but a two-way BinkP
                # session where the hub calls US never flushed them, so a
                # file echo we're subscribed to send upstream just sat in
                # 'pending' forever unless we happened to poll out first.
                pending_hatch = (HatchQueue.query
                                 .filter(HatchQueue.peer_address == net_hub_address,
                                         HatchQueue.status == 'pending')
                                 .order_by(HatchQueue.queued_at)
                                 .all()) if net_hub_address else []
                if pending_hatch:
                    hatched_ids, hatch_failures = await _send_hatch_items(
                        reader, writer, pending_hatch, matched_our_address,
                        peer, recv_state, inbound_files, transcript=transcript)
                    if hatched_ids or hatch_failures:
                        now = datetime.utcnow()
                        for hid in hatched_ids:
                            row = HatchQueue.query.get(hid)
                            if row is not None:
                                row.status = 'sent'
                                row.sent_at = now
                        from .tic import record_hatch_attempt_failure
                        for failed_item, message in hatch_failures:
                            record_hatch_attempt_failure(failed_item, message)
                        db.session.commit()

        await _send_cmd(writer, CMD_EOB, transcript=transcript)
        _flush_transcript_checkpoint(app, poll_log_id, transcript, peer)

        # 6. NOW finish receiving whatever the peer still has queued for us
        #    (already-interleaved offers landed in inbound_files/recv_state
        #    above; this drains anything left, until the peer's own M_EOB).
        inbound_files = await _receive_files(reader, writer, peer, recv_state,
                                             inbound_files, transcript=transcript)
        _flush_transcript_checkpoint(app, poll_log_id, transcript, peer)

        # 7. End of batch -- complete the BinkP protocol handshake NOW,
        #    before importing what we just received below. A real peer
        #    report (a 100+-link hub) traced a mail-loop straight back to
        #    the OLD ordering here: a large historical catch-up (tens of
        #    thousands of messages across ~100 bundles) took long enough to
        #    parse+import+toss that the peer's own connection gave up
        #    waiting mid-import and the session ended without ever reaching
        #    this handshake -- every file had already been individually
        #    M_GOT-acknowledged, but because the session itself never closed
        #    cleanly, the peer's own bookkeeping never marked those bundles
        #    delivered and resent all of them on the next connection,
        #    compounding with every new bundle created in between. Finishing
        #    the handshake here means the peer's session completes in
        #    roughly the time it takes to exchange files, regardless of how
        #    large the resulting import turns out to be.
        await _finish_session(reader, writer, peer, inbound_files, sent_count, transcript=transcript)

        # 8. NOW import inbound packets + scan for .tic file echoes. The
        #    socket is already closed at this point (see above) -- however
        #    long this takes has no further bearing on the peer's own
        #    session/protocol timing.
        #
        # File classification (FTS-0001 packet vs. ZIP mail bundle vs.
        # everything else) is handled by the module-level _extract_packets()
        # / _is_fts_packet() / _is_zip() / _looks_like_mail_bundle_ext()
        # helpers above -- see their docstrings for the wrapper formats
        # this accepts and the .tic-collision bug their extraction fixed.
        def _debug_manifest(fname, buf):
            """Same purpose as binkp.py's _debug_manifest, duplicated here
            because this is a genuinely separate inbound path: the answering
            side (this file, when a hub connects TO us) never routed through
            binkp.py's _import_completed()/_debug_dump_packet() at all, so
            every diagnostic added there was blind to traffic arriving this
            way. Confirmed on the live server: real messages kept landing in
            the DB with zero corresponding manifest entries, which is what
            led to finding this second, uninstrumented code path. Opt-in via
            the same BINKP_DEBUG_DUMP_DIR env var, zero cost unless set."""
            debug_dir = os.environ.get('BINKP_DEBUG_DUMP_DIR')
            if not debug_dir:
                return
            try:
                os.makedirs(debug_dir, exist_ok=True)
                kind = 'fts' if _is_fts_packet(buf) else (
                    'zip' if _is_zip(buf) else 'other')
                line = f'{datetime.utcnow().isoformat()} SRV:{fname} {len(buf)} {kind}\n'
                with open(os.path.join(debug_dir, '_manifest.log'), 'a') as fh:
                    fh.write(line)
            except OSError:
                pass

        def _debug_dump_packet(name, data):
            debug_dir = os.environ.get('BINKP_DEBUG_DUMP_DIR')
            if not debug_dir:
                return
            try:
                os.makedirs(debug_dir, exist_ok=True)
                dump_name = f'{datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")}_srv_{name}'
                with open(os.path.join(debug_dir, dump_name), 'wb') as fh:
                    fh.write(data)
            except OSError as exc:
                logger.warning('BinkP server: failed to dump debug packet %s: %s',
                               name, exc)

        # Steps 8-9 run in a background thread via asyncio.to_thread(), NOT
        # inline on this coroutine. Confirmed real gap found while
        # investigating a second peer sysop's report of multi-minute stalls
        # on BRAND NEW connections (including tiny, sub-3KB pushes that
        # never got so much as a M_GOT before the peer gave up and
        # disconnected): this server runs ONE asyncio event loop
        # (asyncio.start_server in _serve() below) shared by EVERY inbound
        # connection. Moving import to run after THIS session's own socket
        # close (the fix directly above, at "7.") only protects THIS
        # session's own protocol timing -- it does nothing to stop the
        # import's synchronous DB writes / ZIP extraction / regex parsing
        # (potentially thousands of messages during a large catch-up) from
        # monopolizing the single-threaded event loop and starving EVERY
        # OTHER concurrent connection, including ones that haven't even
        # finished their handshake yet. asyncio.to_thread() runs this
        # synchronous work on a worker thread instead, so the event loop
        # stays free to service other sessions the moment THIS session's
        # own _finish_session() above returns, regardless of how long the
        # import takes. Flask's app.app_context() is designed to be entered
        # fresh from any thread (already used this way at steps 3o/6 above,
        # just always from the event-loop thread until now), so no new
        # threading primitive is needed here beyond to_thread() itself.
        def _import_and_log():
            imported_total = 0
            # Real gap found live: a session that received a large TIC
            # binary + manifest (nothing else) showed "Received: 0" in
            # the admin Poll Log even though real data clearly came in
            # (confirmed via the raw transcript) -- messages_received
            # only ever counted imported echomail/netmail packets, never
            # TIC files/binaries written straight to inbound_dir. Track
            # those separately and fold them into the same total below,
            # same "don't discard real activity" fix as the comment on
            # the poll-log-writing block further down.
            files_received = 0
            if inbound_files:
                with app.app_context():
                    inbound_dir = None
                    for fname, payload in inbound_files:
                        _debug_manifest(fname, payload)
                        extracted = list(_extract_packets(fname, payload))
                        if extracted:
                            if net_id is None and downstream_node_id is not None:
                                # A known downstream node, but its hub
                                # identity has no resolvable EchomailNetwork
                                # row (e.g. only zone:net configured, no
                                # actual BinkP network row yet) --
                                # _import_pkt_payload() would otherwise hit
                                # EchoArea/EchomailMessage's NOT-NULL
                                # network_id constraint and lose the whole
                                # packet to a silently-swallowed exception
                                # with zero sysop-visible trace. Loud and
                                # skipped is strictly better than quiet and
                                # lost. NOT the same as the anonymous-
                                # crashmail case below (net_id AND
                                # downstream_node_id both None) -- that one
                                # is expected and handled, not an error.
                                logger.error(
                                    'BinkP %s: received %d packet(s) in %s '
                                    'but no EchomailNetwork is resolved for '
                                    'this session -- cannot import, mail '
                                    'DROPPED. Configure a BinkP network for '
                                    'this hub identity.',
                                    peer, len(extracted), fname)
                                continue
                            # net_id is None here also covers the anonymous/
                            # unrecognized-peer crashmail case (see the
                            # caller-lookup site's own comment) --
                            # _import_pkt_payload() accepts network_id=None
                            # and imports netmail only, skipping any
                            # echomail content in the packet.
                            # Only worth capturing the real socket IP for
                            # the genuinely anonymous case (net_id AND
                            # downstream_node_id both None) -- a known
                            # upstream hub/downstream node already has a
                            # real hub_address/ftn_address to route a
                            # reply through, so there's nothing for a
                            # direct-dial reply to add there.
                            _origin_ip = (peer[0] if peer and net_id is None
                                         and downstream_node_id is None else None)
                            for inner_name, inner_payload in extracted:
                                _debug_dump_packet(inner_name, inner_payload)
                                imported_total += _import_pkt_payload(
                                    inner_payload, net_id, inner_name,
                                    peer_address=remote_addr,
                                    origin_ip=_origin_ip)
                        else:
                            # Non-packet files (TIC manifests, hatched binaries, etc.)
                            # get written to inbound for later processing.
                            # Default landed in /tmp/ which is tmpfs on most distros
                            # — files vanish on service restart. Default to data/
                            # so the sysop can recover from a tossing miss.
                            inbound_dir = inbound_dir or (
                                os.environ.get('BINKP_INBOUND_DIR')
                                or os.path.join(
                                    app.config.get('DATA_DIR') or 'data',
                                    'binkp', 'inbound'))
                            try:
                                safe_name = _sanitize_inbound_filename(fname)
                                os.makedirs(inbound_dir, exist_ok=True)
                                with open(os.path.join(inbound_dir, safe_name), 'wb') as f:
                                    f.write(payload)
                                files_received += 1
                                # Visible-by-default log so the sysop can spot
                                # mystery files that don't match the regex /
                                # magic bytes — easier diagnosis than digging
                                # at DEBUG level.
                                logger.info(
                                    'BinkP: stored unrecognised file %s '
                                    '(%d bytes) in %s — neither ZIP nor FTS-0001 '
                                    'packet, scanning for TIC manifest',
                                    safe_name, len(payload), inbound_dir)
                            except OSError as exc:
                                logger.warning('Failed to write inbound file %s: %s',
                                               fname, exc)
                    # After dropping any non-.pkt files into inbound, run the TIC scan
                    # so file-echo distributions get filed automatically.
                    if inbound_dir and os.path.isdir(inbound_dir):
                        try:
                            from .tic import scan_inbound
                            n = scan_inbound(inbound_dir)
                            if n:
                                logger.info('Processed %d TIC files from %s',
                                            n, inbound_dir)
                        except Exception:
                            logger.exception('TIC scan failed')

            # 9. Record this inbound-initiated session in the same Poll Log a
            # sysop already checks for outbound polls. Before this, ONLY
            # anetbbs/echomail/poller.py (which dials OUT) ever wrote an
            # EchomailPollLog row -- when a remote hub calls IN and
            # delivers mail (exactly how tqwnet/sp00knet actually work:
            # they push to us rather than waiting to be polled), the real
            # imported-message count computed above was discarded, and the
            # sysop's own subsequent outbound poll of that same network
            # legitimately found nothing left to pull (the hub already
            # pushed it here) and logged 0 -- real mail was arriving the
            # whole time, just never reflected in this log. Only written
            # for upstream-hub sessions (net_id set); a downstream node
            # polling US as ITS hub has no EchomailNetwork row to log
            # against (EchomailPollLog.network_id is NOT NULL).
            if net_id is not None:
                with app.app_context():
                    try:
                        from ..models import EchomailPollLog
                        from .poller import _format_transcript
                        final_transcript = _format_transcript(transcript) if transcript else None
                        # Update the "running" row created at session start
                        # (see its creation site) rather than inserting a
                        # second row -- falls back to inserting fresh only
                        # if that row somehow doesn't exist (its own
                        # creation attempt failed and already logged that).
                        log = (EchomailPollLog.query.get(poll_log_id)
                               if poll_log_id is not None else None)
                        if log is None:
                            log = EchomailPollLog(network_id=net_id, node_id=downstream_node_id,
                                                  started_at=session_started_at)
                            db.session.add(log)
                        total_received = imported_total + files_received
                        log.poll_type = _inbound_poll_type(total_received, sent_count)
                        log.completed_at = datetime.utcnow()
                        log.status = 'success'
                        log.messages_sent = sent_count
                        log.messages_received = total_received
                        # Real gap this closes: outbound polls have saved
                        # a frame-by-frame transcript since v1.0b2.47 (see
                        # _log_transcript above); inbound sessions -- a
                        # peer connecting TO us, the exact direction this
                        # session's whole BinkP audit was chasing -- never
                        # did, on either the fix-shipping side or before.
                        log.transcript = final_transcript
                        db.session.commit()
                    except Exception:
                        logger.exception('BinkP %s: failed to write poll log for '
                                         'inbound session (net_id=%s)', peer, net_id)

        await asyncio.to_thread(_import_and_log)
    except Exception as exc:
        if net_id is not None:
            try:
                with app.app_context():
                    from ..models import EchomailPollLog, db as _db
                    from .poller import _format_transcript
                    detail = str(exc)
                    # Same update-not-insert approach as the success path
                    # above -- see that site's comment.
                    log = (EchomailPollLog.query.get(poll_log_id)
                           if poll_log_id is not None else None)
                    if log is None:
                        log = EchomailPollLog(network_id=net_id, node_id=downstream_node_id,
                                              poll_type='both',
                                              started_at=session_started_at)
                        _db.session.add(log)
                    log.completed_at = datetime.utcnow()
                    log.status = 'error'
                    log.error_message = (f'{type(exc).__name__}: {detail}'
                                        if detail else type(exc).__name__)
                    log.messages_sent = sent_count
                    log.messages_received = 0
                    log.transcript = _format_transcript(transcript) if transcript else None
                    _db.session.commit()
            except Exception:
                logger.exception(
                    'BinkP %s: failed to write error poll log for inbound '
                    'session (net_id=%s)', peer, net_id)
        raise
    finally:
        # Dispose the engine bound to `app` (see _new_app()'s docstring) --
        # covers every exit path (success, the except above, or an
        # exception raised before either): without this, each connection
        # leaked its own SQLAlchemy engine/SQLite connection pool with
        # nothing ever reclaiming it.
        try:
            with app.app_context():
                _dispose_app_engine()
        except Exception:
            pass


async def _finish_session(reader, writer, peer, inbound_files, sent_count, transcript=None):
    """Complete binkp/1.1's two-round M_EOB handshake, briefly drain any
    trailing bytes, then close.

    Our OWN first M_EOB is sent earlier now (see _handle_connection),
    right after our own outbound-send phase, specifically so the peer
    hears from us as soon as possible instead of only after we've
    fully drained whatever it's sending us -- a real live report (a
    peer's own session log) showed the peer's binkd waiting in total
    silence for over 2 minutes after delivering its files with no
    signal back from us at all, then giving up, closing the connection,
    and marking the whole transfer failed (0 bytes) even though every
    file had already been individually M_GOT-acknowledged -- so it kept
    resending the same backlog on every subsequent poll.

    Per binkp/1.1 (binkp11.txt): once both sides have exchanged EOB,
    the session doesn't end -- it "restarts by resetting binkp to the
    state it had just after login" (a fresh rescan), and only counts as
    successfully finished once a round happens where NEITHER side sends
    nor receives any command between two consecutive EOB exchanges. Our
    first EOB (sent before receiving anything) gets voided the instant
    real file activity follows it, so it can never by itself satisfy
    that rule -- we still need a SECOND, POST-transfer EOB confirming
    "now that all that's happened, I still have nothing more", to
    complete our half of the empty round the peer is waiting for.

    This function used to only ever send that second EOB REACTIVELY, in
    reply to the peer's own second EOB arriving within a 10s wait --
    never proactively. A real peer (binkd/1.1a-113) confirmed live never
    sends an explicit M_EOB at all in these sessions; it just goes
    silent for its own ~15s grace period, then closes the connection
    outright with no EOB ever exchanged, and its own outbound queue
    never dequeues those files on the next poll despite every one being
    individually M_GOT-acknowledged here -- exactly the "no consecutive
    EOB-to-EOB empty round" case in the spec text above, because we
    never sent the second EOB it needed from OUR side to reach that
    state, only ever having sent the one before any activity happened.
    Sending our own post-transfer EOB immediately (rather than waiting
    to see if the peer sends one first) gives the peer that confirmation
    regardless of whether it ever reciprocates -- a lenient peer that
    just closes instead of replying is still handled cleanly below.

    Also does a brief drain before closing: an immediate close() while
    the peer still has trailing bytes in flight can register on their
    end as an abrupt disconnect rather than a clean session end.
    """
    try:
        await _send_cmd(writer, CMD_EOB, transcript=transcript)
        logger.info('BinkP %s: proactive post-transfer M_EOB sent OK', peer)
    except Exception as exc:
        # _send_cmd() logs the transcript line BEFORE attempting the
        # actual socket write -- if the peer already closed the
        # connection by the time we get here (real live report: this
        # hub's own outbound queue never shrank even after this EOB
        # started being sent, across multiple confirmed-clean-looking
        # sessions), the transcript can show ">> CMD EOB" while the
        # write itself silently failed, making a failed send
        # indistinguishable from a successful one without this log line.
        logger.warning(
            'BinkP %s: proactive post-transfer M_EOB send FAILED (peer '
            'likely already closed): %s', peer, exc)

    try:
        is_cmd, payload = await asyncio.wait_for(
            _recv_frame(reader, transcript=transcript), timeout=10)
        if is_cmd and payload and payload[0] == CMD_EOB:
            await _send_cmd(writer, CMD_EOB, transcript=transcript)
    except Exception:
        pass

    logger.info('BinkP %s: done — received=%d sent=%d', peer, len(inbound_files), sent_count)

    try:
        drain_deadline = asyncio.get_event_loop().time() + 2.0
        while True:
            remaining = drain_deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            chunk = await asyncio.wait_for(reader.read(4096), timeout=remaining)
            if not chunk:
                break
    except Exception:
        pass

    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def _consume_inbound_file_frame(is_cmd, payload, peer, writer, state, files,
                                      transcript=None):
    """Feed one already-read frame into an in-progress inbound-file
    receive, mutating `state` (dict with name/size/buf keys) and `files`
    (list of (name, bytes) tuples) in place. Returns True if the frame
    was consumed as part of a file transfer (a CMD_FILE header, or a
    DATA frame for a file already in progress), False if the caller
    should handle the frame itself (e.g. EOB/ERR).

    Shared between _receive_files() (the main post-handshake receive
    loop) and _send_pkt_file()'s own wait-for-GOT loop -- a peer may
    start delivering its own mail (interleaved CMD_FILE/DATA frames)
    while we're mid-send waiting for an ack on OURS, and before this
    helper existed those frames got silently logged and dropped inside
    _send_pkt_file's wait loop instead of received. Mirrors the
    already-proven binkp.py BinkPClient._consume_inbound_file_frame()
    pattern already used on the outbound-poller side.
    """
    if is_cmd:
        if not payload:
            return False
        cmd = payload[0]
        if cmd == CMD_FILE:
            body = payload[1:].decode('latin-1', errors='replace')
            # "filename size unix_time offset"
            parts = body.split()
            if len(parts) >= 2:
                try:
                    declared_size = int(parts[1])
                except ValueError:
                    declared_size = -1
                if declared_size < 0 or declared_size > MAX_INBOUND_FILE_SIZE:
                    # Reject without ever allocating a buffer for it --
                    # see MAX_INBOUND_FILE_SIZE's own comment above for
                    # why this matters (unauthenticated peers can reach
                    # this path). state['skip'] tells the DATA-frame
                    # branch below to discard bytes as they arrive
                    # instead of buffering them, so a peer that ignores
                    # our M_SKIP and sends the data anyway still can't
                    # grow memory unboundedly.
                    logger.warning(
                        'BinkP %s: refusing oversized/invalid file offer '
                        '%r (declared %s bytes, max %d) -- sending SKIP',
                        peer, parts[0], parts[1], MAX_INBOUND_FILE_SIZE)
                    state['name'] = parts[0]
                    state['size'] = max(declared_size, 0)
                    state['mtime'] = parts[2] if len(parts) >= 3 else '0'
                    state['skip'] = True
                    state['skipped'] = 0
                    await _send_cmd(writer, CMD_SKIP,
                                    f"{parts[0]} {parts[1]} {state['mtime']}",
                                    transcript=transcript)
                    return True
                state['name'] = parts[0]
                state['size'] = declared_size
                state['skip'] = False
                # binkd's tfile_cmp() (prothlp.c) requires an EXACT match
                # on name, size, AND this mtime before it will recognize
                # our M_GOT as acknowledging the file it sent -- real
                # source confirmed live: remove_from_spool() is only
                # ever reached if tfile_cmp() returns 0. This field was
                # parsed here but never stored, so the M_GOT send below
                # had no choice but to hard-code 0 for it -- and 0 never
                # equals a real mtime (e.g. 1784314217), meaning binkd's
                # match ALWAYS failed and it never removed the file from
                # its outbound spool, regardless of how correctly
                # anything else in the session behaved. This is the
                # actual root cause of a real hub (binkd/1.1a-113)
                # resending its entire backlog every poll for months.
                state['mtime'] = parts[2] if len(parts) >= 3 else '0'
                state['buf'] = bytearray()
                logger.info('BinkP %s: receiving %s (%d bytes)',
                           peer, state['name'], state['size'])
            return True
        if cmd == CMD_EOB:
            # A spec-compliant peer sends its own M_EOB the instant its
            # outbound queue empties (FSP-1011 Table 5), independent of
            # whether it's still waiting on us to GOT-ack our own file --
            # so it can arrive while _send_pkt_file()'s wait-for-GOT loop
            # is still running, before _receive_files() ever starts. That
            # loop only checks for CMD_GOT/SKIP/ERR, so the EOB used to
            # fall through to its "some other frame -- keep waiting" debug
            # log with nothing recorded, then _receive_files() would sit
            # through a needless full 120s timeout waiting for an EOB
            # frame that had already arrived and gone unrecognized (real
            # gap found alongside the identical, more severe bug on the
            # outbound-poller side, binkp.py -- see
            # BinkPClient._consume_inbound_file_frame). Record it in
            # `state` (shared across both phases via _handle_connection)
            # instead of just logging it; the caller's own CMD_EOB check
            # still does the actual state transition (this returns False
            # deliberately, matching this function's documented contract
            # of leaving EOB/ERR for the caller to handle) -- see
            # _receive_files()'s upfront check for how the count is used.
            state['eob_count'] = state.get('eob_count', 0) + 1
            logger.info('BinkP %s: M_EOB seen (count=%d)', peer, state['eob_count'])
        return False

    # Data frame.
    if state.get('name') is None:
        return False
    if state.get('skip'):
        # Rejected offer (see MAX_INBOUND_FILE_SIZE above) -- a
        # compliant peer stops sending after M_SKIP, but count and
        # discard bytes instead of buffering them in case a
        # non-compliant/malicious one keeps sending anyway. Once we've
        # seen at least as many bytes as declared, treat the file as
        # drained and go back to waiting for the next offer.
        state['skipped'] = state.get('skipped', 0) + len(payload)
        if state['skipped'] >= state['size']:
            state['name'] = None
            state['skip'] = False
            state['skipped'] = 0
        return True
    state['buf'].extend(payload)
    if len(state['buf']) >= state['size']:
        files.append((state['name'], bytes(state['buf'][:state['size']])))
        await _send_cmd(writer, CMD_GOT,
                        f"{state['name']} {state['size']} {state.get('mtime', '0')}",
                        transcript=transcript)
        state['name'] = None
        state['buf'] = bytearray()
    return True


async def _receive_files(reader, writer, peer, state, files, transcript=None):
    """Receive M_FILE offers + data frames until M_EOB. Appends to
    `files` (list of (name, bytes)) in place; `state` tracks any file
    mid-transfer across calls -- both are shared with _send_pkt_file()'s
    own wait loop so a peer's interleaved file offers during OUR send
    phase land in the same place as anything it sends here."""
    if state.get('eob_count', 0) >= 1:
        # The peer's M_EOB already arrived and was recorded while
        # _send_pkt_file() was still waiting on our own M_GOT -- nothing
        # left to drain. Skip straight to returning instead of blocking
        # on a 120s timeout waiting for a frame that's already been seen.
        logger.info(
            'BinkP %s: M_EOB already seen during our own send phase -- '
            'nothing to drain', peer)
        return files
    # Per-frame wait, deliberately short (was 120s): real live measurement
    # against a real hub (binkd/1.1a-113) showed the TCP connection itself
    # dying ~15s after its last file, consistently, on every single
    # session -- far short of either side's own configured timeout (this
    # 120s value, or the client's 60s), meaning something OUTSIDE our
    # code (the network path, not either mailer's application logic) was
    # silently killing the idle connection well before we ever tried to
    # speak again. Our own confirmatory M_EOB (sent immediately once this
    # loop returns -- see _finish_session()) was therefore always being
    # sent into an already-dead connection, however successful the local
    # socket write looked. Waiting less than that ~15s window before
    # proactively responding gives our confirmation a chance to actually
    # reach the peer while the link is still alive, instead of arriving
    # (or trying to) after it's already gone.
    while True:
        try:
            is_cmd, payload = await asyncio.wait_for(
                _recv_frame(reader, transcript=transcript), timeout=5)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
            break
        if await _consume_inbound_file_frame(is_cmd, payload, peer, writer,
                                             state, files, transcript=transcript):
            continue
        if not is_cmd or not payload:
            continue
        cmd, body = payload[0], payload[1:].decode('latin-1', errors='replace')
        if cmd == CMD_EOB:
            break
        elif cmd == CMD_ERR:
            logger.warning('BinkP %s: client sent ERR: %s', peer, body)
            break
    return files


async def _send_pkt_file(reader, writer, filename, payload, peer, state, files,
                         transcript=None):
    """Send a M_FILE offer + data frames, then wait for M_GOT (or M_SKIP/M_ERR).

    While waiting, any interleaved CMD_FILE/DATA frames the peer sends
    (its own mail, delivered before it acks ours) are captured via
    _consume_inbound_file_frame into the shared `state`/`files` instead
    of being logged and silently dropped as "some other frame" -- real
    gap found live: this listener's own send phase moved earlier (see
    _handle_connection) specifically so we announce our own completion
    promptly instead of making the peer wait through however long our
    receive phase takes, but a peer that starts delivering its mail
    concurrently with acking ours needs those frames to land somewhere.

    Returns True on accepted (M_GOT), False on rejected/skipped/error.
    """
    unix_time = int(datetime.utcnow().timestamp())
    await _send_cmd(writer, CMD_FILE, f'{filename} {len(payload)} {unix_time} 0', transcript=transcript)
    # Send in 4 KB chunks
    chunk = 4096
    for i in range(0, len(payload), chunk):
        await _send_data(writer, payload[i:i+chunk], transcript=transcript)

    # Wait for M_GOT — many BinkP impls won't actually process the file until
    # they confirm. Without this, we'd send M_EOB + close immediately and the
    # remote might not have flushed/written the .pkt to its inbound dir.
    deadline = asyncio.get_event_loop().time() + 60
    while True:
        timeout = max(0.1, deadline - asyncio.get_event_loop().time())
        try:
            is_cmd, frame = await asyncio.wait_for(
                _recv_frame(reader, transcript=transcript), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
            logger.warning('No M_GOT received for %s within 60s', filename)
            return False
        if await _consume_inbound_file_frame(is_cmd, frame, peer, writer,
                                             state, files, transcript=transcript):
            continue
        if not is_cmd or not frame:
            continue
        cmd, body = frame[0], frame[1:].decode('latin-1', errors='replace')
        if cmd == CMD_GOT:
            logger.info('M_GOT received for %s', filename)
            return True
        if cmd in (CMD_SKIP, CMD_ERR):
            logger.warning('Remote rejected %s: cmd=%d body=%s', filename, cmd, body)
            return False
        # Some other frame — log and keep waiting
        logger.debug('Waiting for M_GOT, got cmd=%d body=%s', cmd, body)


async def _send_hatch_items(reader, writer, hatch_items, our_address, peer,
                            state, files, transcript=None):
    """Ship pending HatchQueue rows (binary file + .tic manifest pair) to
    a connected peer, using the same M_FILE/M_GOT exchange as
    _send_pkt_file(). Async counterpart of binkp.py's BinkPClient.
    _send_hatch() (the outbound-poll/dial-out direction) -- this is the
    inbound-listener direction (a downstream node dials INTO us).

    Real gap found in a full echomail-subsystem audit: this listener's
    downstream_node_id branch only ever sent the pending echomail .pkt
    via get_pending_for_node()/_send_pkt_file -- nothing here ever
    queried HatchQueue for pending file-echo items, so a node polling
    IN to the hub never received a file it was subscribed to via
    FileFix, even though the whole rest of the pipeline (subscription,
    queueing, .tic rendering) was already correct. Mirrors poller.py's
    _run_client()'s already-correct hatch-out for the OTHER direction
    (a leaf dialing out to its upstream hub).

    Returns (sent_ids, failures) -- sent_ids is a list of HatchQueue.id
    values successfully sent, failures is a list of (item, message)
    tuples for ones that weren't (same shape as binkp.py's _send_hatch).
    Both callers run inside the same app_context this coroutine does, so
    holding onto the ORM item across the await is safe -- no detached-
    instance risk (see binkp_server.py's own notes elsewhere on that
    hazard for objects that DO cross an app_context boundary).
    """
    from .tic import build_tic_text
    sent_ids = []
    failures = []
    for item in hatch_items:
        try:
            with open(item.binary_path, 'rb') as f:
                binary = f.read()
        except OSError as exc:
            logger.error('Hatch (inbound listener): cannot read binary %s: %s',
                        item.binary_path, exc)
            failures.append((item, f'cannot read binary: {exc}'))
            continue

        accepted = await _send_pkt_file(reader, writer, item.filename, binary,
                                        peer, state, files, transcript=transcript)
        if not accepted:
            logger.warning('Hatch (inbound listener): peer did not ack %s',
                           item.filename)
            failures.append((item, f"peer didn't ack binary {item.filename}"))
            continue

        tic_text = build_tic_text(item, our_address)
        tic_bytes = tic_text.encode('cp437', errors='replace')
        tic_name = item.filename.rsplit('.', 1)[0] + '.tic'
        accepted = await _send_pkt_file(reader, writer, tic_name, tic_bytes,
                                        peer, state, files, transcript=transcript)
        if not accepted:
            logger.warning('Hatch (inbound listener): peer did not ack %s',
                           tic_name)
            failures.append((item, f"peer didn't ack manifest {tic_name}"))
            continue

        sent_ids.append(item.id)
        logger.info('Hatch (inbound listener): shipped %s + %s to %s',
                    item.filename, tic_name, peer)
    return sent_ids, failures


async def _send_outbound_dir_items(reader, writer, outbound_dir, peer,
                                   state, files, transcript=None):
    """Ship any loose files sitting in outbound_dir (see binkp.py's
    resolve_outbound_dir) to a connected peer that dialed IN to us,
    using the same M_FILE/M_GOT exchange as _send_pkt_file/
    _send_hatch_items. Async counterpart of binkp.py's BinkPClient.
    _send_outbound_dir_files() (the outbound-poll/dial-out direction)
    -- this is the inbound-listener direction (a peer dials INTO us
    and we flush whatever's waiting for them too, same as hatch/echo).

    No packet-building, no HatchQueue/.tic manifest -- files are sent
    exactly as found. Returns (sent_count, failures), failures a list
    of (filename, message) tuples."""
    sent = 0
    failures = []
    for name, path in list_outbound_dir_files(outbound_dir):
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError as exc:
            logger.error('Outbound dir (inbound listener): cannot read %s: %s',
                        path, exc)
            failures.append((name, f'cannot read: {exc}'))
            continue
        accepted = await _send_pkt_file(reader, writer, name, data,
                                        peer, state, files, transcript=transcript)
        if not accepted:
            logger.warning('Outbound dir (inbound listener): peer did not ack %s',
                           name)
            failures.append((name, "peer didn't ack file"))
            continue
        archive_sent_outbound_file(outbound_dir, name, path)
        sent += 1
        logger.info('Outbound dir (inbound listener): shipped %s to %s', name, peer)
    return sent, failures


def _import_pkt_payload(pkt_bytes: bytes, network_id: int, filename: str,
                        peer_address: str = None, origin_ip: str = None) -> int:
    """Parse an FTS-0001 .pkt and import each message as inbound. Returns count.

    Routes by AREA: kludge:
      - present → echomail → EchomailMessage (with kludges, seenby, path)
      - absent  → netmail  → NetmailMessage (with kludges, all attribute flags)
    Triggers the areafix bot if a netmail's to_name matches 'areafix' (any
    case), or the filefix bot for 'filefix' (any case).

    `network_id` may be None -- an anonymous/unrecognized peer, accepted
    per FTN nodelist crashmail policy (see binkp_server.py's caller-lookup
    site). Only netmail is imported in that case; any echomail in the same
    packet is logged and dropped, since echo distribution still requires
    real network membership (NetmailMessage.network_id is nullable,
    EchomailMessage/EchoArea's is not).

    `peer_address` is the connecting session's own claimed FTN address
    (this function's caller's `remote_addr`) -- passed through to the
    post-import toss_message() calls as an extra loop-prevention
    fallback on top of SEEN-BY. See tosser.py's toss_message() for why
    this can't just reuse the message's own from_address field.

    `origin_ip` is the connecting session's real socket IP (this
    function's caller's `peer[0]`) -- stored on any netmail imported
    with network_id=None so a reply can dial the sender back directly
    (see poller.py's send_direct_netmail_now()). Ignored for echomail
    and for any session that DOES have a resolvable network_id, since
    those already have a real hub/node to route a reply through.
    """
    import json
    from ..models import (db, EchomailMessage, EchoArea, NetmailMessage,
                          EchomailNetwork, maybe_tag_ansi_subject)
    from .kludges import find_kludge
    from .routing import resolve_netmail_recipient
    from .poller import _record_bad_area

    network = EchomailNetwork.query.get(network_id) if network_id is not None else None

    messages = _parse_ftn_packet(pkt_bytes)
    imported = 0
    echomail_objects = []       # EchomailMessage ORM objects for post-commit toss
    echomail_notify_pairs = []  # (EchomailMessage, EchoArea) for post-commit reply-notify
    netmails_to_process = []   # ids of newly-inserted netmails for post-processing

    for m in messages:
        kludges = m.get('kludges') or []
        kludges_json = json.dumps(kludges) if kludges else None
        seenby_json = json.dumps(m.get('seenby') or []) if m.get('seenby') else None
        path_json = json.dumps(m.get('path') or []) if m.get('path') else None
        chrs = find_kludge(kludges, 'CHRS') or 'CP437 2'

        if m['area_tag']:
            if network_id is None:
                # Anonymous/unrecognized peer -- echomail requires real
                # network membership (subscription), which an unlisted
                # crashmail-only caller doesn't have. Only netmail gets
                # accepted from this session; see this function's docstring.
                logger.warning(
                    'BinkP: dropping echomail (area %r) from unrecognized '
                    'peer %s -- only netmail is accepted from unlisted/'
                    'anonymous callers', m['area_tag'], peer_address)
                continue
            # ECHOMAIL — route to area
            area = EchoArea.query.filter_by(
                network_id=network_id, tag=m['area_tag']).first()
            # Real gap found in a full echomail-subsystem audit: this
            # path (a peer connecting IN to deliver mail) auto-created a
            # brand-new, immediately-active, immediately-subscribed
            # EchoArea for ANY unrecognized tag -- any peer that could
            # complete a BinkP handshake (a downstream node, or the hub
            # that already dials us) could make new echo areas silently
            # appear with zero sysop review, and it never re-checked an
            # EXISTING area's is_subscribed/is_active before importing
            # into it either. poller.py's own _import_message() (the
            # outbound-poll receive path) already routes unrecognized/
            # unsubscribed BinkP tags to BadAreaLog instead (SBBSecho's
            # BadAreaFile semantics) -- this listener path never got the
            # same treatment.
            if area is None:
                _record_bad_area(
                    network, m['area_tag'], m,
                    reason='unknown',
                    title=f"Unknown echomail area: {m['area_tag']} on {network.name if network else network_id}",
                    body=f"Inbound mail arrived tagged for {m['area_tag']!r}, "
                         f"which this BBS does not carry. Review under "
                         f"Admin -> Echomail -> Bad Areas.")
                continue
            if not area.is_subscribed or not area.is_active:
                _record_bad_area(
                    network, m['area_tag'], m,
                    reason='unsubscribed',
                    title=f"Mail dropped for unsubscribed area: {m['area_tag']} on {network.name if network else network_id}",
                    body=f"Inbound mail arrived for {m['area_tag']!r}, which "
                         f"this BBS carries but is not subscribed to (or has "
                         f"deactivated). Review under Admin -> Echomail -> "
                         f"Bad Areas.")
                continue

            # Deduplicate by MSGID, same as poller.py's _import_message()
            # (the outbound-poll import path) already does. This path --
            # a peer connecting IN to deliver mail -- never extracted
            # MSGID onto the row at all, let alone checked for an
            # existing one first: a peer that reconnects and redelivers
            # its backlog (its own retry logic, a flaky link, anything)
            # got every message re-inserted as brand new, unbounded,
            # every single time. Real live report: same ~570-message
            # packet re-"received" on repeat inbound connections a few
            # minutes apart, each one counted as newly imported.
            msgid = find_kludge(kludges, 'MSGID')
            if msgid and EchomailMessage.query.filter_by(
                    msg_id=msgid, area_id=area.id).first():
                continue

            em = EchomailMessage(
                area_id=area.id,
                network_id=network_id,
                msg_id=msgid or None,
                from_name=m['from_name'][:100],
                to_name=m['to_name'][:100],
                subject=maybe_tag_ansi_subject(m['subject'], m['body'])[:200],
                body=m['body'],
                tear_line=(m['tear_line'] or '')[:200] or None,
                origin_line=(m['origin_line'] or '')[:200] or None,
                kludges=kludges_json,
                chrs=chrs,
                seenby=seenby_json,
                path=path_json,
                direction='inbound',
                created_at=datetime.utcnow(),
                imported_at=datetime.utcnow(),
            )
            db.session.add(em)
            echomail_objects.append(em)
            # (em, area) pairs for the post-commit reply-notify pass below
            # -- mirrors netmails_to_process just below, but for echomail/
            # QWK messages addressed to a real local user's handle rather
            # than the generic 'All' convention (see notify_reply.py).
            echomail_notify_pairs.append((em, area))
            # Bump the area counters so the admin index and area list show
            # accurate "Messages" and "Last Activity" columns. The QWK
            # poller does this in poller.py — the listener path was missing
            # it, leading to "0 messages" on areas that had real content.
            area.total_messages = (area.total_messages or 0) + 1
            area.last_message_at = datetime.utcnow()
        else:
            # NETMAIL — point-to-point
            msgid = find_kludge(kludges, 'MSGID')
            # Same dedup gap as echomail above -- poller.py's
            # _import_netmail() already checks NetmailMessage.msgid
            # before inserting; this listener path never did.
            if msgid and NetmailMessage.query.filter_by(msgid=msgid).first():
                continue
            reply = find_kludge(kludges, 'REPLY')
            # Use _parse_ftn_packet's own from_address/to_address rather
            # than re-deriving from @INTL locally -- this used to
            # duplicate that logic here (@INTL only, no header fallback
            # if @INTL was absent, and no @FMPT/@TOPT point-number
            # handling at all), the same "second unpatched path" pattern
            # this codebase has hit repeatedly for BinkP logic that
            # exists in both binkp.py and binkp_server.py. poller.py's
            # own _import_netmail() (the outbound-poll import path)
            # already just uses msg_data.get('from_address')/
            # ('to_address') directly; this inbound-listener path never
            # matched it. Real gap found during a netmail send/receive
            # correctness pass: point-addressed netmail (e.g.
            # "1200:1/2.5") lost its point number on import via this
            # path specifically, even after _parse_ftn_packet itself was
            # fixed to reconstruct it from @FMPT/@TOPT.
            to_addr = m.get('to_address') or ''
            from_addr = m.get('from_address') or ''

            # Content-based dedup fallback, mirroring poller.py's
            # _import_netmail(). Real gap found live: a peer (SBBSecho)
            # polls INTO this listener independently of -- and around
            # the same ~10 minute cadence as -- our own outbound poll of
            # the same hub, resending the same "Area Management
            # Request"/"List of Available Areas" netmail with a freshly
            # regenerated MSGID each time. poller.py's own dedup
            # fallback only covers netmail arriving via OUR outbound
            # polls; this separate inbound-listener import path had none
            # at all, so it kept inserting a fresh row every single time
            # regardless of the fix in poller.py. See poller.py's own
            # comment for why this compares sender+subject+network
            # only, not body (confirmed live the body isn't
            # byte-identical across resends).
            #
            # EXCEPT for netmail addressed to a robot (AreaFix/FileFix
            # and their aliases): each one is a distinct command a
            # sysop is issuing, not a duplicate informational broadcast,
            # and it's completely ordinary for a sysop testing/retrying
            # AreaFix commands to resend with an identical subject line
            # (their client often doesn't vary it) within the 48h
            # window. Confirmed live: a downstream node's repeated
            # AreaFix "%help" request was silently swallowed here every
            # time after the first because an earlier netmail from the
            # same address happened to reuse the same subject -- logged
            # only as "Imported 0 messages", no error, and the AreaFix
            # bot below never even ran since the netmail never made it
            # into netmails_to_process. The exact-MSGID dedup above
            # still guards against a literal retransmit of the same
            # packet, which is the only case that's a true duplicate.
            _to_lower = (m.get('to_name') or '').strip().lower()
            _is_robot_netmail = _to_lower in (
                'areafix', 'area fix', 'areamgr',
                'filefix', 'file fix', 'filemgr')
            _from_name = (m['from_name'] or '')[:120]
            _subject = (m['subject'] or '')[:200]
            _cutoff = datetime.utcnow() - timedelta(hours=48)
            if not _is_robot_netmail and NetmailMessage.query.filter(
                    NetmailMessage.network_id == network_id,
                    NetmailMessage.direction == 'inbound',
                    NetmailMessage.from_name == _from_name,
                    NetmailMessage.from_address == (from_addr or None),
                    NetmailMessage.subject == _subject,
                    NetmailMessage.received_at >= _cutoff).first():
                continue

            # Match a local user by AKA address or by name -- this
            # listener path never did this at all before, so BinkP-
            # received netmail was never linked to a User row and could
            # only be found later by string-matching to_name in the web
            # inbox (and its recipient never got notified of new mail).
            to_user = resolve_netmail_recipient(m['to_name'], to_addr, network)

            nm = NetmailMessage(
                network_id=network_id,
                from_address=from_addr or None,
                to_address=to_addr or None,
                from_name=m['from_name'][:120],
                to_name=m['to_name'][:120],
                to_user_id=to_user.id if to_user else None,
                subject=m['subject'][:200],
                body=m['body'],
                kludges=kludges_json,
                msgid=msgid or None,
                reply_msgid=reply or None,
                chrs=chrs,
                direction='inbound',
                status='received',
                created_at=datetime.utcnow(),
                received_at=datetime.utcnow(),
                origin_ip=(origin_ip if network_id is None else None),
            )
            db.session.add(nm)
            db.session.flush()
            netmails_to_process.append((
                nm.id, (m.get('to_name') or '').lower(),
                to_user.id if to_user else None,
                m['from_name'], m['subject']))
        imported += 1

    db.session.commit()
    logger.info('Imported %d messages from %s into network %s',
                imported, filename, network_id if network_id is not None else 'none (anonymous crashmail)')

    # Hub tosser — fan out each newly imported echomail to downstream nodes.
    # After commit the ORM objects have their .id populated.
    if echomail_objects:
        try:
            from .tosser import toss_message
            for em_obj in echomail_objects:
                if em_obj.id:
                    toss_message(em_obj.id, exclude_peer_address=peer_address)
        except Exception:
            logger.exception('Hub tosser failed for %s', filename)

    # Reply-to-a-real-user notifications -- this listener path never
    # linked an inbound echomail/QWK message's TO name to a local user at
    # all before, so a Synchronet-style "so-and-so replied to you in
    # <network> (<area>)" notice was only ever possible via poller.py's
    # own outbound-poll import path (_import_message), never via a live
    # inbound BinkP session.
    if echomail_notify_pairs:
        from .notify_reply import maybe_notify_recipient
        for em_obj, area_obj in echomail_notify_pairs:
            maybe_notify_recipient(em_obj, area_obj, network)

    # Areafix bot — trigger reply for any netmail addressed to 'areafix'
    # (or any Synchronet-style robot name we recognize). Done AFTER the
    # commit so the netmail is durable before the bot runs. Everything
    # else that resolved to a real local user gets a Notification row
    # (in-app bell + terminal "You have new" banner) -- this listener
    # path never notified the recipient of new netmail at all before.
    from .areafix import handle_areafix_netmail
    from .filefix import handle_filefix_netmail
    from ..features.notify import notify
    for nm_id, to_lower, to_uid, from_name, subject in netmails_to_process:
        if to_lower in ('areafix', 'area fix', 'areamgr'):
            try:
                handle_areafix_netmail(nm_id)
            except Exception:
                logger.exception('Areafix bot failed for netmail %d', nm_id)
        elif to_lower in ('filefix', 'file fix', 'filemgr'):
            try:
                handle_filefix_netmail(nm_id)
            except Exception:
                logger.exception('FileFix bot failed for netmail %d', nm_id)
        elif to_uid:
            try:
                notify(to_uid, 'netmail', title=f'Netmail from {from_name}',
                      body=subject or '', target_url=f'/netmail/{nm_id}')
            except Exception:
                logger.exception('Netmail notify failed for netmail %d', nm_id)

    return imported


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _serve():
    host = os.environ.get('BINKP_LISTEN_HOST', '0.0.0.0')
    port = int(os.environ.get('BINKP_LISTEN_PORT', '24554'))
    our_address = os.environ.get('BINKP_OUR_ADDRESS', '1:1/1')
    system_name = os.environ.get('BINKP_SYSTEM_NAME', 'ANetBBS')

    # Defense in depth alongside MAX_INBOUND_FILE_SIZE (see its comment):
    # bounds how many sessions -- each potentially holding up to that
    # per-file cap in memory -- can be in flight at once, since the
    # file-receive path requires no authentication at all.
    _session_slots = asyncio.Semaphore(MAX_CONCURRENT_SESSIONS)

    async def _wrapper(reader, writer):
        if _session_slots.locked():
            # Full -- reject immediately rather than queuing forever
            # behind the semaphore, so a flood of connections can't
            # pile up waiting.
            logger.warning('BinkP: refusing connection, %d sessions already active',
                            MAX_CONCURRENT_SESSIONS)
            try:
                writer.close()
            except Exception:
                pass
            return
        async with _session_slots:
            try:
                await _handle_connection(reader, writer, our_address, system_name)
            except (ConnectionResetError, BrokenPipeError):
                pass  # health probe or client disconnect — not a crash
            except Exception:
                logger.exception('BinkP session crashed')
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

    server = await asyncio.start_server(_wrapper, host=host, port=port)
    logger.info('BinkP listener on %s:%d as %s', host, port, our_address)
    async with server:
        await server.serve_forever()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
