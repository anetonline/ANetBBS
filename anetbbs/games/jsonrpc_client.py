"""Pure-Python client for Synchronet's real JSON-RPC interbbs protocol
(the "JSON service", conventionally port 10088 -- see
`anetbbs/games/sbbs_reference/json-client.js`/`json-service.js` for the
real upstream source this was verified against, pulled directly from
github.com/SynchronetBBS/sbbs).

Standalone and stdlib-only on purpose: this is invoked as a subprocess
(via Node's `child_process.execFileSync`) by `json-client.js`, the
ANetBBS-authored drop-in replacement for Synchronet's own
`json-client.js` that real, unmodified door scripts `load()` -- see
`anetbbs/games/sbbs_stubs/json-client.js`. Doors that only touch the
documented `JSONClient` public method surface keep working completely
unmodified; this file plus that shim together ARE the compatibility
layer, without needing to reimplement a `Socket` primitive inside the
Node compat shim (there is no synchronous TCP read in Node, so a
generic `Socket` object was the wrong unit of compatibility -- see
the design plan for the full reasoning).

Wire protocol (confirmed against the real `json-sock.js` source, not
guessed): newline-delimited JSON over a plain TCP socket --
`json.dumps(packet) + "\\r\\n"` on the way out, one line in per
response on the way back. No binary framing, no length prefix.

Packet shapes and default wait-for-response behavior below are
transcribed directly from the real `json-client.js` (each method's own
`this.send(...)` call and whether it unconditionally calls
`this.wait()`, only calls it when `TIMEOUT >= 0` (default -1, i.e.
fire-and-forget), or never calls it at all):

    op         func    oper        always waits?
    ----------------------------------------------
    ident      IDENT   (none)      no (fire-and-forget)
    who        QUERY   WHO         yes
    status     QUERY   STATUS      yes
    subscribe  QUERY   SUBSCRIBE   only if timeout>=0 (default: no)
    unsubscribe QUERY  UNSUBSCRIBE only if timeout>=0 (default: no)
    lock       QUERY   LOCK        only if timeout>=0 (default: no)
    unlock     QUERY   LOCK(-1)    only if timeout>=0 (default: no)
    read       QUERY   READ        yes
    slice      QUERY   SLICE       yes
    splice     QUERY   SPLICE      only if timeout>=0 (default: no)
    keys       QUERY   KEYS        yes
    keytypes   QUERY   KEYTYPES    yes
    shift      QUERY   SHIFT       yes
    pop        QUERY   POP         yes
    write      QUERY   WRITE       only if timeout>=0 (default: no)
    remove     QUERY   DELETE      only if timeout>=0 (default: no)
    unshift    QUERY   UNSHIFT     only if timeout>=0 (default: no)
    push       QUERY   PUSH        only if timeout>=0 (default: no)
    raw        (caller-supplied)   yes -- the real client's own low-
                                    level send()/wait() pair, combined
                                    into one call (see raw() below)

LOCK_READ/LOCK_WRITE/LOCK_UNLOCK match the real client's own
documented constants (1 / 2 / -1).
"""
import hashlib
import json
import os
import select
import socket
import sys

LOCK_READ = 1
LOCK_WRITE = 2
LOCK_UNLOCK = -1

# Matches the real client's own settings.SOCK_TIMEOUT (30*1000 ms) and
# CONNECTION_TIMEOUT (10s) defaults.
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_RECV_TIMEOUT = 30.0

# Ops that unconditionally wait for a response, regardless of the
# client's TIMEOUT setting -- transcribed from the real source (see
# module docstring table).
_ALWAYS_WAITS = frozenset((
    'WHO', 'STATUS', 'READ', 'SLICE', 'KEYS', 'KEYTYPES', 'SHIFT', 'POP',
))
# Ops that only wait when the caller explicitly asks for it (the real
# client's default TIMEOUT is -1, meaning "don't wait" for all of
# these) -- fire-and-forget by default, matching real behavior exactly
# so a door doing `jsonClient.write(...)` without using the return
# value doesn't pay for a round trip it never asked for.
_OPTIONAL_WAIT = frozenset((
    'SUBSCRIBE', 'UNSUBSCRIBE', 'LOCK', 'SPLICE', 'WRITE', 'DELETE',
    'UNSHIFT', 'PUSH',
))


class JSONRPCError(Exception):
    """Raised when the server sends back an ERROR packet, or the
    connection/response is otherwise malformed -- mirrors the real
    client's own `throw new Error(packet.data.description)` in
    receive()."""


class JSONRPCClient:
    """Synchronous client for a single TCP connection against a
    Synchronet JSON-RPC server. Two callers use this class two
    different ways:

    - `run_one_call()` uses it exactly the way its name implies: one
      connection, one request, one response, then close -- this is
      what every ordinary read/write/lock/etc. call from a door goes
      through (via json-client.js's `call()`), matching this project's
      long-established "no synchronous TCP read in Node" workaround.
    - `run_listen_session()` uses the SAME class differently: connects
      once and keeps the connection open indefinitely, used ONLY for
      subscribe()-driven push UPDATE packets (a real Synchronet
      client's own persistent-connection use case) -- see that
      function's docstring for why this needed its own separate
      execution path rather than trying to unify with the one-shot
      case."""

    def __init__(self, host, port,
                 connect_timeout=DEFAULT_CONNECT_TIMEOUT,
                 recv_timeout=DEFAULT_RECV_TIMEOUT):
        self.host = host
        self.port = int(port)
        self.connect_timeout = connect_timeout
        self.recv_timeout = recv_timeout
        self._sock = None
        self._rfile = None

    def connect(self):
        self._sock = socket.create_connection(
            (self.host, self.port), timeout=self.connect_timeout)
        # Line-buffered reads, matching the real client's line-delimited
        # framing (recvline()). errors='replace', not strict (the
        # default) -- real crash found live bundling Star Trek: a
        # scoreboard entry (player alias, ship name, etc -- any
        # free-text field a real BBS user typed) can legitimately
        # contain a raw extended-ASCII/CP437 byte, which is not valid
        # UTF-8, and a strict decode blows up the ENTIRE line read
        # with UnicodeDecodeError -- surfaced to callers as "bad
        # arguments: 'utf-8' codec can't decode byte 0x81...", not
        # even hinting at the real cause. Matches the leniency already
        # established for the OTHER read path in this same file (the
        # persistent-connection subscribe() daemon, `chunk.decode(
        # 'utf-8', errors='replace')` below) -- this one-shot path was
        # simply never brought into line with that when it was added.
        self._rfile = self._sock.makefile(
            'r', encoding='utf-8', errors='replace', newline='\n')

    def close(self):
        if self._rfile is not None:
            try:
                self._rfile.close()
            except OSError:
                pass
            self._rfile = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def _send(self, packet):
        data = (json.dumps(packet) + '\r\n').encode('utf-8')
        self._sock.settimeout(self.recv_timeout)
        self._sock.sendall(data)

    # Real gap found in a security/performance audit: readline() below
    # had no max-line-length guard -- a broken or hostile JSON-RPC peer
    # (this is a real socket to json-sock.js's server, not just trusted
    # local IPC) sending an infinite line with no '\n' would grow this
    # process's memory unboundedly while readline() waits for a
    # terminator that never arrives. Real JSON-RPC packets for this
    # API are small control messages; 1MB is generous headroom.
    _MAX_LINE_CHARS = 1_000_000

    def _recv_one(self):
        """Read and parse exactly one line-delimited JSON packet.
        Transparently answers server PINGs with a PONG (matching
        json-sock.js's own Socket.prototype.process()) and skips PONGs
        -- neither is a real response to hand back to the caller."""
        self._sock.settimeout(self.recv_timeout)
        while True:
            line = self._rfile.readline(self._MAX_LINE_CHARS)
            if not line:
                raise JSONRPCError('connection closed by server')
            if len(line) >= self._MAX_LINE_CHARS and not line.endswith('\n'):
                raise JSONRPCError(
                    f'line exceeded {self._MAX_LINE_CHARS} char limit with no '
                    'terminator -- refusing to keep reading')
            line = line.strip()
            if not line:
                continue
            packet = json.loads(line)
            func = str(packet.get('func', '')).upper()
            if func == 'PING':
                self._send({'scope': 'SOCKET', 'func': 'PONG',
                           'data': packet.get('data')})
                continue
            if func == 'PONG':
                continue
            if func == 'ERROR':
                desc = ((packet.get('data') or {}).get('description')
                        if isinstance(packet.get('data'), dict) else None)
                raise JSONRPCError(desc or 'server returned an ERROR packet')
            return packet

    def _query(self, oper, scope, location, data=None, lock=None,
              timeout=-1, wait=None, extra=None):
        packet = {
            'scope': scope,
            'func': 'QUERY',
            'oper': oper,
            'location': location,
            'timeout': timeout,
        }
        if data is not None:
            packet['data'] = data
        if lock is not None:
            packet['lock'] = lock
        if extra:
            packet.update(extra)
        self._send(packet)
        if wait is None:
            wait = (oper in _ALWAYS_WAITS or
                    (oper in _OPTIONAL_WAIT and timeout is not None and timeout >= 0))
        if not wait:
            return None
        response = self._recv_one()
        return response.get('data')

    # -- direct methods, one per real JSONClient method -------------------

    def ident(self, scope, username, password):
        pw_hash = hashlib.md5(str(password).upper().encode('utf-8')).hexdigest()  # nosec B324 -- matches real Synchronet protocol's own md5_calc() scheme, not a security boundary we control
        self._send({'scope': scope, 'func': 'IDENT',
                   'username': username, 'pw': pw_hash})

    def who(self, scope, location):
        return self._query('WHO', scope, location, wait=True)

    def status(self, scope, location):
        return self._query('STATUS', scope, location, wait=True)

    def subscribe(self, scope, location, nick=None, system_name=None, wait=None):
        extra = {}
        if nick is not None:
            extra['nick'] = nick
        if system_name is not None:
            extra['system'] = system_name
        return self._query('SUBSCRIBE', scope, location, extra=extra, wait=wait)

    def unsubscribe(self, scope, location, wait=None):
        return self._query('UNSUBSCRIBE', scope, location, wait=wait)

    def lock(self, scope, location, lock_type, wait=None):
        return self._query('LOCK', scope, location, data=lock_type, wait=wait)

    def unlock(self, scope, location, wait=None):
        return self.lock(scope, location, LOCK_UNLOCK, wait=wait)

    def read(self, scope, location, lock=None):
        return self._query('READ', scope, location, lock=lock, wait=True)

    def slice(self, scope, location, start=None, end=None, lock=None):
        # Real Synchronet's server-side slice (json-db.js) does a plain
        # `array.slice(request.data.start, request.data.end)` -- JS's
        # own native Array.slice already treats an omitted `end` (or
        # `start`) as "through the end of the array" / "from the
        # start", so both are genuinely optional here, not just
        # missing-argument bugs in a caller.
        return self._query('SLICE', scope, location,
                          data={'start': start, 'end': end}, lock=lock, wait=True)

    def splice(self, scope, location, start, num, data, lock=None, wait=None):
        return self._query('SPLICE', scope, location,
                          data={'start': start, 'num': num, 'data': data},
                          lock=lock, wait=wait)

    def keys(self, scope, location, lock=None):
        return self._query('KEYS', scope, location, lock=lock, wait=True)

    def key_types(self, scope, location, lock=None):
        return self._query('KEYTYPES', scope, location, lock=lock, wait=True)

    def shift(self, scope, location, lock=None):
        return self._query('SHIFT', scope, location, lock=lock, wait=True)

    def pop(self, scope, location, lock=None):
        return self._query('POP', scope, location, lock=lock, wait=True)

    def write(self, scope, location, data, lock=None, wait=None):
        return self._query('WRITE', scope, location, data=data, lock=lock, wait=wait)

    def remove(self, scope, location, lock=None, wait=None):
        return self._query('DELETE', scope, location, lock=lock, wait=wait)

    def unshift(self, scope, location, data, lock=None, wait=None):
        return self._query('UNSHIFT', scope, location, data=data, lock=lock, wait=wait)

    def push(self, scope, location, data, lock=None, wait=None):
        return self._query('PUSH', scope, location, data=data, lock=lock, wait=wait)

    def raw(self, packet):
        """Real client's own low-level send()/wait() pair, combined
        into one call since this project's architecture is one
        connection per CLI invocation (see json-client.js's own
        send()/wait() shim, which stores the packet from send() and
        performs the actual round trip here on wait()). Found live
        bundling Thirstyville: `jsonClient.send({scope:"ADMIN",
        func:"TIME"}); jsonClient.wait();` -- a real Synchronet
        "what time does the server think it is" admin query, sent as
        a bare hand-built packet rather than through any of the
        higher-level read/write/etc convenience methods above (which
        this method doesn't replace -- both real client and this one
        can send genuinely arbitrary packets this way, not just
        ADMIN/TIME). Unconditionally waits for a response, matching
        the real client's own `send()` (fire-and-forget) + `wait()`
        (always blocks for a RESPONSE) pairing exactly."""
        self._send(packet)
        response = self._recv_one()
        return response.get('data')


_OP_METHODS = {
    'IDENT': lambda c, a: (c.ident(a['scope'], a['username'], a['password']), None)[1],
    'WHO': lambda c, a: c.who(a['scope'], a.get('location')),
    'STATUS': lambda c, a: c.status(a['scope'], a.get('location')),
    'SUBSCRIBE': lambda c, a: c.subscribe(
        a['scope'], a.get('location'), a.get('nick'), a.get('system_name'), a.get('wait')),
    'UNSUBSCRIBE': lambda c, a: c.unsubscribe(a['scope'], a.get('location'), a.get('wait')),
    'LOCK': lambda c, a: c.lock(a['scope'], a.get('location'), a['lock'], a.get('wait')),
    'UNLOCK': lambda c, a: c.unlock(a['scope'], a.get('location'), a.get('wait')),
    'READ': lambda c, a: c.read(a['scope'], a.get('location'), a.get('lock')),
    'SLICE': lambda c, a: c.slice(
        a['scope'], a.get('location'), a.get('start'), a.get('end'), a.get('lock')),
    'SPLICE': lambda c, a: c.splice(
        a['scope'], a.get('location'), a['start'], a['num'], a.get('data'),
        a.get('lock'), a.get('wait')),
    'KEYS': lambda c, a: c.keys(a['scope'], a.get('location'), a.get('lock')),
    'KEYTYPES': lambda c, a: c.key_types(a['scope'], a.get('location'), a.get('lock')),
    'SHIFT': lambda c, a: c.shift(a['scope'], a.get('location'), a.get('lock')),
    'POP': lambda c, a: c.pop(a['scope'], a.get('location'), a.get('lock')),
    'WRITE': lambda c, a: c.write(
        a['scope'], a.get('location'), a.get('data'), a.get('lock'), a.get('wait')),
    'DELETE': lambda c, a: c.remove(a['scope'], a.get('location'), a.get('lock'), a.get('wait')),
    'UNSHIFT': lambda c, a: c.unshift(
        a['scope'], a.get('location'), a.get('data'), a.get('lock'), a.get('wait')),
    'PUSH': lambda c, a: c.push(
        a['scope'], a.get('location'), a.get('data'), a.get('lock'), a.get('wait')),
    'RAW': lambda c, a: c.raw(a['packet']),
}


def run_one_call(args):
    """Perform exactly one JSON-RPC operation and return
    {'ok': True, 'data': ...} or {'ok': False, 'error': ...}. `args` is
    the same shape read from stdin by main() -- factored out so tests
    can call this directly without a subprocess."""
    op = str(args.get('op', '')).upper()
    handler = _OP_METHODS.get(op)
    if handler is None:
        return {'ok': False, 'error': f'unknown op {op!r}'}
    try:
        with JSONRPCClient(args['host'], args['port'],
                           connect_timeout=args.get('connect_timeout', DEFAULT_CONNECT_TIMEOUT),
                           recv_timeout=args.get('recv_timeout', DEFAULT_RECV_TIMEOUT)) as client:
            if 'ident_username' in args and op != 'IDENT':
                client.ident(args['scope'], args['ident_username'], args.get('ident_password', ''))
            result = handler(client, args)
            return {'ok': True, 'data': result}
    except JSONRPCError as exc:
        return {'ok': False, 'error': str(exc)}
    except (OSError, socket.timeout) as exc:
        return {'ok': False, 'error': f'connection error: {exc}'}
    except (KeyError, TypeError, ValueError) as exc:
        return {'ok': False, 'error': f'bad arguments: {exc}'}


class _LineReader:
    """Incremental line reader over a raw fd, deliberately bypassing
    Python's own buffered sys.stdin. select() reports readiness at the
    OS/kernel level, but a buffered readline() can pull more than one
    line's worth of bytes into its own userspace buffer in a single
    underlying read() syscall -- any extra already-buffered data then
    becomes invisible to a later select() check, even though it's no
    longer sitting in the kernel's pipe buffer at all. Confirmed live:
    json-client.js's ensureDaemon() writes the daemon's initial config
    line and its first subscribe command in quick succession (the
    normal case), and a plain sys.stdin.readline() for the config line
    silently absorbed the subscribe command's bytes too -- the
    select() loop below then waited forever for stdin to become
    "readable" again, since at the OS level it already had been."""

    def __init__(self, fd):
        self.fd = fd
        self._buf = ''

    def readline_blocking(self):
        """Blocks (real blocking read, no select needed) until a full
        line is available or EOF. Used once, for the very first
        (config) line, before any select() loop starts."""
        while '\n' not in self._buf:
            chunk = os.read(self.fd, 65536)
            if not chunk:
                return None
            self._buf += chunk.decode('utf-8', errors='replace')
        line, self._buf = self._buf.split('\n', 1)
        return line

    def pop_ready_lines(self):
        """Non-blocking in practice: caller must already know (via
        select()) that data is available. Returns a list of complete
        lines found (possibly empty if only a partial line arrived so
        far -- it stays buffered for next time), or None on EOF."""
        chunk = os.read(self.fd, 65536)
        if not chunk:
            return None
        self._buf += chunk.decode('utf-8', errors='replace')
        return self.pop_buffered_lines()

    def pop_buffered_lines(self):
        """Returns any complete lines ALREADY sitting in the internal
        buffer, performing NO new read at all -- must be checked
        before every select() call, not only after one reports the fd
        readable: a single underlying read() (in readline_blocking()
        or a previous pop_ready_lines() call) can pull in more than
        one line's worth of bytes at once, and once those bytes are
        out of the kernel's pipe buffer, select() has nothing left to
        report on them -- confirmed live, this is exactly what caused
        the very first subscribe() command after a fresh daemon spawn
        to go unprocessed forever (config line + first command arrive
        close enough together to land in the same read())."""
        lines = []
        while '\n' in self._buf:
            line, self._buf = self._buf.split('\n', 1)
            lines.append(line)
        return lines


def _drain_socket(client, updates_file):
    """Reads every packet currently buffered on `client`'s socket
    (guarded by an outer select() so this doesn't block waiting for
    data that hasn't arrived yet -- see run_listen_session()'s own
    comment on the one accepted, narrow exception to that). Mirrors
    the real client's own receive() packet handling (PING answered
    transparently, PONG dropped) but routes UPDATE packets (real
    Synchronet's push-notification func, confirmed against json-db.js's
    send_packet(client,"UPDATE",oper,location,data) calls) to
    `updates_file` instead of returning them -- nothing is
    synchronously waiting on THIS connection for a specific response
    (all read/write/lock/etc. calls use their own separate one-shot
    connection), so a stray RESPONSE/ERROR here is simply discarded."""
    while True:
        ready, _, _ = select.select([client._sock], [], [], 0)
        if not ready:
            return
        line = client._rfile.readline()
        if not line:
            raise JSONRPCError('connection closed by server')
        line = line.strip()
        if not line:
            continue
        packet = json.loads(line)
        func = str(packet.get('func', '')).upper()
        if func == 'PING':
            client._send({'scope': 'SOCKET', 'func': 'PONG',
                         'data': packet.get('data')})
            continue
        if func == 'PONG':
            continue
        if func == 'UPDATE':
            with open(updates_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(packet) + '\n')
            continue
        # RESPONSE / ERROR: discarded, see docstring.


def run_listen_session(args, stdin_reader):
    """Persistent listen-mode session -- ONE real connection held open
    for the life of this process, used exclusively for subscribe()-
    driven push updates. Ordinary read/write/lock/etc. calls never go
    through this; they keep using the existing one-shot run_one_call()
    path (a separate connection per call), completely unaffected.

    Why this needs to exist at all: Synchronet's subscribe() only
    delivers updates to whatever connection sent the SUBSCRIBE request,
    pushed asynchronously whenever the subscribed location changes --
    there's no way to "poll" for them via a fresh one-shot connection.
    A real client (json-client.js's own receive()/cycle()) reads these
    off its single long-lived socket() on demand. Node has no
    synchronous TCP read, so json-client.js can't hold that socket
    itself -- this process holds it instead, and hands updates to
    Node via a plain file it can synchronously fs.readSync() poll
    during cycle() (see json-client.js's own comment on why a file,
    not a pipe FD, was chosen: no reliance on child_process stdio
    internals).

    `args`: {host, port, scope, updates_file, ident_username?,
    ident_password?, connect_timeout?, recv_timeout?}. `scope` here is
    only used for the initial IDENT (if credentials were given) --
    each command below carries its own `scope`, since nothing prevents
    a door's JSONClient instance from subscribing across more than one
    scope in principle, even though every real door seen so far only
    ever uses one. `stdin_reader` is a `_LineReader` already
    constructed by main() (which used it to read this function's own
    `args` off the first line -- see that class's docstring for why a
    raw incremental reader is required here instead of plain
    sys.stdin.readline(), a real bug found via this project's own
    tests, not a hypothetical). Commands are read one JSON object per
    line thereafter:
      {"cmd": "subscribe", "scope": ..., "location": ..., "nick": ..., "system_name": ...}
      {"cmd": "unsubscribe", "scope": ..., "location": ...}
      {"cmd": "quit"}
    Exits on a "quit" command, on stdin EOF (the parent process closed
    the pipe -- e.g. the door process exited), or on a connection
    error. select() multiplexes between the socket (drain any pending
    updates) and stdin (apply new commands) so neither one starves the
    other; the small `select` timeout keeps this loop from blocking
    indefinitely on either source alone.
    """
    updates_file = args['updates_file']
    client = JSONRPCClient(
        args['host'], args['port'],
        connect_timeout=args.get('connect_timeout', DEFAULT_CONNECT_TIMEOUT),
        recv_timeout=args.get('recv_timeout', DEFAULT_RECV_TIMEOUT))
    try:
        client.connect()
    except (OSError, socket.timeout) as exc:
        json.dump({'ok': False, 'error': f'connection error: {exc}'}, sys.stdout)
        return 1
    scope = args['scope']

    def _apply_commands(lines):
        """Returns True if a "quit" command was among `lines`."""
        for line in lines:
            line = line.strip()
            if not line:
                continue
            cmd = json.loads(line)
            action = str(cmd.get('cmd', '')).lower()
            if action == 'subscribe':
                client.subscribe(cmd.get('scope', scope), cmd.get('location'),
                                 cmd.get('nick'), cmd.get('system_name'),
                                 wait=False)
            elif action == 'unsubscribe':
                client.unsubscribe(cmd.get('scope', scope), cmd.get('location'), wait=False)
            elif action == 'quit':
                return True
        return False

    try:
        if 'ident_username' in args:
            client.ident(scope, args['ident_username'], args.get('ident_password', ''))
        while True:
            # Must check for already-buffered complete lines BEFORE
            # calling select() every iteration, not only in response
            # to a fresh select() readiness signal -- see
            # pop_buffered_lines()'s own docstring for why (a single
            # earlier read() can pull in more than one line's worth of
            # data at once, and select() has nothing left to report
            # once those bytes are out of the kernel's pipe buffer).
            buffered = stdin_reader.pop_buffered_lines()
            if buffered and _apply_commands(buffered):
                break
            readable, _, _ = select.select([client._sock, stdin_reader.fd], [], [], 1.0)
            if client._sock in readable:
                _drain_socket(client, updates_file)
            if stdin_reader.fd in readable:
                lines = stdin_reader.pop_ready_lines()
                if lines is None:
                    return 0  # parent closed the pipe -- door process exited
                if _apply_commands(lines):
                    break
    except (JSONRPCError, OSError, socket.timeout):
        return 1
    finally:
        client.close()
    return 0


def main(argv=None):
    """CLI entrypoint. Two modes:

    - Default (no args): reads ONE JSON object from stdin describing a
      single call (host, port, op, scope, location, data, lock, ...),
      performs it, writes one JSON response object to stdout, exits.
      Invoked by json-client.js via execFileSync for ordinary
      read/write/lock/etc. calls -- see that file's own docstring for
      the calling convention.
    - `--listen`: reads one JSON config object from the FIRST line of
      stdin, then runs run_listen_session() (see its docstring) --
      invoked by json-client.js via a persistent child_process.spawn
      for subscribe()-driven push updates.
    """
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == '--listen':
        reader = _LineReader(sys.stdin.fileno())
        config_line = reader.readline_blocking()
        if config_line is None:
            json.dump({'ok': False, 'error': 'stdin closed before config was received'}, sys.stdout)
            return 1
        try:
            args = json.loads(config_line)
        except json.JSONDecodeError as exc:
            json.dump({'ok': False, 'error': f'invalid JSON on stdin: {exc}'}, sys.stdout)
            return 1
        return run_listen_session(args, reader)
    raw = sys.stdin.read()
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as exc:
        json.dump({'ok': False, 'error': f'invalid JSON on stdin: {exc}'}, sys.stdout)
        return 1
    result = run_one_call(args)
    json.dump(result, sys.stdout)
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
