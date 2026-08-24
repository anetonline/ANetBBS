# setup.py
from pathlib import Path
from setuptools import setup, find_packages

_HERE = Path(__file__).resolve().parent


def _read_requirements():
    """Read requirements.txt directly so `pip install -e .` enforces
    the exact same (CVE-aware) version floors requirements.txt
    specifies.

    Real gap found in a security/performance audit: install.sh,
    update.sh, and docker/Dockerfile all install this package via
    `pip install -e .`, which uses install_requires (below) --
    NEVER `pip install -r requirements.txt`. This list used to be a
    separately hand-maintained copy of the same floors, and had
    drifted out of sync with every CVE floor bump requirements.txt
    received in a prior audit round: several packages were missing
    entirely (cryptography had no floor at all) and several others
    were still on their old, pre-CVE-fix floors here even though
    requirements.txt had already been bumped (aiohttp, eventlet,
    asyncssh, Pillow, requests). That meant the CVE fixes never
    actually reached a real install, including this project's own
    live production server. Reading requirements.txt as the single
    source of truth makes that drift structurally impossible instead
    of relying on remembering to update two files in lockstep.
    """
    reqs = []
    for line in (_HERE / 'requirements.txt').read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        reqs.append(line)
    return reqs


setup(
    name="anetbbs",
    version="1.0.44",
    packages=find_packages(),
    install_requires=_read_requirements(),
    entry_points={
        'console_scripts': [
            'anetbbs=anetbbs.main:main',
            'anetbbs-web=anetbbs.web_app:main',
            'anetbbs-cfg=anetbbs.cfg.app:main',
            'anetbbs-install=anetbbs.installer.wizard:main',
            'anetbbs-upgrade=anetbbs.installer.upgrade:main',
            'anetbbs-symlinks=anetbbs.installer.symlinks:main',
            'anetbbs-cleanup=anetbbs.installer.cleanup:main',
            'anetbbs-import-users=tools.import_users:main',
            'anetbbs-prepare-dos=tools.prepare_dos_games:main',
        ],
    },
)
