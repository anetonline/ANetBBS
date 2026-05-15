"""
Outbound MSP client — fire a single Message-Send Protocol packet at a
remote BBS and return whether the wire write succeeded.
"""
import logging
import socket

from .protocol import encode, MSP_DEFAULT_PORT

logger = logging.getLogger(__name__)


def send_msp(host: str, recipient: str, message: str,
             sender: str = '', sender_real_name: str = '',
             sender_system: str = '',
             port: int = MSP_DEFAULT_PORT,
             timeout: float = 10.0) -> bool:
    """Send a Message-Send Protocol message. Returns True on success.

    `sender` should be the BARE username only — Synchronet builds the
    "reply to" address as `<sender>@<reverse-DNS-of-peer-IP>` and chokes
    if `sender` already contains an @. Pass the BBS name in
    `sender_system` (lands in the MSP cookie field) instead.

    `sender_real_name` populates the MSP sender_terminal field. Synchronet
    displays it after the address; an empty value contributes to their
    `(<no name>)` rendering when IDENT is also unavailable.
    """
    payload = encode(recipient=recipient, sender=sender, message=message,
                     sender_terminal=sender_real_name,
                     cookie=sender_system)
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
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
