"""
MRC Protocol v1.3 packet handling for bridge service.
"""
import re

class MRCProtocol:
    SEPARATOR = '~'
    VALID_CHAR_RANGE = range(32, 126)
    RESERVED_HANDLES = ['SERVER', 'CLIENT', 'NOTME']

    # Real gap found in a security/performance audit: parse_packet()
    # below used to return every field completely raw -- sanitize_field()
    # is only ever applied on the OUTBOUND/create_packet() side, never
    # to INBOUND data. Safety today rests entirely on downstream
    # consumers remembering to re-sanitize before displaying anything
    # (anetbbs/features/mrc_chat.py's _pipe_to_ansi/_strip_pipe already
    # do, correctly) -- a defense-in-depth gap: any future or
    # additional consumer that forgot to re-sanitize would reopen the
    # exact cross-network ANSI/control-byte-injection class of bug
    # already fixed elsewhere this audit (anetirc2.py, mrc_chat.py,
    # core/session.py, core/finger_server.py). Stripped here, at the
    # actual parse boundary, so nothing downstream has to remember to.
    # A self-contained copy rather than importing
    # anetbbs.core.text_safety's version -- mrc/ is a deliberately
    # independent package/service (its own systemd unit) and never
    # imports from anetbbs.
    _ESCAPE_SEQUENCE_RE = re.compile(
        r'\x1b(?:\[[0-9;?]*[A-Za-z]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-Za-z]|[A-Za-z0-9=><~])')
    _CONTROL_BYTE_RE = re.compile(r'[\x00-\x1f\x7f]')

    @classmethod
    def _strip_untrusted(cls, text: str) -> str:
        if not text:
            return ''
        return cls._CONTROL_BYTE_RE.sub('', cls._ESCAPE_SEQUENCE_RE.sub('', text))

    # Real gap found in a security/performance audit: MRC_BRIDGE_LOG_LEVEL
    # =DEBUG's raw wire-packet trace ("MRC RAW OUT/IN", "MYSTIC RAW
    # OUT/IN" in main.py / mystic_connection.py) logged every packet
    # completely unredacted -- IDENTIFY/REGISTER/UPDATE/ROOMPASS carry
    # their password as plain text inside the packet's own command
    # field (see create_server_command()'s docstring for the real spec
    # template, e.g. "user~bbs~room~SERVER~msgext~~IDENTIFY password~"),
    # so turning on DEBUG tracing for any other diagnostic reason put
    # every login password a user typed into the bridge's own log file
    # in plaintext, permanently. main.py already had a narrower
    # _redact_command_for_logs() used on one INFO-level line
    # (WS server_cmd) -- centralized here so both that call site and
    # the raw-trace lines in both main.py and mystic_connection.py
    # (which never imports from main.py, to avoid a circular import)
    # share one redaction word list.
    _REDACTED_COMMAND_PREFIXES = ('IDENTIFY ', 'REGISTER ', 'UPDATE ', 'ROOMPASS ')

    @classmethod
    def redact_command_for_logs(cls, cmd: str) -> str:
        c = (cmd or '').strip()
        if not c:
            return c
        upper = c.upper()
        for prefix in cls._REDACTED_COMMAND_PREFIXES:
            if upper.startswith(prefix):
                return prefix + '********'
        return c

    @classmethod
    def redact_packet_for_logs(cls, raw: str) -> str:
        """Redact the command field of a full raw wire packet before
        it's written to a debug trace log. Safe to call on a partial/
        unparsable line too -- only touches field index 6 (the
        command/message field) when the packet has enough separators
        to have one at all; anything shorter is returned unchanged."""
        if not raw:
            return raw
        parts = raw.split(cls.SEPARATOR)
        if len(parts) >= 7:
            parts[6] = cls.redact_command_for_logs(parts[6])
        return cls.SEPARATOR.join(parts)

    @classmethod
    def sanitize_field(cls, field: str, allow_spaces: bool = True) -> str:
        if not field:
            return ''
        field = field.replace(cls.SEPARATOR, '')
        field = ''.join(c for c in field if ord(c) in cls.VALID_CHAR_RANGE)
        if not allow_spaces:
            field = field.replace(' ', '_')
        return field

    @classmethod
    def norm_room(cls, room: str) -> str:
        r = (room or '').strip()
        if r.startswith('#'):
            r = r[1:]
        r = r.replace(' ', '_')
        return r

    @classmethod
    def create_packet(cls, f1: str, f2: str, f3: str, f4: str, f5: str, f6: str, f7: str) -> str:
        fields = [
            cls.sanitize_field(f1, allow_spaces=False),
            cls.sanitize_field(f2, allow_spaces=False),
            cls.sanitize_field(f3, allow_spaces=False),
            cls.sanitize_field(f4, allow_spaces=False),
            cls.sanitize_field(f5, allow_spaces=False),
            cls.sanitize_field(f6, allow_spaces=False),
            cls.sanitize_field(f7, allow_spaces=True),
        ]
        return cls.SEPARATOR.join(fields) + cls.SEPARATOR + '\n'

    @classmethod
    def parse_packet(cls, packet: str) -> dict:
        packet = (packet or '').strip()
        if not packet:
            raise ValueError("Invalid packet: empty")
        if packet.endswith(cls.SEPARATOR):
            packet = packet[:-1]
        parts = packet.split(cls.SEPARATOR)
        if len(parts) != 7:
            raise ValueError(f"Invalid packet: expected 7 fields, got {len(parts)} (raw={packet!r})")
        parts = [cls._strip_untrusted(p) for p in parts]
        return {
            'from_user': parts[0],
            'from_site': parts[1],
            'from_room': parts[2],
            'to_user': parts[3],
            'msg_ext': parts[4],
            'to_room': parts[5],
            'message': parts[6]
        }

    @classmethod
    def create_handshake(cls, bbs_name: str, platform_info: str = '') -> str:
        """`platform_info`'s trailing version segment identifies MRC
        client/protocol compatibility to the upstream hub, in the
        hub's own numbering scheme -- NOT the host BBS software's own
        release version. A prior fix mistakenly derived this from
        ANetBBS's own VERSION file (assuming an old hardcoded value
        was just meaningless drift); the hub's real OLDVERSION check
        rejects anything below its own current floor (observed live:
        1.2.9), a series unrelated to ANetBBS's v1.0.x. See
        install.sh's MRC_CLIENT_COMPAT_VERSION for the real, deliberately
        independent value actually shipped."""
        bbs_name = cls.sanitize_field(bbs_name, allow_spaces=True)
        if platform_info:
            platform_info = cls.sanitize_field(platform_info, allow_spaces=False)
            return f"{bbs_name}{cls.SEPARATOR}{platform_info}\n"
        return f"{bbs_name}{cls.SEPARATOR}\n"

    @classmethod
    def create_message(cls, user: str, bbs: str, room: str, to_user: str, to_room: str, message: str, msg_ext: str = '') -> str:
        room = cls.norm_room(room)
        to_room = cls.norm_room(to_room)
        return cls.create_packet(user, bbs, room, to_user or '', msg_ext or '', to_room, message)

    @classmethod
    def create_control_command(cls, command: str, user: str = 'CLIENT', bbs: str = '', room: str = '', msg_ext: str = '') -> str:
        room = cls.norm_room(room)
        return cls.create_packet(user, bbs, room, 'SERVER', msg_ext, room, command)

    @classmethod
    def create_server_command(cls, user: str, bbs: str, room: str, command: str, to_room: str = None) -> str:
        # Per the actual official MRC protocol spec (bbswiki.
        # bottomlessabyss.net MRCDoc:MRC_Protocol, obtained directly --
        # not inferred from any one client's source): most "Client
        # session context" commands (MOTD, WHOON, LIST, USERS, etc.)
        # use a POPULATED toRoom, e.g. MOTD's documented template is
        # literally "user~bbs~room~SERVER~msgext~room~MOTD~". Only
        # IDENTIFY/REGISTER/UPDATE ("MRC Trust" verbs) are documented
        # with an empty toRoom ("user~bbs~room~SERVER~msgext~~IDENTIFY
        # password~"). An earlier revision of this function emptied
        # toRoom for EVERY command, reasoning from one reference
        # client's (uMRC) sendCmdPacket helper hardcoding it empty
        # unconditionally -- that client's own shortcut, not the spec.
        room = cls.norm_room(room)
        if to_room is None:
            stripped = command.strip()
            cmd_word = stripped.upper().split()[0] if stripped else ''
            to_room = '' if cmd_word in ('IDENTIFY', 'REGISTER', 'UPDATE') else room
        to_room = cls.norm_room(to_room) if to_room else ''
        return cls.create_packet(user, bbs, room, 'SERVER', '', to_room, command)

    @classmethod
    def create_iamhere(cls, user: str, bbs: str, room: str, extension: str = '') -> str:
        room = cls.norm_room(room)
        cmd = f"IAMHERE:{extension}" if extension else "IAMHERE"
        return cls.create_packet(user, bbs, room, 'SERVER', '', room, cmd)

    @classmethod
    def create_imalive(cls, bbs: str, pid: str = '0', msg_ext: str = '', bbsname: str = '') -> str:
        cmd = f"IMALIVE:{bbsname}" if bbsname else "IMALIVE"
        return cls.create_packet('CLIENT', bbs, pid, 'SERVER', msg_ext, '', cmd)

    @classmethod
    def create_newroom(cls, user: str, bbs: str, old_room: str, new_room: str) -> str:
        oldr = cls.norm_room(old_room)
        newr = cls.norm_room(new_room) or 'lobby'
        cmd = f"NEWROOM:{oldr}:{newr}"
        if not oldr:
            return cls.create_packet(user, bbs, '', 'SERVER', '', '', cmd)
        return cls.create_packet(user, bbs, oldr, 'SERVER', '', oldr, cmd)

    @classmethod
    def create_logoff(cls, user: str, bbs: str, room: str) -> str:
        # Per the official MRC protocol spec (bbswiki.bottomlessabyss.net
        # MRCDoc:MRC_Protocol): LOGOFF's documented template is
        # "user~bbs~room~SERVER~msgext~room~LOGOFF~" -- BOTH fromRoom
        # and toRoom populated with the room name. Two earlier revisions
        # of this function tried emptying toRoom-only (matching uMRC's
        # own LOGOFF call) and then both fields (matching Synchronet's
        # JS connector) while chasing a live "must /identify every
        # time" report -- neither actually matches the real spec, which
        # is authoritative over any one client's own implementation
        # choices. This restores the originally-correct, spec-matching
        # format.
        room = cls.norm_room(room)
        return cls.create_packet(user, bbs, room, 'SERVER', '', room, 'LOGOFF')

    @classmethod
    def validate_handle(cls, handle: str) -> bool:
        if not handle or handle.upper() in cls.RESERVED_HANDLES:
            return False
        for c in handle:
            if ord(c) not in cls.VALID_CHAR_RANGE:
                return False
        return True