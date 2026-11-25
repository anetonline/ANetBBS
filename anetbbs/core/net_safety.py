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
    # Every resolved address must be acceptable, not just the first
    # (audit M-3): a name with one public and one internal record is
    # refused outright rather than depending on resolver order.
    allowed_ports = own_ports() if callable(own_ports) else own_ports
    for _f, _t, _p, _c, _sa in infos[1:]:
        try:
            _ip = ipaddress.ip_address(_sa[0])
        except ValueError:
            return None, None, 'Invalid resolved address'
        if _ip.is_loopback and allowed_ports and port in allowed_ports:
            continue
        if not is_public_address(_ip):
            return None, None, 'Connections to private/internal addresses are not allowed'

    if ip.is_loopback and allowed_ports and port in allowed_ports:
        return family, sockaddr, None
    if not is_public_address(ip):
        return None, None, 'Connections to private/internal addresses are not allowed'
    return family, sockaddr, None


def is_public_address(ip):
    """True only for globally routable unicast addresses.

    Audit M-3: the earlier deny-list (private/link-local/loopback/
    reserved/multicast/unspecified) let 100.64.0.0/10 through -- carrier-
    grade NAT and the whole Tailscale tailnet, including 100.100.100.100 --
    because Python reports those as neither private nor reserved. An
    allow-list on is_global covers that and any other non-global range.
    IPv4-mapped/compatible IPv6 forms are judged as the IPv4 address."""
    if isinstance(ip, str):
        ip = ipaddress.ip_address(ip)
    if ip.version == 6:
        mapped = ip.ipv4_mapped or (ip.sixtofour if ip.sixtofour else None)
        if mapped is not None and not is_public_address(mapped):
            return False
    return bool(ip.is_global and not ip.is_multicast)


def safe_http_fetch(url, *, timeout=20, max_bytes=5 * 1024 * 1024,
                    max_redirects=3, user_agent='ANetBBS/1.0', own_ports=None):
    """GET `url` with every hop resolved once, validated, and pinned.

    Audit M-3: callers used `curl -L` / urllib / requests, which follow
    redirects on their own -- a public URL could 30x to an internal
    address and the response came back to the caller. This never lets the
    HTTP client follow a redirect: each Location is parsed, re-checked
    with resolve_safe_destination(), and fetched with curl --resolve
    pinned to the validated IP (keeping SNI/Host correct).

    Returns (body_bytes, None) or (None, error_message)."""
    import subprocess
    from urllib.parse import urlparse, urljoin
    for _hop in range(max_redirects + 1):
        parsed = urlparse(url or '')
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            return None, f'refused: not a plain http(s) URL: {url!r}'
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        _fam, sockaddr, err = resolve_safe_destination(parsed.hostname, port, own_ports)
        if err:
            return None, f'refused: {err}'
        ip = sockaddr[0]
        pin = f'[{ip}]' if ':' in ip else ip
        try:
            r = subprocess.run(
                ['curl', '-s', '--fail', '--proto', '=http,https',
                 '--max-redirs', '0', '--max-time', str(timeout),
                 '--max-filesize', str(max_bytes), '-A', user_agent,
                 '--resolve', f'{parsed.hostname}:{port}:{pin}',
                 '-w', '\n%{http_code}\n%{redirect_url}', '--', url],
                capture_output=True, timeout=timeout + 5)
        except subprocess.TimeoutExpired:
            return None, f'fetch timed out after {timeout}s'
        except OSError as exc:
            return None, f'fetch failed: {exc}'
        if r.returncode != 0:
            return None, (f'fetch failed: curl exited {r.returncode}: '
                          f'{r.stderr.decode(errors="replace")[:200]}')
        body, _, tail = r.stdout.rpartition(b'\n')
        body, _, code = body.rpartition(b'\n')
        redirect = tail.decode(errors='replace').strip()
        code = code.decode(errors='replace').strip()
        if code.startswith('3') and redirect:
            url = urljoin(url, redirect)
            continue
        if len(body) > max_bytes:
            return None, 'response too large'
        return body, None
    return None, f'refused: more than {max_redirects} redirects'
