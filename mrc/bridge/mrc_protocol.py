"""
MRC Protocol v1.3 packet handling for bridge service.
"""

class MRCProtocol:
    SEPARATOR = '~'
    VALID_CHAR_RANGE = range(32, 126)
    RESERVED_HANDLES = ['SERVER', 'CLIENT', 'NOTME']

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