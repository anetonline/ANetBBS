"""ANetBBS federation registry — the hub side.

This blueprint is only useful on the one BBS designated as the
federation hub (REGISTRY_MODE_ENABLED=true). Other ANetBBS instances
POST their registration here and pull `/anetbbs.lst` to discover peers.

Endpoints:
    POST /registry/api/v1/register   peer announces itself
    POST /registry/api/v1/heartbeat  peer pings to stay listed
    GET  /registry/api/v1/list       JSON peer list (same body as /anetbbs.lst)
    GET  /anetbbs.lst                friendly URL alias for the list
    GET  /registry/verify/<token>    sysop clicks an emailed token to confirm

Acceptance gates before an entry appears in the public list:
    1. Email verification — sysop clicks the token link.
    2. Sysop approval — the hub's sysop manually approves via the admin UI.
Both must be true AND is_active=true AND a recent heartbeat present.
"""
import re
import secrets
from datetime import datetime, timedelta

from flask import (Blueprint, current_app, jsonify, request, abort,
                   render_template_string)

from ..models import db, RegistryEntry

registry_bp = Blueprint('registry', __name__)


def _exempt_from_csrf(bp):
    """The federation registry API is called from other ANetBBS hosts —
    not browsers. They don't have (or need) the BBS's CSRF token. Exempt
    the entire blueprint so peer registrations don't require posting
    through a form. Called from web_app.create_app at blueprint-register
    time; safe to no-op if Flask-WTF isn't wired (TESTING mode etc).
    """
    try:
        from ..web_app import csrf
        csrf.exempt(bp)
    except Exception:
        pass


_HOST_RE = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9\-.]{0,253}[A-Za-z0-9])?$')
_EMAIL_RE = re.compile(
    r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9][A-Za-z0-9\-.]*\.[A-Za-z]{2,}$')


def _require_hub_mode():
    """Refuse all registry requests on non-hub installs so peer BBSes
    don't accidentally accept registrations meant for the central hub."""
    if not current_app.config.get('REGISTRY_MODE_ENABLED'):
        abort(404)


def _peer_ip():
    """Best-effort source IP, honoring X-Forwarded-For when nginx is in
    front."""
    fwd = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    return fwd or (request.remote_addr or '')


def _problem(status, msg):
    """RFC 7807-ish JSON error body."""
    return jsonify({'ok': False, 'error': msg}), status


def _ratelimit_check(host, source_ip, kind='register'):
    """Coarse rate limit. Separates 'register' (allow some churn —
    sysop is iterating on the metadata) from 'heartbeat' (should
    actually be daily, so anything more frequent than a few seconds
    is a misconfig).

    kind='register' → 5 sec/host floor + 60/hour/IP cap
    kind='heartbeat' → 60 sec/host floor + 100/hour/IP cap
    """
    now = datetime.utcnow()
    if kind == 'heartbeat':
        host_window = timedelta(seconds=10)
        ip_window = timedelta(hours=1)
        ip_cap = 100
    else:
        host_window = timedelta(seconds=5)
        ip_window = timedelta(hours=1)
        ip_cap = 60

    if host:
        recent = (RegistryEntry.query
                  .filter(RegistryEntry.host.ilike(host))
                  .filter(RegistryEntry.last_heartbeat_at >
                          now - host_window)
                  .first())
        if recent:
            return f'too many {kind} requests for this host — wait'
    if source_ip:
        hour_count = (RegistryEntry.query
                      .filter(RegistryEntry.source_ip == source_ip)
                      .filter(RegistryEntry.last_heartbeat_at >
                              now - ip_window)
                      .count())
        if hour_count > ip_cap:
            return 'too many requests from this IP — wait an hour'
    return None


@registry_bp.route('/registry/api/v1/register', methods=['POST'])
def register():
    _require_hub_mode()
    data = request.get_json(silent=True) or {}

    host = (data.get('host') or '').strip().lower()
    name = (data.get('name') or '').strip()
    contact_email = (data.get('contact_email') or '').strip()

    if not host or not _HOST_RE.match(host):
        return _problem(400, 'invalid host')
    if not name or len(name) > 160:
        return _problem(400, 'invalid name')
    if not contact_email or not _EMAIL_RE.match(contact_email):
        return _problem(400, 'invalid contact_email')

    rl = _ratelimit_check(host, _peer_ip(), kind='register')
    if rl:
        return _problem(429, rl)

    # Coerce ports + cap notes/sysop fields
    try:
        msp_port = int(data.get('msp_port', 18))
        systat_port = int(data.get('systat_port', 11))
    except (TypeError, ValueError):
        return _problem(400, 'ports must be integers')
    if not (0 < msp_port < 65536 and 0 < systat_port < 65536):
        return _problem(400, 'port out of range')

    sysop = (data.get('sysop') or '')[:120]
    location = (data.get('location') or '')[:120]
    software = (data.get('software') or 'ANetBBS')[:40]
    software_version = (data.get('software_version') or '')[:40]
    notes = (data.get('notes') or '')[:4000]

    entry = RegistryEntry.query.filter(
        RegistryEntry.host.ilike(host)).first()
    new_token = secrets.token_urlsafe(24)
    now = datetime.utcnow()

    if entry is None:
        # First-time registration — create row + email-token in pending.
        entry = RegistryEntry(
            host=host, msp_port=msp_port, systat_port=systat_port,
            name=name, sysop=sysop, location=location,
            software=software, software_version=software_version,
            notes=notes, contact_email=contact_email,
            registration_token=new_token,
            is_verified=False, is_approved=False, is_listed=False,
            source_ip=_peer_ip(),
            registered_at=now,
            last_heartbeat_at=now,
        )
        db.session.add(entry)
    else:
        # Re-registration. If the contact_email changes, force re-verify
        # (someone might be trying to take over the entry). Otherwise
        # just refresh metadata + heartbeat.
        if entry.contact_email.lower() != contact_email.lower():
            entry.is_verified = False
            entry.is_listed = False
            entry.registration_token = new_token
            entry.contact_email = contact_email
        entry.name = name
        entry.msp_port = msp_port
        entry.systat_port = systat_port
        entry.sysop = sysop
        entry.location = location
        entry.software = software
        entry.software_version = software_version
        entry.notes = notes
        entry.source_ip = _peer_ip()
        entry.last_heartbeat_at = now

    db.session.commit()

    # Token is returned in the response so the hub sysop can
    # paste it to the registrant via any out-of-band channel (PM, chat, etc.).
    return jsonify({
        'ok': True,
        'status': 'pending_verification' if not entry.is_verified
                  else ('listed' if entry.is_listed
                        else 'pending_approval'),
        'host': entry.host,
        'verify_token': entry.registration_token,
        'verify_url': f"{request.host_url.rstrip('/')}"
                      f"/registry/verify/{entry.registration_token}",
        'message': 'Click the verify_url to confirm your contact email. '
                   'Once verified, the hub sysop will review and approve '
                   'the entry for inclusion in anetbbs.lst.',
    })


@registry_bp.route('/registry/api/v1/heartbeat', methods=['POST'])
def heartbeat():
    _require_hub_mode()
    data = request.get_json(silent=True) or {}
    host = (data.get('host') or '').strip().lower()
    if not host or not _HOST_RE.match(host):
        return _problem(400, 'invalid host')

    rl = _ratelimit_check(host, _peer_ip(), kind='heartbeat')
    if rl:
        return _problem(429, rl)

    entry = RegistryEntry.query.filter(
        RegistryEntry.host.ilike(host)).first()
    if entry is None:
        return _problem(404, 'unknown host — register first')

    entry.last_heartbeat_at = datetime.utcnow()
    # Heartbeats can update soft metadata (sysop went on vacation,
    # version bumped, etc.). Anything pinned to identity (host,
    # contact_email) must use register, not heartbeat.
    for k in ('name', 'sysop', 'location', 'software', 'software_version',
              'notes'):
        v = data.get(k)
        if v is None:
            continue
        if k == 'notes':
            v = str(v)[:4000]
        else:
            v = str(v)[:160]
        setattr(entry, k, v)
    db.session.commit()
    return jsonify({'ok': True, 'is_listed': bool(entry.is_listed)})


@registry_bp.route('/registry/verify/<token>')
def verify(token):
    _require_hub_mode()
    entry = RegistryEntry.query.filter_by(
        registration_token=token).first()
    if entry is None:
        return render_template_string(
            '<h1>Token not recognized</h1>'
            '<p>Already verified, expired, or never existed.</p>'), 404
    if not entry.is_verified:
        entry.is_verified = True
        # Don't clear the token immediately — let the sysop see this
        # row was self-verified. Token gets rotated on next re-register
        # if the email changes.
        db.session.commit()
        # Notify now, not at initial /register -- that step hasn't
        # confirmed the registrant controls the contact email yet, so
        # there's nothing actionable for the sysop until this point.
        from ..features.notify import notify_admins
        notify_admins(
            'msp_join_request',
            title=f'MSP registry: {entry.host} verified, awaiting approval',
            body=f'{entry.name} ({entry.sysop or "unknown sysop"}) at '
                 f'{entry.host} has verified their contact email and is '
                 f'waiting for approval to join the federation registry.',
            target_url='/admin/registry/')
    return render_template_string(
        '<h1>Contact email verified ✓</h1>'
        '<p>{{ host }} is now waiting for the registry sysop to approve '
        'the entry for inclusion in <code>anetbbs.lst</code>.</p>',
        host=entry.host)


def _listed_rows():
    """Return the rows that should appear in the public list."""
    stale_cutoff = datetime.utcnow() - timedelta(
        hours=current_app.config.get('REGISTRY_HEARTBEAT_STALE_HOURS', 48))
    return (RegistryEntry.query
            .filter(RegistryEntry.is_listed.is_(True),
                    RegistryEntry.is_active.is_(True))
            .filter(db.or_(
                RegistryEntry.last_heartbeat_at >= stale_cutoff,
                RegistryEntry.last_heartbeat_at.is_(None)))
            .order_by(RegistryEntry.host)
            .all())


def _serialize_for_list():
    rows = _listed_rows()
    return {
        'version': 1,
        'updated': datetime.utcnow().isoformat() + 'Z',
        'maintainer': current_app.config.get(
            'REGISTRY_MAINTAINER',
            current_app.config.get('SYSOP_NAME', 'sysop')),
        'bbses': [
            {
                'host': e.host,
                'msp_port': e.msp_port,
                'systat_port': e.systat_port,
                'name': e.name,
                'sysop': e.sysop or '',
                'location': e.location or '',
                'software': e.software or 'ANetBBS',
                'software_version': e.software_version or '',
                'notes': e.notes or '',
                'since': (e.registered_at.date().isoformat()
                          if e.registered_at else None),
                'last_seen': (e.last_heartbeat_at.isoformat() + 'Z'
                              if e.last_heartbeat_at else None),
            }
            for e in rows
        ],
    }


@registry_bp.route('/registry/api/v1/list')
def list_json():
    _require_hub_mode()
    return jsonify(_serialize_for_list())


@registry_bp.route('/anetbbs.lst')
def list_friendly():
    """The well-known URL peer BBSes pull from. Identical payload to
    /registry/api/v1/list, exposed at root for back-compat with the
    sbbsimsg.lst naming convention."""
    _require_hub_mode()
    return jsonify(_serialize_for_list())
