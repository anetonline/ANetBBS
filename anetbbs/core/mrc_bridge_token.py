"""Token a cookie-less local terminal client presents to the MRC bridge
when it doesn't connect over loopback (docker-compose). Derived from the
SECRET_KEY every ANetBBS process already shares -- no extra config. The
bridge side is mrc/bridge/bbs_auth.py:terminal_token(). (Audit N-4.)"""
import hashlib
import hmac


def bridge_token(secret_key):
    key = secret_key.encode() if isinstance(secret_key, str) else (secret_key or b'')
    return hmac.new(key, b'anetbbs-mrc-bridge-terminal', hashlib.sha256).hexdigest()


def bridge_headers(secret_key=None):
    if secret_key is None:
        try:
            from ..config import get_config
            secret_key = get_config().SECRET_KEY
        except Exception:
            return {}
    return {'X-ANetBBS-Bridge-Token': bridge_token(secret_key)} if secret_key else {}
