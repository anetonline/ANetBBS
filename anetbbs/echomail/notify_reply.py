# anetbbs/echomail/notify_reply.py
"""
Notify a local user when an inbound echomail (FidoNet) or QWK-network
conference message arrives addressed TO their handle in a public area --
the same idea as Synchronet's "so-and-so replied to you in FidoNet
(area name)" notice. Both network types land in the same EchomailMessage
table/import pipeline (see anetbbs/echomail/poller.py's _import_message),
so one helper covers both.

Only fires for a genuine TO name, never for the generic "everyone in this
area" convention (to_name == 'All' or a close alias) -- that's the vast
majority of echomail/QWK traffic and firing on it would notify nobody in
particular for every single message in every area.
"""
import logging

from .routing import resolve_user_by_name_or_address

logger = logging.getLogger(__name__)

# Common "this isn't really addressed to a specific person" conventions
# seen across FTN/QWK traffic. Matched case-insensitively against to_name
# BEFORE trying to resolve a real user, so a coincidental username match
# (e.g. someone actually registered as "Sysop") can't misfire on ordinary
# broadcast-style mail either.
_NOT_A_PERSON = {'all', 'everyone', 'everybody', 'anyone', 'all users', 'sysop'}


def maybe_notify_recipient(msg, area, network):
    """If `msg` (an already-flushed EchomailMessage, msg.id populated) is
    addressed to a real local user's handle, notify them. Best-effort --
    swallows its own errors, same as every other notify() call site."""
    to_name = (msg.to_name or '').strip()
    if not to_name or to_name.lower() in _NOT_A_PERSON:
        return
    try:
        user = resolve_user_by_name_or_address(to_name, msg.to_address)
        if user is None:
            return
        from ..features.notify import notify
        notify(user.id, 'echomail_reply',
               title=f'{msg.from_name} wrote to you',
               body=f'in {network.name} ({area.name})',
               target_url=f'/echomail/{area.id}/{msg.id}')
    except Exception:
        logger.debug('echomail reply-notify failed for msg %s', getattr(msg, 'id', None),
                     exc_info=True)
