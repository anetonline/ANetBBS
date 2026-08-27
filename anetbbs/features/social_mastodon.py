# anetbbs/features/social_mastodon.py
"""
Minimal Mastodon posting client -- just enough to post one text+image
status from the auto-social-posting queue (web/social_admin.py). Not a
general Mastodon client.

Auth: a personal access token (your instance -> Settings -> Development
-> New Application, scopes write:statuses + write:media) --
config.py's MASTODON_INSTANCE_URL / MASTODON_ACCESS_TOKEN. Two real HTTP
calls per post, per the documented API (https://docs.joinmastodon.org/):
    1. POST /api/v1/media      -- multipart image upload -> a media id
    2. POST /api/v1/statuses   -- status text + that media id
"""
import requests

_TIMEOUT = 15


def post(instance_url, access_token, text, image_bytes=None, image_alt=''):
    """Returns (True, post_url) on success, (False, error_message) on failure."""
    if not instance_url or not access_token:
        return False, 'not configured'

    base = instance_url.rstrip('/')
    headers = {'Authorization': f'Bearer {access_token}'}

    media_ids = []
    if image_bytes:
        try:
            r = requests.post(
                f'{base}/api/v1/media',
                headers=headers,
                files={'file': ('image.png', image_bytes, 'image/png')},
                data={'description': image_alt},
                timeout=_TIMEOUT,
            )
            if r.status_code not in (200, 202):
                return False, f'image upload failed: HTTP {r.status_code} {r.text[:200]}'
            media_ids = [r.json()['id']]
        except (requests.RequestException, KeyError, ValueError) as exc:
            return False, f'image upload failed: {exc}'

    try:
        r = requests.post(
            f'{base}/api/v1/statuses',
            headers=headers,
            data={'status': text, 'media_ids[]': media_ids} if media_ids
                 else {'status': text},
            timeout=_TIMEOUT,
        )
        if r.status_code not in (200, 201):
            return False, f'post failed: HTTP {r.status_code} {r.text[:200]}'
        return True, r.json().get('url', '')
    except (requests.RequestException, KeyError, ValueError) as exc:
        return False, f'post failed: {exc}'
