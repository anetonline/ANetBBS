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
        # Reference client (umrc-client/main.c's sendCmdPacket, which
        # backs every generic /command) sends toRoom empty for EVERY
        # command, unconditionally -- not just IDENTIFY/REGISTER/UPDATE
        # as an earlier, narrower fix here assumed. A populated toRoom
        # here was a real, verified-against-source wire mismatch on
        # LOGOFF specifically (see create_logoff) and, by the same
        # unconditional rule, on every other generic command routed
        # through this function too.
        room = cls.norm_room(room)
        if to_room is None:
            to_room = ''
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
        # Reference client sends toRoom empty for LOGOFF
        # (sendMsgPacket(&mrcSock, "SERVER", "", "", "LOGOFF")) -- this
        # used to send the room name in that field instead, a real
        # verified-against-source wire mismatch on the exact packet
        # sent every time a user leaves.
        room = cls.norm_room(room)
        return cls.create_packet(user, bbs, room, 'SERVER', '', '', 'LOGOFF')

    @classmethod
    def validate_handle(cls, handle: str) -> bool:
        if not handle or handle.upper() in cls.RESERVED_HANDLES:
            return False
        for c in handle:
            if ord(c) not in cls.VALID_CHAR_RANGE:
                return False
        return True