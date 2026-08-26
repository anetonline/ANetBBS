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
import hmac
import logging
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
    """Best-effort source IP, used as the key for _ratelimit_check() below.

    Real gap found in a full auth-security audit: this trusted a
    client-supplied X-Forwarded-For header unconditionally, with no
    trusted-proxy boundary -- any direct connection could set an
    arbitrary value to dodge the registration rate limit entirely
    (a fresh fake source_ip every request never trips the same bucket
    twice). web_app.py's ProxyFix (opt-in via TRUST_PROXY_HEADERS) is
    now the only place XFF is trusted, rewriting request.remote_addr
    itself when the sysop has confirmed Flask sits behind their own
    trusted proxy -- see web/auth.py's _client_ip() for the full
    writeup of this same fix.
    """
    return request.remote_addr or ''


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
    # Fresh every register call (new row AND re-register alike) — this
    # is what heartbeat() now requires as proof of "same client that
    # just registered," independent of email verification. Re-issuing
    # it on every call also makes a legitimate peer whose stored key
    # went stale (e.g. this row predates the security fix) self-heal
    # the moment its registry_client.py falls back to register().
    new_heartbeat_key = secrets.token_urlsafe(24)
    now = datetime.utcnow()
    needs_verify_email = False

    if entry is None:
        # First-time registration — create row + email-token in pending.
        entry = RegistryEntry(
            host=host, msp_port=msp_port, systat_port=systat_port,
            name=name, sysop=sysop, location=location,
            software=software, software_version=software_version,
            notes=notes, contact_email=contact_email,
            registration_token=new_token, heartbeat_key=new_heartbeat_key,
            is_verified=False, is_approved=False, is_listed=False,
            source_ip=_peer_ip(),
            registered_at=now,
            last_heartbeat_at=now,
        )
        db.session.add(entry)
        needs_verify_email = True
    else:
        # Re-registration. If the contact_email changes, force re-verify
        # AND re-approval (someone might be trying to take over the
        # entry). Security fix: this used to reset is_verified/is_listed
        # but leave is_approved untouched, which meant a hijacker who
        # re-registered a known host under their own email could get
        # verified (see below) and then ride the ORIGINAL sysop
        # approval straight back onto the public list via the
        # background probe's auto-relist — no fresh sysop action
        # anywhere in that chain. is_approved is now part of what a
        # changed contact_email invalidates.
        if entry.contact_email.lower() != contact_email.lower():
            entry.is_verified = False
            entry.is_approved = False
            entry.is_listed = False
            entry.registration_token = new_token
            entry.contact_email = contact_email
            needs_verify_email = True
        entry.heartbeat_key = new_heartbeat_key
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

    verify_url = (f"{request.host_url.rstrip('/')}"
                 f"/registry/verify/{entry.registration_token}")

    # Security fix: the verify_url used to be returned directly in this
    # response — but that defeats the entire point of "email
    # verification," since anyone who can POST /register (no auth
    # required) got the token instantly, with no proof they control
    # contact_email at all. It now gets emailed to contact_email
    # instead, closing the register→verify→auto-relist hijack chain.
    # Fallback: on an install with no SMTP configured, keep the old
    # behavior (return it directly) rather than leaving the registrant
    # with no way to ever verify — matches this project's original
    # documented intent ("until [email] is up, the state file is the
    # source of truth").
    email_sent = False
    if needs_verify_email:
        from ..mailer import smtp_enabled, send_email
        if smtp_enabled():
            try:
                email_sent, _err = send_email(
                    contact_email,
                    f'Verify your ANetBBS federation registration for {entry.host}',
                    f'{entry.name} ({entry.host}) was just registered with '
                    f'this hub. Click the link below to confirm you control '
                    f'this contact email — once verified, the hub sysop will '
                    f'review and approve the entry for inclusion in '
                    f'anetbbs.lst.\n\n{verify_url}\n\n'
                    f"If you didn't request this, you can ignore this email.")
            except Exception:
                logging.getLogger(__name__).warning(
                    'registry: failed to email verify link to %s',
                    contact_email, exc_info=True)

    message = ('Once verified, the hub sysop will review and approve '
              'the entry for inclusion in anetbbs.lst.')
    if not needs_verify_email:
        message = 'Contact email already verified — ' + message
    elif email_sent:
        message = 'Check your email for a verification link. ' + message
    else:
        message = ('SMTP is not configured on this hub — click the '
                  'verify_url below directly. ') + message

    body = {
        'ok': True,
        'status': 'pending_verification' if not entry.is_verified
                  else ('listed' if entry.is_listed
                        else 'pending_approval'),
        'host': entry.host,
        'heartbeat_key': entry.heartbeat_key,
        'message': message,
    }
    if needs_verify_email and not email_sent:
        body['verify_token'] = entry.registration_token
        body['verify_url'] = verify_url
    return jsonify(body)


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

    # Security fix: heartbeat previously required nothing but a `host`
    # string that matches an existing row — no ownership proof at all.
    # Since `host` is published verbatim by this same hub at
    # /anetbbs.lst and /registry/api/v1/list, anyone could silently
    # overwrite a listed peer's public name/sysop/location/notes with
    # zero notification (heartbeat never touches is_verified/
    # is_approved, so the entry stayed publicly listed the whole time).
    # Now requires the per-entry heartbeat_key issued at register time,
    # compared in constant time. A legacy row with no key yet (created
    # before this fix shipped) can never pass this check — that's
    # deliberate: it forces one self-heal register() call rather than
    # silently granting the old, unauthenticated behavior.
    provided_key = (data.get('heartbeat_key') or '').strip()
    if not entry.heartbeat_key or not hmac.compare_digest(
            provided_key, entry.heartbeat_key):
        return _problem(401, 'missing or invalid heartbeat_key — '
                             're-register to obtain a current one')

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
        notify_body = (
            f'{entry.name} ({entry.sysop or "unknown sysop"}) at '
            f'{entry.host} has verified their contact email and is '
            f'waiting for approval to join the federation registry.')
        notify_admins(
            'msp_join_request',
            title=f'MSP registry: {entry.host} verified, awaiting approval',
            body=notify_body,
            target_url='/admin/registry/')
        # In-app notifications alone only reach a sysop who's already
        # looking at the site -- real gap: this used to be the only
        # signal, so a join request could sit unnoticed indefinitely.
        # Email the actual site contact address too, best-effort (a no-op
        # if SMTP isn't configured -- send_email() checks that itself).
        sysop_email = (current_app.config.get('SYSOP_EMAIL') or '').strip()
        if sysop_email:
            try:
                from ..mailer import send_email
                send_email(
                    sysop_email,
                    f'ANetBBS federation: {entry.host} wants to join',
                    f'{notify_body}\n\n'
                    f'Review at: {request.host_url.rstrip("/")}'
                    f'/admin/registry/')
            except Exception:
                logging.getLogger(__name__).warning(
                    'registry: failed to email sysop about %s', entry.host,
                    exc_info=True)
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
