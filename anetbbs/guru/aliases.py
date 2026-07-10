"""Phrase -> extra FTS5 search terms for the Ask Anet guru door.

Free-text questions rarely match wiki terminology exactly ("netmail" vs
"PM" vs "direct message"). This is a small hand-maintained dict on
purpose, not a database table: it only changes when a phrasing gap is
noticed or a wiki page is added, and a Python dict is trivially
reviewable in a diff (unlike a DB table nobody remembers to migrate or
back up). Keys are lowercase substrings matched against the raw
question; values are extra search terms merged into the FTS5 query.
"""

ALIASES = {
    'netmail':          ['netmail', 'fidonet', 'binkp'],
    'private message':  ['private', 'messages', 'pm'],
    'direct message':   ['private', 'messages'],
    ' pm ':             ['private', 'messages'],
    'notification':     ['notifications', 'bell', 'alerts'],
    'notify':           ['notifications'],
    'alert':            ['notifications'],
    'chat':             ['chat', 'irc', 'mrc'],
    'irc':              ['chat'],
    'talk to people':   ['chat'],
    'instant message':  ['instant', 'messages', 'msp'],
    'game':             ['games', 'doors'],
    'door':             ['doors', 'games'],
    'play':             ['games'],
    'echomail':         ['echomail', 'fidonet'],
    'echo mail':        ['echomail'],
    'message board':    ['message', 'boards'],
    'forum':            ['message', 'boards'],
    'file':             ['files'],
    'download':         ['files'],
    'upload':           ['files'],
    'ebook':            ['ebooks'],
    'rss':              ['rss', 'news'],
    'news':             ['rss'],
    'who is online':    ['who', 'online'],
    'password':         ['password'],
}
