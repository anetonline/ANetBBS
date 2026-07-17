# anetbbs/models.py
"""
Database models for ANetBBS
Supports both SQLite (development) and PostgreSQL (production)
"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):
    """Every ANetBBS process (web, terminal, MRC bridge, finger, binkp --
    5 separate OS processes today, 5 separate containers under Docker)
    opens its own engine against the SAME SQLite file. This has always
    "worked" on bare metal purely because they share one OS user and one
    local filesystem -- there was never any WAL mode or busy-timeout
    tuning, just SQLAlchemy/pysqlite's untuned defaults. WAL mode +
    a real busy timeout meaningfully reduces "database is locked" risk
    from concurrent writers (web + terminal + binkp all write), and
    costs nothing on a single-process/single-user dev setup either.
    This is an Engine-class-level hook, so it fires for every engine any
    process creates -- no per-service wiring needed.
    """
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def _default_hub_identity_id():
    """Column default for hub_identity_id FKs (see HubIdentity below) --
    resolves to the install's default hub identity at flush time. This
    means every existing call site that constructs an EchomailNetwork,
    BinkPNode, QWKNode, NetworkJoinConfig, or NetworkJoinRequest directly
    (admin routes, terminal QWK approval, seed data, tests) needs no
    changes -- new rows land on the default identity automatically.
    Rows that predate the hub_identity_id column are backfilled
    separately, once, in web_app.py's _lightweight_migrate() (a bare
    ALTER TABLE ADD COLUMN always gives existing rows a literal NULL
    regardless of this default -- it only governs new INSERTs).
    """
    row = HubIdentity.query.filter_by(is_default=True).first()
    return row.id if row else None


class Theme(db.Model):
    """User interface theme model"""
    __tablename__ = 'themes'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    display_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    css_variables = db.Column(db.Text, nullable=False)
    is_default = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class User(UserMixin, db.Model):
    """User model for authentication and profiles"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login = db.Column(db.DateTime)
    login_count = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    is_admin = db.Column(db.Boolean, default=False)
    display_name = db.Column(db.String(100))
    bio = db.Column(db.Text)
    location = db.Column(db.String(100))
    website = db.Column(db.String(255))
    avatar_url = db.Column(db.String(500))
    avatar_upload = db.Column(db.String(255))
    signature = db.Column(db.Text)
    tagline = db.Column(db.String(160))           # message footer rotator
    date_of_birth = db.Column(db.Date)
    show_email = db.Column(db.Boolean, default=False)
    theme_id = db.Column(db.Integer, db.ForeignKey('themes.id'), nullable=True)
    # JSON-encoded {kind: bool} map. Missing key = default-on.
    notify_prefs = db.Column(db.Text)
    # Mystic/Synchronet-style numeric access level (0-255). admin gets 100+.
    # Lets sysop create tiers: e.g. 10=newuser, 20=verified, 50=power, 100=sysop.
    access_level = db.Column(db.Integer, default=10, index=True)
    # ANSI codepage preference: 'cp437' (DOS classic) or 'utf8'.
    codepage = db.Column(db.String(8), default='cp437')
    # Preferred language code (ISO-639-1) for menu translations: en/es/fr/...
    language = db.Column(db.String(8), default='en')
    # Sixel graphics capability override: 'auto' (DA1-detect, default),
    # 'forced_on' (assume support -- e.g. Windows Terminal over SSH,
    # which supports sixel but doesn't self-report via DA1), or
    # 'forced_off' (never use it, even if detected).
    sixel_mode = db.Column(db.String(10), default='auto')
    # New User Verification — sysop approves before user can log in
    # (only enforced when NUV_ENABLED config flag is set).
    is_verified = db.Column(db.Boolean, default=True, index=True)

    # Relationships
    posts = db.relationship('Post', backref='author', lazy='dynamic', cascade='all, delete-orphan')
    messages = db.relationship('Message', backref='author', lazy='dynamic', cascade='all, delete-orphan')
    theme = db.relationship('Theme', backref='theme_users', lazy=True, foreign_keys=[theme_id])
    
    def set_password(self, password):
        """Hash and set user password"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Verify user password"""
        return check_password_hash(self.password_hash, password)
    
    def update_login(self):
        """Update login timestamp and count.
        Null-safe — if login_count is NULL (legacy rows), treats it as 0."""
        self.last_login = datetime.utcnow()
        self.login_count = (self.login_count or 0) + 1
        db.session.commit()
    
    def __repr__(self):
        return f'<User {self.username}>'


class Board(db.Model):
    """Message board/forum model"""
    __tablename__ = 'boards'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    order = db.Column(db.Integer, default=0)
    # Optional ANSI banner rendered at top of the board's index page.
    # Stored as the rendered escape-coded string (same format as ansi_text
    # on AnsiArt). Sysop can paste output of `Download .ans`.
    ansi_banner = db.Column(db.Text)
    # Sub-conference / category — group related boards. e.g. "General",
    # "Tech", "FidoNet". Boards in the same category are listed together.
    category = db.Column(db.String(80), default='', index=True)
    # Minimum user access_level required to read this board (0=public, 10=registered).
    min_access_level = db.Column(db.Integer, default=10)
    # Minimum user access_level required to post to this board (0=all, 10=registered).
    # NULL means "same as min_access_level".
    min_write_level = db.Column(db.Integer, nullable=True, default=None)

    # Relationships
    posts = db.relationship('Post', backref='board', lazy='dynamic', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Board {self.name}>'


class Post(db.Model):
    """Message board post/thread model"""
    __tablename__ = 'posts'
    
    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id'), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('posts.id'), index=True)
    
    subject = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_pinned = db.Column(db.Boolean, default=False, index=True)
    is_locked = db.Column(db.Boolean, default=False)

    # Relationships
    replies = db.relationship('Post', backref=db.backref('parent', remote_side=[id]),
                            lazy='dynamic', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Post {self.subject}>'


class Message(db.Model):
    """Bulletin/announcement model"""
    __tablename__ = 'messages'
    
    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    expires_at = db.Column(db.DateTime)
    is_pinned = db.Column(db.Boolean, default=False)
    
    def __repr__(self):
        return f'<Message {self.title}>'


class ChatMessage(db.Model):
    """Chat message history model"""
    __tablename__ = 'chat_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    username = db.Column(db.String(80), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationship
    user = db.relationship('User', backref='chat_messages', lazy=True)
    
    def __repr__(self):
        return f'<ChatMessage from {self.username}>'


class GameCategory(db.Model):
    """Sysop-managed door/game categories (e.g. Space, RPG, Strategy)."""
    __tablename__ = 'game_categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<GameCategory {self.name}>'


class Game(db.Model):
    """Game model for the Game Center"""
    __tablename__ = 'games'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(50), default='other')
    min_access_level = db.Column(db.Integer, default=0)

    # Game type determines how it's launched
    # Types: 'door_dos', 'door_dosemu', 'door_dos_browser', 'door_native',
    #        'door_mystic', 'door_mystic_mps', 'door_synchronet',
    #        'door_rlogin', 'door_telnet', 'builtin_web'
    game_type = db.Column(db.String(20), nullable=False)

    # Door game settings
    executable_path = db.Column(db.String(500))
    working_directory = db.Column(db.String(500))
    command_line_args = db.Column(db.String(500))
    drop_file_type = db.Column(db.String(20))
    drop_file_path = db.Column(db.String(500))
    use_dosbox = db.Column(db.Boolean, default=False)
    needs_fossil_driver = db.Column(db.Boolean, default=False)

    # door_rlogin only: Synchronet-style BBS tag (e.g. "ANET") the remote
    # game server uses to namespace inbound users by source BBS. Kept as
    # its own field rather than folded into command_line_args because the
    # real wire value needs a literal space ("username -s-TAG") and
    # command_line_args is whitespace-split into USER_TEMPLATE/PASSWORD/
    # [TERMINAL] *before* template expansion, so a space inside the
    # template field would silently break the split.
    rlogin_bbs_tag = db.Column(db.String(20))

    # Mystic BBS Python game settings
    mystic_script_path = db.Column(db.String(500))

    # Synchronet JS game settings
    synchronet_script_path = db.Column(db.String(500))
    synchronet_exec_dir = db.Column(db.String(500))

    # Web game settings
    web_game_module = db.Column(db.String(100))
    web_game_url = db.Column(db.String(500))

    # General settings
    max_nodes = db.Column(db.Integer, default=1)
    is_active = db.Column(db.Boolean, default=True)
    is_multiplayer = db.Column(db.Boolean, default=False)
    play_count = db.Column(db.Integer, default=0)
    icon = db.Column(db.String(50))

    # Per-front-end availability -- most game types only exist on one
    # front end anyway (is_active is the only switch that matters for
    # them), but a few (currently: the ebook reader) have a real,
    # independent implementation on both web and terminal, so a sysop
    # may want e.g. terminal-only with the web version turned off.
    # Both default True so every existing game keeps working exactly as
    # before with no config changes required.
    web_enabled = db.Column(db.Boolean, default=True)
    terminal_enabled = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # InterBBS score sharing (anetbbs/echomail/interbbs_sync.py): opt-in
    # per game, defaults on so the feature works day one once the
    # install-wide GAMES_INTERBBS_ENABLED switch is flipped. A sysop can
    # turn off relaying for one specific game without disabling the
    # whole feature.
    share_scores_interbbs = db.Column(db.Boolean, default=True)

    # Relationships
    sessions = db.relationship('GameSession', backref='game', lazy='dynamic')
    scores = db.relationship('GameScore', backref='game', lazy='dynamic')

    def __repr__(self):
        return f'<Game {self.name}>'


class GameSession(db.Model):
    """Active game session tracking"""
    __tablename__ = 'game_sessions'

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey('games.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    node_number = db.Column(db.Integer, nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='active')  # active, completed, crashed, timeout
    pid = db.Column(db.Integer)

    user = db.relationship('User', backref='game_sessions')

    def __repr__(self):
        return f'<GameSession {self.id} game={self.game_id} user={self.user_id}>'


class GameScore(db.Model):
    """Game high score / leaderboard entry"""
    __tablename__ = 'game_scores'

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey('games.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    score = db.Column(db.Integer, default=0)
    details = db.Column(db.Text)  # JSON with extra score data
    achieved_at = db.Column(db.DateTime, default=datetime.utcnow)

    # InterBBS score sharing (anetbbs/echomail/interbbs_sync.py): NULL =
    # earned locally on this install. Non-NULL = imported from another
    # ANetBBS system over echomail -- origin_bbs is the sending system's
    # name, remote_msg_id is the source EchomailMessage.msg_id (dedup
    # key, and the load-bearing check that prevents this score from
    # ever being relayed back out again, which would bounce forever).
    #
    # user_id is a hard NOT NULL FK (unlike WallPost.username, a plain
    # string) -- imported rows point at a lazily-created ghost User
    # (username='__interbbs_import__', is_active=False) rather than
    # loosening the column, since SQLite can't relax an existing NOT
    # NULL via ALTER TABLE and this codebase's migration helper only
    # ever adds columns. The real remote player name lives in
    # remote_username instead; display_username below picks the right
    # one so callers never need their own fallback logic.
    origin_bbs = db.Column(db.String(100), nullable=True)
    remote_msg_id = db.Column(db.String(100), nullable=True, index=True, unique=True)
    remote_username = db.Column(db.String(100), nullable=True)

    user = db.relationship('User', backref='game_scores')

    @property
    def display_username(self):
        """Real username, or remote_username if this is an imported row."""
        if self.origin_bbs is not None and self.remote_username:
            return self.remote_username
        return self.user.username if self.user else self.remote_username

    def __repr__(self):
        return f'<GameScore {self.score} game={self.game_id} user={self.user_id}>'


class WebGameWallet(db.Model):
    """Persistent casino wallet — balance persists across sessions, resets each ISO week (Monday)."""
    __tablename__ = 'web_game_wallets'
    __table_args__ = (db.UniqueConstraint('user_id', 'game_slug', name='uq_wgw_user_slug'),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    game_slug = db.Column(db.String(50), nullable=False)
    balance = db.Column(db.Integer, nullable=False, default=0)
    peak_balance = db.Column(db.Integer, nullable=False, default=0)
    starting_balance = db.Column(db.Integer, nullable=False, default=500)
    week_start = db.Column(db.String(10), nullable=False)  # 'YYYY-MM-DD' of ISO week Monday
    last_active = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='game_wallets')

    def __repr__(self):
        return f'<WebGameWallet {self.game_slug} user={self.user_id} balance={self.balance}>'


class UserSession(db.Model):
    """Active user session tracking for online presence"""
    __tablename__ = 'user_sessions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True, index=True)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))
    page = db.Column(db.String(255))

    # Cascade-delete the session row when the user is deleted. Without
    # this, SQLAlchemy defaults to "set FK to NULL" on user delete —
    # but user_id is NOT NULL, so the parent delete blows up with
    # `IntegrityError: NOT NULL constraint failed: user_sessions.user_id`.
    user = db.relationship(
        'User',
        backref=db.backref('session', uselist=False,
                           cascade='all, delete-orphan'),
        lazy=True)

    def __repr__(self):
        return f'<UserSession user_id={self.user_id}>'


class PrivateMessage(db.Model):
    """Private message between users"""
    __tablename__ = 'private_messages'

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    read_at = db.Column(db.DateTime)
    is_deleted_sender = db.Column(db.Boolean, default=False)
    is_deleted_recipient = db.Column(db.Boolean, default=False)

    sender = db.relationship('User', foreign_keys=[sender_id], backref='sent_messages')
    recipient = db.relationship('User', foreign_keys=[recipient_id], backref='received_messages')

    def __repr__(self):
        return f'<PrivateMessage {self.subject}>'


class FileUpload(db.Model):
    """User file upload record"""
    __tablename__ = 'file_uploads'

    id = db.Column(db.Integer, primary_key=True)
    uploader_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.Integer, default=0)
    mime_type = db.Column(db.String(100))
    description = db.Column(db.Text)
    download_count = db.Column(db.Integer, default=0)
    is_public = db.Column(db.Boolean, default=True)
    # Optional file area scoping — null means a generic top-level upload.
    file_area_id = db.Column(db.Integer, db.ForeignKey('file_areas.id'),
                             index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    # sha256 hex digest of the file's contents — dupe-check within the
    # same file_area_id scope (anetbbs/features/file_dedup.py). Nullable:
    # rows created before this column existed simply never match.
    content_hash = db.Column(db.String(64), index=True)

    uploader = db.relationship('User', backref='uploads', lazy=True)
    file_area = db.relationship('FileArea')

    def __repr__(self):
        return f'<FileUpload {self.filename}>'


class EchomailNetwork(db.Model):
    """FidoNet-style network configuration"""
    __tablename__ = 'echomail_networks'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    network_type = db.Column(db.String(10), nullable=False)  # 'binkp' or 'qwk'
    description = db.Column(db.Text)

    # BinkP settings
    binkp_host = db.Column(db.String(255))
    binkp_port = db.Column(db.Integer, default=24554)
    binkp_password = db.Column(db.String(255))
    # Optional separate AreaFix password — many hubs configure a distinct
    # password for AreaFix requests vs the BinkP session secret. If blank,
    # falls back to binkp_password for backward compatibility.
    areafix_password = db.Column(db.String(255))
    our_address = db.Column(db.String(50))  # FTN address like 1:234/567
    hub_address = db.Column(db.String(50))
    # Per FSP-1028 the qualified-address domain suffix (addr@domain) must be
    # <=8 chars, [a-z0-9_~-]+. Without this, the poller derives it from
    # `name` (truncated/lowercased), which can produce an awkward result for
    # a long display name -- e.g. "ANotherNetwork" truncates to "anothern".
    # Set this to override with something shorter/cleaner while keeping the
    # display name intact. Blank/NULL falls back to the old name-derived
    # behavior for backward compatibility with existing networks.
    ftn_domain = db.Column(db.String(8))

    # QWK settings
    qwk_host = db.Column(db.String(255))
    qwk_port = db.Column(db.Integer)
    qwk_username = db.Column(db.String(100))
    qwk_password = db.Column(db.String(255))
    qwk_packet_id = db.Column(db.String(8))
    # The HUB's QWK system ID (e.g. "VERT" for DOVE-Net). qnet-ftp's
    # convention: download <hub_id>.qwk, upload <packet_id>.rep.
    qwk_hub_id = db.Column(db.String(16))
    # Override the default URL path. Set this when the upstream uses a
    # non-standard layout (e.g. Dove-Net's qnet.dl URL). If blank, the
    # client falls back to {host}:{port}/qwk/{packet_id}.qwk.
    qwk_download_url = db.Column(db.String(500))
    qwk_upload_url = db.Column(db.String(500))

    poll_interval_minutes = db.Column(db.Integer, default=60)
    last_poll_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # SBBSecho parity:
    # - default_recipient: where netmail addressed to an unknown name lands
    # - cram_md5: prefer/require CRAM-MD5 BinkP auth (Synchronet default)
    # - binkp_tls: implicit TLS for outbound BinkP
    default_recipient = db.Column(db.String(100))
    cram_md5 = db.Column(db.Boolean, default=True)
    binkp_tls = db.Column(db.Boolean, default=False)

    # Which real-world hub identity this transport row belongs to, if
    # any (see HubIdentity). Only meaningful for networks that represent
    # THIS install acting as a hub -- a plain leaf/spoke network (polling
    # someone else's hub) has no hub identity of its own and this stays
    # on whatever the default identity happens to be, unused.
    hub_identity_id = db.Column(db.Integer, db.ForeignKey('hub_identities.id'),
                                default=_default_hub_identity_id, nullable=True, index=True)

    areas = db.relationship('EchoArea', backref='network', lazy='dynamic', cascade='all, delete-orphan')
    poll_logs = db.relationship('EchomailPollLog', backref='network', lazy='dynamic', cascade='all, delete-orphan')
    hub_identity = db.relationship('HubIdentity', backref=db.backref('networks', lazy='dynamic'))

    def __repr__(self):
        return f'<EchomailNetwork {self.name}>'


class EchoArea(db.Model):
    """Echomail message area/conference"""
    __tablename__ = 'echo_areas'

    id = db.Column(db.Integer, primary_key=True)
    network_id = db.Column(db.Integer, db.ForeignKey('echomail_networks.id'), nullable=False, index=True)
    tag = db.Column(db.String(100), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    is_subscribed = db.Column(db.Boolean, default=True)
    is_sysop_only = db.Column(db.Boolean, default=False, index=True)
    min_access_level = db.Column(db.Integer, default=10)
    category = db.Column(db.String(80))
    order = db.Column(db.Integer, default=0)
    total_messages = db.Column(db.Integer, default=0)
    last_message_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    messages = db.relationship('EchomailMessage', backref='area', lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<EchoArea {self.tag}>'


class EchomailMessage(db.Model):
    """Individual echomail message"""
    __tablename__ = 'echomail_messages'

    id = db.Column(db.Integer, primary_key=True)
    area_id = db.Column(db.Integer, db.ForeignKey('echo_areas.id'), nullable=False, index=True)
    network_id = db.Column(db.Integer, db.ForeignKey('echomail_networks.id'), nullable=False, index=True)
    msg_id = db.Column(db.String(100), index=True)
    reply_id = db.Column(db.String(100))
    from_name = db.Column(db.String(100), nullable=False)
    from_address = db.Column(db.String(50))
    to_name = db.Column(db.String(100), nullable=False)
    to_address = db.Column(db.String(50))
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    tear_line = db.Column(db.String(200))
    origin_line = db.Column(db.String(200))
    # FTN kludges preserved verbatim — JSON-encoded array of "@KLUDGE val" lines.
    # Without this we'd lose MSGID/REPLY threading, CHRS encoding declaration,
    # PATH/SEENBY routing info, and the message would not be reforwarded
    # correctly to downstream peers.
    kludges = db.Column(db.Text)
    chrs = db.Column(db.String(40), default='CP437 2')   # CHRS kludge value
    seenby = db.Column(db.Text)                           # SEEN-BY list (JSON)
    path = db.Column(db.Text)                             # PATH list (JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)
    direction = db.Column(db.String(10), default='inbound')  # 'inbound' or 'outbound'
    # For outbound messages: timestamp of successful send to the network.
    # NULL means "queued, not yet sent". Inbound messages leave this NULL.
    sent_at = db.Column(db.DateTime, index=True)

    def __repr__(self):
        return f'<EchomailMessage {self.subject}>'


class EchomailReadStatus(db.Model):
    """Per-user read tracking for echomail messages"""
    __tablename__ = 'echomail_read_status'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    message_id = db.Column(db.Integer, db.ForeignKey('echomail_messages.id'), nullable=False, index=True)
    read_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'message_id', name='uq_echomail_read'),)

    def __repr__(self):
        return f'<EchomailReadStatus user={self.user_id} msg={self.message_id}>'


class EchomailPollLog(db.Model):
    """Echomail polling history"""
    __tablename__ = 'echomail_poll_logs'

    id = db.Column(db.Integer, primary_key=True)
    network_id = db.Column(db.Integer, db.ForeignKey('echomail_networks.id'), nullable=False, index=True)
    poll_type = db.Column(db.String(10), default='both')  # 'send', 'receive', 'both'
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='running')  # 'success', 'error', 'partial', 'running'
    messages_sent = db.Column(db.Integer, default=0)
    messages_received = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text)
    # Frame-by-frame BinkP session transcript (send/receive commands,
    # data-frame sizes, connect/disconnect, timestamped) -- lets a
    # sysop see exactly what happened on the wire for a failed poll
    # without needing server log access. QWK polls leave this blank.
    transcript = db.Column(db.Text)

    def __repr__(self):
        return f'<EchomailPollLog network={self.network_id} status={self.status}>'


# ---------------------------------------------------------------------------
# Phase 5: BBS menu system (data-driven menus for telnet/SSH/rlogin)
# ---------------------------------------------------------------------------

class BbsMenu(db.Model):
    """A BBS menu shown on telnet/SSH/rlogin. Each menu has an `ansi_screen`
    (raw ANSI art shown above the prompt), a `prompt`, and a list of MenuItems.
    Use `name` as the unique key referenced from menu actions (e.g. action_args
    for action_type='goto' should be the target menu's name)."""

    __tablename__ = 'bbs_menus'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)  # 'main', 'sysop', 'games', etc
    title = db.Column(db.String(100), nullable=False)              # Shown at top
    ansi_screen = db.Column(db.Text)                               # Raw ANSI art (CP437)
    prompt = db.Column(db.String(100), default='Choice: ')
    is_default = db.Column(db.Boolean, default=False)              # Shown after login if true
    min_access = db.Column(db.Integer, default=0)                  # 0=any, 100=admin
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship('BbsMenuItem', backref='menu',
                            order_by='BbsMenuItem.sort_order',
                            lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<BbsMenu {self.name}>'


class BbsMenuItem(db.Model):
    """A single item on a BbsMenu. The user types `hotkey` to invoke `action_type`
    with `action_args`. Action types:
      - 'goto':       jump to another menu (action_args = menu name)
      - 'door':       launch a door game (action_args = Game.id)
      - 'boards':     enter message-boards UI
      - 'pm':         enter PM inbox
      - 'pm_send':    compose new PM
      - 'bulletins':  read bulletins
      - 'echo':       echomail areas
      - 'echo_post':  compose echomail
      - 'files':      file library
      - 'who':        who's online
      - 'profile':    view profile
      - 'edit_prof':  edit profile
      - 'passwd':     change password
      - 'sysop':      sysop tools (admin only)
      - 'chat':       chat menu
      - 'rss':        RSS news reader
      - 'ebooks':     ebook reader (search/read free Gutenberg classics)
      - 'logoff':     end the session
    """

    __tablename__ = 'bbs_menu_items'

    id = db.Column(db.Integer, primary_key=True)
    menu_id = db.Column(db.Integer, db.ForeignKey('bbs_menus.id'), nullable=False, index=True)
    hotkey = db.Column(db.String(4), nullable=False)              # 'M', '1', 'F2', etc
    label = db.Column(db.String(80), nullable=False)              # 'Message Boards'
    action_type = db.Column(db.String(20), nullable=False)
    action_args = db.Column(db.String(255))                       # see action_type docstring
    min_access = db.Column(db.Integer, default=0)                 # hide if user.access < this
    sort_order = db.Column(db.Integer, default=0)
    is_visible = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f'<BbsMenuItem menu={self.menu_id} {self.hotkey}={self.label!r}>'


# ---------------------------------------------------------------------------
# Phase 6: FidoNet support — nodelist, netmail, AKAs, TIC, areafix
# ---------------------------------------------------------------------------

class Nodelist(db.Model):
    """Imported nodelist metadata (one row per imported file).

    A nodelist is the FidoNet network's address book — a flat-file directory
    of every system on the network, regenerated weekly. We store metadata
    about each imported file plus all entries (NodelistEntry rows below).
    """
    __tablename__ = 'nodelists'

    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(40), default='fidonet', index=True)  # network name
    filename = db.Column(db.String(120))                              # NODELIST.001 etc
    day_of_year = db.Column(db.Integer)
    release_date = db.Column(db.Date)
    crc_checksum = db.Column(db.String(16))
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)
    entry_count = db.Column(db.Integer, default=0)

    entries = db.relationship('NodelistEntry', backref='nodelist',
                              lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Nodelist {self.domain} day={self.day_of_year}>'


class NodelistEntry(db.Model):
    """One row from a parsed nodelist file."""
    __tablename__ = 'nodelist_entries'

    id = db.Column(db.Integer, primary_key=True)
    nodelist_id = db.Column(db.Integer, db.ForeignKey('nodelists.id'),
                            nullable=False, index=True)
    zone = db.Column(db.Integer, nullable=False, index=True)
    net = db.Column(db.Integer, nullable=False, index=True)
    node = db.Column(db.Integer, nullable=False, index=True)
    point = db.Column(db.Integer, default=0)
    keyword_type = db.Column(db.String(16))    # Zone/Net/Region/Hub/Pvt/Hold/Down/None
    system_name = db.Column(db.String(120))
    location = db.Column(db.String(120))
    sysop_name = db.Column(db.String(120), index=True)
    phone = db.Column(db.String(60))
    baud_rate = db.Column(db.Integer)
    flags = db.Column(db.Text)                 # JSON-encoded {flag: value|true}

    @property
    def address(self):
        if self.point:
            return f'{self.zone}:{self.net}/{self.node}.{self.point}'
        return f'{self.zone}:{self.net}/{self.node}'

    def __repr__(self):
        return f'<NodelistEntry {self.address} {self.system_name!r}>'


class UserAka(db.Model):
    """Multiple FTN addresses (AKAs) the sysop can send mail FROM.

    A sysop running multiple nodes (e.g. 1:234/5 in Fidonet AND 21:1/100 in
    fsxnet) needs to pick which AKA to use when composing netmail. This table
    tracks alternate addresses available to a user (only one can be primary)."""
    __tablename__ = 'user_akas'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    address = db.Column(db.String(60), nullable=False)   # 'zone:net/node[.point]'
    domain = db.Column(db.String(40), default='fidonet')
    is_primary = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('akas', lazy='dynamic',
                                                      cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<UserAka user={self.user_id} {self.address}>'


class RegistryEntry(db.Model):
    """A registered ANetBBS instance in the central federation registry.

    The BBS designated as the registry hub (REGISTRY_MODE_ENABLED=true)
    accepts `POST /registry/api/v1/register` from peer ANetBBS instances
    and exposes the approved-list at `/anetbbs.lst`. Peers pull that list
    daily into their local `BbsDirectoryEntry` table for browsing/IM.

    Two acceptance gates before an entry appears in the public list:
      1. Email verification — sysop clicks a token link sent to
         `contact_email` (proves they own that mailbox).
      2. Sysop approval — the hub's sysop manually approves the entry
         via the admin UI (prevents drive-by spam even with email).
    `is_listed` is the public flag — only entries where both gates are
    true AND `is_active=true` show up in the JSON output.
    """
    __tablename__ = 'registry_entries'

    id = db.Column(db.Integer, primary_key=True)
    host = db.Column(db.String(255), unique=True, nullable=False, index=True)
    msp_port = db.Column(db.Integer, default=18)
    systat_port = db.Column(db.Integer, default=11)
    # Friendly metadata shown to other BBS users browsing the directory.
    name = db.Column(db.String(160), nullable=False)
    sysop = db.Column(db.String(120))
    location = db.Column(db.String(120))
    software = db.Column(db.String(40), default='ANetBBS')
    software_version = db.Column(db.String(40))
    notes = db.Column(db.Text)
    # Provenance + verification
    contact_email = db.Column(db.String(255), nullable=False)
    registration_token = db.Column(db.String(64), index=True)  # email-verify
    is_verified = db.Column(db.Boolean, default=False, index=True)
    is_approved = db.Column(db.Boolean, default=False, index=True)
    is_listed = db.Column(db.Boolean, default=False, index=True)
    is_active = db.Column(db.Boolean, default=True)
    source_ip = db.Column(db.String(45))   # IP that POSTed /register
    # Liveness tracking — the hub probes SYSTAT periodically and stale
    # entries get dropped from the public list after N missed probes.
    registered_at = db.Column(db.DateTime, default=datetime.utcnow,
                              nullable=False)
    last_heartbeat_at = db.Column(db.DateTime)
    last_probe_at = db.Column(db.DateTime)
    last_probe_ok = db.Column(db.Boolean, default=False)
    consecutive_probe_failures = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<RegistryEntry {self.host}>'


class NetmailMessage(db.Model):
    """Point-to-point FidoNet netmail (NOT an echo — addressed to one node).

    Netmail is FTN's email: single recipient, with kludge lines (MSGID/REPLY/
    INTL/CHARSET/PATH/FMPT/TOPT/...) preserved verbatim. We store kludges as
    a JSON-encoded array so they round-trip correctly when forwarded.
    """
    __tablename__ = 'netmail_messages'

    id = db.Column(db.Integer, primary_key=True)
    network_id = db.Column(db.Integer, db.ForeignKey('echomail_networks.id'),
                           index=True)
    from_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    to_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)

    from_address = db.Column(db.String(60), index=True)   # FTN addr of sender
    to_address = db.Column(db.String(60), index=True)     # FTN addr of recipient
    from_name = db.Column(db.String(120), nullable=False)
    to_name = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(200), default='')
    body = db.Column(db.Text, nullable=False)

    # FTN kludges — JSON-encoded array of "@KLUDGE value" lines so we can
    # round-trip them when forwarding. Examples: "MSGID 1:234/5 12abcd",
    # "REPLY 1:234/5 11abcd", "INTL 2:5020/1042 1:234/5", "CHRS UTF-8 4".
    kludges = db.Column(db.Text)
    msgid = db.Column(db.String(200), index=True)        # parsed from MSGID kludge
    reply_msgid = db.Column(db.String(200))              # parsed from REPLY kludge
    chrs = db.Column(db.String(40), default='CP437 2')   # CHRS kludge

    # FTN attribute flags (from the message header). True/False columns make
    # SQL queries readable. See FTS-0001 §5.2 for canonical bitfield order.
    is_private = db.Column(db.Boolean, default=False)
    is_crash = db.Column(db.Boolean, default=False)      # crashmail (direct delivery)
    is_recd = db.Column(db.Boolean, default=False)       # received by recipient
    is_sent = db.Column(db.Boolean, default=False)
    is_filereq = db.Column(db.Boolean, default=False)    # file request
    is_killsent = db.Column(db.Boolean, default=False)   # kill/sent flag
    is_local = db.Column(db.Boolean, default=False)      # locally generated
    is_hold = db.Column(db.Boolean, default=False)
    is_immediate = db.Column(db.Boolean, default=False)

    # Lifecycle
    direction = db.Column(db.String(10), default='outbound')   # 'inbound'/'outbound'
    status = db.Column(db.String(20), default='draft')
    # status values:
    #   'draft' - user is composing; not queued
    #   'queued' - ready to send on next poll
    #   'sent' - successfully transmitted to uplink/peer
    #   'received' - inbound, sitting in user's inbox
    #   'read' - inbound, user has read it
    #   'failed' - send error (see error_message)
    error_message = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    sent_at = db.Column(db.DateTime, index=True)
    # Indexed as of v1.0b2.145 -- poller.py's content-based netmail dedup
    # fallback (v1.0b2.143) filters on this column to bound its lookback
    # window, but without an index that filter does nothing at the SQL
    # level: SQLite narrows via whichever OTHER indexed column it picks
    # (from_address is the obvious one, and not very selective for a
    # single flooding hub address) and then walks EVERY matching row --
    # unbounded by time -- doing a TEXT comparison against `body` for
    # each one. Under eventlet (this app's web process monkey-patches
    # threading, but NOT sqlite3), a slow SQLite query blocks the entire
    # process for every user on every page until it completes, which is
    # exactly the shape of a real live report: the whole web UI hanging
    # for minutes after this dedup fallback shipped, on an install that
    # had accumulated many rows from the very flood the fallback exists
    # to catch.
    received_at = db.Column(db.DateTime, index=True)
    read_at = db.Column(db.DateTime)

    # Soft-delete: independent for sender and recipient
    deleted_by_sender = db.Column(db.Boolean, default=False)
    deleted_by_recipient = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f'<NetmailMessage {self.from_address} -> {self.to_address} {self.subject!r}>'


class TicFile(db.Model):
    """Incoming TIC file — FidoNet file echo distribution.

    A TIC file is a small text manifest sent through FTN file echos describing
    a binary file (size, CRC, area, replaces, etc.). We parse received TICs,
    fetch the referenced file from the inbound dir, and auto-file it into the
    matching FileArea.
    """
    __tablename__ = 'tic_files'

    id = db.Column(db.Integer, primary_key=True)
    area_tag = db.Column(db.String(80), index=True)      # FILEAREA tag
    filename = db.Column(db.String(120))                 # FILE
    size_bytes = db.Column(db.BigInteger)                # SIZE
    crc32 = db.Column(db.String(16))                     # CRC
    description = db.Column(db.Text)                     # DESC + LDESC lines
    origin = db.Column(db.String(60))                    # ORIGIN address
    from_address = db.Column(db.String(60))              # FROM address
    seenby = db.Column(db.Text)                          # SEENBY list (JSON)
    path = db.Column(db.Text)                            # PATH list (JSON)
    raw_content = db.Column(db.Text)                     # full TIC text

    received_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    processed_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='pending')  # pending/filed/error/skipped
    error_message = db.Column(db.Text)
    file_upload_id = db.Column(db.Integer, db.ForeignKey('file_uploads.id'),
                               index=True)              # filed-as
    file_area_id = db.Column(db.Integer, db.ForeignKey('file_areas.id'),
                             index=True)
    stored_path = db.Column(db.String(500))             # absolute path of binary

    def __repr__(self):
        return f'<TicFile {self.area_tag}/{self.filename}>'


class AreafixLog(db.Model):
    """Log of areafix robot interactions — area subscription requests via netmail.

    When a downstream node netmails our areafix bot with `+ECHO.AREA`, we
    add it to their subscription list and reply with the action taken. This
    table is the audit trail."""
    __tablename__ = 'areafix_logs'

    id = db.Column(db.Integer, primary_key=True)
    network_id = db.Column(db.Integer, db.ForeignKey('echomail_networks.id'),
                           index=True)
    from_address = db.Column(db.String(60), index=True)
    request_type = db.Column(db.String(20))          # subscribe/unsubscribe/list/help
    area_tags = db.Column(db.Text)                   # comma-list of areas affected
    response = db.Column(db.Text)                    # what we replied with
    success = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    # Which robot handled this request. Reused (not a separate FileFixLog
    # table) for the filefix bot too -- these columns are already generic
    # to "a robot processed a subscription request via netmail", nothing
    # here is message-echo-specific except this table's name/docstring.
    # Default 'areafix' so pre-existing rows (all message-echo requests,
    # from before this column existed) read correctly without a backfill.
    bot = db.Column(db.String(20), default='areafix')

    def __repr__(self):
        return f'<AreafixLog {self.from_address} {self.request_type}>'


# ---------------------------------------------------------------------------
# Phase 7: Sysop / security tools — audit log, rate limit, password reset
# ---------------------------------------------------------------------------

class PasswordResetToken(db.Model):
    """Self-service password reset tokens. Single-use, time-limited."""
    __tablename__ = 'password_reset_tokens'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    token = db.Column(db.String(80), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    used_at = db.Column(db.DateTime)
    requested_ip = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('reset_tokens',
                                                      lazy='dynamic',
                                                      cascade='all, delete-orphan'))

    @property
    def is_valid(self):
        if self.used_at is not None:
            return False
        return datetime.utcnow() < self.expires_at

    def __repr__(self):
        return f'<PasswordResetToken user={self.user_id} valid={self.is_valid}>'


class EmailVerifyToken(db.Model):
    """Single-use email address verification tokens, issued on registration."""
    __tablename__ = 'email_verify_tokens'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    token = db.Column(db.String(80), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('email_verify_tokens',
                                                      lazy='dynamic',
                                                      cascade='all, delete-orphan'))

    @property
    def is_valid(self):
        return self.used_at is None and datetime.utcnow() < self.expires_at


class SmtpConfig(db.Model):
    """Single-row sysop SMTP relay config for outbound email (verification, resets)."""
    __tablename__ = 'smtp_config'

    id = db.Column(db.Integer, primary_key=True)
    enabled = db.Column(db.Boolean, default=False, nullable=False)
    email_verify_enabled = db.Column(db.Boolean, default=False, nullable=False)
    host = db.Column(db.String(255), default='')
    port = db.Column(db.Integer, default=587)
    username = db.Column(db.String(255), default='')
    password = db.Column(db.String(255), default='')
    from_address = db.Column(db.String(255), default='')
    from_name = db.Column(db.String(255), default='')
    use_tls = db.Column(db.Boolean, default=True, nullable=False)
    use_ssl = db.Column(db.Boolean, default=False, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    @classmethod
    def get(cls):
        """Return the singleton config row, creating it (disabled) if absent."""
        row = cls.query.first()
        if row is None:
            row = cls()
            db.session.add(row)
            db.session.commit()
        return row


SECURITY_QUESTIONS = [
    "What was the name of your first pet?",
    "What city were you born in?",
    "What is your mother's maiden name?",
    "What was the name of your elementary school?",
    "What was the make of your first car?",
    "What is the name of the street you grew up on?",
    "What was your childhood nickname?",
    "What is the name of your favorite childhood friend?",
    "What was the name of your first employer?",
    "What is the middle name of your oldest sibling?",
]


class UserSecurityAnswer(db.Model):
    """Hashed answers to user-chosen security questions for password recovery."""
    __tablename__ = 'user_security_answers'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    question = db.Column(db.String(300), nullable=False)
    answer_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('security_answers',
                                                      cascade='all, delete-orphan'))

    def check_answer(self, raw):
        return check_password_hash(self.answer_hash, raw.strip().lower())

    @staticmethod
    def hash_answer(raw):
        return generate_password_hash(raw.strip().lower())

    def __repr__(self):
        return f'<UserSecurityAnswer user={self.user_id}>'


class UserActivity(db.Model):
    """Audit log of user actions — sysop-visible.

    activity_type is a short slug. Common values:
      login, logout, register, post, post_reply, msg_sent, msg_read,
      file_upload, file_download, door_played, chat_msg, profile_edit,
      password_changed, theme_changed
    """
    __tablename__ = 'user_activities'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=True, index=True)   # nullable for anon events
    activity_type = db.Column(db.String(40), nullable=False, index=True)
    details = db.Column(db.Text)                     # free-form context (URL, target id, etc)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))
    service = db.Column(db.String(20))               # web/telnet/ssh/rlogin
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    user = db.relationship('User', backref=db.backref('activities',
                                                      lazy='dynamic'))

    def __repr__(self):
        return f'<UserActivity user={self.user_id} {self.activity_type}>'


class RegistrationAttempt(db.Model):
    """Tracks signup attempts by IP for rate-limiting purposes.

    A new row is written for every POST to the registration page (success or
    failure). The auth code uses a window query (last N minutes) to throttle
    repeat attempts from the same IP."""
    __tablename__ = 'registration_attempts'

    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(db.String(45), nullable=False, index=True)
    username_attempted = db.Column(db.String(80))
    success = db.Column(db.Boolean, default=False)
    error_reason = db.Column(db.String(200))
    user_agent = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    def __repr__(self):
        return f'<RegistrationAttempt {self.ip_address} success={self.success}>'


class IrcServerConfig(db.Model):
    """Per-user saved IRC server preference (server, port, nick, channels)."""
    __tablename__ = 'irc_server_configs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        unique=True, nullable=False, index=True)
    server = db.Column(db.String(120), default='irc.libera.chat')
    port = db.Column(db.Integer, default=6667)
    use_ssl = db.Column(db.Boolean, default=False)
    nick = db.Column(db.String(40))
    channels = db.Column(db.Text)             # comma-separated list
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('irc_config',
                                                      uselist=False,
                                                      cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<IrcServerConfig user={self.user_id} {self.server}:{self.port}>'


class IrcPreset(db.Model):
    """Sysop-configured IRC server presets shown in the terminal IRC chat menu."""
    __tablename__ = 'irc_presets'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)        # e.g. "Libera.Chat"
    server = db.Column(db.String(120), nullable=False)
    port = db.Column(db.Integer, default=6667)
    use_ssl = db.Column(db.Boolean, default=False)
    default_nick = db.Column(db.String(40))
    channels = db.Column(db.Text)                           # auto-join, comma-separated
    is_active = db.Column(db.Boolean, default=True)
    order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<IrcPreset {self.name} {self.server}:{self.port}>'


class AddressBookEntry(db.Model):
    """Per-user contacts — FTN addresses + email + free-form notes."""
    __tablename__ = 'address_book'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    ftn_address = db.Column(db.String(60))     # 'zone:net/node[.point]'
    email = db.Column(db.String(120))
    notes = db.Column(db.Text)
    favorite = db.Column(db.Boolean, default=False, index=True)
    crashmail_default = db.Column(db.Boolean, default=False)
    last_used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('contacts',
                                                      lazy='dynamic',
                                                      cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<AddressBookEntry {self.name} ({self.ftn_address or self.email})>'


class Poll(db.Model):
    """A sysop-or-user-created poll. Answers stored in PollOption."""
    __tablename__ = 'polls'

    id = db.Column(db.Integer, primary_key=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                           nullable=False, index=True)
    question = db.Column(db.String(400), nullable=False)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True, index=True)
    closes_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    creator = db.relationship('User', backref='polls_created')
    options = db.relationship('PollOption', backref='poll',
                              lazy='dynamic',
                              cascade='all, delete-orphan',
                              order_by='PollOption.sort_order')

    def __repr__(self):
        return f'<Poll {self.id} {self.question[:30]!r}>'


class PollOption(db.Model):
    __tablename__ = 'poll_options'

    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey('polls.id'),
                        nullable=False, index=True)
    text = db.Column(db.String(200), nullable=False)
    sort_order = db.Column(db.Integer, default=0)

    votes = db.relationship('PollVote', backref='option',
                            lazy='dynamic',
                            cascade='all, delete-orphan')

    @property
    def vote_count(self):
        return self.votes.count()


class PollVote(db.Model):
    """One row per (user, poll) — uniqueness enforced to prevent duplicate votes."""
    __tablename__ = 'poll_votes'

    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey('polls.id'),
                        nullable=False, index=True)
    option_id = db.Column(db.Integer, db.ForeignKey('poll_options.id'),
                          nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('poll_id', 'user_id',
                                           name='uq_poll_vote_user'),)

    user = db.relationship('User', backref='poll_votes')


class ChatBan(db.Model):
    """Bans on users in MRC chat rooms — global or per-room.

    A NULL `room` value means a global ban (kicked from all rooms). expires_at
    NULL means permanent. The MRC bridge consults this table on each message
    and refuses traffic from banned users."""
    __tablename__ = 'chat_bans'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    room = db.Column(db.String(80), index=True)
    reason = db.Column(db.Text)
    issued_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    expires_at = db.Column(db.DateTime, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    user = db.relationship('User', foreign_keys=[user_id], backref='chat_bans')
    issued_by = db.relationship('User', foreign_keys=[issued_by_id])

    @property
    def is_active(self):
        if self.expires_at is None:
            return True
        return datetime.utcnow() < self.expires_at

    def __repr__(self):
        return f'<ChatBan user={self.user_id} room={self.room}>'


# ---------------------------------------------------------------------------
# Phase 8: File areas (FidoNet file echos), shoutbox, saved messages, etc.
# ---------------------------------------------------------------------------

class FileArea(db.Model):
    """A FidoNet file echo (or local file area).

    File areas group hatched/uploaded files by topic — e.g. FILES.GAMES,
    FILES.UTILS, MAGAZINES. The TIC processor looks up an area by `tag` and
    files matching binaries here. Local-only areas have network_id NULL."""
    __tablename__ = 'file_areas'

    id = db.Column(db.Integer, primary_key=True)
    network_id = db.Column(db.Integer, db.ForeignKey('echomail_networks.id'),
                           index=True)
    tag = db.Column(db.String(80), nullable=False, unique=True, index=True)
    name = db.Column(db.String(120))
    description = db.Column(db.Text)
    storage_path = db.Column(db.String(500))      # absolute dir for binaries
    is_active = db.Column(db.Boolean, default=True)
    is_subscribed = db.Column(db.Boolean, default=True)   # receive from upstream
    is_sysop_only = db.Column(db.Boolean, default=False)
    min_access_level = db.Column(db.Integer, default=10)
    # Minimum level to upload to this area. NULL means "same as min_access_level".
    min_write_level = db.Column(db.Integer, nullable=True, default=None)
    upload_permission = db.Column(db.String(20), default='users')
    # upload_permission values: 'users' / 'sysop' / 'none'
    password = db.Column(db.String(80))           # optional area password
    # When this file area is the network's nodelist distribution echo (e.g.
    # Z1DAILY for FidoNet, tqwinfo for TQWnet), inbound TICs are unpacked
    # and the nodelist text is auto-imported into the Nodelist table —
    # tagged with `nodelist_domain` so `/nodelist/?domain=tqwnet` filters work.
    is_nodelist_source = db.Column(db.Boolean, default=False)
    nodelist_domain = db.Column(db.String(40))    # e.g. 'tqwnet', 'fidonet'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    network = db.relationship('EchomailNetwork',
                              backref=db.backref('file_areas', lazy='dynamic',
                                                 cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<FileArea {self.tag}>'


class ShoutboxPost(db.Model):
    """One-line shouts posted to the site-wide shoutbox visible on the home page."""
    __tablename__ = 'shoutbox_posts'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    text = db.Column(db.String(280), nullable=False)
    is_hidden = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    user = db.relationship('User', backref=db.backref('shouts', lazy='dynamic',
                                                      cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<ShoutboxPost #{self.id} by {self.user_id}>'


class SavedMessage(db.Model):
    """User-saved bookmarks of messages (echomail / netmail / board posts).

    `kind` discriminates which table the `target_id` references:
        'echomail' -> EchomailMessage.id
        'netmail'  -> NetmailMessage.id
        'post'     -> Post.id (board)
        'pm'       -> Message.id (private message)
    """
    __tablename__ = 'saved_messages'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    kind = db.Column(db.String(16), nullable=False, index=True)
    target_id = db.Column(db.Integer, nullable=False, index=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    user = db.relationship('User', backref=db.backref('saved_msgs', lazy='dynamic',
                                                      cascade='all, delete-orphan'))

    __table_args__ = (
        db.UniqueConstraint('user_id', 'kind', 'target_id',
                            name='uq_saved_msg_user_kind_target'),
    )

    def __repr__(self):
        return f'<SavedMessage user={self.user_id} {self.kind}#{self.target_id}>'


class MessageSlug(db.Model):
    """Short permalink slug for any kind of message — sharable URL.

    Generates a short opaque token (6-8 chars, base62) on first request for
    a message, stored here. Subsequent shares of the same message reuse the
    token. URL: /m/<slug>"""
    __tablename__ = 'message_slugs'

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(16), unique=True, nullable=False, index=True)
    kind = db.Column(db.String(16), nullable=False)        # echomail/netmail/post/pm
    target_id = db.Column(db.Integer, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('kind', 'target_id',
                                           name='uq_message_slug_target'),)

    def __repr__(self):
        return f'<MessageSlug {self.slug} -> {self.kind}#{self.target_id}>'


class FileEchoSubscription(db.Model):
    """Downstream peer subscriptions for FidoNet file echo distribution.

    A row says: send all hatched files in `file_area_id` onward to FTN address
    `peer_address`. Used by the TIC hatch-out queue to compute the SEEN-BY /
    PATH lines and the recipients list when re-broadcasting an inbound TIC.
    """
    __tablename__ = 'file_echo_subscriptions'

    id = db.Column(db.Integer, primary_key=True)
    file_area_id = db.Column(db.Integer, db.ForeignKey('file_areas.id'),
                             nullable=False, index=True)
    peer_address = db.Column(db.String(60), nullable=False, index=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    file_area = db.relationship('FileArea', backref=db.backref('subscriptions',
                                                               lazy='dynamic',
                                                               cascade='all, delete-orphan'))

    __table_args__ = (db.UniqueConstraint('file_area_id', 'peer_address',
                                           name='uq_file_echo_sub'),)


class HatchQueue(db.Model):
    """Outbound TIC + binary queued for delivery to a downstream peer.

    The poller / binkp client picks `pending` rows for a peer, builds a TIC
    file referencing the binary, sends both, and flips status to `sent`.
    Failures bump retry_count; we cap at retry_max and mark `failed`."""
    __tablename__ = 'hatch_queue'

    id = db.Column(db.Integer, primary_key=True)
    file_area_id = db.Column(db.Integer, db.ForeignKey('file_areas.id'),
                             nullable=False, index=True)
    peer_address = db.Column(db.String(60), nullable=False, index=True)
    binary_path = db.Column(db.String(500), nullable=False)
    filename = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    crc32 = db.Column(db.String(16))
    size_bytes = db.Column(db.BigInteger)
    seenby = db.Column(db.Text)              # JSON of inherited SEEN-BY entries
    path = db.Column(db.Text)                # JSON of inherited PATH entries
    status = db.Column(db.String(16), default='pending', index=True)
    # status: pending / sent / failed
    retry_count = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text)
    queued_at = db.Column(db.DateTime, default=datetime.utcnow,
                          nullable=False, index=True)
    sent_at = db.Column(db.DateTime)

    file_area = db.relationship('FileArea')

    def __repr__(self):
        return f'<HatchQueue {self.filename} -> {self.peer_address}>'


class SysopBroadcast(db.Model):
    """Sysop one-line announcements pushed to all online users in real time.

    The web stores them so users joining later can still see recent
    broadcasts; the telnet/SSH/rlogin sessions render them on their next
    menu loop. Marking expires_at lets old broadcasts auto-hide."""
    __tablename__ = 'sysop_broadcasts'

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                          nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    expires_at = db.Column(db.DateTime, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    sender = db.relationship('User')

    def __repr__(self):
        return f'<SysopBroadcast #{self.id} by {self.sender_id}>'


class SysopPage(db.Model):
    """A user-initiated 'page sysop' — surfaces as a real-time toast on
    sysop browser tabs and stays in this table for follow-up."""
    __tablename__ = 'sysop_pages'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    service = db.Column(db.String(20))           # web/telnet/ssh/rlogin
    message = db.Column(db.Text)
    answered = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)
    answered_at = db.Column(db.DateTime)

    user = db.relationship('User')

    def __repr__(self):
        return f'<SysopPage #{self.id} by {self.user_id} answered={self.answered}>'


class SitePage(db.Model):
    """Sysop-editable static-ish page content. Used for the BBS history page,
    welcome screen, etc. Looked up by `slug`; content is Markdown."""
    __tablename__ = 'site_pages'

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(40), unique=True, nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    content_md = db.Column(db.Text, default='')
    updated_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    updated_by = db.relationship('User')

    def __repr__(self):
        return f'<SitePage {self.slug}>'


class PeerBbs(db.Model):
    """A known peer BBS we can finger / send telegrams to.

    The cross-BBS presence aggregator queries each peer's finger service
    periodically and caches who's online so users can see activity across
    the BBS network — Synchronet-style "active sysops" feel."""
    __tablename__ = 'peer_bbses'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    hostname = db.Column(db.String(160), nullable=False, unique=True, index=True)
    finger_port = db.Column(db.Integer, default=79)
    telnet_port = db.Column(db.Integer, default=23)
    web_url = db.Column(db.String(400))
    location = db.Column(db.String(120))
    software = db.Column(db.String(80))
    ftn_address = db.Column(db.String(60))
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    is_approved = db.Column(db.Boolean, default=True)
    submitted_by_user_id = db.Column(db.Integer)
    last_polled_at = db.Column(db.DateTime)
    last_response = db.Column(db.Text)
    last_error = db.Column(db.Text)
    online_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<PeerBbs {self.name} {self.hostname}>'


class ExternalBbsCache(db.Model):
    """Cached entries from external BBS directories (TelnetBBSGuide, IPTIA).

    Refreshed in the background every 6 hours or on admin demand.
    source values: 'telnetbbsguide', 'iptia'
    """
    __tablename__ = 'external_bbs_cache'

    id           = db.Column(db.Integer, primary_key=True)
    source       = db.Column(db.String(30), nullable=False, index=True)
    name         = db.Column(db.String(200), nullable=False)
    telnet_host  = db.Column(db.String(200))
    telnet_port  = db.Column(db.Integer, default=23)
    web_url      = db.Column(db.String(400))
    location     = db.Column(db.String(200))
    software     = db.Column(db.String(80))
    sysop        = db.Column(db.String(120))
    description  = db.Column(db.Text)
    cached_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f'<ExternalBbsCache {self.source} {self.name}>'


class UserAccessFlags(db.Model):
    """Per-user feature suspension flags — sysop sets these to restrict access.

    All flags default False (no restriction). Rows are created on first save,
    so existing users without a row are treated as fully unrestricted.
    New table — safe for db.create_all() on upgrade, no ALTER TABLE needed."""
    __tablename__ = 'user_access_flags'

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    no_echomail = db.Column(db.Boolean, default=False, nullable=False)
    no_mrc      = db.Column(db.Boolean, default=False, nullable=False)
    no_irc      = db.Column(db.Boolean, default=False, nullable=False)
    no_games    = db.Column(db.Boolean, default=False, nullable=False)
    no_qwk      = db.Column(db.Boolean, default=False, nullable=False)
    no_files    = db.Column(db.Boolean, default=False, nullable=False)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by  = db.Column(db.String(80))

    user = db.relationship('User', backref=db.backref('access_flags', uselist=False))


class UserNote(db.Model):
    """Private sysop note attached to a user account.

    Visible only to admins via /admin/users/<id>/notes. Useful for tracking
    moderation history, contact attempts, "this user is a regular Echomail
    contributor", etc."""
    __tablename__ = 'user_notes'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                          nullable=False, index=True)
    note = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    user = db.relationship('User', foreign_keys=[user_id], backref='notes_about')
    author = db.relationship('User', foreign_keys=[author_id])

    def __repr__(self):
        return f'<UserNote about={self.user_id} by={self.author_id}>'


class EchomailLastRead(db.Model):
    """Per-(user, area) lastread pointer. Faster than scanning EchomailReadStatus.

    Stores the highest message_id the user has seen in each area. Used by
    the "next unread" jump to skip straight to where they left off."""
    __tablename__ = 'echomail_lastread'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    area_id = db.Column(db.Integer, db.ForeignKey('echo_areas.id'),
                        nullable=False, index=True)
    last_message_id = db.Column(db.Integer)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'area_id',
                                           name='uq_lastread_user_area'),)


class CalendarEvent(db.Model):
    """Sysop-managed calendar event. All times UTC."""
    __tablename__ = 'calendar_events'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    starts_at = db.Column(db.DateTime, nullable=False, index=True)
    ends_at = db.Column(db.DateTime)
    location = db.Column(db.String(200))
    is_published = db.Column(db.Boolean, default=True, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                              nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship('User')


class FileQueueEntry(db.Model):
    """Pending uploads awaiting sysop approval before going public."""
    __tablename__ = 'file_queue'

    id = db.Column(db.Integer, primary_key=True)
    file_area_id = db.Column(db.Integer, db.ForeignKey('file_areas.id'),
                             nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    quarantine_path = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    size_bytes = db.Column(db.BigInteger)
    status = db.Column(db.String(16), default='pending', index=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    reviewed_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    file_area = db.relationship('FileArea')
    user = db.relationship('User', foreign_keys=[user_id])
    reviewed_by = db.relationship('User', foreign_keys=[reviewed_by_id])


class GeminiCapsule(db.Model):
    """Per-user Gemini (gemtext) capsule — small static page exposed at
    /gemini/<username>. Sysop can also publish a site-wide capsule."""
    __tablename__ = 'gemini_capsules'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        unique=True, nullable=False, index=True)
    title = db.Column(db.String(120))
    content = db.Column(db.Text)             # raw gemtext
    is_published = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('gemini_capsule',
                                                      uselist=False,
                                                      cascade='all, delete-orphan'))


class WordFilter(db.Model):
    """Naughty-word filter — replace matched terms with ****."""
    __tablename__ = 'word_filters'

    id = db.Column(db.Integer, primary_key=True)
    pattern = db.Column(db.String(80), unique=True, nullable=False)
    replacement = db.Column(db.String(80), default='****')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Notification(db.Model):
    """In-app notification for a user — @mentions, replies, sysop pings."""
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    kind = db.Column(db.String(32), nullable=False, index=True)
    title = db.Column(db.String(200))
    body = db.Column(db.Text)
    target_url = db.Column(db.String(500))
    is_read = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    user = db.relationship('User', backref=db.backref('notifications',
                                                      lazy='dynamic'))


class PostReaction(db.Model):
    """User reaction to a board post — one row per (user, post, kind).

    `kind` is a slug: 'like', 'heart', 'lol', 'wow', 'sad'."""
    __tablename__ = 'post_reactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'),
                        nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User')
    post = db.relationship('Post', backref='reactions')

    __table_args__ = (db.UniqueConstraint('user_id', 'post_id', 'kind',
                                           name='uq_post_reaction'),)


class Webhook(db.Model):
    """Outbound webhook — POST JSON to a URL when an event fires.

    `event` is one of: shout, post, bulletin, login, achievement, broadcast,
    sysop_page, echomail -- see docs/23-webhooks.md for what payload keys
    each one actually sends (they differ per event; there is no universal
    placeholder set). `template` is an optional JSON body using {key}
    placeholders substituted from that event's payload; blank uses the
    default json.dumps(payload) shape."""
    __tablename__ = 'webhooks'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    event = db.Column(db.String(40), nullable=False, index=True)
    method = db.Column(db.String(10), default='POST')
    template = db.Column(db.Text)
    secret = db.Column(db.String(120))
    is_active = db.Column(db.Boolean, default=True)
    last_called_at = db.Column(db.DateTime)
    last_status = db.Column(db.Integer)
    last_error = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Achievement(db.Model):
    """A badge a user can earn. Defined here so sysop sees the catalog."""
    __tablename__ = 'achievements'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    icon = db.Column(db.String(40))     # bootstrap-icon class fragment
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class UserAchievement(db.Model):
    """Award row — user X earned achievement Y at time Z."""
    __tablename__ = 'user_achievements'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    achievement_id = db.Column(db.Integer, db.ForeignKey('achievements.id'),
                               nullable=False, index=True)
    earned_at = db.Column(db.DateTime, default=datetime.utcnow,
                          nullable=False, index=True)

    user = db.relationship('User', backref='achievements_earned')
    achievement = db.relationship('Achievement')

    __table_args__ = (db.UniqueConstraint('user_id', 'achievement_id',
                                           name='uq_user_achievement'),)


class MotdEntry(db.Model):
    """Random message-of-the-day pool. Login screens show one at random."""
    __tablename__ = 'motd_entries'

    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    weight = db.Column(db.Integer, default=1)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CallerLog(db.Model):
    """Per-login record — who connected, from where, on what protocol."""
    __tablename__ = 'caller_log'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    username = db.Column(db.String(80))
    service = db.Column(db.String(20))
    ip_address = db.Column(db.String(45))
    country = db.Column(db.String(60))
    duration_seconds = db.Column(db.Integer)
    started_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)
    # InterBBS Last Callers (anetbbs/echomail/interbbs_sync.py) -- same
    # shape/meaning as WallPost.origin_bbs/remote_msg_id. NULL = local
    # login. ip_address is NEVER populated for imported rows (privacy --
    # no precedent anywhere in this codebase for cross-BBS IP sharing).
    origin_bbs = db.Column(db.String(100), nullable=True)
    remote_msg_id = db.Column(db.String(100), nullable=True, index=True, unique=True)


class DialoutDestination(db.Model):
    """A registered remote BBS for the telnet/SSH dial-out menu.

    Sysop maintains the list via /admin/dialout. Users see the directory
    when picking 'Dial Out' from the BBS menu."""
    __tablename__ = 'dialout_destinations'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    hostname = db.Column(db.String(160), nullable=False)
    port = db.Column(db.Integer, default=23)
    protocol = db.Column(db.String(16), default='telnet')
    description = db.Column(db.Text)
    sort_order = db.Column(db.Integer, default=100)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SharedFileLink(db.Model):
    """Anonymous-access token for a file in a file area.

    Used to share a download URL with someone who isn't a registered user
    of this BBS. Token is opaque, optionally with an expiry and a
    download-count limit. Audit log of accesses kept for the sysop."""
    __tablename__ = 'shared_file_links'

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(32), unique=True, nullable=False, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                              nullable=False, index=True)
    file_area_id = db.Column(db.Integer, db.ForeignKey('file_areas.id'),
                             nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    note = db.Column(db.String(200))
    expires_at = db.Column(db.DateTime, index=True)
    max_downloads = db.Column(db.Integer)               # null = unlimited
    download_count = db.Column(db.Integer, default=0)
    last_accessed_at = db.Column(db.DateTime)
    last_accessed_ip = db.Column(db.String(45))
    is_revoked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    creator = db.relationship('User')
    file_area = db.relationship('FileArea')

    @property
    def is_valid(self):
        if self.is_revoked:
            return False
        if self.expires_at and self.expires_at <= datetime.utcnow():
            return False
        if self.max_downloads is not None and \
                (self.download_count or 0) >= self.max_downloads:
            return False
        return True


class MrcIrcBridge(db.Model):
    """Bidirectional MRC ↔ IRC relay configuration.

    A bridge connects an MRC room to an IRC server+channel; messages flow
    both ways with sender-name prefixing so participants on each side can
    tell who said what."""
    __tablename__ = 'mrc_irc_bridges'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    mrc_room = db.Column(db.String(80), nullable=False)
    mrc_handle = db.Column(db.String(40), default='ircbridge')
    mrc_ws_url = db.Column(db.String(255))            # ws://host/ws
    irc_server = db.Column(db.String(120), nullable=False)
    irc_port = db.Column(db.Integer, default=6667)
    irc_use_ssl = db.Column(db.Boolean, default=False)
    irc_nick = db.Column(db.String(40), default='ANETBridge')
    irc_channel = db.Column(db.String(80), nullable=False)
    irc_channel_key = db.Column(db.String(80))
    sasl_user = db.Column(db.String(80))
    sasl_pass = db.Column(db.String(120))
    is_active = db.Column(db.Boolean, default=False)
    last_started_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<MrcIrcBridge {self.name}: {self.mrc_room} <-> {self.irc_channel}>'


class AnsiArt(db.Model):
    """Reusable ANSI art piece — created via the web editor.

    Stored two ways:
        grid_json    JSON-encoded {width, height, cells: [...]} for editor reload
        ansi_text    pre-rendered escape-coded text for serving to telnet
    """
    __tablename__ = 'ansi_art'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    description = db.Column(db.Text)
    width = db.Column(db.Integer, default=80)
    height = db.Column(db.Integer, default=25)
    grid_json = db.Column(db.Text)
    ansi_text = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    created_by = db.relationship('User')

    def __repr__(self):
        return f'<AnsiArt {self.slug}>'


class IrcChannelLog(db.Model):
    """Per-channel IRC log lines. Sysop opt-in via env var IRC_LOG_CHANNELS."""
    __tablename__ = 'irc_channel_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    server = db.Column(db.String(120), index=True)
    channel = db.Column(db.String(80), index=True)
    nick = db.Column(db.String(40))
    kind = db.Column(db.String(16))   # message, action, join, part, etc.
    text = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)


class UserBlock(db.Model):
    """One user blocking another. Blocked target cannot send PMs to the
    blocker; @mentions of the blocker by the target are suppressed; and
    the UI can dim posts authored by blocked users."""
    __tablename__ = 'user_blocks'

    id = db.Column(db.Integer, primary_key=True)
    blocker_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                           nullable=False, index=True)
    blocked_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                           nullable=False, index=True)
    reason = db.Column(db.String(280))
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    blocker = db.relationship('User', foreign_keys=[blocker_id],
                              backref=db.backref('blocks_made', lazy='dynamic'))
    blocked = db.relationship('User', foreign_keys=[blocked_id],
                              backref=db.backref('blocked_by', lazy='dynamic'))

    __table_args__ = (db.UniqueConstraint('blocker_id', 'blocked_id',
                                          name='uq_user_block'),)


class BoardModerator(db.Model):
    """Many-to-many: a user is a moderator of a board.

    Moderators can delete posts/replies in their board (in addition to
    sysops/admins). Sysop assigns moderators in /admin/boards/<id>/moderators."""
    __tablename__ = 'board_moderators'

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id'),
                         nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    granted_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False)

    board = db.relationship('Board', backref=db.backref('moderators',
                                                        lazy='dynamic',
                                                        cascade='all, delete-orphan'))
    user = db.relationship('User', backref=db.backref('moderating',
                                                      lazy='dynamic'))

    __table_args__ = (db.UniqueConstraint('board_id', 'user_id',
                                          name='uq_board_moderator'),)


class UserGroup(db.Model):
    """A user-formed group/clan: name, tag, leader, members.

    Useful for door-game teams (e.g. tradewars factions) or BBS art crews.
    The `tag` is shown next to usernames as a badge."""
    __tablename__ = 'user_groups'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True, index=True)
    tag = db.Column(db.String(8))                       # short prefix tag
    description = db.Column(db.Text)
    leader_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                          nullable=False, index=True)
    is_open = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    leader = db.relationship('User', foreign_keys=[leader_id],
                             backref='groups_led')


class UserGroupMember(db.Model):
    """Membership of a user in a group."""
    __tablename__ = 'user_group_members'

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('user_groups.id'),
                         nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow,
                          nullable=False)

    group = db.relationship('UserGroup',
                            backref=db.backref('members', lazy='dynamic',
                                               cascade='all, delete-orphan'))
    user = db.relationship('User', backref=db.backref('group_memberships',
                                                      lazy='dynamic'))

    __table_args__ = (db.UniqueConstraint('group_id', 'user_id',
                                          name='uq_group_member'),)


class BoardSubscription(db.Model):
    """User following a board — gets notifications for new top-level posts."""
    __tablename__ = 'board_subscriptions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id'),
                         nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False)

    user = db.relationship('User', backref=db.backref('board_subs',
                                                      lazy='dynamic'))
    board = db.relationship('Board', backref=db.backref('subscribers',
                                                        lazy='dynamic'))

    __table_args__ = (db.UniqueConstraint('user_id', 'board_id',
                                          name='uq_board_subscription'),)


class IpBan(db.Model):
    """Sysop-banned IP / CIDR. Login + register routes refuse matching IPs."""
    __tablename__ = 'ip_bans'

    id = db.Column(db.Integer, primary_key=True)
    cidr = db.Column(db.String(45), nullable=False, unique=True, index=True)
    reason = db.Column(db.String(280))
    banned_by_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                             index=True)
    expires_at = db.Column(db.DateTime, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False)

    banned_by = db.relationship('User')


class IpWhitelist(db.Model):
    """IPs / CIDRs that bypass all ban and country-block checks."""
    __tablename__ = 'ip_whitelist'

    id = db.Column(db.Integer, primary_key=True)
    cidr = db.Column(db.String(45), nullable=False, unique=True, index=True)
    note = db.Column(db.String(280))
    added_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    added_by = db.relationship('User')


class AutoBanConfig(db.Model):
    """Sysop-configurable thresholds for the login auto-ban (see
    anetbbs/web/auth.py _login_rate_exceeded). Singleton row, same
    pattern as SmtpConfig. Defaults match the feature-request resolution:
    same 10-attempts/5-minute trigger as before, but a 1-hour ban instead
    of permanent, with everything editable and a full on/off switch."""
    __tablename__ = 'auto_ban_config'

    id = db.Column(db.Integer, primary_key=True)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    attempt_limit = db.Column(db.Integer, default=10, nullable=False)
    window_seconds = db.Column(db.Integer, default=300, nullable=False)
    # 0 = permanent ban (matches IpBan.expires_at=None), same as the
    # manual ban form's "TTL days, 0 = permanent" convention.
    ban_duration_hours = db.Column(db.Integer, default=1, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    @classmethod
    def get(cls):
        """Return the singleton config row, creating it (with defaults) if absent."""
        row = cls.query.first()
        if row is None:
            row = cls()
            db.session.add(row)
            db.session.commit()
        return row


class BoardLastRead(db.Model):
    """Per-user last-visit timestamp per board, for unread counts."""
    __tablename__ = 'board_last_read'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    board_id = db.Column(db.Integer, db.ForeignKey('boards.id'),
                         nullable=False, index=True)
    last_read_at = db.Column(db.DateTime, default=datetime.utcnow,
                             nullable=False)

    __table_args__ = (db.UniqueConstraint('user_id', 'board_id',
                                          name='uq_board_last_read'),)


class BbsAnsiScreen(db.Model):
    """Named raw ANSI/CP437 screen the BBS displays at lifecycle points.

    Built-in slots used by the session loop:
      - 'welcome'  : pre-login banner shown to telnet visitors. SSH/rlogin
                     auto-login users skip it (their client already gave
                     credentials).
      - 'goodbye'  : shown on logoff to every protocol.
      - 'newuser'  : shown after registration, before first login.
    The sysop can also create custom slots and reference them from menu
    items (action_type='ansi', action_args=<slot name>).
    """
    __tablename__ = 'bbs_ansi_screens'

    id = db.Column(db.Integer, primary_key=True)
    slot = db.Column(db.String(40), unique=True, nullable=False, index=True)
    title = db.Column(db.String(120))
    body = db.Column(db.Text, nullable=False)         # Raw ANSI bytes (text)
    pause_after = db.Column(db.Boolean, default=False)  # Wait for Enter
    is_active = db.Column(db.Boolean, default=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)


class UserTimeBudget(db.Model):
    """Mystic-style time budget per user.

    `time_limit_min` is the per-session ceiling (0 = unlimited).
    `daily_limit_min` is the per-24h ceiling.
    `bank_minutes` accumulates earned time the user can spend on doors etc.
    Counter columns are reset on demand by the daily roll-over."""
    __tablename__ = 'user_time_budgets'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        unique=True, nullable=False, index=True)
    time_limit_min = db.Column(db.Integer, default=60)         # per session
    daily_limit_min = db.Column(db.Integer, default=240)       # per 24h
    bank_minutes = db.Column(db.Integer, default=0)
    used_today_min = db.Column(db.Integer, default=0)
    last_reset_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('time_budget',
                                                       uselist=False))


class OneLiner(db.Model):
    """Logoff one-liners — short messages users leave on the way out.
    Like Mystic's 'one-liner' wall."""
    __tablename__ = 'one_liners'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    text = db.Column(db.String(120), nullable=False)
    is_hidden = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    user = db.relationship('User')


class FileRatio(db.Model):
    """Per-user upload/download bytes tracker (Synchronet/Mystic style)."""
    __tablename__ = 'file_ratios'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        unique=True, nullable=False, index=True)
    bytes_uploaded = db.Column(db.BigInteger, default=0)
    bytes_downloaded = db.Column(db.BigInteger, default=0)
    files_uploaded = db.Column(db.Integer, default=0)
    files_downloaded = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow,
                             onupdate=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('file_ratio',
                                                       uselist=False))


class NewUserQuestion(db.Model):
    """Sysop-defined question shown on registration. Answer stored
    verbatim per user in NewUserAnswer."""
    __tablename__ = 'newuser_questions'

    id = db.Column(db.Integer, primary_key=True)
    prompt = db.Column(db.String(280), nullable=False)
    is_required = db.Column(db.Boolean, default=False)
    sort_order = db.Column(db.Integer, default=0, index=True)
    is_active = db.Column(db.Boolean, default=True, index=True)


class NewUserAnswer(db.Model):
    """A user's answer to a NewUserQuestion."""
    __tablename__ = 'newuser_answers'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    question_id = db.Column(db.Integer, db.ForeignKey('newuser_questions.id'),
                            nullable=False, index=True)
    answer = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User')
    question = db.relationship('NewUserQuestion')

    __table_args__ = (db.UniqueConstraint('user_id', 'question_id',
                                          name='uq_newuser_answer'),)


class NodeActivity(db.Model):
    """Terminal session live activity — heartbeat from each running
    telnet/SSH/rlogin session so the web sysop can see what each user
    is currently doing (NodeSpy-style)."""
    __tablename__ = 'node_activity'

    id = db.Column(db.Integer, primary_key=True)
    slot = db.Column(db.Integer, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    username = db.Column(db.String(80), index=True)
    protocol = db.Column(db.String(16))                # telnet/ssh/rlogin
    peer = db.Column(db.String(80))
    page = db.Column(db.String(80))                    # current menu/area
    action = db.Column(db.String(120))                 # last action label
    last_screen = db.Column(db.Text)                   # snapshot of last screen
    started_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow,
                          nullable=False, index=True)
    # Sysop kick flag — set by /admin/control/nodespy/<slot>/kick.
    # The terminal session's periodic checker polls this on its own
    # asyncio loop and self-disconnects when set. Cross-process safe
    # since the flag lives in SQLite (anetbbs-web sets, anetbbs-telnet
    # reads).
    kick_requested = db.Column(db.Boolean, default=False, nullable=False,
                                index=True)
    kick_reason = db.Column(db.String(200))

    user = db.relationship('User')


class MenuTranslation(db.Model):
    """Multi-language menu string overrides.

    Look up by (lang, key) — key is something like 'main.title' or
    'item.boards.label'. Falls back to the source text if missing."""
    __tablename__ = 'menu_translations'

    id = db.Column(db.Integer, primary_key=True)
    lang = db.Column(db.String(8), nullable=False, index=True)
    key = db.Column(db.String(200), nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('lang', 'key',
                                          name='uq_menu_trans'),)


class Newsletter(db.Model):
    """Sysop newsletter — broadcast a multi-paragraph message to all users
    via PM (and optionally email if SMTP is configured)."""
    __tablename__ = 'newsletters'

    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    sent_by_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                           nullable=False)
    sent_at = db.Column(db.DateTime)                   # null = draft
    recipients_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    sent_by = db.relationship('User')


class BbsDirectoryEntry(db.Model):
    """An entry in the inter-BBS directory.

    Refreshed daily from two sources:
      - `sbbsimsg.lst` from Vertrauen (Synchronet BBSes) — `source='sbbsimsg'`
      - `anetbbs.lst` from the federation hub (ANetBBS BBSes) — `source='anetbbs'`
    One row per known BBS that listens on UDP/SYSTAT + TCP/MSP. The
    `software` column lets the /imsg/ directory page filter or badge
    by BBS family."""
    __tablename__ = 'bbs_directory'

    id = db.Column(db.Integer, primary_key=True)
    hostname = db.Column(db.String(160), unique=True, nullable=False, index=True)
    ip_address = db.Column(db.String(45))
    name = db.Column(db.String(160))
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow,
                             index=True)
    notes = db.Column(db.Text)
    # Extended metadata (anetbbs.lst sources fill these; sbbsimsg rows
    # leave them NULL).
    sysop = db.Column(db.String(120))
    location = db.Column(db.String(120))
    software = db.Column(db.String(40))            # e.g. 'ANetBBS', 'Synchronet', 'Mystic'
    software_version = db.Column(db.String(40))
    msp_port = db.Column(db.Integer, default=18)
    systat_port = db.Column(db.Integer, default=11)
    source = db.Column(db.String(20), default='sbbsimsg', index=True)
    # source: 'sbbsimsg' / 'anetbbs' / 'manual'


class InstantMessage(db.Model):
    """Inter-BBS instant message (RFC 1312 MSP / Synchronet IMSG).

    A short, real-time message delivered from another BBS over MSP. We
    store it so it's visible in the user's IM inbox even if they were
    offline when it arrived. `origin` distinguishes how it got here in
    case we add other transports later (e.g. an MRC IM bridge)."""
    __tablename__ = 'instant_messages'

    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                             nullable=False, index=True)
    sender_label = db.Column(db.String(160))   # "user@bbsname" if known
    sender_host = db.Column(db.String(120))    # peer IP / hostname
    body = db.Column(db.Text, nullable=False)
    received_at = db.Column(db.DateTime, default=datetime.utcnow,
                            nullable=False, index=True)
    is_read = db.Column(db.Boolean, default=False, index=True)
    origin = db.Column(db.String(20), default='msp')

    recipient = db.relationship('User', backref='instant_messages')


class MessageVote(db.Model):
    """Per-user up/down vote on any kind of message.

    `message_type` selects the target table — one of 'post' (board posts),
    'echomail' (echomail messages), 'netmail' (FTN netmail), 'pm' (private
    messages). `value` is +1 (upvote) or -1 (downvote). The unique
    constraint keeps each user to one vote per message; the route layer
    handles toggling and switching."""
    __tablename__ = 'message_votes'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                        nullable=False, index=True)
    message_type = db.Column(db.String(20), nullable=False, index=True)
    message_id = db.Column(db.Integer, nullable=False, index=True)
    value = db.Column(db.Integer, nullable=False, default=1)  # +1 or -1
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'message_type', 'message_id',
                             name='uq_message_vote_user'),
        db.Index('ix_message_vote_target', 'message_type', 'message_id'),
    )

    user = db.relationship('User')


class BadAreaLog(db.Model):
    """Echomail tags received for areas we don't carry — sysop review queue.

    Mirrors SBBSecho's BadAreaFile semantics. We don't auto-create areas on
    inbound traffic; the sysop reviews this list and either subscribes or
    ignores. A repeat-counter helps spot the noisy ones.
    """
    __tablename__ = 'bad_area_log'

    id = db.Column(db.Integer, primary_key=True)
    network_id = db.Column(db.Integer,
                           db.ForeignKey('echomail_networks.id'),
                           nullable=False, index=True)
    tag = db.Column(db.String(100), nullable=False, index=True)
    sample_from = db.Column(db.String(100))
    sample_subject = db.Column(db.String(200))
    count = db.Column(db.Integer, default=1)
    first_seen_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('network_id', 'tag', name='uq_bad_area_per_net'),
    )

    network = db.relationship('EchomailNetwork')

    def __repr__(self):
        return f'<BadAreaLog {self.network_id}:{self.tag} x{self.count}>'


class RssFeed(db.Model):
    """A subscribed RSS / Atom feed.

    Sysops manage the list at /admin/rss/ ; the background poller fetches
    each active feed periodically (default 30 min) and stores items in
    RssItem. Per-user read state lives in RssReadStatus.
    """
    __tablename__ = 'rss_feeds'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    url = db.Column(db.String(500), nullable=False, unique=True)
    site_url = db.Column(db.String(500))   # human-facing homepage of the feed
    description = db.Column(db.String(500))
    category = db.Column(db.String(60), default='general')
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    min_access_level = db.Column(db.Integer, default=0)  # 0 = all registered users
    last_fetched_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship('RssItem', backref='feed',
                            cascade='all, delete-orphan',
                            order_by='RssItem.published_at.desc()')

    def __repr__(self):
        return f'<RssFeed {self.name} @ {self.url}>'


class RssItem(db.Model):
    """A single article from an RSS feed."""
    __tablename__ = 'rss_items'

    id = db.Column(db.Integer, primary_key=True)
    feed_id = db.Column(db.Integer,
                        db.ForeignKey('rss_feeds.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    guid = db.Column(db.String(500), nullable=False, index=True)
    title = db.Column(db.String(500))
    link = db.Column(db.String(1000))
    author = db.Column(db.String(200))
    summary = db.Column(db.Text)         # plain-text or limited-HTML summary
    content_html = db.Column(db.Text)    # full HTML if available (atom:content)
    image_url = db.Column(db.String(1000))  # first image found in content/enclosure
    published_at = db.Column(db.DateTime, index=True)
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('feed_id', 'guid', name='uq_rss_item_per_feed'),
    )

    def __repr__(self):
        return f'<RssItem {self.feed_id}:{self.title!r}>'


class RssReadStatus(db.Model):
    """Per-user read marker for an RssItem.

    Presence of a row = user has read it. Absence = unread. We don't
    bother with explicit 'unread' rows.
    """
    __tablename__ = 'rss_read_status'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer,
                        db.ForeignKey('users.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    item_id = db.Column(db.Integer,
                        db.ForeignKey('rss_items.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    read_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'item_id', name='uq_rss_read_per_user'),
    )


# ---------------------------------------------------------------------------
# Wiki — collaborative documentation pages with revision history.
# ---------------------------------------------------------------------------

class WikiPage(db.Model):
    """A single wiki page identified by a URL slug.

    Pages are markdown with `[[Wiki Link]]` cross-references rendered to
    `/wiki/<slug>` URLs. Edits write a new WikiRevision and bump the
    page's body/title/updated_at to match the latest revision.
    """
    __tablename__ = 'wiki_pages'

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(160), nullable=False, unique=True, index=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False, default='')
    summary = db.Column(db.String(300))     # last edit summary, displayed in lists
    is_locked = db.Column(db.Boolean, default=False, nullable=False)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False, index=True)
    view_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)
    created_by_id = db.Column(db.Integer,
                              db.ForeignKey('users.id', ondelete='SET NULL'),
                              nullable=True)
    updated_by_id = db.Column(db.Integer,
                              db.ForeignKey('users.id', ondelete='SET NULL'),
                              nullable=True)

    revisions = db.relationship(
        'WikiRevision', backref='page', lazy='dynamic',
        cascade='all, delete-orphan',
        order_by='WikiRevision.rev_num.desc()')
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    updated_by = db.relationship('User', foreign_keys=[updated_by_id])

    def __repr__(self):
        return f'<WikiPage {self.slug!r}>'


class WikiRevision(db.Model):
    """An immutable historical version of a WikiPage.

    Every edit writes one. rev_num is monotonic per-page (1, 2, 3, …)
    so revisions sort cleanly without relying on timestamp tie-breaks.
    """
    __tablename__ = 'wiki_revisions'

    id = db.Column(db.Integer, primary_key=True)
    page_id = db.Column(db.Integer,
                        db.ForeignKey('wiki_pages.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    rev_num = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    edit_summary = db.Column(db.String(300))
    author_id = db.Column(db.Integer,
                          db.ForeignKey('users.id', ondelete='SET NULL'),
                          nullable=True)
    author_ip = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=datetime.utcnow,
                           nullable=False, index=True)

    author = db.relationship('User')

    __table_args__ = (
        db.UniqueConstraint('page_id', 'rev_num', name='uq_wiki_rev_per_page'),
    )

    def __repr__(self):
        return f'<WikiRevision page={self.page_id} rev={self.rev_num}>'


class ScheduledEvent(db.Model):
    """Sysop-defined scheduled tasks — door maintenance, DB vacuum, log
    rotation, ad-hoc shell commands. Anything that needs to run on a
    cron-style cadence outside the web request lifecycle.

    Each row references one *handler* (``handler_key``) from the bundled
    registry in :mod:`anetbbs.events.handlers`. Handlers are functions
    that take a JSON params dict; the runner serializes the dict from
    ``params_json``. Adding a new scheduled action = adding a handler
    + a row, no scheduler-thread changes.

    The schedule itself is a JSON document so we don't need a new column
    per cadence kind. Shapes:

        {"kind": "daily", "time": "03:00"}                  — every day at HH:MM UTC
        {"kind": "hourly", "minute": 5}                     — every hour at :MM
        {"kind": "weekly", "day": 6, "time": "04:30"}       — DOW 0=Mon..6=Sun
        {"kind": "interval", "minutes": 30}                 — every N minutes after last run

    ``last_run_at`` plus the schedule define ``next_run_at`` (computed
    in code, not stored — recomputing keeps schedule edits simple).
    """
    __tablename__ = 'scheduled_events'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    # Stable identifier the runner looks up. e.g. 'tw2_maint', 'db_vacuum',
    # 'log_rotate', 'shell'.
    handler_key = db.Column(db.String(80), nullable=False, index=True)
    # JSON-encoded params; handler-specific shape. Empty {} when none.
    params_json = db.Column(db.Text, default='{}', nullable=False)
    # JSON-encoded schedule descriptor — see class docstring for shapes.
    schedule_json = db.Column(db.Text, default='{"kind": "daily", "time": "03:00"}',
                              nullable=False)
    is_enabled = db.Column(db.Boolean, default=True, nullable=False, index=True)
    # Liveness tracking — runner stamps these every fire.
    last_run_at = db.Column(db.DateTime, index=True)
    last_status = db.Column(db.String(20))   # 'ok' | 'fail' | 'skip'
    last_duration_ms = db.Column(db.Integer)
    # Truncated to first ~4 KB of stdout+stderr for the admin UI.
    last_output = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<ScheduledEvent {self.id} {self.handler_key} enabled={self.is_enabled}>'


# ---------------------------------------------------------------------------
# Graffiti Wall
# ---------------------------------------------------------------------------

class WallPost(db.Model):
    """One graffiti-wall post — up to two pipe-color-encoded lines."""
    __tablename__ = 'wall_posts'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    display_name = db.Column(db.String(100))
    # Raw text with Synchronet/Mystic pipe color codes (|12Hello |07world).
    # Each line max ~160 bytes stored; 79 printable chars displayed.
    line1 = db.Column(db.String(200), nullable=False, default='')
    line2 = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    node = db.Column(db.Integer, default=1)
    # InterBBS Wall (anetbbs/echomail/interbbs_sync.py): NULL = authored
    # locally on this install. Non-NULL = imported from another ANetBBS
    # system over echomail -- origin_bbs is the sending system's name,
    # remote_msg_id is the source EchomailMessage.msg_id (dedup key, and
    # the load-bearing check that prevents this post from ever being
    # relayed back out again, which would bounce forever).
    origin_bbs = db.Column(db.String(100), nullable=True)
    remote_msg_id = db.Column(db.String(100), nullable=True, index=True, unique=True)

    def __repr__(self):
        return f'<WallPost {self.id} by {self.username}>'


# ---------------------------------------------------------------------------
# Logon / Logoff Modules
# ---------------------------------------------------------------------------

class LoginModule(db.Model):
    """An action that runs automatically at logon or logoff for the user.

    module_type values:
      wall        — show/prompt on the graffiti wall (params: none)
      ansi        — display an ANSI screen slot (params: {"slot": "welcome"})
      shell       — run a shell command (params: {"command": "/path/to/script"})
      door_native — run a native Linux door (params: {"path": "...", "args": "..."})
      door_python — run a Python door module (params: {"module": "...", ...})

    event_type:  'logon' | 'logoff'
    """
    __tablename__ = 'login_modules'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    event_type = db.Column(db.String(10), nullable=False, index=True)   # logon | logoff
    module_type = db.Column(db.String(20), nullable=False)              # wall | ansi | shell | …
    params_json = db.Column(db.Text, default='{}', nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    # Minimum user access_level to see this module (0 = everyone after login).
    min_access_level = db.Column(db.Integer, default=0, nullable=False)
    # Sort order — lower numbers run first.
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    # If True, skip this module when the user chooses fast logon.
    skip_on_fast_logon = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<LoginModule {self.id} {self.event_type}:{self.module_type} {self.name!r}>'


class UserField(db.Model):
    """Sysop-defined custom user profile fields.

    field_type values: text | url | number | select | textarea
    choices: JSON-encoded list of strings (select only), e.g. '["Option A","Option B"]'
    """
    __tablename__ = 'user_fields'

    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(50), unique=True, nullable=False)   # internal slug
    label           = db.Column(db.String(100), nullable=False)               # display label
    field_type      = db.Column(db.String(20), default='text', nullable=False)
    choices         = db.Column(db.Text)                                      # JSON for select
    required        = db.Column(db.Boolean, default=False, nullable=False)
    show_in_profile = db.Column(db.Boolean, default=True, nullable=False)
    sort_order      = db.Column(db.Integer, default=0, nullable=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    values = db.relationship('UserFieldValue', backref='field', lazy='dynamic',
                              cascade='all, delete-orphan')

    def __repr__(self):
        return f'<UserField {self.name!r}>'


class UserFieldValue(db.Model):
    """A single user's value for a sysop-defined custom field."""
    __tablename__ = 'user_field_values'

    id       = db.Column(db.Integer, primary_key=True)
    user_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    field_id = db.Column(db.Integer, db.ForeignKey('user_fields.id'), nullable=False, index=True)
    value    = db.Column(db.Text)

    __table_args__ = (db.UniqueConstraint('user_id', 'field_id', name='uq_user_field'),)

    def __repr__(self):
        return f'<UserFieldValue user={self.user_id} field={self.field_id}>'


BUILTIN_FIELDS = [
    ('display_name', 'Display Name'),
    ('bio',          'Bio / About Me'),
    ('location',     'Location'),
    ('website',      'Website'),
    ('signature',    'Forum Signature'),
    ('tagline',      'FTN Tagline'),
    ('date_of_birth','Date of Birth'),
    ('show_email',   'Show Email Option'),
]


class BuiltinFieldConfig(db.Model):
    """Sysop-controlled enable/disable for each built-in profile field."""
    __tablename__ = 'builtin_field_configs'

    id         = db.Column(db.Integer, primary_key=True)
    field_name = db.Column(db.String(50), unique=True, nullable=False)
    enabled    = db.Column(db.Boolean, default=True, nullable=False)

    def __repr__(self):
        return f'<BuiltinFieldConfig {self.field_name!r} enabled={self.enabled}>'


def get_builtin_field_config():
    """Return {field_name: bool} for all built-in fields, defaulting to True."""
    rows = {r.field_name: r.enabled for r in BuiltinFieldConfig.query.all()}
    return {name: rows.get(name, True) for name, _label in BUILTIN_FIELDS}


# ---------------------------------------------------------------------------
# Hub functionality: downstream BinkP nodes, per-node echo subscriptions,
# outbound hold queue, and QWK node registry.
# ---------------------------------------------------------------------------

class HubIdentity(db.Model):
    """A distinct echomail/QWK hub identity this install operates.

    Most installs have exactly one (is_default=True), and nothing else
    in the app needs to know multi-hub identities exist at all --
    hub_identity_id FKs elsewhere default to this row automatically (see
    _default_hub_identity_id near the top of this file). A sysop running
    more than one real hub network -- own zone:net, own QWK hub ID, own
    downstream node pool, own nodelist, own join form -- creates
    additional rows via the admin HubIdentity CRUD.

    EchomailNetwork rows remain the *transport* config (BinkP or QWK) for
    a real-world hub; one HubIdentity can have a BinkP-transport row and
    a QWK-transport row both pointing at it here, mirroring how a hub's
    BinkP and QWK sides already share one real-world join process (see
    NetworkJoinConfig).
    """
    __tablename__ = 'hub_identities'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    # URL-safe, used in /join/<slug>/ and the per-identity nodelist route.
    slug = db.Column(db.String(50), nullable=False, unique=True, index=True)
    # Short QWK system ID (e.g. 'ANET') -- qnet-ftp convention: download
    # <hub_id>.qwk, upload <packet_id>.rep. Formalizes the previously
    # undocumented QWK_HUB_ID env var as this identity's value.
    qwk_hub_id = db.Column(db.String(16))
    binkp_zone = db.Column(db.Integer)
    binkp_net = db.Column(db.Integer)
    binkp_hub_node = db.Column(db.Integer, default=1)
    # Qualified-address domain suffix for this identity's nodelist/AKA
    # (see EchomailNetwork.ftn_domain for the same FSP-1028 constraint).
    binkp_domain = db.Column(db.String(8))
    # Nodelist metadata -- falls back to the install's generic
    # SYSOP_NAME/BBS_LOCATION config when blank.
    nodelist_sysop = db.Column(db.String(100))
    nodelist_location = db.Column(db.String(100))
    nodelist_phone = db.Column(db.String(50))
    nodelist_speed = db.Column(db.Integer, default=115200)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    # Exactly one row should have this set -- an application-level
    # invariant enforced in the admin CRUD form, not a DB constraint
    # (this codebase's SQLite migration helper only ever adds columns,
    # never adds/loosens real constraints on an existing table).
    is_default = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<HubIdentity {self.name!r} ({self.slug})>'


class BinkPNode(db.Model):
    """A downstream BinkP node that polls us as a hub.

    Unlike EchomailNetwork (which represents an *upstream* hub we connect to),
    a BinkPNode is a *downstream* peer: they authenticate to our listener,
    pick up mail we've tossed to them, and deliver their own outbound mail.
    """
    __tablename__ = 'binkp_nodes'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    ftn_address = db.Column(db.String(60), nullable=False, unique=True, index=True)
    password = db.Column(db.String(255), nullable=False)
    sysop = db.Column(db.String(100))
    system_name = db.Column(db.String(100))
    location = db.Column(db.String(100))
    email = db.Column(db.String(200))
    phone = db.Column(db.String(50))
    baud = db.Column(db.Integer, default=115200)
    is_active = db.Column(db.Boolean, default=True, index=True)
    last_seen_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    # Which hub identity this downstream peer belongs to (see
    # HubIdentity). Scopes inbound BinkP auth and outbound packet
    # sender-stamping so nodes of different hub identities can't
    # authenticate against each other's AKA.
    hub_identity_id = db.Column(db.Integer, db.ForeignKey('hub_identities.id'),
                                default=_default_hub_identity_id, nullable=True, index=True)

    subscriptions = db.relationship('EchoAreaNode', backref='node',
                                    lazy='dynamic', cascade='all, delete-orphan')
    hold_queue = db.relationship('BinkPHoldQueue', backref='node',
                                 lazy='dynamic', cascade='all, delete-orphan')
    hub_identity = db.relationship('HubIdentity', backref=db.backref('binkp_nodes', lazy='dynamic'))

    def __repr__(self):
        return f'<BinkPNode {self.ftn_address}>'


class EchoAreaNode(db.Model):
    """Per-node echo area subscription for hub fan-out.

    A row here means: toss new messages in `echo_area_id` to `node_id` when
    they arrive or are locally composed. The tosser reads these to build the
    BinkPHoldQueue entries.
    """
    __tablename__ = 'echo_area_nodes'

    id = db.Column(db.Integer, primary_key=True)
    node_id = db.Column(db.Integer, db.ForeignKey('binkp_nodes.id'),
                        nullable=False, index=True)
    echo_area_id = db.Column(db.Integer, db.ForeignKey('echo_areas.id'),
                             nullable=False, index=True)
    subscribed_at = db.Column(db.DateTime, default=datetime.utcnow)

    echo_area = db.relationship('EchoArea',
                                backref=db.backref('node_subscriptions', lazy='dynamic'))

    __table_args__ = (db.UniqueConstraint('node_id', 'echo_area_id',
                                          name='uq_echo_area_node'),)

    def __repr__(self):
        return f'<EchoAreaNode node={self.node_id} area={self.echo_area_id}>'


class BinkPHoldQueue(db.Model):
    """Outbound echomail message held for delivery to a downstream BinkP node.

    The BinkP listener checks these when a node connects and flushes all
    pending entries for that node's FTN address into one .pkt file.
    """
    __tablename__ = 'binkp_hold_queue'

    id = db.Column(db.Integer, primary_key=True)
    node_id = db.Column(db.Integer, db.ForeignKey('binkp_nodes.id'),
                        nullable=False, index=True)
    message_id = db.Column(db.Integer, db.ForeignKey('echomail_messages.id'),
                           nullable=False, index=True)
    status = db.Column(db.String(16), default='pending', index=True)
    queued_at = db.Column(db.DateTime, default=datetime.utcnow,
                          nullable=False, index=True)
    sent_at = db.Column(db.DateTime)
    retry_count = db.Column(db.Integer, default=0)

    message = db.relationship('EchomailMessage',
                               backref=db.backref('hold_entries', lazy='dynamic'))

    __table_args__ = (db.UniqueConstraint('node_id', 'message_id',
                                           name='uq_binkp_hold'),)

    def __repr__(self):
        return f'<BinkPHoldQueue node={self.node_id} msg={self.message_id} {self.status}>'


class QWKNode(db.Model):
    """A downstream QWK node registered with our hub.

    Nodes poll via HTTP GET /qwkhub/<packet_id>.qwk (or FTP) to download a
    per-node QWK packet and POST their REP packets to upload new messages.
    The packet_id is their unique identifier on this hub (e.g. "MYSYS").
    """
    __tablename__ = 'qwk_nodes'

    id = db.Column(db.Integer, primary_key=True)
    packet_id = db.Column(db.String(8), nullable=False, unique=True, index=True)
    name = db.Column(db.String(100), nullable=False)
    sysop = db.Column(db.String(100))
    email = db.Column(db.String(200))
    password = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True, index=True)
    last_poll_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    # Which hub identity this downstream peer belongs to (see
    # HubIdentity). Resolves this node's QWK hub ID (packet filename
    # convention) -- packet_id itself stays globally unique across all
    # hub identities (not per-identity; a deliberate simplification, see
    # docs/CHANGELOG.md for the multi-hub-identity feature).
    hub_identity_id = db.Column(db.Integer, db.ForeignKey('hub_identities.id'),
                                default=_default_hub_identity_id, nullable=True, index=True)

    last_sent = db.relationship('QWKNodeLastSent', backref='node',
                                lazy='dynamic', cascade='all, delete-orphan')
    hub_identity = db.relationship('HubIdentity', backref=db.backref('qwk_nodes', lazy='dynamic'))

    def __repr__(self):
        return f'<QWKNode {self.packet_id}>'


class QWKNodeLastSent(db.Model):
    """Per-node, per-area high-water mark for QWK hub delivery.

    A row here means the node is subscribed to that echo area for QWK
    delivery. last_message_id is the id of the last EchomailMessage that
    was included in a packet for this node — NULL means subscribed but
    nothing sent yet. The QWK hub builder queries messages with id >
    last_message_id to build incremental packets.
    """
    __tablename__ = 'qwk_node_last_sent'

    id = db.Column(db.Integer, primary_key=True)
    node_id = db.Column(db.Integer, db.ForeignKey('qwk_nodes.id'),
                        nullable=False, index=True)
    echo_area_id = db.Column(db.Integer, db.ForeignKey('echo_areas.id'),
                             nullable=False, index=True)
    last_message_id = db.Column(db.Integer, db.ForeignKey('echomail_messages.id'),
                                nullable=True)
    conf_number = db.Column(db.Integer)

    echo_area = db.relationship('EchoArea',
                                backref=db.backref('qwk_subscriptions', lazy='dynamic'))
    last_message = db.relationship('EchomailMessage')

    __table_args__ = (db.UniqueConstraint('node_id', 'echo_area_id',
                                           name='uq_qwk_node_last_sent'),)

    def __repr__(self):
        return f'<QWKNodeLastSent node={self.node_id} area={self.echo_area_id}>'


class QWKNodeRequest(db.Model):
    """Self-service application from a BBS sysop to join as a QWK node.

    Submitted via BBS terminal (Option B flow). Hub sysop approves/denies
    through the web admin; on approval a QWKNode record is auto-created and
    credentials are shown to the applicant on their next terminal visit.
    """
    __tablename__ = 'qwk_node_requests'

    id                  = db.Column(db.Integer, primary_key=True)
    # Applicant-supplied
    bbs_name            = db.Column(db.String(100), nullable=False)
    packet_id           = db.Column(db.String(8),   nullable=False, index=True)
    sysop_name          = db.Column(db.String(100))
    email               = db.Column(db.String(200))
    bbs_address         = db.Column(db.String(200))
    notes               = db.Column(db.Text)
    # Submission meta
    status              = db.Column(db.String(20), default='pending', index=True)
    applied_via         = db.Column(db.String(20), default='terminal')
    applied_by_user_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    applied_by_username = db.Column(db.String(100))
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)
    # Review
    reviewed_at         = db.Column(db.DateTime)
    reviewed_by         = db.Column(db.String(100))
    deny_reason         = db.Column(db.Text)
    # Approval output
    generated_password  = db.Column(db.String(50))
    seen_by_applicant   = db.Column(db.Boolean, default=False)
    node_id             = db.Column(db.Integer, db.ForeignKey('qwk_nodes.id'), nullable=True)
    # Set by the hub on creation (whether created via the /qwkhub/apply
    # API from a remote BBS, or locally when this install IS the hub).
    # A remote applicant's terminal session doesn't have its own login
    # on the hub, so this opaque token is how it later re-identifies
    # its own request via GET /qwkhub/status/<token> without needing
    # one -- see anetbbs/web/qwk_hub.py.
    request_token       = db.Column(db.String(64), unique=True, index=True, nullable=True)
    # Which hub identity this application targets -- always the
    # install's default identity in practice today (the terminal
    # wizard and the /qwkhub/apply API are both default-identity-only
    # by design; see HubIdentity), but stamped explicitly so
    # approve_qwk_request() can propagate it onto the created QWKNode
    # without guessing.
    hub_identity_id     = db.Column(db.Integer, db.ForeignKey('hub_identities.id'),
                                    default=_default_hub_identity_id, nullable=True, index=True)

    hub_identity = db.relationship('HubIdentity', backref=db.backref('qwk_node_requests', lazy='dynamic'))

    def __repr__(self):
        return f'<QWKNodeRequest {self.packet_id} [{self.status}]>'


class NetworkJoinConfig(db.Model):
    """Config for the public "apply to join this network" page
    (anetbbs/web/network_join.py). One config per HubIdentity, not per
    EchomailNetwork row -- a hub's BinkP and QWK EchomailNetwork rows
    share one set of areas and one real-world join process (a real
    applicant's infopack has a single application template covering
    both transports), so one join-form config covers both transports of
    one hub identity.

    Was a true install-wide singleton before multi-hub-identity support;
    now there is one row per HubIdentity, addressed via
    NetworkJoinConfig.get(hub_identity_id) -- the classmethod's default
    argument resolves to the install's default identity, so the many
    existing zero-arg call sites keep working unchanged for installs
    with only one hub identity (the common case).
    """
    __tablename__ = 'network_join_config'

    id = db.Column(db.Integer, primary_key=True)
    hub_identity_id = db.Column(db.Integer, db.ForeignKey('hub_identities.id'),
                                default=_default_hub_identity_id, nullable=True, index=True)
    enabled = db.Column(db.Boolean, default=False, nullable=False)
    network_name = db.Column(db.String(100), default='')
    intro_text = db.Column(db.Text)
    # BinkP sequential node numbering (optional). When both are set, node
    # approval auto-assigns the next unused {zone}:{net}/N address instead
    # of trusting the applicant-submitted binkp_ftn_address verbatim. Leave
    # unset (either/both NULL) to keep the legacy behavior: use whatever
    # the applicant typed in as-is. QWK packet_id is never auto-numbered --
    # that stays fully applicant/sysop-chosen regardless of this setting.
    binkp_zone = db.Column(db.Integer)
    binkp_net = db.Column(db.Integer)
    infopack_filename = db.Column(db.String(255))
    infopack_original_filename = db.Column(db.String(255))
    infopack_uploaded_at = db.Column(db.DateTime)
    infopack_size = db.Column(db.Integer)
    rules_member_name = db.Column(db.String(255))
    rules_text = db.Column(db.Text)
    rules_text_extracted_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    hub_identity = db.relationship('HubIdentity', backref=db.backref('join_configs', lazy='dynamic'))

    @classmethod
    def get(cls, hub_identity_id=None):
        """Return this hub identity's join-form config row, creating it
        (disabled) if absent. hub_identity_id=None resolves to the
        install's default identity -- every pre-multi-hub call site
        keeps working unchanged for installs with only one identity."""
        if hub_identity_id is None:
            hub_identity_id = _default_hub_identity_id()
        row = cls.query.filter_by(hub_identity_id=hub_identity_id).first()
        if row is None:
            row = cls(hub_identity_id=hub_identity_id)
            db.session.add(row)
            db.session.commit()
        return row


class NetworkJoinRequest(db.Model):
    """A public application to join this hub's network, submitted via
    the anonymous /join/ web form. Generic across transports -- an
    applicant may fill in BinkP details, QWK details, or both, matching
    the real-world infopack application template (see NetworkJoinConfig)
    that asks for both in one form.

    No password-input fields anywhere: the real session/download
    password is always hub-generated at approval time (see
    approve_join_request in anetbbs/web/hub_admin.py), mirroring
    QWKNodeRequest's existing approval flow -- never trust a credential
    proposed by an unauthenticated web submitter.
    """
    __tablename__ = 'network_join_requests'

    id = db.Column(db.Integer, primary_key=True)
    # Which hub identity this application was submitted to -- set
    # explicitly by the /join/ (default identity) or /join/<slug>/
    # (a specific identity) route at submission time, so approval reads
    # NetworkJoinConfig.get(request.hub_identity_id) instead of always
    # the default identity's zone:net auto-numbering.
    hub_identity_id = db.Column(db.Integer, db.ForeignKey('hub_identities.id'),
                                default=_default_hub_identity_id, nullable=True, index=True)
    # Applicant-supplied, general info
    name = db.Column(db.String(100))
    location = db.Column(db.String(150))
    bbs_name = db.Column(db.String(100), nullable=False)
    bbs_software = db.Column(db.String(100))
    bbs_os = db.Column(db.String(100))
    telnet_address = db.Column(db.String(200))
    website_url = db.Column(db.String(400))
    email = db.Column(db.String(200), nullable=False)
    # BinkP section -- blank if not requesting BinkP
    binkp_ftn_address = db.Column(db.String(60))
    binkp_crash_or_hold = db.Column(db.String(10))
    # QWK section -- blank if not requesting QWK
    qwk_packet_id = db.Column(db.String(8))
    notes = db.Column(db.Text)
    # The read-and-checked rules gate. Rejected server-side on submit if
    # not true, not just disabled client-side.
    rules_ack = db.Column(db.Boolean, nullable=False, default=False)
    # Submission meta
    status = db.Column(db.String(20), default='pending', index=True)
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    # Review
    reviewed_at = db.Column(db.DateTime)
    reviewed_by = db.Column(db.String(100))
    deny_reason = db.Column(db.Text)
    # Approval output -- zero, one, or two of these get set depending on
    # which transport section(s) the applicant filled in.
    binkp_node_id = db.Column(db.Integer, db.ForeignKey('binkp_nodes.id'), nullable=True)
    qwk_node_id = db.Column(db.Integer, db.ForeignKey('qwk_nodes.id'), nullable=True)
    generated_binkp_password = db.Column(db.String(50))
    generated_qwk_password = db.Column(db.String(50))

    hub_identity = db.relationship('HubIdentity', backref=db.backref('join_requests', lazy='dynamic'))

    def __repr__(self):
        return f'<NetworkJoinRequest {self.bbs_name} [{self.status}]>'


class EbookCache(db.Model):
    """Fetched-once, cached-forever ebook text. Book content never
    changes once published, so unlike RssItem (re-polled periodically)
    this is a fetch-on-first-request cache with no expiry.
    """
    __tablename__ = 'ebook_cache'
    __table_args__ = (
        db.UniqueConstraint('source', 'source_id', name='uq_ebook_cache_source_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(20), nullable=False, default='gutenberg')
    source_id = db.Column(db.String(50), nullable=False, index=True)
    title = db.Column(db.String(300))
    author = db.Column(db.String(300))
    language = db.Column(db.String(10))
    content = db.Column(db.Text)          # cleaned plain-text body
    chapters_json = db.Column(db.Text)    # JSON list of {title, start_offset}
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<EbookCache {self.source}:{self.source_id} "{self.title}">'


class EbookBookmark(db.Model):
    """A named bookmark within a book, per user."""
    __tablename__ = 'ebook_bookmarks'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer,
                        db.ForeignKey('users.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    source = db.Column(db.String(20), nullable=False, default='gutenberg')
    source_id = db.Column(db.String(50), nullable=False, index=True)
    title = db.Column(db.String(300))
    author = db.Column(db.String(300))
    name = db.Column(db.String(100))
    position = db.Column(db.Integer, default=0)  # character offset into content
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='ebook_bookmarks')

    def __repr__(self):
        return f'<EbookBookmark {self.source}:{self.source_id} "{self.name}" user={self.user_id}>'


class EbookReadingHistory(db.Model):
    """Per-user 'last read here' marker, one row per (user, book) —
    mirrors RssReadStatus's presence-based idiom but also tracks position
    so 'continue reading' can resume exactly where the user left off.
    """
    __tablename__ = 'ebook_reading_history'
    __table_args__ = (
        db.UniqueConstraint('user_id', 'source', 'source_id',
                             name='uq_ebook_history_user_book'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer,
                        db.ForeignKey('users.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    source = db.Column(db.String(20), nullable=False, default='gutenberg')
    source_id = db.Column(db.String(50), nullable=False, index=True)
    title = db.Column(db.String(300))
    author = db.Column(db.String(300))
    last_position = db.Column(db.Integer, default=0)
    last_read_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship('User', backref='ebook_history')

    def __repr__(self):
        return f'<EbookReadingHistory {self.source}:{self.source_id} user={self.user_id}>'
