# anetbbs/web/network_join.py
"""
Public "apply to join this network" page.

GET/POST /join/               -- the application form
GET      /join/infopack.zip   -- download the sysop-uploaded infopack

Only exists on the designated hub install (REGISTRY_MODE_ENABLED) AND
only once the sysop has enabled + configured it via Hub Management's
"Join Form" tab (anetbbs/web/hub_admin.py) -- same _require_hub_mode()
gating convention as anetbbs/web/qwk_hub.py and anetbbs/web/registry.py.

This is a real browser form (not a machine/API client like qwk_hub.py
or registry.py), so it uses normal Flask-WTF CSRF tokens rather than
being csrf-exempted -- anonymous Flask sessions still get a session
cookie for CSRF token storage fine.
"""
import logging
import os
from datetime import datetime, timedelta

from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, abort, current_app, send_from_directory)
from flask_wtf import FlaskForm
from wtforms import (StringField, TextAreaField, BooleanField, SubmitField)
from wtforms.validators import DataRequired, Length, Optional, Email

from ..models import db, NetworkJoinConfig, NetworkJoinRequest

logger = logging.getLogger(__name__)

network_join_bp = Blueprint('network_join', __name__, url_prefix='/join')


def _require_hub_mode():
    if not current_app.config.get('REGISTRY_MODE_ENABLED'):
        abort(404)


def _peer_ip():
    fwd = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    return fwd or (request.remote_addr or '')


class JoinApplicationForm(FlaskForm):
    name = StringField('Your Name', validators=[DataRequired(), Length(max=100)])
    location = StringField('Location', validators=[Optional(), Length(max=150)])
    bbs_name = StringField('BBS / System Name', validators=[DataRequired(), Length(max=100)])
    bbs_software = StringField('BBS Software', validators=[Optional(), Length(max=100)])
    bbs_os = StringField('Operating System', validators=[Optional(), Length(max=100)])
    telnet_address = StringField('Telnet Address', validators=[Optional(), Length(max=200)])
    website_url = StringField('Website URL', validators=[Optional(), Length(max=400)])
    email = StringField('Email Address', validators=[DataRequired(), Email(), Length(max=200)])

    binkp_ftn_address = StringField(
        'BinkP Address (leave blank if QWK/offline-mail only)',
        validators=[Optional(), Length(max=60)])
    binkp_crash_or_hold = StringField(
        'Crash or Hold', validators=[Optional(), Length(max=10)])

    qwk_packet_id = StringField(
        'QWK Packet ID (leave blank if BinkP only)',
        validators=[Optional(), Length(max=8)])

    notes = TextAreaField('Notes', validators=[Optional(), Length(max=2000)])

    rules_ack = BooleanField(
        'I have read and agree to the rules above',
        validators=[DataRequired(message='You must confirm you read the rules.')])

    submit = SubmitField('Submit Application')

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators=extra_validators)
        if not (self.binkp_ftn_address.data or '').strip() and \
           not (self.qwk_packet_id.data or '').strip():
            self.binkp_ftn_address.errors.append(
                'Fill in either a BinkP address or a QWK packet ID (or both).')
            ok = False
        return ok


def _ratelimit_check(source_ip):
    """Coarse rate limit -- mirrors anetbbs/web/registry.py's
    _ratelimit_check(): a floor per IP plus an hourly cap, both keyed
    off NetworkJoinRequest's own created_at/ip_address columns."""
    if not source_ip:
        return None
    now = datetime.utcnow()
    recent = (NetworkJoinRequest.query
             .filter(NetworkJoinRequest.ip_address == source_ip)
             .filter(NetworkJoinRequest.created_at > now - timedelta(seconds=30))
             .first())
    if recent:
        return 'You already submitted an application recently -- please wait a bit.'
    hour_count = (NetworkJoinRequest.query
                 .filter(NetworkJoinRequest.ip_address == source_ip)
                 .filter(NetworkJoinRequest.created_at > now - timedelta(hours=1))
                 .count())
    if hour_count >= 10:
        return 'Too many applications from this IP -- please wait an hour.'
    return None


@network_join_bp.route('/', methods=['GET', 'POST'])
def apply():
    _require_hub_mode()
    cfg = NetworkJoinConfig.get()
    if not cfg.enabled:
        abort(404)

    form = JoinApplicationForm()
    if form.validate_on_submit():
        rl = _ratelimit_check(_peer_ip())
        if rl:
            flash(rl, 'danger')
            return render_template('network_join/apply.html', cfg=cfg, form=form)

        req = NetworkJoinRequest(
            name=form.name.data.strip(),
            location=(form.location.data or '').strip() or None,
            bbs_name=form.bbs_name.data.strip(),
            bbs_software=(form.bbs_software.data or '').strip() or None,
            bbs_os=(form.bbs_os.data or '').strip() or None,
            telnet_address=(form.telnet_address.data or '').strip() or None,
            website_url=(form.website_url.data or '').strip() or None,
            email=form.email.data.strip(),
            binkp_ftn_address=(form.binkp_ftn_address.data or '').strip() or None,
            binkp_crash_or_hold=(form.binkp_crash_or_hold.data or '').strip() or None,
            qwk_packet_id=((form.qwk_packet_id.data or '').strip().upper() or None),
            notes=(form.notes.data or '').strip() or None,
            rules_ack=bool(form.rules_ack.data),
            ip_address=_peer_ip(),
            user_agent=(request.headers.get('User-Agent') or '')[:255],
        )
        db.session.add(req)
        db.session.commit()

        from ..features.notify import notify_admins
        notify_admins(
            'network_join_app',
            title=f'Network join application: {req.bbs_name}',
            body=f'{req.bbs_name} ({req.name}) applied to join '
                 f'{cfg.network_name or "the network"}. '
                 f'Review under Admin -> Echomail -> Hub -> Join Requests.',
            target_url='/admin/echomail/hub/join/requests')

        return render_template('network_join/submitted.html', cfg=cfg, req=req)

    return render_template('network_join/apply.html', cfg=cfg, form=form)


@network_join_bp.route('/infopack.zip')
def download_infopack():
    _require_hub_mode()
    cfg = NetworkJoinConfig.get()
    if not cfg.enabled or not cfg.infopack_filename:
        abort(404)
    join_dir = current_app.config.get(
        'NETWORK_JOIN_DIR',
        os.path.join(current_app.config['DATA_DIR'], 'network_join'))
    return send_from_directory(
        join_dir, cfg.infopack_filename, as_attachment=True,
        download_name=cfg.infopack_original_filename or cfg.infopack_filename)
