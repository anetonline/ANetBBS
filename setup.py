# setup.py
from setuptools import setup, find_packages

setup(
    name="anetbbs",
    version="1.0b2.post132",
    packages=find_packages(),
    install_requires=[
        # Core
        'bcrypt>=4.0.0',
        # Web framework
        'Flask>=2.3.0',
        'Flask-Login>=0.6.2',
        'Flask-SocketIO>=5.3.0',
        'Flask-WTF>=1.1.0',
        'WTForms>=3.0.0',
        # Database
        'SQLAlchemy>=2.0.0',
        'Flask-SQLAlchemy>=3.0.0',
        'Flask-Migrate>=4.0.0',
        # Async / WebSockets
        'python-socketio>=5.9.0',
        # eventlet 0.38.0+ required for Python 3.13 — the start_joinable_thread
        # fix did not land in the 0.37.x series (confirmed: 0.37.0 from both
        # piwheels and PyPI source still crashes on Python 3.13).
        # The <0.38 pin was wrong: gunicorn's eventlet worker uses the default
        # epoll hub, not the asyncio hub that 0.38 removed. 0.38+ is safe.
        # greenlet 3.0.0+ required for Python 3.12/3.13 threading compatibility.
        'eventlet>=0.38.0',
        'greenlet>=3.0.0',
        # SSH server
        'asyncssh>=2.14.0',
        # MRC web bridge (standalone aiohttp service)
        'aiohttp>=3.9.0',
        # Production server. <23 because gunicorn 23 dropped the eventlet
        # worker entry point and fresh installs then can't start.
        'gunicorn>=22.0.0,<23',
        # Environment variables
        'python-dotenv>=1.0.0',
        # Email validation
        'email-validator>=2.0.0',
        'Pillow>=10.0.0',
        # HTTP client — used by webhooks.py (top-level import); missing from
        # this list previously caused fresh `pip install -e .` runs to crash
        # whenever the webhook dispatcher loaded.
        'requests>=2.32.0',
        # Markdown rendering for messages — soft-imported in web_app.py
        # (graceful degradation if absent), but listed so default installs
        # have it.
        'markdown>=3.5',
        'bleach>=6.1',
        # RSS / Atom feed parser — used by anetbbs.rss.poller
        'feedparser>=6.0',
        # FTP server — anetbbs.ftp serves the FileArea tree
        'pyftpdlib>=2.0.0',
        # FTPS support: pyftpdlib gates TLS_FTPHandler behind pyOpenSSL
        # being importable. Without this, FTPS silently fails to enable.
        'pyopenssl>=24.0.0',
        # Service Control Center — per-PID CPU% + memory sampler at
        # /admin/control/. Reads /proc, no special privileges.
        'psutil>=5.9.0',
        # Spell check for ANEdit (anetbbs/features/anedit.py). Pure Python,
        # bundles its own dictionary, imported lazily and degrades silently
        # if ever missing — see requirements.txt for the full rationale.
        'pyspellchecker>=0.8.0',
    ],
    entry_points={
        'console_scripts': [
            'anetbbs=anetbbs.main:main',
            'anetbbs-web=anetbbs.web_app:main',
            'anetbbs-install=anetbbs.installer.wizard:main',
            'anetbbs-upgrade=anetbbs.installer.upgrade:main',
            'anetbbs-symlinks=anetbbs.installer.symlinks:main',
            'anetbbs-cleanup=anetbbs.installer.cleanup:main',
            'anetbbs-import-users=tools.import_users:main',
            'anetbbs-prepare-dos=tools.prepare_dos_games:main',
        ],
    },
)
