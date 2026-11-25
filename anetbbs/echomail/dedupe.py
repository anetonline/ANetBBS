"""Echomail duplicate detection shared by every import path (audit N-7).

FTN dedupes on (MSGID, area). A message imported from a QWK REP upload
carries a MSGID chosen by the uploading node, so a node could pre-plant a
MSGID and make a LATER, different message with that MSGID look like a
duplicate. A REP-sourced row (from_address 'QWK:<node>') therefore only
counts as a duplicate when the content also matches; genuine re-deliveries
of the same message are still suppressed.
"""


def is_duplicate_echomail(msg_id, area_id, subject=None, body=None):
    from ..models import EchomailMessage
    if not msg_id:
        return False
    for row in EchomailMessage.query.filter_by(msg_id=msg_id, area_id=area_id).limit(5):
        if not (row.from_address or '').startswith('QWK:'):
            return True
        if (row.subject or '') == (subject or '') and (row.body or '') == (body or ''):
            return True
    return False
