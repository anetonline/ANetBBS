"""Pure-Python HTTP client for anetbbs's real Synchronet http.js
replacement (see anetbbs/games/sbbs_stubs/http.js's own docstring for
the full design). Performs exactly ONE HTTP request per invocation and
returns the raw response bytes -- Node's http.js shim replays those
bytes through a trivial fake-socket object so the REAL, unmodified
Synchronet header-construction/response-parsing logic (SetupGet,
AddDefaultHeaders, ReadStatus, ReadHeaders, ReadBody, etc -- all pure
JS with no Socket dependency in the real vendored source) keeps
working completely unchanged; only the actual network I/O (which real
Synchronet does via a native Socket object Node has no equivalent for)
is replaced, mirroring the same "shell out to Python for the one part
Node genuinely can't do synchronously" pattern already established for
jsonrpc_client.py.

Invoked via Node's child_process.execFileSync, matching that exact
same subprocess-per-call pattern -- reads one JSON request description
from stdin, writes one JSON response to stdout, exits.

Real Synchronet's own http.js always sends "Connection: close" and an
HTTP/1.0 request line (confirmed reading the real vendored source at
sbbs_stubs/http.js's AddDefaultHeaders()/SetupGet()) -- the server is
expected to close the connection once the response is fully sent, so
reading until EOF is the correct way to capture a complete response,
not a simplification of real behavior.
"""
import json
import socket
import ssl
import sys

DEFAULT_TIMEOUT = 60.0
# Real gap found in a security/performance audit: the read loop below
# had no total-size cap, bounded only by a per-recv idle timeout that
# RESETS on every successful read -- a slow-but-steady malicious or
# misbehaving server reachable via a door's http.js call could keep
# this process's memory growing indefinitely just by trickling data
# faster than the idle timeout. This is a short-lived, per-request
# subprocess (dies after one call), so the blast radius is smaller
# than a long-running daemon, but a single huge response can still
# balloon this one process before anything else would catch it.
MAX_RESPONSE_SIZE = 50 * 1024 * 1024


def _do_request(args):
    host = args['host']
    port = int(args['port'])
    scheme = args.get('scheme', 'http')
    request_line = args['request_line']
    headers = args.get('headers', [])
    body = args.get('body')
    timeout = float(args.get('timeout') or DEFAULT_TIMEOUT)

    raw = (request_line + '\r\n').encode('latin-1', errors='replace')
    for h in headers:
        raw += (h + '\r\n').encode('latin-1', errors='replace')
    raw += b'\r\n'
    if body is not None:
        raw += body.encode('latin-1', errors='replace') if isinstance(body, str) else bytes(body)

    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        if scheme == 'https':
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.sendall(raw)
        chunks = []
        total = 0
        while True:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_SIZE:
                raise ValueError(
                    f'response exceeded {MAX_RESPONSE_SIZE} byte limit')
            chunks.append(chunk)
        response = b''.join(chunks)
    finally:
        try:
            sock.close()
        except OSError:
            pass

    return {'ok': True, 'response': response.decode('latin-1')}


def main(argv=None):
    raw = sys.stdin.read()
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as exc:
        json.dump({'ok': False, 'error': f'invalid JSON on stdin: {exc}'}, sys.stdout)
        return 1
    try:
        result = _do_request(args)
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)}
    json.dump(result, sys.stdout)
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
