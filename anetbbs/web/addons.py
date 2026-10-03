"""Admin -> Add-ons -- one install mechanism for optional, not-in-the-
stock-release pieces that previously each needed their own manual scp
+ extract (the Enhanced Client overlay, see
tools/build_enhanced_client_addon.sh) or already had their own
one-off button (the TheDraw font pack, see
anetbbs/web/ansi_editor.py's install_fonts()).

Deliberately does NOT use the sudo/systemd-run machinery
anetbbs/web/upgrades.py's full self-upgrade relies on. That machinery
exists because a self-upgrade replaces every file in the install and
must swap atomically out from under the running service -- an add-on
only ever ADDS a handful of new files under well-known subdirectories,
and both add-ons here are already designed to be inert until present/
enabled (main.py's own try/except ModuleNotFoundError guard around
EnhancedServer; tdf_fonts.scan_fonts() just sees an empty directory
until fonts exist), so a plain file write under the install root --
the same permission level the web process already writes
TDF_FONTS_DIR, the database, and the log file with -- is enough. The
Enhanced Client needs a restart afterward only because Python imports
enhanced_server.py once at process startup, not because of any file
permission gap; that case gets the same plain instructional message
anetbbs/web/admin.py already shows for other restart-requiring
settings changes, never a triggered restart.
"""
import os
import tarfile
import zipfile

from flask import Blueprint, current_app, jsonify, render_template
from flask_login import login_required

from .access_control import require_admin_or_403
from ._addon_download import DownloadError, download_zip_to_tempfile


addons_bp = Blueprint('addons', __name__, url_prefix='/admin/addons')


def _install_root(cfg):
    """Resolve the ANetBBS install root. INSTALL_DIR config wins;
    otherwise derive from this file's location -- same fallback
    anetbbs/web/upgrades.py's own _wrapper_path() uses."""
    install_dir = cfg.get('INSTALL_DIR') or ''
    if not install_dir:
        # anetbbs/web/addons.py -> install root is two parents up
        install_dir = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
    return install_dir


def _enhanced_client_installed(app):
    root = _install_root(app.config)
    return os.path.exists(os.path.join(root, 'anetbbs', 'core', 'enhanced_server.py'))


def _tdf_fonts_installed(app):
    from ..features import tdf_fonts
    fonts_dir = app.config.get('TDF_FONTS_DIR', '')
    return bool(fonts_dir and tdf_fonts.scan_fonts(fonts_dir))


# Fixed, in-code registry -- these are deploy-time options (which
# GitHub Release a sysop pointed each URL config key at), not database
# rows, same spirit as TDF_FONTS_PACK_URL itself being a config var.
ADDONS = [
    {
        'id': 'enhanced_client',
        'name': 'Enhanced Client',
        'description': ('Browser-based Canvas+WebSocket terminal client '
                         'with its own server-side listener. Optional '
                         'overlay -- never part of the stock release.'),
        'url_config_key': 'ENHANCED_CLIENT_ADDON_URL',
        'archive_format': 'tar.gz',
        'extract_mode': 'preserve_paths',
        # Kept in sync with tools/build_enhanced_client_addon.sh's own
        # FILES allowlist + its anetbbs/enhanced_client/ directory walk
        # -- anything in the downloaded archive outside this list is
        # skipped, never written, regardless of what the archive claims.
        'allowed_prefixes': (
            'anetbbs/core/enhanced_server.py',
            'anetbbs/features/enhanced_protocol.py',
            'anetbbs/static/fonts/Flexi_IBM_VGA_False.ttf',
            'anetbbs/static/fonts/Flexi_IBM_VGA_False.woff',
            'anetbbs/enhanced_client/',
        ),
        'needs_restart': 'anetbbs.service',
        'installed_check': _enhanced_client_installed,
    },
    {
        'id': 'tdf_fonts',
        'name': 'TheDraw Font Pack',
        'description': ('TheDraw .TDF fonts for the web ANSI editor\'s '
                         'font picker. Also installable from that '
                         'editor\'s own panel -- listed here too since '
                         'it is fetched the same way.'),
        'url_config_key': 'TDF_FONTS_PACK_URL',
        'archive_format': 'zip',
        'extract_mode': 'flatten',
        'dest_dir_config_key': 'TDF_FONTS_DIR',
        'suffix': '.tdf',
        'needs_restart': None,
        'installed_check': _tdf_fonts_installed,
    },
]


def _iter_archive_entries(tmp_path, archive_format):
    """Yields (relpath, data) for every real file in the downloaded
    archive -- abstracts over zip (the font pack's real format) vs
    tar.gz (build_enhanced_client_addon.sh's real output format) so
    the extraction helpers below don't care which one a given add-on's
    Release asset actually is. For tar.gz, strips the archive's own
    top-level versioned directory (ANetBBS-EnhancedClient-addon-X.Y.Z/)
    the same way that script's own install instructions already tell a
    sysop to do by hand with `tar --strip-components=1`."""
    if archive_format == 'zip':
        with zipfile.ZipFile(tmp_path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                yield info.filename, z.read(info)
    elif archive_format == 'tar.gz':
        with tarfile.open(tmp_path, 'r:gz') as t:
            for member in t.getmembers():
                if not member.isfile():
                    continue
                name = member.name.split('/', 1)[1] if '/' in member.name else member.name
                f = t.extractfile(member)
                yield name, (f.read() if f else b'')
    else:
        raise ValueError(f'unknown archive_format: {archive_format!r}')


def _extract_flatten(entries, dest_dir, suffix):
    """Writes every entry whose basename ends with `suffix` directly
    into dest_dir, discarding whatever folder structure the archive
    itself used -- same flattening ansi_editor.install_fonts() already
    does and for the same reason (tdf_fonts.scan_fonts() only scans
    dest_dir itself, not subfolders)."""
    os.makedirs(dest_dir, exist_ok=True)
    root = os.path.realpath(dest_dir)
    installed = skipped = 0
    for relpath, data in entries:
        name = os.path.basename(relpath)
        if suffix and not name.lower().endswith(suffix):
            continue
        dest = os.path.realpath(os.path.join(dest_dir, name))
        if dest != root and not dest.startswith(root + os.sep):
            skipped += 1
            continue
        with open(dest, 'wb') as out:
            out.write(data)
        installed += 1
    return installed, skipped


def _extract_preserve_paths(entries, install_root, allowed_prefixes):
    """Writes each entry to its real relative path under install_root,
    but only when that path matches one of allowed_prefixes (an exact
    file path, or a directory prefix ending in '/') -- anything else in
    the archive is silently skipped, never written. Real zip-slip
    containment check applies on top, same discipline as the font
    installer's own flatten path."""
    root = os.path.realpath(install_root)
    installed = skipped = 0
    for relpath, data in entries:
        rel = relpath.lstrip('/')
        if not any(rel == p or rel.startswith(p) for p in allowed_prefixes):
            skipped += 1
            continue
        dest = os.path.realpath(os.path.join(install_root, rel))
        if dest != root and not dest.startswith(root + os.sep):
            skipped += 1
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as out:
            out.write(data)
        installed += 1
    return installed, skipped


@addons_bp.route('/')
@login_required
def index():
    require_admin_or_403()
    items = []
    for entry in ADDONS:
        url = current_app.config.get(entry['url_config_key'], '')
        try:
            installed = bool(entry['installed_check'](current_app))
        except Exception:
            installed = False
        items.append({
            'id': entry['id'],
            'name': entry['name'],
            'description': entry['description'],
            'configured': bool(url),
            'installed': installed,
            'url_config_key': entry['url_config_key'],
            'needs_restart': entry.get('needs_restart'),
        })
    return render_template('admin/addons.html', items=items)


@addons_bp.route('/<addon_id>/install', methods=['POST'])
@login_required
def install_addon(addon_id):
    require_admin_or_403()
    entry = next((a for a in ADDONS if a['id'] == addon_id), None)
    if entry is None:
        return jsonify({'error': 'unknown add-on'}), 404

    url = current_app.config.get(entry['url_config_key'], '')
    if not url:
        return jsonify({'error': f"{entry['url_config_key']} is not configured — "
                                  "set it in your environment/config first"}), 400

    try:
        tmp_path = download_zip_to_tempfile(url, user_agent='ANetBBS/addons')
    except DownloadError as exc:
        return jsonify({'error': str(exc)}), 502

    try:
        entries = list(_iter_archive_entries(tmp_path, entry['archive_format']))

        if entry['extract_mode'] == 'flatten':
            dest_dir = current_app.config.get(entry['dest_dir_config_key'], '')
            if not dest_dir:
                return jsonify({'error': f"{entry['dest_dir_config_key']} is not configured"}), 400
            installed, skipped = _extract_flatten(entries, dest_dir, entry.get('suffix', ''))
        else:
            install_root = _install_root(current_app.config)
            installed, skipped = _extract_preserve_paths(
                entries, install_root, entry['allowed_prefixes'])
    except (zipfile.BadZipFile, tarfile.TarError, ValueError, OSError) as exc:
        return jsonify({'error': f'install failed: {exc}'}), 500
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if installed == 0:
        return jsonify({'error': 'downloaded archive contained no matching files'}), 500

    result = {'installed': installed, 'skipped': skipped}
    if entry.get('needs_restart'):
        result['restart_hint'] = ('Installed. Some changes require a service '
                                   f"restart: sudo systemctl restart {entry['needs_restart']}")
    return jsonify(result)
