"""Matching a name or FTN address on a message to one local account.

One resolver for every place that turns a free-text recipient into a
user id: FTN netmail import, private QWK mail import, reply
notifications and the one-time upgrade pass over older private rows.

Rules, in order:

1. An FTN address that is exactly one user's AKA.
2. An exact username (usernames are unique and fixed at signup), unless
   another account uses the same string as its display or real name.
3. An exact display name or real name, but only when exactly one
   account uses that string in any of those fields, and the name isn't
   one of the generic role names below.

Anything else is "no local match": the caller falls back to its own
default (the network's default recipient for FTN netmail, the sysop's
review queue otherwise). Comparisons are case-insensitive and exact;
no LIKE patterns.
"""
from sqlalchemy import func, or_

from ..models import EchoArea, User, UserAka, db

# Generic role names that a display or real name never resolves to.
# Mail addressed to these goes to the configured default recipient or
# the sysop's review queue instead.
RESERVED_NAMES = frozenset({
    'all', 'sysop', 'sys op', 'co-sysop', 'cosysop', 'admin',
    'administrator', 'root', 'postmaster', 'moderator', 'staff',
    'support', 'areafix', 'area fix', 'areamgr', 'filefix', 'filemgr',
    'ping', 'echomail', 'netmail', 'robot', 'system', 'everyone',
})

_ASCII_LOWER = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ',
                             'abcdefghijklmnopqrstuvwxyz')


def fold(value):
    """Case-fold the same way SQLite's lower() does (ASCII only)."""
    return (value or '').strip().translate(_ASCII_LOWER)


def is_private_area(area):
    return area is not None and (area.tag or '').strip().upper() == 'NETMAIL'


def _session(session):
    return session if session is not None else db.session


def _eq(column, value):
    return func.lower(column) == func.lower(value)


def resolve_local_user_id(name, address=None, session=None):
    """Return the id of the one local account `name`/`address` refers
    to, or None."""
    s = _session(session)
    address = (address or '').strip()
    if address:
        ids = {r[0] for r in s.query(UserAka.user_id)
               .filter(UserAka.address == address).all()}
        if len(ids) == 1:
            return ids.pop()
    name = (name or '').strip()
    if not name or fold(name) == 'all':
        return None
    by_username = {r[0] for r in s.query(User.id)
                   .filter(_eq(User.username, name)).all()}
    holders = {r[0] for r in s.query(User.id)
               .filter(or_(_eq(User.display_name, name),
                           _eq(User.real_name, name))).all()}
    if len(by_username) == 1:
        # A username wins only if no other account uses the same string
        # as its display or real name (a collision that predates the
        # signup and profile checks); otherwise nobody gets it.
        return next(iter(by_username)) if holders <= by_username else None
    if by_username or fold(name) in RESERVED_NAMES:
        return None
    if len(holders) == 1:
        return holders.pop()
    return None


def resolve_local_user(name, address=None, session=None):
    uid = resolve_local_user_id(name, address, session=session)
    if uid is None:
        return None
    return _session(session).get(User, uid)


def name_conflict(name, user_id, session=None):
    """True if `name` can't be used as a display or real name by
    `user_id`: it's a reserved role name, or another account already
    uses it as a username, display name or real name."""
    folded = fold(name)
    if not folded:
        return False
    if folded in RESERVED_NAMES:
        return True
    s = _session(session)
    q = s.query(User.id).filter(or_(_eq(User.username, name.strip()),
                                    _eq(User.display_name, name.strip()),
                                    _eq(User.real_name, name.strip())))
    if user_id is not None:
        q = q.filter(User.id != user_id)
    return q.first() is not None


def _assign_echomail(msg, session):
    if (msg.direction or 'inbound') == 'inbound':
        if msg.to_user_id is None:
            msg.to_user_id = resolve_local_user_id(
                msg.to_name, msg.to_address, session=session)
    elif msg.from_user_id is None:
        # Compose paths set from_user_id themselves; this covers any
        # other writer of a locally-authored private row.
        msg.from_user_id = resolve_local_user_id(msg.from_name,
                                                 session=session)


def assign_private_parties(session, msg):
    """Called once for each new EchomailMessage/NetmailMessage before
    it's written, and by the upgrade pass for older rows.

    Private QWK rows: an imported row gets its local recipient; a row
    written here gets its local author. Its other party is remote, so no
    local account is linked for it. FTN netmail keeps whatever the
    import path linked (it uses the same resolver plus the network's
    default recipient); an inbound row still unlinked is resolved here.
    """
    from ..models import NetmailMessage
    if isinstance(msg, NetmailMessage):
        if msg.direction == 'inbound' and msg.to_user_id is None:
            msg.to_user_id = resolve_local_user_id(
                msg.to_name, msg.to_address, session=session)
        msg.owner_resolved = True
        return
    area = msg.area
    if area is None and msg.area_id is not None:
        area = session.get(EchoArea, msg.area_id)
    if is_private_area(area):
        _assign_echomail(msg, session)
    msg.owner_resolved = True


NAME_IN_USE_MESSAGE = ('That name is already in use or reserved. '
                       'Please choose another.')


def identity_name_error(user, attr, new_value, session=None):
    """The one check every profile editor (web, ANSI, PETSCII) applies
    before saving a display or real name. Returns an error message, or
    None when the value may be saved: blank, unchanged (case-insensitive),
    set by an admin on their own account, or not in use by anyone else."""
    if attr not in ('display_name', 'real_name'):
        return None
    value = (new_value or '').strip()
    if not value or fold(value) == fold(getattr(user, attr, None)):
        return None
    if getattr(user, 'is_admin', False):
        return None
    if name_conflict(value, getattr(user, 'id', None), session=session):
        return NAME_IN_USE_MESSAGE
    return None


# ------------------------------------------------ older rows (upgrade)
#
# Rows stored before this upgrade carry names only. Names can have been
# taken over since (a new account with that username, a changed display
# name), so the upgrade pass links a row only when it is certain, and
# otherwise leaves it admin-only and queues it for sysop review. The
# same classifier backs tools/audit_private_message_ownership.py, which
# previews exactly what the upgrade will do, read-only.

ASSIGNED = ('assigned_username', 'assigned_aka', 'link_kept')
REVIEW_REASONS = ('inferred_name', 'ambiguous', 'reserved', 'account_newer',
                  'stale_link', 'inferred_link', 'account_deleted')


class AccountSnapshot:
    """Plain-data view of every account, for classifying older rows.

    users: iterable of (id, username, display_name, real_name, created_at)
    akas:  iterable of (user_id, address, created_at)
    """

    def __init__(self, users, akas=(), admin_ids=()):
        self.users = {}
        self.by_username = {}
        self.by_other = {}
        for uid, username, display_name, real_name, created_at in users:
            self.users[uid] = (username, created_at)
            if fold(username):
                self.by_username.setdefault(fold(username), set()).add(uid)
            for n in (display_name, real_name):
                if fold(n):
                    self.by_other.setdefault(fold(n), set()).add(uid)
        self.akas = {}
        for uid, address, created_at in akas:
            if address:
                self.akas.setdefault(address.strip(), []).append((uid, created_at))
        self.admin_ids = set(admin_ids)

    def created(self, uid):
        u = self.users.get(uid)
        return u[1] if u else None

    @classmethod
    def load(cls, session=None):
        s = _session(session)
        users = s.query(User.id, User.username, User.display_name,
                        User.real_name, User.created_at).all()
        akas = s.query(UserAka.user_id, UserAka.address, UserAka.created_at).all()
        admins = [r[0] for r in s.query(User.id).filter(User.is_admin.is_(True)).all()]
        return cls(users, akas, admins)


def _older(created, stored_at):
    return created is not None and stored_at is not None and created <= stored_at


def classify_history(snap, name, address, stored_at):
    """Decide the local party of an older row from its name/address.

    Returns (user_id, reason, suggested_user_id). user_id is set only
    for a certain match: an address held by exactly one account, or an
    exact username that no other account uses in any name field, isn't a
    reserved role name, and belonged to an account that already existed
    when the row was stored. Everything else gets user_id None.
    """
    address = (address or '').strip()
    if address and address in snap.akas:
        holders = {uid for uid, _c in snap.akas[address]}
        if len(holders) != 1:
            return None, 'ambiguous', None
        uid = holders.pop()
        first = min((c for u, c in snap.akas[address] if c is not None), default=None)
        if uid in snap.users and _older(first, stored_at) \
                and _older(snap.created(uid), stored_at):
            return uid, 'assigned_aka', None
        return None, 'account_newer', uid
    f = fold(name)
    if not f or f == 'all':
        return None, 'unresolved', None
    by_username = snap.by_username.get(f, set())
    candidates = set(by_username) | snap.by_other.get(f, set())
    if not candidates:
        return None, 'unresolved', None
    single = next(iter(candidates)) if len(candidates) == 1 else None
    if f in RESERVED_NAMES:
        return None, 'reserved', single
    if single is None:
        return None, 'ambiguous', None
    if not _older(snap.created(single), stored_at):
        return None, 'account_newer', single
    if by_username:
        return single, 'assigned_username', None
    return None, 'inferred_name', single


def check_existing_link(snap, uid, name, address, stored_at, party='to'):
    """Decide whether an id already stored on an older FTN netmail row
    still holds. Returns (keep, reason, suggested_user_id).

    Sender links (party 'from') were only ever written from the
    logged-in account at compose time, never from a name, so they are
    kept whenever the account exists and predates the message, whatever
    name (username, display or real name) the message was signed with.
    Recipient links get the stricter checks below.

    Kept when the account still exists, already existed when the row was
    stored, and the link is certain: the account is an admin (who sees
    all mail anyway), holds the row's address alone, or has the row's
    name as its username with no other account using that name and the
    name not a reserved role name. A link to a missing or newer account
    is stale (no suggestion); any other link was inferred, typically
    from a display name, and is queued with the old account suggested.
    """
    if uid not in snap.users or not _older(snap.created(uid), stored_at):
        return False, 'stale_link', None
    if party == 'from':
        return True, 'link_kept', None
    if uid in snap.admin_ids:
        return True, 'link_kept', None
    if {u for u, _c in snap.akas.get((address or '').strip(), [])} == {uid}:
        return True, 'link_kept', None
    f = fold(name)
    others = (snap.by_username.get(f, set()) | snap.by_other.get(f, set())) - {uid}
    if f and f == fold(snap.users[uid][0]) and f not in RESERVED_NAMES and not others:
        return True, 'link_kept', None
    return False, 'inferred_link', uid


def _stored_at(m):
    return getattr(m, 'imported_at', None) or getattr(m, 'created_at', None) \
        or getattr(m, 'received_at', None)


def plan_history_row(snap, kind, m):
    """What the upgrade pass does with one older row, without changing it.

    Returns a list of (party, new_user_id, reason, suggested, previous).
    kind is 'qwk' (private QWK row) or 'netmail'."""
    stored = _stored_at(m)
    out = []
    inbound = (m.direction or 'inbound') == 'inbound'
    if kind == 'qwk':
        if inbound:
            uid, reason, sug = classify_history(snap, m.to_name, m.to_address, stored)
            out.append(('to', uid, reason, sug, None))
        else:
            uid, reason, sug = classify_history(snap, m.from_name, None, stored)
            out.append(('from', uid, reason, sug, None))
        return out
    for party in ('to', 'from'):
        current = getattr(m, f'{party}_user_id')
        if current is not None:
            keep, reason, sug = check_existing_link(
                snap, current, getattr(m, f'{party}_name'),
                getattr(m, f'{party}_address'), stored, party=party)
            out.append((party, current if keep else None, reason, sug, current))
        elif party == 'to' and inbound:
            uid, reason, sug = classify_history(snap, m.to_name, m.to_address, stored)
            out.append(('to', uid, reason, sug, None))
    return out


def _review(kind, message_id, party, reason, suggested, previous):
    from ..models import PrivateOwnershipReview
    return PrivateOwnershipReview(
        message_kind=kind, message_id=message_id, party=party, reason=reason,
        suggested_user_id=suggested, previous_user_id=previous, status='pending')


def backfill_private_parties(batch=500):
    """One-time, conservative pass over private rows stored before this
    upgrade (see classify_history). Certain matches are linked; doubtful
    ones stay admin-only with a review entry; nothing is ever linked from
    a display or real name alone. Each row is processed once. Returns
    {'rows': n, 'assigned': n, 'review': n}."""
    from ..models import EchomailMessage, NetmailMessage
    stats = {'rows': 0, 'assigned': 0, 'review': 0}
    snap = None
    area_ids = [a.id for a in EchoArea.query.all() if is_private_area(a)]
    targets = [('netmail', NetmailMessage, None)]
    if area_ids:
        targets.insert(0, ('qwk', EchomailMessage, area_ids))
    for kind, model, ids in targets:
        while True:
            q = model.query.filter(model.owner_resolved.is_(None))
            if ids is not None:
                q = q.filter(model.area_id.in_(ids))
            rows = q.order_by(model.id).limit(batch).all()
            if not rows:
                break
            if snap is None:
                snap = AccountSnapshot.load()
            for m in rows:
                for party, uid, reason, sug, prev in plan_history_row(snap, kind, m):
                    setattr(m, f'{party}_user_id', uid)
                    if reason in ASSIGNED:
                        stats['assigned'] += 1
                    elif reason in REVIEW_REASONS:
                        db.session.add(_review(kind, m.id, party, reason, sug, prev))
                        stats['review'] += 1
                m.owner_resolved = True
            db.session.commit()
            stats['rows'] += len(rows)
    return stats


# ----------------------------------------------------- account deletion

def detach_user_from_messages(conn, user_id):
    """Unlink an account from every private-message row before the
    account is deleted, whichever deletion path is used. Messages are
    kept (admin-only) and queued for review, so an account that later
    reuses the id can't inherit them. Uses Core statements on `conn` so
    it runs inside the caller's transaction, including from a mapper
    event. Returns the number of links removed."""
    from datetime import datetime
    from sqlalchemy import false, insert, select, update
    from ..models import EchomailMessage, NetmailMessage, PrivateOwnershipReview
    if user_id is None:
        return 0
    em = EchomailMessage.__table__
    nm = NetmailMessage.__table__
    rv = PrivateOwnershipReview.__table__
    private_ids = [r[0] for r in conn.execute(
        select(EchoArea.__table__.c.id).where(
            func.upper(func.trim(EchoArea.__table__.c.tag)) == 'NETMAIL'))]
    now = datetime.utcnow()
    removed = 0
    for table, kind, reviewed in (
            (em, 'qwk', em.c.area_id.in_(private_ids) if private_ids else false()),
            (nm, 'netmail', None)):
        for party in ('to', 'from'):
            col = table.c[f'{party}_user_id']
            cond = col == user_id
            q = select(table.c.id).where(cond)
            if reviewed is not None:
                q = q.where(reviewed)
            ids = [r[0] for r in conn.execute(q)]
            if ids:
                conn.execute(insert(rv), [
                    {'message_kind': kind, 'message_id': i, 'party': party,
                     'reason': 'account_deleted', 'suggested_user_id': None,
                     'previous_user_id': user_id, 'status': 'pending',
                     'created_at': now} for i in ids])
            res = conn.execute(update(table).where(cond).values({col.name: None}))
            removed += max(0, res.rowcount or 0)
    conn.execute(update(rv).where(rv.c.suggested_user_id == user_id)
                 .values(suggested_user_id=None))
    return removed


# Columns that account deletion unlinks (above) instead of deleting rows.
PRESERVED_USER_REFS = frozenset({
    ('echomail_messages', 'from_user_id'), ('echomail_messages', 'to_user_id'),
    ('netmail_messages', 'from_user_id'), ('netmail_messages', 'to_user_id'),
})


__all__ = ['RESERVED_NAMES', 'fold', 'is_private_area',
           'resolve_local_user_id', 'resolve_local_user', 'name_conflict',
           'identity_name_error', 'assign_private_parties',
           'AccountSnapshot', 'classify_history', 'check_existing_link',
           'plan_history_row', 'backfill_private_parties',
           'detach_user_from_messages', 'PRESERVED_USER_REFS']
