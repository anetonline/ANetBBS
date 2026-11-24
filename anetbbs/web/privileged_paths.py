"""Fixed locations of the root-run helper scripts (audit H-1).

sudoers grants the service user exactly these paths. They are installed
root:root 0755 by install.sh / update.sh into a root-owned directory,
outside INSTALL_DIR, because the service user owns INSTALL_DIR and could
otherwise rewrite a script that root executes. The web app only ever
reads these files' existence; it never writes them.
"""
import os

WRAPPER_DIR = '/usr/local/libexec/anetbbs'
UPGRADE_WRAPPER = os.path.join(WRAPPER_DIR, 'run_upgrade.sh')
RESTORE_WRAPPER = os.path.join(WRAPPER_DIR, 'run_restore.sh')
# Written by the detached root upgrade unit; root-owned dir, file 0644.
ROOT_UPGRADE_LOG = '/var/log/anetbbs/upgrade.log'
