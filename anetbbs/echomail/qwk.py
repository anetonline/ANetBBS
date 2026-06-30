# anetbbs/echomail/qwk.py
"""
QWK mail packet handler for ANetBBS.

Supports:
- Downloading QWK packets via HTTP
- Parsing QWK packet format (CONTROL.DAT, MESSAGES.DAT)
- Importing messages into EchomailMessage records
- Generating REP packets from outbound messages
- Dove-Net style QWK networking
"""
import io
import re
import struct
import zipfile
import logging
import urllib.request
from datetime import datetime
import hashlib
import time

logger = logging.getLogger(__name__)

# QWK constants
QWK_HEADER_SIZE = 128       # Each message header block is 128 bytes
QWK_BLOCK_SIZE = 128        # Data is in 128-byte blocks


def _parse_control_dat(data: str):
    """
    Parse CONTROL.DAT from a QWK packet.
    Returns dict with bbs_name, bbs_city, bbs_phone, bbs_sysop, conference_list.
    """
    lines = [l.strip() for l in data.replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    info = {
        'bbs_name': lines[0] if len(lines) > 0 else '',
        'bbs_city': lines[1] if len(lines) > 1 else '',
        'bbs_phone': lines[2] if len(lines) > 2 else '',
        'bbs_sysop': lines[3] if len(lines) > 3 else '',
        'conferences': {},
    }

    # Conference block starts at line 11 (index 10)
    # Format: conf_number followed by conf_name on next line
    i = 10
    while i < len(lines):
        num_str = lines[i].strip()
        if num_str.isdigit():
            conf_num = int(num_str)
            conf_name = lines[i + 1].strip() if i + 1 < len(lines) else f'Conference {conf_num}'
            info['conferences'][conf_num] = conf_name
            i += 2
        else:
            i += 1

    return info


def _clean_body(text: str):
    """Clean a Synchronet/QWK message body and split out kludges/tear/origin.

    Returns dict with keys: body, msg_id, reply_id, tear_line, origin_line.

    - Strips Ctrl-A (\\x01) Synchronet color/attribute codes (Ctrl-A + 1 char).
    - Pulls FTN kludge lines (@MSGID, @REPLY, @TZ, @CHRS, @PID, ...) out of
      the visible body. @MSGID/@REPLY values are returned separately.
    - Splits off the FTN tear line ("---") and origin line ("* Origin:" or
      Synchronet's þ-delimited tagline that follows the tear).
    """
    msg_id = None
    reply_id = None
    chrs = None
    tear_line = None
    origin_line = None

    # Strip Ctrl-A (0x01) followed by one attribute byte.
    text = re.sub(r'\x01.', '', text)

    out_lines = []
    found_tear = False
    KLUDGE_KEYWORDS = {
        'TZ', 'TZUTC', 'PID', 'TID', 'NOTE', 'ENC',
        'PATH', 'SEEN-BY', 'VIA', 'SOFT', 'INTL', 'FMPT', 'TOPT',
        'FLAGS', 'RFC-', 'X-',
    }
    for line in text.split('\n'):
        stripped = line.lstrip()
        if stripped.startswith('@'):
            keyword = stripped[1:].split(':', 1)[0].split(' ', 1)[0].upper()
            if keyword == 'MSGID':
                _, _, val = stripped.partition(':')
                msg_id = val.strip()
                continue
            if keyword in ('REPLY', 'REPLYID', 'REPLYTO'):
                _, _, val = stripped.partition(':')
                reply_id = val.strip()
                continue
            if keyword in ('CHRS', 'CHARSET'):
                _, _, val = stripped.partition(':')
                chrs = val.strip() or None
                continue
            if keyword in KLUDGE_KEYWORDS or keyword.startswith('RFC-') or keyword.startswith('X-'):
                continue
        if not found_tear and (stripped == '---' or stripped.startswith('--- ')):
            tear_line = line.rstrip()
            found_tear = True
            continue
        if found_tear and stripped.startswith('* Origin:'):
            origin_line = line.rstrip()
            continue
        if found_tear and origin_line is None and ('\xfe' in line or 'þ' in line):
            origin_line = line.rstrip()
            continue
        out_lines.append(line)

    body = '\n'.join(out_lines).strip()
    return {
        'body': body,
        'msg_id': msg_id,
        'reply_id': reply_id,
        'chrs': chrs,
        'tear_line': tear_line,
        'origin_line': origin_line,
    }


def _parse_messages_dat(data: bytes, conferences: dict):
    """
    Parse MESSAGES.DAT from a QWK packet.
    Returns list of message dicts.
    """
    messages = []
    if len(data) < QWK_HEADER_SIZE:
        return messages

    # First 128 bytes is the "Welcoming" block — log it for diagnostics
    welcome_block = data[:QWK_HEADER_SIZE]
    welcome_id = welcome_block.rstrip(b'\x00 ').decode('ascii', errors='replace')
    logger.info('QWK hub sys_id from welcoming block: %r', welcome_id)
    pos = QWK_HEADER_SIZE

    while pos + QWK_HEADER_SIZE <= len(data):
        block = data[pos:pos + QWK_HEADER_SIZE]
        pos += QWK_HEADER_SIZE

        # Status flag: ' '/'-' = public echomail, '*'/'+' = private netmail
        status_byte = block[0:1]
        is_private = status_byte in (b'*', b'+')
        msg_num = block[1:8].decode('ascii', errors='replace').strip()
        date = block[8:21].decode('ascii', errors='replace').strip()
        to_name = block[21:46].decode('latin-1', errors='replace').strip('\x00 ')
        from_name = block[46:71].decode('latin-1', errors='replace').strip('\x00 ')
        subject = block[71:96].decode('latin-1', errors='replace').strip('\x00 ')
        # bytes 96..107 — password (unused)
        # bytes 108..115 — reference message number (8-char ASCII)
        try:
            ref_num_str = block[108:116].decode('ascii', errors='replace').strip()
            ref_num = int(ref_num_str) if ref_num_str else 0
        except Exception:
            ref_num = 0
        # bytes 116..121 — number of message blocks (6-char ASCII!), includes header
        try:
            num_chunks_str = block[116:122].decode('ascii', errors='replace').strip()
            num_chunks = int(num_chunks_str) if num_chunks_str else 1
        except Exception:
            num_chunks = 1
        # bytes 123..124 — conference number (binary, little-endian uint16)
        try:
            conf_num = struct.unpack('<H', block[123:125])[0]
        except Exception:
            conf_num = 0

        # byte 122 — active flag: 0xE1=active, 0xE2=killed/deleted
        active_flag = block[122]

        # Sanity guard against malformed packets — never read >50k blocks
        if num_chunks < 1 or num_chunks > 50000:
            num_chunks = 1

        # num_chunks includes the header block; body is (num_chunks - 1) blocks
        body_blocks = max(num_chunks - 1, 0)
        body_size = body_blocks * QWK_BLOCK_SIZE
        body_raw = data[pos:pos + body_size]
        pos += body_size

        if active_flag == 0xE2:
            logger.debug("QWK: skipping killed message %s in conf %s", msg_num, conf_num)
            continue

        raw_str = body_raw.decode('latin-1', errors='replace')
        if b'\x1b' in body_raw:
            # ANSI art: \xe3 marks QWK record boundaries, not line breaks.
            # Strip it; real line structure comes from \r\n sequences in the art.
            body_text = (raw_str
                         .replace('\xe3', '')
                         .replace('\r\n', '\n')
                         .replace('\r', '\n')
                         .rstrip('\x00 \n'))
        else:
            # Plain text: \xe3 is the QWK paragraph/line separator.
            body_text = (raw_str
                         .replace('\xe3', '\n')
                         .replace('\r\n', '\n')
                         .replace('\r', '\n')
                         .rstrip('\x00 \n'))
        # Strip SAUCE record: 0x1A (Ctrl+Z) marks the end of art content.
        # Everything from 0x1A onward is binary metadata that must not be stored.
        _sauce = body_text.find('\x1a')
        if _sauce >= 0:
            body_text = body_text[:_sauce]
        # QWK 0xE3 separators can land anywhere inside a CSI sequence.
        # Strip \n from any position within an ANSI escape sequence so the
        # regex in _ansi_to_html can match them.
        body_text = re.sub(r'\x1b\n?\[[0-9;?\n]*[@-~]',
                           lambda m: m.group(0).replace('\n', ''), body_text)
        clean = _clean_body(body_text)

        if not clean['body']:
            logger.warning(
                'QWK inbound blank body skipped: conf=%d from=%r subj=%r raw_head=%r',
                conf_num, from_name, subject, body_raw[:256],
            )
            continue

        # Drop messages from conferences not advertised in CONTROL.DAT —
        # these are almost always the result of a misaligned read.
        # Exception: conf_num=0 is the netmail/personal-mail conference and
        # is always valid (BBSes don't list it in CONTROL.DAT).
        if conferences and conf_num != 0 and conf_num not in conferences:
            logger.debug("QWK: dropping msg from unknown conference %s", conf_num)
            continue

        if conf_num == 0 or is_private:
            # Inbound netmail — route into a virtual NETMAIL area.
            area_tag = 'NETMAIL'
            area_name = 'Netmail (Private)'
        else:
            # Use plain numeric tag (e.g. '2010') — the QWK spec and Dove-Net
            # docs identify conferences by number, not by a 'QWK_N' prefix.
            area_tag = str(conf_num)
            area_name = conferences.get(conf_num, f'Conference {conf_num}')

        logger.info('QWK inbound msg: conf=%d area_tag=%r from=%r subj=%r',
                    conf_num, area_tag, from_name, subject)
        messages.append({
            'from_name': from_name,
            'to_name': to_name or 'All',
            'subject': subject,
            'date_str': date,
            'body': clean['body'],
            'msg_id': clean['msg_id'],
            'reply_id': clean['reply_id'],
            'chrs': clean['chrs'],
            'tear_line': clean['tear_line'],
            'origin_line': clean['origin_line'],
            'area_tag': area_tag,
            'area_name': area_name,
            'conf_num': conf_num,
            'msg_num': msg_num,
            'ref_num': ref_num,
            'is_private': is_private,
        })

    return messages


def _build_rep_packet(messages, packet_id: str, hub_id: str = '') -> bytes:
    """
    Build a REP packet (zip file) containing MESSAGES.DAT with outbound messages.
    Returns the bytes of the .rep zip file.

    hub_id: the hub's QWK system ID (e.g. 'DOVE' for Dove-Net). Synchronet's
    un_rep.cpp extracts the zip and looks for '{hub.sys_id}.msg' — if the inner
    file is named with the node's packet_id instead, the hub prints "MSG file
    not received" and silently rejects the entire REP with no FTP error.
    """
    # inner_id must be resolved first — it's used in both the welcoming block
    # and the ZIP filename.
    inner_id = (hub_id.strip() or packet_id).upper()

    buf = io.BytesIO()
    msg_dat = io.BytesIO()

    # Welcoming block — Synchronet's un_rep.cpp checks that the first block
    # starts with cfg.sys_id (the hub's BBS ID, e.g. "VERT").  All-zero bytes
    # fail strnicmp and cause the entire REP to be silently rejected with
    # "Incorrect QWK BBS ID" before any messages are processed.
    welcome = bytearray(QWK_HEADER_SIZE)
    _id_bytes = inner_id.encode('ascii', errors='replace')[:QWK_HEADER_SIZE]
    welcome[:len(_id_bytes)] = _id_bytes
    msg_dat.write(bytes(welcome))

    for i, msg in enumerate(messages, 1):
        # Private/netmail messages: status='*', conf_num=0 (network mail).
        # We mark netmail by direction='netmail' on the EchomailMessage row;
        # the recipient's username goes in to_name and (optionally) FTN
        # address in to_address. Synchronet/Mystic route by name on DOVE-Net.
        is_private = getattr(msg, 'direction', '') == 'netmail'
        if is_private:
            conf_num = 0
        else:
            conf_num = getattr(msg, '_qwk_conf_num', 0)
            if conf_num == 0:
                # If conf_num is 0 for a non-private message, the hub will treat
                # it as personal mail and NOT broadcast it to the echomail feed.
                # This usually means the area tag was renamed away from 'QWK_N'
                # or area_id is missing. Log prominently so sysop can diagnose.
                logger.warning(
                    'QWK REP: msg #%d (%r) has conf_num=0 — will be sent as '
                    'personal mail on hub, NOT broadcast. Check that the echo '
                    "area tag is in 'QWK_<N>' format.",
                    getattr(msg, 'id', i), msg.subject)

        # Inject a MSGID into the body if the message doesn't already have one.
        # Synchronet's QNET-FTP deduplicates by MSGID; missing MSGIDs can cause
        # the hub to silently drop or misroute messages on some configurations.
        body_raw = msg.body or ''
        if '@MSGID:' not in body_raw:
            existing_id = getattr(msg, 'msg_id', None) or getattr(msg, 'msgid', None)
            if existing_id:
                msgid_str = existing_id
            else:
                # Generate a stable unique ID from content so re-polls don't
                # create duplicate MSGIDs.
                digest = hashlib.md5(
                    f'{packet_id}{getattr(msg,"id",i)}{msg.subject}{msg.from_name}'.encode()
                ).hexdigest()[:8]
                msgid_str = f'{packet_id.upper()}_{digest}_{int(time.time()):x}'
            body_raw = f'@MSGID: {msgid_str}\n' + body_raw

        body_text = body_raw.replace('\r\n', '\xe3').replace('\r', '\xe3').replace('\n', '\xe3')
        body_encoded = body_text.encode('latin-1', errors='replace')
        # Pad body to 128-byte block boundary (spaces, as Synchronet does)
        remainder = len(body_encoded) % QWK_BLOCK_SIZE
        if remainder:
            body_encoded += b' ' * (QWK_BLOCK_SIZE - remainder)
        num_chunks = 1 + len(body_encoded) // QWK_BLOCK_SIZE

        logger.info('QWK REP msg #%d: to=%r subj=%r conf=%d blocks=%d',
                    i, msg.to_name, msg.subject, conf_num, num_chunks)

        header = bytearray(b' ' * QWK_HEADER_SIZE)
        header[0] = ord('*' if is_private else ' ')
        # Bytes 1-7: conference number in REP mode (NOT sequential message index).
        # Synchronet un_rep.cpp routes via: long confnum = atol((char*)block+1)
        # Using the message counter here sends all messages to conference 1 (or
        # whatever atol of the counter happens to be), silently misrouting them.
        conf_num_str = str(conf_num).encode('ascii')[:7].ljust(7)
        header[1:8] = conf_num_str
        date_str = datetime.utcnow().strftime('%m-%d-%y%H:%M').encode('ascii')[:13].ljust(13)
        header[8:21] = date_str
        to_b = (msg.to_name or 'All').encode('latin-1', errors='replace')[:25].ljust(25)
        header[21:46] = to_b
        from_b = (msg.from_name or 'Sysop').encode('latin-1', errors='replace')[:25].ljust(25)
        header[46:71] = from_b
        subj_b = (msg.subject or '').encode('latin-1', errors='replace')[:25].ljust(25)
        header[71:96] = subj_b
        # bytes 96..107: password (12 spaces, unused for REP)
        # bytes 108..115: reference message number (ASCII, right-justified)
        header[108:116] = b'0'.rjust(8)
        # bytes 116..121: number of blocks (ASCII, right-justified, includes header)
        header[116:122] = str(num_chunks).encode('ascii')[:6].rjust(6)
        header[122] = 0xE1  # active flag
        struct.pack_into('<H', header, 123, conf_num)
        header[127] = ord(' ')  # tagline marker

        msg_dat.write(bytes(header))
        msg_dat.write(body_encoded)

    msg_dat.seek(0)
    msg_bytes = msg_dat.read()

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Synchronet's pack_rep.cpp uses uppercase: SAFEPRINTF2(str, "%s%s.MSG", ...)
        # un_rep.cpp's extract_files_from_archive rejects filenames containing chars
        # outside SAFEST_FILENAME_CHARS and breaks (not continues) — meaning lowercase
        # letters in 'VERT.msg' can cause total extraction failure, 0 files extracted,
        # silent REP rejection ("MSG file not received").  Use uppercase to match native.
        zf.writestr(f'{inner_id}.MSG', msg_bytes)

    return buf.getvalue()


class QWKClient:
    """
    QWK mail client — download packets via HTTP, parse them,
    and generate REP packets for outbound messages.
    """

    def __init__(self, host: str, port: int, username: str, password: str,
                 packet_id: str = 'ANET', timeout: int = 60,
                 download_url: str = '', upload_url: str = '',
                 hub_id: str = ''):
        self.host = host
        self.port = port or 80
        self.username = username
        self.password = password
        self.packet_id = packet_id or 'ANET'
        # Hub system ID — used for ALL Synchronet QNET-FTP filenames:
        #   download: {hub_id}.qwk  (e.g. VERT.qwk)
        #   upload:   {hub_id}.rep  (e.g. VERT.rep)
        #   inner MSG: {hub_id}.MSG (e.g. VERT.MSG)
        # packet_id is the node's own registered ID (e.g. ANETBBS) — used for
        # FTP login identity and auto-generated MSGIDs, NOT for filenames.
        # Fall back to packet_id only when hub_id is unconfigured (legacy).
        self.hub_id = hub_id or self.packet_id
        self.timeout = timeout
        self.download_url = download_url or ''
        self.upload_url = upload_url or ''
        # DOVE-Net auto-defaults — when the sysop just sets
        # host=dove.synchro.net we fill in the qnet-ftp convention.
        if (host or '').lower() in ('dove.synchro.net', 'vert.synchro.net'):
            if not self.download_url:
                self.download_url = 'ftp://dove.synchro.net/{hub_id}.qwk'
            if not self.upload_url:
                # Synchronet's FTP server accepts STOR {hub_id}.rep at root
                # (no subdirectory).  It stores the file to data_dir/file/{usernum}.rep
                # and the event thread processes it via unpack_rep().  Uploading to
                # /incoming/ goes to the BBS file area and is never QWK-processed.
                self.upload_url = 'ftp://dove.synchro.net/{hub_id}.rep'
            if self.port == 80:
                self.port = 21

    def _base_url(self):
        if self.host and self.host.startswith(('http://', 'https://')):
            return self.host.rstrip('/')
        return f'http://{self.host}:{self.port or 80}'

    def _resolve_download_url(self):
        if self.download_url:
            return self.download_url.format(
                host=self.host, port=self.port,
                user=self.username, password=self.password,
                packet=self.packet_id, hub_id=self.hub_id)
        return f'{self._base_url()}/qwk/{self.packet_id}.qwk'

    def _resolve_upload_url(self):
        if self.upload_url:
            return self.upload_url.format(
                host=self.host, port=self.port,
                user=self.username, password=self.password,
                packet=self.packet_id, hub_id=self.hub_id)
        return f'{self._base_url()}/rep/{self.packet_id}.rep'

    def _add_basic_auth(self, req):
        if self.username:
            import base64 as _b64
            creds = _b64.b64encode(
                f'{self.username}:{self.password or ""}'.encode()
            ).decode('ascii')
            req.add_header('Authorization', f'Basic {creds}')
        return req

    def poll(self, outbound_messages=None, data_dir: str = '/tmp'):
        """
        Download a QWK packet, parse messages, upload REP for outbound.

        Returns:
            dict with 'received' (list of parsed message dicts) and 'sent' count.
        """
        result = {'received': [], 'sent': 0}

        qwk_url = self._resolve_download_url()
        logger.info("QWK: downloading packet from %s", qwk_url)
        try:
            if qwk_url.startswith(('ftp://', 'ftps://')):
                qwk_data = self._ftp_download(qwk_url)
            else:
                req = self._add_basic_auth(urllib.request.Request(qwk_url))
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    qwk_data = resp.read()
        except Exception as exc:
            raise ConnectionError(f"QWK: failed to download packet: {exc}") from exc

        result['received'] = self._parse_qwk_packet(qwk_data)

        if outbound_messages:
            rep_data = _build_rep_packet(outbound_messages, self.packet_id, self.hub_id)
            rep_url = self._resolve_upload_url()
            logger.info("QWK: uploading REP packet to %s", rep_url)
            try:
                if rep_url.startswith(('ftp://', 'ftps://')):
                    self._ftp_upload(rep_url, rep_data)
                else:
                    req = self._add_basic_auth(
                        urllib.request.Request(rep_url, data=rep_data, method='PUT'))
                    with urllib.request.urlopen(req, timeout=self.timeout):
                        pass
                result['sent'] = len(outbound_messages)
            except Exception as exc:
                logger.warning("QWK: failed to upload REP: %s", exc)

        return result

    def _ftp_download(self, url):
        """Fetch a QWK packet via FTP/FTPS (QNET-FTP transport).

        Tries the configured path first, then a list of common qnet-ftp
        filename conventions (<packet_id>.qwk, <hub_id>.qwk, in upper
        and lower case). If everything 404s, lists the login dir + qnet/
        and includes that in the error so the sysop can see what files
        actually exist on the hub.
        """
        import ftplib
        import io as _io
        from urllib.parse import urlparse, unquote
        u = urlparse(url)
        host = u.hostname or self.host
        port = u.port or 21
        user = unquote(u.username) if u.username else (self.username or 'anonymous')
        pw = unquote(u.password) if u.password else (self.password or '')
        path = (u.path or '/').lstrip('/')
        ftp_cls = ftplib.FTP_TLS if u.scheme == 'ftps' else ftplib.FTP
        with ftp_cls() as ftp:
            ftp.connect(host, port, timeout=self.timeout)
            login_resp = ftp.login(user, pw)
            logger.info('QWK FTP login as %s @ %s:%s -> %s',
                        user, host, port,
                        (login_resp or '').replace('\n', ' '))
            if u.scheme == 'ftps':
                try:
                    ftp.prot_p()
                except Exception:
                    pass
            ftp.set_pasv(True)

            # Build candidate filenames (in priority order).
            candidates = []
            seen = set()
            def _add(c):
                if c and c not in seen:
                    seen.add(c); candidates.append(c)
            _add(path)
            # hub_id first: for DOVE-Net you download <hub_id>.qwk (e.g. VERT.qwk),
            # not your own <packet_id>.qwk (e.g. ANET.qwk).
            for base in ((self.hub_id or '').strip(),
                         (self.packet_id or '').strip()):
                if not base:
                    continue
                _add(f'{base}.qwk')
                _add(f'{base}.QWK')
                _add(f'{base.lower()}.qwk')
                _add(f'{base.upper()}.QWK')

            last_error = None
            saw_no_new_messages = False
            for cand in candidates:
                buf = _io.BytesIO()
                try:
                    ftp.retrbinary(f'RETR {cand}', buf.write)
                    logger.info('QWK FTP retrieved %s (%d bytes)',
                                cand, buf.tell())
                    return buf.getvalue()
                except ftplib.error_perm as exc:
                    last_error = f'RETR {cand}: {exc}'
                    logger.info('QWK FTP %s', last_error)
                    # Synchronet sends 550 when there's nothing new — that's
                    # an idle hub, not an error. Detect and short-circuit.
                    if 'no new messages' in str(exc).lower() or \
                       'no qwk packet created' in str(exc).lower():
                        saw_no_new_messages = True
                    continue

            if saw_no_new_messages:
                logger.info('QWK FTP %s: no new messages on hub', host)
                return b''

            # Couldn't find it — gather a directory listing for the error.
            listings = []
            for d in ('', 'qnet', 'QNET'):
                try:
                    files = ftp.nlst(d) if d else ftp.nlst()
                    listings.append(f'{d or "/"}: {files[:30]}')
                except Exception:
                    pass
            raise FileNotFoundError(
                f'No QWK packet found. Tried {candidates}. '
                f'Last: {last_error}. Files: {" | ".join(listings) or "<none>"}')

    def _ftp_upload(self, url, data):
        """Upload bytes via FTP/FTPS to the path in the URL."""
        import ftplib
        import io as _io
        from urllib.parse import urlparse, unquote
        u = urlparse(url)
        host = u.hostname or self.host
        port = u.port or 21
        user = unquote(u.username) if u.username else (self.username or 'anonymous')
        pw = unquote(u.password) if u.password else (self.password or '')
        # Strip leading slash so STOR is relative to the FTP home dir, not
        # the filesystem root. Mirrors the lstrip in _ftp_download — without
        # this, Synchronet's QNET-FTP puts the REP in the wrong directory and
        # the hub's processor never sees it (no error, but silent drop).
        path = (u.path or '/').lstrip('/')
        ftp_cls = ftplib.FTP_TLS if u.scheme == 'ftps' else ftplib.FTP
        with ftp_cls() as ftp:
            ftp.connect(host, port, timeout=self.timeout)
            login_resp = ftp.login(user, pw)
            logger.info('QWK FTP upload login as %s @ %s:%s -> %s',
                        user, host, port, (login_resp or '').replace('\n', ' '))
            if u.scheme == 'ftps':
                try:
                    ftp.prot_p()
                except Exception:
                    pass
            ftp.set_pasv(True)
            resp = ftp.storbinary(f'STOR {path}', _io.BytesIO(data))
            logger.info('QWK FTP uploaded %s (%d bytes) → %s', path, len(data), resp)
            # Verify: Synchronet's FTP server does not expose data_dir/file/*.rep
            # back via RETR, so a 550 error here is expected and normal — it means
            # the REP was accepted and queued for unpack_rep() processing by the
            # event thread.  A successful RETR (file still readable) would mean the
            # file landed in the wrong place (e.g., a file-area directory) and will
            # never be QWK-processed.
            try:
                verify_buf = _io.BytesIO()
                ftp.retrbinary(f'RETR {path}', verify_buf.write)
                logger.warning('QWK FTP verify: RETR %s succeeded (%d bytes) — '
                               'file is in a BBS file area, NOT the QWK queue. '
                               'Check that the upload URL has no path prefix '
                               '(should be ftp://dove.synchro.net/{hub_id}.rep).',
                               path, verify_buf.tell())
            except Exception as ve:
                logger.info('QWK FTP verify: RETR %s → %s (expected — '
                            'Synchronet stores the REP internally; '
                            'event thread will call unpack_rep())', path, ve)

    def _parse_qwk_packet(self, data: bytes):
        """Parse a QWK zip packet and return list of message dicts."""
        if not data:
            return []
        try:
            buf = io.BytesIO(data)
            with zipfile.ZipFile(buf, 'r') as zf:
                names_upper = {n.upper(): n for n in zf.namelist()}
                control_name = names_upper.get('CONTROL.DAT')
                messages_name = names_upper.get('MESSAGES.DAT')

                if not control_name or not messages_name:
                    logger.warning("QWK: missing CONTROL.DAT or MESSAGES.DAT in packet")
                    return []

                control_data = zf.read(control_name).decode('latin-1', errors='replace')
                messages_data = zf.read(messages_name)

            info = _parse_control_dat(control_data)
            logger.info('QWK CONTROL.DAT bbs=%r conferences=%r',
                        info.get('bbs_name', ''),
                        sorted(info['conferences'].items()))
            messages = _parse_messages_dat(messages_data, info['conferences'])
            return messages
        except zipfile.BadZipFile as exc:
            logger.error("QWK: bad zip file: %s", exc)
            return []
