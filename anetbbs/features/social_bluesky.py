# anetbbs/features/social_bluesky.py
"""
Minimal Bluesky (AT Protocol) posting client -- just enough to post one
text+image status from the auto-social-posting queue
(web/social_admin.py). Not a general AT Protocol client.

Auth: a Bluesky "app password" (bsky.app -> Settings -> App Passwords),
NOT the account's real login password -- config.py's BLUESKY_HANDLE /
BLUESKY_APP_PASSWORD. Three real HTTP calls per post, per the documented
API (https://docs.bsky.app/):
    1. com.atproto.server.createSession  -- handle+app password -> accessJwt/did
    2. com.atproto.repo.uploadBlob       -- raw image bytes -> a blob ref
    3. com.atproto.repo.createRecord     -- the actual post, embedding that blob
"""
import datetime as _dt

import requests

_BASE = 'https://bsky.social/xrpc'
_TIMEOUT = 15


def post(handle, app_password, text, image_bytes=None, image_alt=''):
    """Returns (True, post_url) on success, (False, error_message) on failure."""
    if not handle or not app_password:
        return False, 'not configured'

    try:
        r = requests.post(f'{_BASE}/com.atproto.server.createSession',
                          json={'identifier': handle, 'password': app_password},
                          timeout=_TIMEOUT)
        if r.status_code != 200:
            return False, f'login failed: HTTP {r.status_code} {r.text[:200]}'
        session = r.json()
        access_jwt = session['accessJwt']
        did = session['did']
    except (requests.RequestException, KeyError, ValueError) as exc:
        return False, f'login failed: {exc}'

    headers = {'Authorization': f'Bearer {access_jwt}'}

    embed = None
    if image_bytes:
        try:
            r = requests.post(f'{_BASE}/com.atproto.repo.uploadBlob',
                              headers={**headers, 'Content-Type': 'image/png'},
                              data=image_bytes, timeout=_TIMEOUT)
            if r.status_code != 200:
                return False, f'image upload failed: HTTP {r.status_code} {r.text[:200]}'
            blob = r.json()['blob']
            embed = {
                '$type': 'app.bsky.embed.images',
                'images': [{'image': blob, 'alt': image_alt}],
            }
        except (requests.RequestException, KeyError, ValueError) as exc:
            return False, f'image upload failed: {exc}'

    record = {
        '$type': 'app.bsky.feed.post',
        'text': text,
        'createdAt': _dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
    }
    if embed:
        record['embed'] = embed

    try:
        r = requests.post(f'{_BASE}/com.atproto.repo.createRecord',
                          headers=headers,
                          json={'repo': did, 'collection': 'app.bsky.feed.post',
                               'record': record},
                          timeout=_TIMEOUT)
        if r.status_code != 200:
            return False, f'post failed: HTTP {r.status_code} {r.text[:200]}'
        uri = r.json().get('uri', '')
        # at://did:plc:xxx/app.bsky.feed.post/<rkey> -> a real bsky.app URL
        rkey = uri.rsplit('/', 1)[-1] if uri else ''
        post_url = f'https://bsky.app/profile/{handle}/post/{rkey}' if rkey else uri
        return True, post_url
    except (requests.RequestException, KeyError, ValueError) as exc:
        return False, f'post failed: {exc}'
