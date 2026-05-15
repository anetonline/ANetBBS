"""Slug helpers for wiki page URLs.

A slug is the URL-safe form of a page title. We deliberately keep
slugs lowercase and dash-separated so URLs remain Unix-friendly and
case-insensitive across filesystems / databases.

  "Getting Started"   -> "getting-started"
  "BinkP & QWK"       -> "binkp-qwk"
  "MRC / IRC Bridge"  -> "mrc-irc-bridge"
"""
import re
import unicodedata

_SLUG_KEEP = re.compile(r'[^a-z0-9\-]+')
_DASH_RUN = re.compile(r'-+')


def slugify(s, allow_hash=False):
    """Return a slugified version of `s`.

    - Lowercased
    - ASCII-folded (NFKD)
    - Non-alphanumerics collapsed to single dashes
    - Leading / trailing dashes stripped
    - Empty input returns 'untitled'
    - allow_hash=True keeps `#` and what follows (used for anchor refs).
    """
    if s is None:
        return 'untitled'
    s = str(s).strip().lower()
    if not s:
        return 'untitled'
    # NFKD strip diacritics (ñ → n, é → e).
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    # Replace ampersand with " and " before stripping punctuation, so
    # "BinkP & QWK" doesn't collapse to "binkp-qwk" missing the &.
    s = s.replace('&', ' and ')
    # Spaces, slashes, etc. → dashes
    s = re.sub(r'[\s/_\\.]+', '-', s)
    if allow_hash and '#' in s:
        head, tail = s.split('#', 1)
        head = _SLUG_KEEP.sub('-', head)
        tail = _SLUG_KEEP.sub('-', tail)
        s = head + '#' + tail
    else:
        s = _SLUG_KEEP.sub('-', s)
    s = _DASH_RUN.sub('-', s).strip('-')
    return s or 'untitled'
