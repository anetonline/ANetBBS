# tests/conftest.py
"""
Shared pytest fixtures for the whole tests/ tree.

anetbbs/features/rate_limit.py's `_buckets` is a process-global,
in-memory sliding-window dict (by design -- see its own docstring:
"Single-process only"), never reset between test files. Any test that
POSTs to /auth/login (many do, across many different test files/
classes) shares ONE cumulative counter for the whole pytest process,
regardless of which test DB/app instance each test class uses. A full
`pytest tests/` run can then trip a REAL 429 partway through -- not a
bug in the app, but a real test-isolation gap: whichever test happens
to be the one that pushes the shared bucket over "10 attempts / 5 min"
fails with a 429 where it expected a 200, and WHICH test that is
depends on collection order/count, so it can pass locally and fail in
CI (or vice versa) for no code reason at all. Confirmed by reproducing
it directly: every individual test file passed in isolation, but a
full-suite run deterministically failed on the same handful of
files/tests until this fixture was added.

Autouse + function-scoped so it runs before/after every single test,
including unittest.TestCase-style tests (pytest applies autouse
fixtures to those too) -- the safest, simplest fix rather than trying
to track down and coordinate every individual test file that happens
to touch /auth/login.
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    from anetbbs.features import rate_limit
    rate_limit._buckets.clear()
    yield
    rate_limit._buckets.clear()
