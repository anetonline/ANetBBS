"""Custom WTForms validators shared across web blueprints.

WTForms' built-in ``Email`` validator delegates to ``email-validator``,
which (as of 2.x) rejects RFC 6761 special-use TLDs like ``.local``,
``.test``, ``.invalid``, ``.example``. BBS sysops legitimately use
internal-only addresses (``admin@anetbbs.local``, FidoNet-style
``user@fidonet.org`` aliases, ``sysop@bbs.lan``) and we don't want
profile/registration forms to reject them. We swap in a permissive
regex check that still catches obvious junk but allows any TLD.
"""
import re

from wtforms.validators import ValidationError


# Same shape as the HTML5 input[type=email] spec, minus the TLD whitelist.
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)+$"
)


class PermissiveEmail:
    """Regex-only email validator. Accepts any RFC-shaped address,
    including ``.local`` and other special-use TLDs."""

    def __init__(self, message=None):
        self.message = message or 'Invalid email address.'

    def __call__(self, form, field):
        value = (field.data or '').strip()
        if not value:
            return
        if not _EMAIL_RE.match(value):
            raise ValidationError(self.message)
