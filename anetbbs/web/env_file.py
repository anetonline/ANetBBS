"""Where the web admin pages read and write the install's .env."""
import os

_PACKAGE_ROOT_ENV = os.path.abspath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', '.env'))


def env_path():
    """The install's .env: <INSTALL_DIR>/.env when the app config sets
    INSTALL_DIR (the test configs point it at a temp tree), otherwise the
    directory above the package, as before. Tests saving settings used to
    write the developer's own checkout .env this way."""
    try:
        from flask import current_app
        install_dir = current_app.config.get('INSTALL_DIR')
    except RuntimeError:          # no app context
        install_dir = None
    if install_dir:
        return os.path.join(install_dir, '.env')
    return _PACKAGE_ROOT_ENV
