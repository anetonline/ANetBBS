"""
Outbound MSP client — fire a single Message-Send Protocol packet at a
remote BBS and return whether the wire write succeeded.
"""
import logging
import socket

from .protocol import encode, MSP_DEFAULT_PORT
from ..core.net_safety import resolve_safe_destination

logger = logging.getLogger(__name__)


def send_msp(host: str, recipient: str, message: str,
             sender: str = '', sender_real_name: str = '',
             sender_system: str = '',
             port: int = MSP_DEFAULT_PORT,
             timeout: float = 10.0) -> bool:
    """Send a Message-Send Protocol message. Returns True on success.

    Field mapping confirmed 2026-09-28 against Synchronet's own real
    source (github.com/SynchronetBBS/sbbs) rather than guessed -- both
    sides:
      - Receiver: exec/mspservice.js builds the "Instant Message from
        <sender> [<SENDER-TERM if non-empty>] [<signature, else the
        raw connecting IP>] (<reverse-DNS host_name, else a literal
        "<no name>"> -- only when signature was empty>):" line.
      - Sender: exec/load/sbbsimsg_lib.js's own send_msg() literally
        sends `sender + "\0\0\0" + system.name + "\0"` -- i.e. it
        ALWAYS leaves SENDER-TERM and cookie empty, and puts its own
        BBS name in the LAST field, MSP's "signature" (RFC 1312 names
        it as an auth signature, but Synchronet's own sender/receiver
        both treat it purely as a free-text BBS-identity string, right
        down to internally naming the received value `bbs`).

    `sender` should be the BARE username only — Synchronet builds the
    "reply to" address as `<sender>@<reverse-DNS-of-peer-IP>` and chokes
    if `sender` already contains an @. Pass the BBS name in
    `sender_system` instead -- it's encoded into the wire `signature`
    field below (NOT `cookie`, which Synchronet's own receiver reads
    into a variable but never actually uses for anything). Leaving this
    empty is exactly what produced both real bugs found live
    2026-09-27/28: the bracket falling back to the raw connecting IP
    instead of a BBS name, and the trailing "(<no name>)" (which,
    per mspservice.js, only ever renders in that same
    signature-was-empty fallback branch -- populating `signature`
    eliminates it entirely, not just replaces it).

    `sender_real_name` populates the MSP SENDER-TERM field (RFC 1312 --
    officially a reply-routing "terminal name", explicitly allowed to be
    empty; Synchronet's own sender never fills it). Confirmed live
    2026-09-27 that Synchronet echoes this field back RAW, directly
    after the sender's name -- a caller that falls back to re-sending
    the username here (instead of leaving it empty when no distinct
    real name exists) produces a visible duplicate, e.g. "Instant
    Message from StingRay StingRay [ip] (<no name>)". Callers should
    pass '' rather than a username fallback.

    SSRF guard: `host`/`port` reach this function directly from two
    free-text, no-format-validation user inputs (the web /imsg/send
    form and the terminal "Send Inter-BBS Instant Message" menu) with
    no admin gate on either. Without a destination check, any logged-in
    user could aim the server's own outbound connection at internal
    infrastructure (loopback, RFC1918, link-local/cloud-metadata) and
    use the distinct "delivered"/"host unreachable" outcomes as a
    connect-success oracle for internal recon. Resolved here (not left
    to each caller) since this is the one real choke point both inputs
    funnel through.
    """
    family, sockaddr, error = resolve_safe_destination(host, port)
    if error:
        logger.warning('MSP: refused destination %s:%s — %s', host, port, error)
        return False
    payload = encode(recipient=recipient, sender=sender, message=message,
                     sender_terminal=sender_real_name,
                     signature=sender_system)
    try:
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(sockaddr)
            s.sendall(payload)
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            # Read whatever the server sends back (usually nothing, but
            # some implementations echo a status line). Discard it.
            try:
                s.recv(1024)
            except socket.timeout:
                pass
        logger.info('MSP: delivered to %s:%s for "%s" (%d bytes)',
                    host, port, recipient, len(payload))
        return True
    except OSError as exc:
        logger.warning('MSP: send to %s:%s failed: %s', host, port, exc)
        return False
