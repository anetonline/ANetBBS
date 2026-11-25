"""Who may see a private message.

Every private-message view asks this module: inbox, sent and drafts
lists, read, reply, delete, bookmarks, telegrams, search, threads, the
terminal readers and offline (QWK) packets. The SQL filters and the
per-row checks sit side by side so they can't disagree.

Rules:

* Ownership is by account id only: a message belongs to its linked
  local sender and/or linked local recipient. Names are never compared
  at read time. Ids are assigned once, when a message is stored, by
  ``anetbbs/messaging/recipients.py``.
* FTN netmail (NetmailMessage) additionally belongs to the holder of
  its own-side FTN address: the recipient address for inbound mail and
  the sender address for outbound mail. Addresses are a user's AKAs;
  admins also hold each active network's node address (the sysop
  inbox and review queue).
* Private QWK mail lives in EchomailMessage rows of an area tagged
  NETMAIL. Other areas are public to anyone the area's own access
  rules admit; those rules are checked separately by the callers.
* Admins see every private QWK row (unchanged).
"""
from flask import g, has_request_context
from sqlalchemy import false, or_, true

from ..models import EchoArea, EchomailMessage, EchomailNetwork, \
    NetmailMessage, UserAka, db


def _request_cache(key):
    if has_request_context():
        return g.setdefault(key, {})
    return None


def _uid(user):
    uid = getattr(user, 'id', None) if user is not None else None
    if uid is None or (hasattr(user, 'is_authenticated')
                       and not user.is_authenticated):
        return None
    return uid


def _is_admin(user):
    return bool(_uid(user) is not None and getattr(user, 'is_admin', False))


# ---------------------------------------------------------------- netmail

def user_netmail_addresses(user):
    """FTN addresses that identify `user` as a netmail party: their own
    AKAs, plus each active network's node address for admins. Cached
    per request."""
    uid = _uid(user)
    if uid is None:
        return []
    cache = _request_cache('_netmail_addrs')
    if cache is not None and uid in cache:
        return list(cache[uid])
    addrs = [a.address for a in UserAka.query.filter_by(user_id=uid).all()
             if a.address]
    if _is_admin(user):
        for net in EchomailNetwork.query.filter_by(is_active=True).all():
            if net.our_address and net.our_address not in addrs:
                addrs.append(net.our_address)
    if cache is not None:
        cache[uid] = list(addrs)
    return addrs


def netmail_inbound_owner_clause(user, addrs=None):
    """SQL filter for inbound NetmailMessage rows addressed to `user`.
    Combine with ``NetmailMessage.direction == 'inbound'``."""
    uid = _uid(user)
    if uid is None:
        return false()
    if addrs is None:
        addrs = user_netmail_addresses(user)
    clauses = [NetmailMessage.to_user_id == uid]
    if addrs:
        clauses.append(NetmailMessage.to_address.in_(addrs))
    return or_(*clauses)


def netmail_outbound_owner_clause(user, addrs=None):
    """SQL filter for outbound NetmailMessage rows sent by `user`.
    Combine with ``NetmailMessage.direction == 'outbound'``."""
    uid = _uid(user)
    if uid is None:
        return false()
    if addrs is None:
        addrs = user_netmail_addresses(user)
    clauses = [NetmailMessage.from_user_id == uid]
    if addrs:
        clauses.append(NetmailMessage.from_address.in_(addrs))
    return or_(*clauses)


def netmail_visible(user, m, addrs=None):
    """Row-level check, equivalent to the two SQL filters above."""
    uid = _uid(user)
    if m is None or uid is None:
        return False
    if addrs is None:
        addrs = user_netmail_addresses(user)
    if m.direction == 'inbound':
        return m.to_user_id == uid or (bool(m.to_address)
                                       and m.to_address in addrs)
    if m.direction == 'outbound':
        return m.from_user_id == uid or (bool(m.from_address)
                                         and m.from_address in addrs)
    return False


# ---------------------------------------------------------------- PMs

def pm_visible(user, pm):
    """A PM belongs to its sender and its recipient (account ids only)."""
    uid = _uid(user)
    if pm is None or uid is None:
        return False
    # A side that deleted its copy no longer sees it (fix F-2).
    if uid == pm.recipient_id and not pm.is_deleted_recipient:
        return True
    if uid == pm.sender_id and not pm.is_deleted_sender:
        return True
    return False


# ------------------------------------------- private QWK (NETMAIL area)

def private_area_ids():
    """Ids of every EchoArea that holds private mail. Cached per request."""
    cache = _request_cache('_private_area_ids')
    if cache is not None and 'ids' in cache:
        return cache['ids']
    ids = frozenset(
        r[0] for r in db.session.query(EchoArea.id)
        .filter(db.func.upper(db.func.trim(EchoArea.tag)) == 'NETMAIL').all())
    if cache is not None:
        cache['ids'] = ids
    return ids


def private_echomail_owner_clause(user_id):
    """SQL filter: private QWK rows linked to `user_id`."""
    if user_id is None:
        return false()
    return or_(EchomailMessage.from_user_id == user_id,
               EchomailMessage.to_user_id == user_id)


def echomail_privacy_clause(user_id, is_admin=False):
    """SQL filter for any EchomailMessage query a user can see: rows in
    public areas, plus private rows linked to this user. Admins: all.
    Area access levels are still the caller's job."""
    if is_admin and user_id is not None:
        return true()
    ids = private_area_ids()
    if not ids:
        return true()
    public = EchomailMessage.area_id.notin_(list(ids))
    if user_id is None:
        return public
    return or_(public, private_echomail_owner_clause(user_id))


def echomail_private_visible(user_id, is_admin, msg):
    """Row-level check, equivalent to echomail_privacy_clause()."""
    if msg is None:
        return False
    if is_admin and user_id is not None:
        return True
    if msg.area_id not in private_area_ids():
        return True
    return user_id is not None and user_id in (msg.from_user_id,
                                               msg.to_user_id)


def qwk_netmail_visible(user, msg):
    """Ownership of a private QWK row for a web user object (admin
    handling is the caller's)."""
    uid = _uid(user)
    return (msg is not None and uid is not None
            and uid in (msg.from_user_id, msg.to_user_id))


__all__ = [
    'user_netmail_addresses', 'netmail_inbound_owner_clause',
    'netmail_outbound_owner_clause', 'netmail_visible', 'pm_visible',
    'private_area_ids', 'private_echomail_owner_clause',
    'echomail_privacy_clause', 'echomail_private_visible',
    'qwk_netmail_visible',
]
