# anetbbs/config.py
"""
Configuration management for ANetBBS
Supports different environments (development, production)
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory
BASE_DIR = Path(__file__).parent.parent

# Systemd services load .env via EnvironmentFile=, so os.environ is already
# correct when running as a real service -- load_dotenv()'s default
# override=False means it never clobbers those. But nothing in this
# codebase ever called load_dotenv() anywhere, so any script run manually
# (a one-shot tools/*.py maintenance script, a bare `python -m ...`, a
# plain interactive shell) never saw .env at all and silently fell back to
# DevelopmentConfig's anetbbs_dev.db instead of the real anetbbs.db --
# live-caught running tools/dedupe_qwk_messages.py by hand, which reported
# "nothing to clean up" against an empty database while the real one had
# hundreds of duplicate rows, no error or warning either way.
load_dotenv(BASE_DIR / '.env')


class Config:
    """Base configuration"""
    
    # Security
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    
    # Database
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False
    # Paired with models.py's WAL-mode PRAGMA hook: a 15s busy timeout so
    # concurrent writers (multiple processes/containers sharing one
    # SQLite file) retry instead of immediately raising "database is
    # locked" under brief write contention.
    SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'timeout': 15}}
    
    # Telnet Server
    TELNET_ENABLED = os.environ.get('TELNET_ENABLED', 'true').lower() == 'true'
    TELNET_HOST = os.environ.get('TELNET_HOST', '0.0.0.0')
    TELNET_PORT = int(os.environ.get('TELNET_PORT', '2233'))

    # SSH Server
    SSH_ENABLED = os.environ.get('SSH_ENABLED', 'true').lower() == 'true'
    SSH_HOST = os.environ.get('SSH_HOST', '0.0.0.0')
    SSH_PORT = int(os.environ.get('SSH_PORT', '2234'))
    SSH_HOST_KEY_FILE = os.environ.get('SSH_HOST_KEY_FILE', 'data/ssh_host_key')

    # rlogin Server (inherently insecure — disabled by default)
    RLOGIN_ENABLED = os.environ.get('RLOGIN_ENABLED', 'false').lower() == 'true'
    RLOGIN_HOST = os.environ.get('RLOGIN_HOST', '0.0.0.0')
    RLOGIN_PORT = int(os.environ.get('RLOGIN_PORT', '513'))

    # FTP Server — serves the file areas. Anonymous access is read-only and
    # limited to FileAreas with is_active=True AND is_sysop_only=False.
    # Authenticated users can upload to areas whose upload_permission lets
    # them. Per-area enforcement happens in the auth layer.
    FTP_ENABLED = os.environ.get('FTP_ENABLED', 'false').lower() == 'true'
    FTP_HOST = os.environ.get('FTP_HOST', '0.0.0.0')
    FTP_PORT = int(os.environ.get('FTP_PORT', '21'))
    FTP_ANON_ENABLED = os.environ.get('FTP_ANON_ENABLED', 'true').lower() == 'true'

    # Finger (RFC 1288) — its own standalone service, no enable flag since
    # it's always its own systemd unit/container. finger_server.py reads
    # these two env vars directly (not via this Config class), but they're
    # also exposed here so anything using app.config (health checks,
    # preflight validation) can see the actual configured port instead of
    # silently assuming the default.
    FINGER_LISTEN_HOST = os.environ.get('FINGER_LISTEN_HOST', '0.0.0.0')
    FINGER_LISTEN_PORT = int(os.environ.get('FINGER_LISTEN_PORT', '79'))

    # Optional TLS — reuses the same cert nginx is using. Set both paths to
    # enable FTPS (AUTH TLS) on the same port. Without these, only plain
    # FTP works.
    FTP_TLS_CERTFILE = os.environ.get('FTP_TLS_CERTFILE', '')
    FTP_TLS_KEYFILE = os.environ.get('FTP_TLS_KEYFILE', '')
    # Passive mode port range — needs to be opened on the firewall and
    # forwarded to this host if behind NAT. Tight default range keeps the
    # exposed surface small.
    FTP_PASV_PORTS = os.environ.get('FTP_PASV_PORTS', '40000-40050')
    # Where to assemble the virtual FTP tree (symlinks to each FileArea's
    # storage_path). Rebuilt on every server start.
    FTP_ROOT_DIR = os.environ.get('FTP_ROOT_DIR', 'data/ftp_root')
    # Banner shown on connect — replace at will.
    FTP_BANNER = os.environ.get('FTP_BANNER', 'ANetBBS FTP — file areas')

    # ANetBBS federation registry / ANotherNetwork QWK hub —
    #
    #   REGISTRY_MODE_ENABLED: this install itself IS the hub (bbs.a-net.fyi).
    #     Gates the federation directory (anetbbs.lst), the QWK/BinkP hub
    #     admin UI, and the QWK node "apply for a node" API — all of those
    #     only make sense on the one designated hub. Leave this false on
    #     every OTHER install. Easy to confuse with REGISTRY_SELF_REGISTER
    #     below (opposite meaning: "I am the hub" vs "register WITH the
    #     hub") — do not set both true on the same install.
    #   REGISTRY_URL: the hub's base URL. On a peer install, this is where
    #     your BBS sends its federation registration/heartbeat AND where
    #     the terminal "Apply for ANotherNetwork QWK node" wizard actually
    #     submits to (both point at the same physical hub). Defaults to
    #     the public ANetBBS hub; set blank to disable federation/QWK-
    #     node-apply entirely. Ignored on the install that has
    #     REGISTRY_MODE_ENABLED=true (that install IS what this points at).
    #   REGISTRY_SELF_REGISTER: if true, this (peer) BBS auto-registers
    #     itself against REGISTRY_URL on startup + heartbeats daily.
    REGISTRY_MODE_ENABLED = os.environ.get(
        'REGISTRY_MODE_ENABLED', 'false').lower() == 'true'
    REGISTRY_URL = os.environ.get(
        'REGISTRY_URL', 'https://bbs.a-net.fyi')
    REGISTRY_SELF_REGISTER = os.environ.get(
        'REGISTRY_SELF_REGISTER', 'false').lower() == 'true'
    # How long an entry can go without a heartbeat before SYSTAT probes
    # start counting against it. The probe thread itself runs every
    # REGISTRY_PROBE_INTERVAL_SEC and drops `is_listed` to false after
    # REGISTRY_PROBE_FAILURE_THRESHOLD consecutive failures.
    REGISTRY_HEARTBEAT_STALE_HOURS = int(os.environ.get(
        'REGISTRY_HEARTBEAT_STALE_HOURS', '48'))
    REGISTRY_PROBE_INTERVAL_SEC = int(os.environ.get(
        'REGISTRY_PROBE_INTERVAL_SEC', '3600'))
    REGISTRY_PROBE_FAILURE_THRESHOLD = int(os.environ.get(
        'REGISTRY_PROBE_FAILURE_THRESHOLD', '3'))
    REGISTRY_HEARTBEAT_INTERVAL_SEC = int(os.environ.get(
        'REGISTRY_HEARTBEAT_INTERVAL_SEC', '86400'))   # daily

    # Sysop / BBS metadata used by self-registration against the
    # federation hub. SYSOP_EMAIL is the contact_email that receives
    # the verify token; SYSOP_NAME + BBS_LOCATION show up in the
    # public anetbbs.lst entry.
    SYSOP_NAME = os.environ.get('SYSOP_NAME', '')
    SYSOP_EMAIL = os.environ.get('SYSOP_EMAIL', '')
    BBS_LOCATION = os.environ.get('BBS_LOCATION', '')
    
    # Web Server
    WEB_HOST = os.environ.get('WEB_HOST', '0.0.0.0')
    WEB_PORT = int(os.environ.get('WEB_PORT', '5000'))
    
    # Session
    PERMANENT_SESSION_LIFETIME = 3600 * 24 * 7  # 7 days
    # Set SESSION_COOKIE_SECURE=true in .env only when serving over HTTPS.
    # Leaving it false (the default) lets HTTP-only installs work correctly;
    # the Secure flag makes browsers silently drop the session cookie on plain
    # HTTP connections, which breaks CSRF token delivery.
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    # Application
    BBS_NAME = os.environ.get('BBS_NAME', 'ANetBBS')
    BBS_DESCRIPTION = os.environ.get('BBS_DESCRIPTION', 'A Modern BBS System')
    # Public hostname/domain shown on the About page connect commands.
    # Defaults to whatever Host header the request carries.
    BBS_DOMAIN = os.environ.get('BBS_DOMAIN', '')
    # Personal web-pages toggle. When true, the app serves the contents
    # of {DATA_DIR}/personal_pages/ at the BBS root URL — e.g. dropping
    # a `photography/` folder makes it visible at /photography/ with
    # auto-index.html resolution.
    PERSONAL_PAGES_ENABLED = os.environ.get(
        'PERSONAL_PAGES_ENABLED', 'false').lower() == 'true'
    # New User Verification — when true, fresh registrations are marked
    # is_verified=False and have to wait for sysop approval before they
    # can log in. Like Mystic's NUV mode.
    NUV_ENABLED = os.environ.get('NUV_ENABLED', 'false').lower() == 'true'
    # Number of telnet/SSH/rlogin "nodes" (1-100). Each concurrent
    # terminal session occupies one node. Used by the multinode chat,
    # who's-online slot tracking, and admin capacity displays.
    BBS_NODES = max(1, min(100, int(os.environ.get('BBS_NODES', '8'))))
    # Idle disconnect for terminal menus (seconds). 0 = never idle out.
    # IRC/MRC are web features and unaffected.
    IDLE_TIMEOUT_SECONDS = int(os.environ.get('IDLE_TIMEOUT_SECONDS', '1800'))
    
    # Data directory
    DATA_DIR = os.path.join(BASE_DIR, 'data')

    # Public Downloads — sysop drops release tarballs / utilities into
    # this directory and they auto-appear on /downloads/. Defaults to
    # {DATA_DIR}/releases. Created on first use if missing.
    DOWNLOADS_ENABLED = os.environ.get(
        'DOWNLOADS_ENABLED', 'true').lower() == 'true'
    DOWNLOADS_DIR = os.environ.get(
        'DOWNLOADS_DIR', os.path.join(BASE_DIR, 'data', 'releases'))
    # Filename whitelist — only matching files appear on the page. The
    # default covers every archive format BBS sysops ship + checksum
    # sidecars. Comma-separated extensions; case-insensitive.
    DOWNLOADS_EXTENSIONS = os.environ.get(
        'DOWNLOADS_EXTENSIONS',
        'tar.gz,tgz,zip,7z,bz2,xz,rar,iso,img,asc,sig,sha256,md5,txt,nfo,diz')

    # Security — country blocking via ip-api.com (free, no registration).
    # Comma-separated ISO 3166-1 alpha-2 codes to block (e.g. CN,RU,KP).
    # Leave blank to disable. Lookup results are cached in-memory for 1 hour.
    BLOCKED_COUNTRIES = os.environ.get('BLOCKED_COUNTRIES', '')

    # Wiki edit gate — minimum requirements to edit any wiki page.
    # Set both to 0 to disable the gate entirely.
    WIKI_MIN_POSTS = int(os.environ.get('WIKI_MIN_POSTS', '5'))
    WIKI_MIN_DAYS = int(os.environ.get('WIKI_MIN_DAYS', '3'))

    # Inter-BBS Instant Messaging (MSP / RFC 1312)
    MSP_ENABLED = True
    MSP_BIND_HOST = '0.0.0.0'
    MSP_PORT = int(os.environ.get('MSP_PORT', '18'))
    # SYSTAT / ActiveUser UDP service (Synchronet IMSG companion)
    SYSTAT_ENABLED = True
    SYSTAT_BIND_HOST = '0.0.0.0'
    SYSTAT_PORT = int(os.environ.get('SYSTAT_PORT', '11'))
    # Inter-BBS directory (sbbsimsg.lst from Vertrauen)
    SBBSIMSG_LIST_URL = 'ftp://vert.synchro.net/sbbsimsg.lst'
    SBBSIMSG_AUTO_REFRESH = True
    SBBSIMSG_REFRESH_SECONDS = 86400   # daily

    # File uploads
    UPLOAD_MAX_SIZE = 100 * 1024 * 1024  # 100MB
    AVATAR_MAX_SIZE = 2 * 1024 * 1024  # 2MB
    ALLOWED_EXTENSIONS = {
        # Images / docs
        'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf', 'txt', 'md',
        # Archives (BBS bread-and-butter)
        'zip', 'rar', '7z', 'tar', 'gz', 'tgz', 'bz2', 'tbz2', 'xz',
        'lzh', 'lha', 'arj', 'arc', 'cab',
        # Software / executables
        'exe', 'com', 'bat', 'cmd',
        # Source / data
        'iso', 'img', 'nfo', 'diz', 'csv', 'json', 'xml', 'log',
        # Audio / video
        'mp3', 'wav', 'ogg', 'flac', 'mp4', 'avi', 'mkv', 'mov',
        # Code
        'py', 'js', 'html', 'css', 'c', 'h', 'cpp', 'hpp', 'java',
    }
    AVATAR_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    UPLOADS_DIR = os.path.join(DATA_DIR, 'uploads')
    AVATARS_DIR = os.path.join(DATA_DIR, 'avatars')
    NETWORK_JOIN_DIR = os.path.join(DATA_DIR, 'network_join')
    
    # Logging
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_FILE = os.path.join(BASE_DIR, 'bbs.log')
    
    # MRC Bridge Configuration
    MRC_BRIDGE_HOST = os.environ.get('MRC_BRIDGE_HOST', 'localhost')
    MRC_BRIDGE_PORT = int(os.environ.get('MRC_BRIDGE_PORT', '8080'))
    MRC_BRIDGE_USE_SSL = os.environ.get('MRC_BRIDGE_USE_SSL', 'false').lower() == 'true'
    MRC_BRIDGE_WS_PATH = os.environ.get('MRC_BRIDGE_WS_PATH', '/mrcws')

    # Games Configuration
    GAMES_ENABLED = os.environ.get('GAMES_ENABLED', 'true').lower() == 'true'
    GAMES_DATA_DIR = os.path.join(DATA_DIR, 'games')
    GAMES_MAX_NODES = int(os.environ.get('GAMES_MAX_NODES', '10'))
    GAMES_SESSION_TIMEOUT = int(os.environ.get('GAMES_SESSION_TIMEOUT', '3600'))
    DOSBOX_PATH = os.environ.get('DOSBOX_PATH', '/usr/bin/dosbox')
    DOSEMU_PATH = os.environ.get('DOSEMU_PATH', '/usr/bin/dosemu')
    NODEJS_PATH = os.environ.get('NODEJS_PATH', '/usr/bin/node')
    MYSTIC_PYTHON_PATH = os.environ.get('MYSTIC_PYTHON_PATH', '')
    MYSTIC_BBS_PATH = os.environ.get('MYSTIC_BBS_PATH', '/usr/local/bin/mystic')

    # Echomail Configuration
    ECHOMAIL_ENABLED = os.environ.get('ECHOMAIL_ENABLED', 'true').lower() == 'true'
    ECHOMAIL_POLL_ENABLED = os.environ.get('ECHOMAIL_POLL_ENABLED', 'true').lower() == 'true'
    ECHOMAIL_DATA_DIR = os.path.join(DATA_DIR, 'echomail')
    ECHOMAIL_ORIGIN_LINE = os.environ.get('ECHOMAIL_ORIGIN_LINE', 'ANetBBS - A Modern BBS System')
    ECHOMAIL_TEAR_LINE = os.environ.get('ECHOMAIL_TEAR_LINE', '--- ANetBBS')

    # InterBBS Wall / Last Callers -- opt-in sharing of local Graffiti
    # Wall posts / recent-caller entries with other ANetBBS installs over
    # a dedicated echomail area (see anetbbs/echomail/interbbs_sync.py).
    # Scoped to ONE specific EchomailNetwork (picked in the admin UI, not
    # every active network this install happens to carry) -- relaying to
    # every configured network indiscriminately would silently create an
    # ANET_WALL area on an unrelated third-party FTN network too.
    WALL_INTERBBS_ENABLED = os.environ.get(
        'WALL_INTERBBS_ENABLED', 'false').lower() == 'true'
    WALL_INTERBBS_NETWORK_ID = os.environ.get('WALL_INTERBBS_NETWORK_ID') or None
    LASTCALLERS_INTERBBS_ENABLED = os.environ.get(
        'LASTCALLERS_INTERBBS_ENABLED', 'false').lower() == 'true'
    LASTCALLERS_INTERBBS_NETWORK_ID = os.environ.get('LASTCALLERS_INTERBBS_NETWORK_ID') or None
    # Hide sysop logins from the user-facing Last Callers displays (web
    # oneliners page, terminal Last Callers screen, terminal inline
    # "Last 10 Callers" block) -- a sysop who logs in several times a
    # day can otherwise flood the list with themselves instead of real
    # users. Off by default (unchanged behavior); does NOT affect the
    # admin audit view, which always shows everything.
    LASTCALLERS_HIDE_SYSOP = os.environ.get(
        'LASTCALLERS_HIDE_SYSOP', 'false').lower() == 'true'

    # InterBBS door-game score sharing -- opt-in relay of new personal-best
    # GameScore rows with other ANetBBS installs, same pattern as Wall/Last
    # Callers above (dedicated echomail area, one specific network only).
    GAMES_INTERBBS_ENABLED = os.environ.get(
        'GAMES_INTERBBS_ENABLED', 'false').lower() == 'true'
    GAMES_INTERBBS_NETWORK_ID = os.environ.get('GAMES_INTERBBS_NETWORK_ID') or None


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        f'sqlite:///{os.path.join(Config.DATA_DIR, "anetbbs_dev.db")}'
    SQLALCHEMY_ECHO = False


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    TESTING = False

    # Use PostgreSQL in production or fallback to SQLite
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        f'sqlite:///{os.path.join(Config.DATA_DIR, "anetbbs.db")}'
    
    # SECRET_KEY validation happens at runtime, not import
    #@property
    #def SECRET_KEY(self):
        #key = os.environ.get('SECRET_KEY')
        #if not key:
            #raise ValueError("SECRET_KEY environment variable must be set in production")
        #return key


class TestingConfig(Config):
    """Testing configuration"""
    DEBUG = True
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False


# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}


def get_config(env=None):
    """Get configuration for specified environment"""
    if env is None:
        env = os.environ.get('FLASK_ENV', 'development')
    return config.get(env, config['default'])
