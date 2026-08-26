"""ANetBBS Pulse -- read-only, mobile-first sysop status dashboard."""
import os
import shutil
import time
from datetime import datetime, timedelta

from flask import (Blueprint, Response, current_app, jsonify, render_template,
                   send_from_directory)
from flask_login import login_required

from ..models import (Board, CallerLog, NodeActivity, Post, User,
                      UserSession, db)
from ..version import VERSION
from .access_control import require_admin_or_403 as _require_admin


pulse_bp = Blueprint('pulse', __name__, url_prefix='/admin/pulse')


def _iso(value):
    return value.isoformat() + 'Z' if value else None


def _installation_root():
    return (current_app.config.get('INSTALL_DIR') or
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))))


def _disk_snapshot():
    try:
        usage = shutil.disk_usage(_installation_root())
        return {
            'total': usage.total,
            'used': usage.used,
            'free': usage.free,
            'percent': round((usage.used / usage.total) * 100, 1)
                       if usage.total else 0,
        }
    except OSError:
        return None


def _host_uptime():
    try:
        with open('/proc/uptime', 'r', encoding='ascii') as handle:
            return int(float(handle.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


def _activity_snapshot(now):
    """Every query here is independently guarded. A single unhealthy or
    mid-migration table must degrade that one section to an empty/zero
    result, not take down the whole status endpoint -- a monitoring
    dashboard that 500s the moment one table hiccups defeats its own
    purpose."""
    day_ago = now - timedelta(days=1)
    five_min_ago = now - timedelta(minutes=5)

    recent_callers = []
    try:
        callers = (CallerLog.query.order_by(CallerLog.started_at.desc())
                   .limit(8).all())
        recent_callers = [{
            'username': row.username or '?',
            'protocol': row.service or '',
            'started_at': _iso(row.started_at),
            'duration_seconds': row.duration_seconds,
        } for row in callers]
    except Exception:
        db.session.rollback()

    live_nodes = []
    try:
        nodes = (NodeActivity.query
                 .filter(NodeActivity.last_seen >= five_min_ago)
                 .order_by(NodeActivity.slot.asc()).all())
        live_nodes = [{
            'slot': row.slot,
            'username': row.username or '?',
            'protocol': row.protocol or '',
            'page': row.page or '',
            'action': row.action or '',
            'started_at': _iso(row.started_at),
            'last_seen': _iso(row.last_seen),
        } for row in nodes]
    except Exception:
        db.session.rollback()

    web_users = []
    try:
        web_sessions = (UserSession.query
                        .filter(UserSession.last_seen >= five_min_ago).all())
        for row in web_sessions:
            user = db.session.get(User, row.user_id) if row.user_id else None
            web_users.append({
                'username': user.username if user else '?',
                'page': row.page or '',
                'last_seen': _iso(row.last_seen),
            })
    except Exception:
        db.session.rollback()

    totals = {
        'users': 0,
        'boards': 0,
        'posts': 0,
        'new_users_24h': 0,
        'new_posts_24h': 0,
        'terminal_online': len(live_nodes),
        'web_online': len(web_users),
    }
    try:
        totals['users'] = User.query.count()
        totals['boards'] = Board.query.count()
        totals['posts'] = Post.query.count()
        totals['new_users_24h'] = User.query.filter(
            User.created_at >= day_ago).count()
        totals['new_posts_24h'] = Post.query.filter(
            Post.created_at >= day_ago).count()
    except Exception:
        db.session.rollback()

    return {
        'totals': totals,
        'nodes': live_nodes,
        'web_users': web_users,
        'recent_callers': recent_callers,
    }


def _service_snapshot():
    # Reuse the Service Control Center's runtime-aware systemd/Docker and
    # listener probes. Pulse never imports or calls any state-changing helper.
    from .control import KNOWN_UNITS, _enrich_unit
    services = [_enrich_unit(current_app.config, item)
                for item in KNOWN_UNITS]
    for service in services:
        # Keep the phone payload compact and omit descriptive/control metadata.
        service.pop('desc', None)
        service.pop('icon', None)
    return services


@pulse_bp.after_request
def _private_response(response):
    """Authenticated status must never be retained by shared caches."""
    response.headers['Cache-Control'] = 'private, no-store, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@pulse_bp.route('/')
@login_required
def index():
    _require_admin()
    return render_template('admin/pulse.html')


@pulse_bp.route('/api/status')
@login_required
def status():
    _require_admin()
    now = datetime.utcnow()
    services = _service_snapshot()
    activity = _activity_snapshot(now)

    metrics = {'available': False, 'units': {}}
    try:
        from .metrics import latest_per_unit
        metrics = {'available': True, 'units': latest_per_unit()}
    except Exception:  # psutil absent or sampler not started
        pass

    unhealthy = sum(
        1 for service in services
        if not service['is_running'] or
        service['listener_summary'] in ('partial', 'all_down'))

    return jsonify({
        'ok': unhealthy == 0,
        'updated': _iso(now),
        'version': VERSION,
        'bbs_name': current_app.config.get('BBS_NAME', 'ANetBBS'),
        'host': {
            'uptime_seconds': _host_uptime(),
            'disk': _disk_snapshot(),
        },
        'summary': {
            'services_total': len(services),
            'services_healthy': len(services) - unhealthy,
            'services_unhealthy': unhealthy,
        },
        'services': services,
        'metrics': metrics,
        **activity,
    })


@pulse_bp.route('/manifest.webmanifest')
@login_required
def manifest():
    _require_admin()
    return jsonify({
        'name': f"{current_app.config.get('BBS_NAME', 'ANetBBS')} Pulse",
        'short_name': 'ANet Pulse',
        'description': 'Read-only mobile status for ANetBBS',
        'start_url': '/admin/pulse/',
        'scope': '/admin/pulse/',
        'display': 'standalone',
        'background_color': '#090d14',
        'theme_color': '#00d4ff',
        'icons': [{
            'src': '/static/pulse/icon.svg',
            'sizes': 'any',
            'type': 'image/svg+xml',
            'purpose': 'any maskable',
        }, {
            'src': '/static/pulse/apple-touch-icon.png',
            'sizes': '180x180',
            'type': 'image/png',
        }],
    })


@pulse_bp.route('/sw.js')
@login_required
def service_worker():
    _require_admin()
    response = send_from_directory(
        os.path.join(current_app.static_folder, 'pulse'),
        'sw.js', mimetype='application/javascript')
    response.headers['Service-Worker-Allowed'] = '/admin/pulse/'
    return response
