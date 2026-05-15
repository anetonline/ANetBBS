# anetbbs/web/contacts.py
"""
Address book — user-managed contacts (FTN + email).

Routes:
    GET  /contacts/                list contacts (most-recently-used first)
    POST /contacts/                add contact
    POST /contacts/<id>/delete     delete contact
    POST /contacts/<id>/favorite   toggle favorite flag
    GET  /contacts/json            JSON list (for autocomplete in netmail compose)
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user

from ..models import db, AddressBookEntry
from ..echomail.routing import parse_address

contacts_bp = Blueprint('contacts', __name__, url_prefix='/contacts')


@contacts_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        ftn = (request.form.get('ftn_address') or '').strip()
        email = (request.form.get('email') or '').strip()
        notes = (request.form.get('notes') or '').strip()
        crashmail = bool(request.form.get('crashmail_default'))

        if not name:
            flash('Name is required.', 'danger')
        elif ftn and not parse_address(ftn):
            flash(f'Invalid FTN address: {ftn!r}', 'danger')
        elif not (ftn or email):
            flash('Provide at least an FTN address or an email.', 'danger')
        else:
            entry = AddressBookEntry(
                user_id=current_user.id,
                name=name, ftn_address=ftn or None,
                email=email or None, notes=notes or None,
                crashmail_default=crashmail,
            )
            db.session.add(entry)
            db.session.commit()
            flash(f'Added contact {name}.', 'success')
        return redirect(url_for('contacts.index'))

    entries = (AddressBookEntry.query
               .filter_by(user_id=current_user.id)
               .order_by(AddressBookEntry.favorite.desc(),
                         AddressBookEntry.name)
               .all())
    return render_template('contacts/index.html', entries=entries)


@contacts_bp.route('/<int:entry_id>/delete', methods=['POST'])
@login_required
def delete(entry_id):
    e = AddressBookEntry.query.get_or_404(entry_id)
    if e.user_id != current_user.id:
        abort(403)
    db.session.delete(e)
    db.session.commit()
    flash('Contact removed.', 'success')
    return redirect(url_for('contacts.index'))


@contacts_bp.route('/<int:entry_id>/favorite', methods=['POST'])
@login_required
def toggle_favorite(entry_id):
    e = AddressBookEntry.query.get_or_404(entry_id)
    if e.user_id != current_user.id:
        abort(403)
    e.favorite = not bool(e.favorite)
    db.session.commit()
    return redirect(url_for('contacts.index'))


@contacts_bp.route('/json')
@login_required
def json_list():
    """JSON list for the netmail compose autocomplete dropdown."""
    entries = (AddressBookEntry.query
               .filter_by(user_id=current_user.id)
               .order_by(AddressBookEntry.favorite.desc(),
                         AddressBookEntry.name)
               .all())
    return jsonify([{
        'id': e.id, 'name': e.name,
        'ftn': e.ftn_address or '', 'email': e.email or '',
        'crash': bool(e.crashmail_default),
    } for e in entries])
