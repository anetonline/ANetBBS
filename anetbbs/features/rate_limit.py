"""
Tiny in-memory sliding-window rate limiter.

Single-process only — fine for a typical BBS. Not safe across multiple
gunicorn workers without an external store; flag in deployment docs.

Usage:
    from anetbbs.features.rate_limit import rate_limit

    @app.route('/login', methods=['POST'])
    @rate_limit('login', limit=10, window=300)   # 10 / 5 min
    def login():
        ...

By default the key is the client IP. Pass `key_fn=lambda: current_user.id`
for per-user limits.
"""
import random
import time
import threading
from collections import defaultdict, deque
from functools import wraps
from flask import request, jsonify, abort

_buckets = defaultdict(deque)
_lock = threading.Lock()

# Real gap found in a security audit (ironic, in the rate limiter
# itself): _gc() only prunes stale TIMESTAMPS out of the one bucket
# being checked right now -- it never removes the now-empty deque
# (or the dict KEY) from _buckets itself. Every distinct key ever
# seen (by default the client IP; some routes key by user id) got a
# PERMANENT entry for the life of the process, since nothing ever
# revisits a one-time visitor's key again to notice its bucket has
# gone fully stale. On a long-running production server hit by
# scanning bots / dynamic IPs / many distinct users, _buckets only
# grew -- the same "leaky per-key module-level cache" shape as this
# project's own v1.0.21 production incident.
#
# No configured rate-limit window in this app is anywhere near this
# long, so any bucket whose newest timestamp is older than this is
# unambiguously abandoned, regardless of which specific (shorter)
# window its own limiter actually uses -- safe to drop the whole key.
_SWEEP_STALE_SECONDS = 3600
# 1-in-N chance per _check() call of running the sweep -- cheap
# enough (a single dict-comprehension pass) not to need a separate
# timer/thread, matching the same probabilistic-purge pattern already
# used in web/auth.py's _auto_ban_ip().
_SWEEP_PROBABILITY = 500


def _gc(name_key, window):
    """Drop timestamps older than `window` seconds."""
    cutoff = time.time() - window
    bucket = _buckets[name_key]
    while bucket and bucket[0] < cutoff:
        bucket.popleft()


def _sweep_stale_buckets():
    cutoff = time.time() - _SWEEP_STALE_SECONDS
    stale_keys = [k for k, v in _buckets.items() if not v or v[-1] < cutoff]
    for k in stale_keys:
        del _buckets[k]


def _check(name_key, limit, window):
    with _lock:
        _gc(name_key, window)
        if len(_buckets[name_key]) >= limit:
            return False
        _buckets[name_key].append(time.time())
        if random.randint(1, _SWEEP_PROBABILITY) == 1:
            _sweep_stale_buckets()
        return True


def rate_limit(name, limit, window, key_fn=None, on_block=None, on_exceed=None):
    """Decorator. `name` is a unique identifier for the route. `limit`
    is the maximum allowed requests in `window` seconds. Either can also
    be a zero-arg callable, resolved fresh on every request instead of
    once at decoration time — use this when the threshold needs to come
    from a sysop-editable setting (e.g. AutoBanConfig) rather than being
    a fixed constant. `key_fn` is a callable returning a string identifier
    (default: client IP). `on_block` is a callable returning a Flask
    response when blocked (default: 429 JSON for /api/* paths, plain
    abort(429) otherwise). `on_exceed` is an optional no-arg side-effect
    callable fired before the 429 response (e.g. to trigger an auto-ban);
    exceptions are swallowed.
    """
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if key_fn is None:
                key = request.remote_addr or 'unknown'
            else:
                key = str(key_fn())
            bucket_key = f'{name}:{key}'
            resolved_limit = limit() if callable(limit) else limit
            resolved_window = window() if callable(window) else window
            if not _check(bucket_key, resolved_limit, resolved_window):
                if on_exceed is not None:
                    try:
                        on_exceed()
                    except Exception:
                        pass
                if on_block is not None:
                    return on_block()
                if request.path.startswith('/api/'):
                    return jsonify({
                        'error': 'rate limited',
                        'limit': resolved_limit,
                        'window_seconds': resolved_window,
                    }), 429
                abort(429, description=f'Too many requests — limit '
                      f'{resolved_limit} per {resolved_window // 60 or 1} min(s).')
            return fn(*args, **kwargs)
        return wrapper
    return deco


def _user_or_ip():
    """Default key for authenticated rate limits — user.id if logged in,
    else IP. Used for /imsg/send-style routes."""
    from flask_login import current_user
    if current_user.is_authenticated:
        return f'u{current_user.id}'
    return request.remote_addr or 'unknown'
