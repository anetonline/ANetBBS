# deploy/secret_helper.py
# Ensures app.config["SECRET_KEY"] is a plain string/bytes.
import os, secrets

def ensure_secret_key(app):
    """
    Prefer environment SECRET_KEY, then app.config value.
    If the value is not a string/bytes (e.g. a property descriptor), prefer env,
    otherwise generate a random hex fallback (development only) and warn.
    """
    secret = os.environ.get("SECRET_KEY") or app.config.get("SECRET_KEY")
    if not isinstance(secret, (str, bytes)):
        # If env var present, prefer it; else generate fallback
        env_secret = os.environ.get("SECRET_KEY")
        if env_secret:
            secret = env_secret
        else:
            secret = secrets.token_hex(32)
            try:
                app.logger.warning("SECRET_KEY missing or invalid; using generated ephemeral key. Set SECRET_KEY in env or config for production.")
            except Exception:
                pass
    app.config["SECRET_KEY"] = secret
