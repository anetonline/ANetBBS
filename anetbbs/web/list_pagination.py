# anetbbs/web/list_pagination.py
"""Pagination helper for views that build a plain Python list in memory
(no queryable DB model to paginate with Flask-SQLAlchemy's .paginate()).

Used by file_areas.py, where files come from a filesystem directory scan
rather than a DB table. Exposes the same subset of the Flask-SQLAlchemy
Pagination interface the existing pagination markup already expects
(see echomail/area.html): page, pages, has_prev, has_next, prev_num,
next_num, iter_pages().
"""


class ListPagination:
    def __init__(self, page, per_page, total):
        self.per_page = per_page
        self.total = total
        self.pages = max(1, (total + per_page - 1) // per_page)
        self.page = max(1, min(page, self.pages))
        self.has_prev = self.page > 1
        self.has_next = self.page < self.pages
        self.prev_num = self.page - 1
        self.next_num = self.page + 1

    def slice(self, items):
        start = (self.page - 1) * self.per_page
        return items[start:start + self.per_page]

    def iter_pages(self, left_edge=2, left_current=2, right_current=3, right_edge=2):
        """Yields page numbers with None standing in for a '...' gap —
        same algorithm as Flask-SQLAlchemy's Pagination.iter_pages(), used
        here since a large file area can span hundreds of pages (a plain
        1..N range, fine for the small page counts docs/CHANGELOG sees,
        would be unusable at that size)."""
        last = 0
        for num in range(1, self.pages + 1):
            if (num <= left_edge
                    or (self.page - left_current - 1 < num < self.page + right_current)
                    or num > self.pages - right_edge):
                if last + 1 != num:
                    yield None
                yield num
                last = num
