# anetbbs/core/net_safety.py
"""Shared SSRF-guard helper.

Real gap found in a security/performance audit: this exact resolve-
then-validate-then-connect pattern already existed once, in
anetbbs/web/web_terminal.py (a deliberate "connect to any external
host" feature that still needs to refuse internal-network targets),
but two OTHER places that fetch a URL from data an attacker can
influence -- the RSS feed poller (a sysop-configured feed URL, but
also reachable via an already-compromised admin session) and the RSS
sixel-image preview (an individual feed ITEM's image URL, controlled
by whoever published that feed, not the sysop) -- never got the same
treatment. Extracted here so all three (and anything else that needs
it later) share one implementation instead of three separately-
maintained copies that can drift out of sync.
"""
import ipaddress
import socket


def resolve_safe_destination(host, port, own_ports=None):
    """Resolve `host` ONCE and validate the resolved IP isn't private/
    link-local/loopback/reserved/multicast, before any caller connects
    to it. Resolving here (rather than letting the caller re-resolve
    the hostname string at connect time) avoids a DNS-rebinding gap
    where a hostname that resolves safely at check-time could resolve
    to an internal address by connect-time.

    `own_ports`: optional set/callable of ports this specific caller
    is allowed to reach on loopback (e.g. web_terminal.py dialing this
    same BBS's own telnet/SSH ports). Leave None (the default) for
    callers with no legitimate reason to ever hit loopback at all --
    that's the right default for anything fetching a URL embedded in
    someone else's content (RSS feed items, etc.), not just a user-
    typed destination.

    Returns (family, sockaddr, None) on success, or
    (None, None, error_message) on rejection.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return None, None, f'Could not resolve host: {host}'
    if not infos:
        return None, None, f'Could not resolve host: {host}'

    family, _socktype, _proto, _canonname, sockaddr = infos[0]
    try:
        ip = ipaddress.ip_address(sockaddr[0])
    except ValueError:
        return None, None, 'Invalid resolved address'

    allowed_ports = own_ports() if callable(own_ports) else own_ports
    if ip.is_loopback and allowed_ports and port in allowed_ports:
        return family, sockaddr, None
    if (ip.is_private or ip.is_link_local or ip.is_loopback
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
        return None, None, 'Connections to private/internal addresses are not allowed'
    return family, sockaddr, None
