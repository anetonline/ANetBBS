"""Shared helpers for private user-to-user messaging (PMs, FTN netmail,
telegrams, QWK-routed netmail).

``access`` holds the one set of ownership rules every view uses, so the
inbox lists, the read/reply/delete routes, bookmarks and any future API
can't drift apart.
"""
