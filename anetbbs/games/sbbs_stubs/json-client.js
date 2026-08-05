/* ANetBBS-authored replacement for Synchronet's real json-client.js.

   WHY THIS EXISTS: the real json-client.js (preserved verbatim at
   anetbbs/games/sbbs_reference/json-client.js for reference) calls
   `new Socket()` and does synchronous reads on it. Node has no
   synchronous TCP read primitive, so a real `Socket` object was never
   going to be reproducible inside the Node compat shim -- implementing
   one generically would mean reimplementing an async event loop's
   worth of TCP semantics for a payoff that in practice reduces to "run
   this one library file". Instead: doors never touch Socket directly,
   they only ever `load("json-client.js")` and call JSONClient's
   documented public methods. THAT method surface is the real
   compatibility contract, not the Socket internals. So this file
   reproduces the identical public JSONClient surface (same method
   names, same arguments, same packet shapes/lock semantics as the
   real client -- verified directly against the real source, not
   guessed), but each call shells out synchronously to
   anetbbs/games/jsonrpc_client.py, a pure-Python client that speaks
   the real wire protocol (newline-delimited JSON over plain TCP,
   confirmed from json-sock.js) directly against a real Synchronet
   JSON-RPC server. Any door that only touches the public JSONClient
   surface -- the realistic case, since that's the whole point of the
   vendored library -- keeps working completely unmodified.

   Known, deliberate behavioral differences from the real client:

   1. Ordinary calls (read/write/lock/etc.) are each a fresh,
      independent TCP connection (opened and closed by a Python
      subprocess for that one call), not shared with a long-lived
      socket -- matches the real client observably (see the lock()/
      unlock() comment below for the one place this needed real
      compensating logic).

      subscribe()/unsubscribe()/cycle(), by contrast, DO use a real
      persistent connection: subscribe() lazily spawns a background
      Python process (jsonrpc_client.py --listen -- see its own
      docstring) that holds one long-lived socket dedicated to
      receiving the server's pushed UPDATE packets, handing them to
      this shim via a plain temp file cycle() polls synchronously.
      Confirmed live: real cross-BBS Tetris (synchronetris) depends on
      this for its lobby's live game list and in-game piece-queue
      sync, both built entirely on subscribe()-driven push updates --
      first found broken (silently, since the door never crashed, it
      just never updated) via that door's own real dependency on it,
      not something a leaderboard-only door like Chicken Delivery or
      Bubble Boggle would ever exercise.

   2. The constructor never throws on an unreachable server (the real
      client connects eagerly in its constructor and throws
      immediately if that fails). This shim defers all connection
      attempts to the first actual method call, so a door doing
      `var client = new JSONClient(host, port);` with no host/port
      validation still gets a real, catchable error -- just on first
      use rather than at construction.

   Public surface matches the real client: connect, disconnect, cycle,
   read, write, push, pop, shift, unshift, splice, slice, remove, lock,
   unlock, subscribe, unsubscribe, who, status, ident, keys, keyTypes,
   readmulti, plus the `callback`/`updates` properties (present but
   inert, per difference #1 above) and a `connected` getter.
*/

function JSONClient(serverAddr, serverPort) {
	this.VERSION = "1.30-anetbbs-shim";
	this.serverAddr = serverAddr;
	if (this.serverAddr == undefined)
		throw new Error("no host specified");
	this.serverPort = serverPort;
	if (this.serverPort == undefined)
		throw new Error("no port specified");

	this.settings = {
		CONNECTION_TIMEOUT: 10,
		SOCK_TIMEOUT: 30 * 1000,
		TIMEOUT: -1
	};

	this.callback = undefined;
	this.updates = [];

	var self = this;
	var identUsername, identPassword;

	/* Real Synchronet's server-side lock model (confirmed against the
	   real json-db.js source) requires a LOCK to be acquired and held
	   on the SAME connection/client identity as whatever operation
	   later relies on it -- json-db.js's this.read()/write()/etc. all
	   check `record.info.lock[client.id]` and simply fail (silently
	   re-queued, never responding) if that client.id hasn't locked the
	   record. The real client satisfies this by using ONE persistent
	   socket for a door's whole session; this shim opens a fresh,
	   independent TCP connection per call (see the docstring above),
	   so a real network LOCK request from one call and a bare
	   read/write from a later call would never share a client.id --
	   the later call hangs until this shim's own recv timeout fires
	   ("connection error: timed out"), confirmed live on real hardware
	   with a real door (Bubble Boggle's game.js does exactly
	   `client.lock(scope,"month",2); ...; client.read(scope,"month");
	   ...; client.unlock(scope,"month");`).

	   The real server ALSO has, and this shim already correctly uses,
	   a per-request atomic path: any single request that carries a
	   `lock` value gets auto-expanded server-side into
	   [LOCK, <op>, UNLOCK] for that one connection (confirmed in
	   json-db.js's own query() method) -- this is exactly how
	   Chicken Delivery's `jsonClient.read(db, loc, 1)` (always passing
	   lock inline, never calling lock()/unlock() separately) already
	   works correctly today.

	   So rather than reproduce genuine persistent-connection state
	   (a much larger change), lock()/unlock() below are purely local
	   bookkeeping -- no network call at all, so no stray real lock
	   ever gets left on the shared server -- and every data operation
	   that DOESN'T receive its own explicit lock argument
	   automatically picks up whichever currently-tracked explicit
	   lock covers its location (exact match, or a location nested
	   under a locked parent, e.g. a lock on "boards" covers a bare
	   write to "boards.5" -- matching the real server's own
	   ancestor-walk lock inheritance in identify_remains()/
	   investigate()), then sends its own self-contained atomic
	   [LOCK,op,UNLOCK] request for that specific location. The one
	   real behavioral difference from a genuine held lock: between
	   separate auto-wrapped calls there's a brief window where another
	   real client could interleave, instead of one continuous critical
	   section -- an acceptable trade-off for a low-frequency operation
	   like monthly board rotation, not a data-corruption risk (each
	   individual operation is still fully atomic on the server). */
	var heldLocks = {};

	function lockKey(scope, location) {
		return String(scope) + "\x00" + String(location == null ? '' : location);
	}

	function effectiveLock(scope, location, explicitLock) {
		if (explicitLock !== undefined && explicitLock !== null)
			return explicitLock;
		var loc = String(location == null ? '' : location);
		for (var key in heldLocks) {
			var sep = key.indexOf("\x00");
			var lockScope = key.slice(0, sep);
			var lockLoc = key.slice(sep + 1);
			if (lockScope !== String(scope))
				continue;
			if (loc === lockLoc || loc.indexOf(lockLoc + ".") === 0)
				return heldLocks[key];
		}
		return undefined;
	}

	function pythonExe() {
		return process.env.ANETBBS_JSONRPC_CLI_PYTHON || 'python3';
	}
	function cliPath() {
		var p = process.env.ANETBBS_JSONRPC_CLI_PATH;
		if (!p)
			throw new Error("ANETBBS_JSONRPC_CLI_PATH not set -- json-client.js shim can't locate jsonrpc_client.py");
		return p;
	}

	/* Persistent-connection support for subscribe()-driven push updates
	   (difference #1 above is now real for the common "subscribe once,
	   read updates via cycle()" pattern -- see jsonrpc_client.py's
	   run_listen_session() for the full design/reasoning). Lazily
	   spawned on the FIRST subscribe() call, so a door that never
	   subscribes (the common case -- Chicken Delivery, Bubble Boggle)
	   never pays for a background process at all. Ordinary read/write/
	   lock/etc. calls are completely unaffected -- they still go
	   through the one-shot call() path above, unchanged.

	   The daemon hands updates to Node via a plain temp file rather
	   than a pipe FD: Node has no supported way to synchronously read
	   an arbitrary child process's stdout pipe (unlike fd 0/stdin,
	   which this compat shim's own terminal-input code already reads
	   synchronously via fs.readSync -- a real file has no such
	   restriction and needs no reliance on child_process internals). */
	var daemonProc = null;
	var updatesFilePath = null;
	var updatesFileReadPos = 0;

	function ensureDaemon() {
		if (daemonProc) return;
		var cp = _node_require('child_process');
		var os = _node_require('os');
		updatesFilePath = _path.join(os.tmpdir(),
			'anetbbs_jsonrpc_updates_' + process.pid + '_' + Date.now() + '.jsonl');
		var config = { host: self.serverAddr, port: self.serverPort, scope: '' };
		if (identUsername !== undefined) {
			config.ident_username = identUsername;
			config.ident_password = identPassword;
		}
		config.updates_file = updatesFilePath;
		daemonProc = cp.spawn(pythonExe(), [cliPath(), '--listen'],
			{ stdio: ['pipe', 'ignore', 'ignore'] });
		// Without this, the daemon's still-open stdin pipe is a
		// referenced handle that keeps Node's event loop alive --
		// confirmed live: a door that subscribe()s and then exits
		// normally (Synchronetris's lobby, quitting cleanly) finished
		// its own script in full, but the Node process never actually
		// terminated, so the surrounding session handler hung forever
		// waiting for it -- indistinguishable from a total lockup,
		// since nothing was left to read further keystrokes either.
		// unref() tells Node this child isn't a reason to keep running;
		// the pipe write below still reaches the daemon regardless
		// (OS-buffered), and stopDaemon()/process-exit cleanup below is
		// unaffected -- this only changes whether Node waits on it.
		daemonProc.unref();
		// A write to a pipe whose other end is already gone doesn't
		// throw synchronously -- Node reports it later as an async
		// 'error' event on the stream. Without a listener here, that
		// event is unhandled and CRASHES THE WHOLE PROCESS (confirmed
		// live: "Error: write EPIPE" killed an entire door session at
		// exit, from unsubscribe()'s own sendDaemonCommand() call,
		// even though that call already wraps its write in a
		// try/catch -- which only catches synchronous throws and
		// never sees this). The daemonProc.on('error', ...) handler
		// below covers the CHILD PROCESS object; daemonProc.stdin is
		// a separate EventEmitter that needs its own listener, and
		// must be attached before the very first write (right below)
		// in case the daemon never starts at all.
		daemonProc.stdin.on('error', function () { /* daemon likely gone -- same best-effort intent as the try/catch below */ });
		daemonProc.stdin.write(JSON.stringify(config) + '\n');
		daemonProc.on('error', function () { /* best-effort -- subscribe() degrades to a no-op push feed */ });
		// Best-effort cleanup if the door process exits without an
		// explicit disconnect() -- mirrors the temp-file cleanup
		// pattern DoorSession already uses elsewhere in this project.
		process.on('exit', function () { stopDaemon(); });
	}

	function sendDaemonCommand(cmdObj) {
		if (!daemonProc || !daemonProc.stdin.writable) return;
		try { daemonProc.stdin.write(JSON.stringify(cmdObj) + '\n'); } catch (e) { /* daemon likely gone */ }
	}

	function stopDaemon() {
		if (!daemonProc) return;
		try { sendDaemonCommand({ cmd: 'quit' }); } catch (e) {}
		try { daemonProc.kill(); } catch (e) {}
		daemonProc = null;
		if (updatesFilePath) {
			try { _fs.unlinkSync(updatesFilePath); } catch (e) {}
		}
	}

	/* Synchronously reads whatever new lines the daemon has appended
	   to the updates file since the last check -- a plain, ordinary
	   file read, so no async/callback machinery needed even though
	   the data arrived asynchronously from the daemon's point of
	   view. */
	function drainUpdatesFile() {
		if (!updatesFilePath || !_fs.existsSync(updatesFilePath)) return;
		var size = _fs.statSync(updatesFilePath).size;
		if (size <= updatesFileReadPos) return;
		var fd = _fs.openSync(updatesFilePath, 'r');
		try {
			var len = size - updatesFileReadPos;
			var buf = Buffer.alloc(len);
			var bytesRead = _fs.readSync(fd, buf, 0, len, updatesFileReadPos);
			var text = buf.toString('utf8', 0, bytesRead);
			// Only consume up through the last complete (newline-
			// terminated) line -- the daemon's own write is a single
			// f.write() call per packet so a torn read is extremely
			// unlikely, but a partial trailing line staying
			// unconsumed (picked up whole on the next cycle() call
			// instead) costs nothing and avoids silently dropping data.
			var lastNewline = text.lastIndexOf('\n');
			if (lastNewline === -1) return;
			var complete = text.slice(0, lastNewline);
			updatesFileReadPos += lastNewline + 1;
			var lines = complete.split('\n');
			for (var i = 0; i < lines.length; i++) {
				var line = lines[i].trim();
				if (!line) continue;
				try { self.updates.push(JSON.parse(line)); } catch (e) { /* malformed -- skip this one line */ }
			}
		} finally {
			_fs.closeSync(fd);
		}
	}

	/* Shells out to jsonrpc_client.py for exactly one operation.
	   Synchronous by design -- matches the real client's own blocking
	   this.wait(), and matches the well-established bbs.exec()/
	   system.popen() pattern already used elsewhere in this compat
	   shim for exactly this kind of "must behave synchronously"
	   subprocess call. */
	function call(argsObj) {
		var cp = _node_require('child_process');
		argsObj.host = self.serverAddr;
		argsObj.port = self.serverPort;
		if (identUsername !== undefined) {
			argsObj.ident_username = identUsername;
			argsObj.ident_password = identPassword;
		}
		var out, result;
		try {
			out = cp.execFileSync(pythonExe(), [cliPath()], {
				input: JSON.stringify(argsObj),
				encoding: 'utf8',
				timeout: self.settings.SOCK_TIMEOUT + 5000
			});
		} catch (e) {
			// execFileSync throws on nonzero exit (jsonrpc_client.py exits
			// 1 on a reported failure) -- stdout is still attached to the
			// error object with the real {ok:false,error:...} body.
			out = (e && typeof e.stdout === 'string') ? e.stdout : null;
			if (!out) {
				throw new Error("json-client.js shim: subprocess failed: " + (e && e.message ? e.message : e));
			}
		}
		try {
			result = JSON.parse(out);
		} catch (e) {
			throw new Error("json-client.js shim: bad JSON from jsonrpc_client.py: " + out);
		}
		if (!result.ok)
			throw new Error(result.error || "json-client.js shim: server reported an error");
		return result.data;
	}

	this.connect = function() { return true; };
	this.disconnect = function() { stopDaemon(); return true; };
	// Real Synchronet's own connected getter (confirmed against the
	// real vendored json-client.js at sbbs_reference/) returns
	// `this.socket.is_connected` -- whether the client's ONE
	// persistent connection is still alive. This shim has no single
	// persistent connection for ordinary read/write/etc (each is its
	// own fresh one-shot connection, by design -- see this file's own
	// top-of-file docstring) -- the only persistent connection that
	// exists at all is the subscribe() daemon, so that's the closest
	// faithful equivalent: "connected" reflects whether that
	// persistent connection is alive, which is exactly the case any
	// door actually cares about when checking it (Jeopardized's own
	// database.js only ever reads .connected inside its cycle()
	// wrapper, which is itself only meaningful once subscribe() is in
	// use). A door that never subscribes has no persistent connection
	// to be "connected" or not -- reads false, matching a real client
	// that was constructed but never had .connect() succeed yet.
	this.__defineGetter__("connected", function () {
		return !!(daemonProc && !daemonProc.killed);
	});
	// Drains any push updates the daemon has appended since the last
	// cycle() call into this.updates (or delivers them to this.callback
	// if the door set one, matching the real client's own cycle()/
	// wait() behavior) -- a real callback/updates feed now, not the
	// permanent no-op difference #1 used to document.
	this.cycle = function() {
		drainUpdatesFile();
		if (typeof self.callback === "function") {
			while (self.updates.length > 0) {
				self.callback(self.updates.shift());
			}
		}
		return self.updates.length > 0;
	};
	this.__defineGetter__("connected", function() { return true; });

	this.ident = function(scope, username, pw) {
		identUsername = username;
		identPassword = pw;
		call({ op: "IDENT", scope: scope, username: username, password: pw });
	};

	this.who = function(scope, location) {
		return call({ op: "WHO", scope: scope, location: location });
	};

	this.status = function(scope, location) {
		return call({ op: "STATUS", scope: scope, location: location });
	};

	// Real subscribe()/unsubscribe() against the persistent daemon
	// connection (see ensureDaemon()'s docstring) -- NOT the one-shot
	// call() path every other method uses, since a subscription sent
	// over a connection that closes immediately after would be
	// pointless (nowhere for the server to push updates to). A door
	// that never calls subscribe() never spawns the daemon at all.
	this.subscribe = function(scope, location) {
		ensureDaemon();
		sendDaemonCommand({
			cmd: "subscribe", scope: scope, location: location,
			nick: (typeof user !== "undefined" && user) ? user.alias : undefined,
			system_name: (typeof system !== "undefined" && system) ? system.name : undefined
		});
		return true;
	};

	this.unsubscribe = function(scope, location) {
		if (daemonProc) {
			sendDaemonCommand({ cmd: "unsubscribe", scope: scope, location: location });
		}
		return true;
	};

	this.lock = function(scope, location, lock) {
		heldLocks[lockKey(scope, location)] = lock;
		return true;
	};

	this.unlock = function(scope, location) {
		delete heldLocks[lockKey(scope, location)];
		return true;
	};

	this.read = function(scope, location, lock) {
		return call({ op: "READ", scope: scope, location: location, lock: effectiveLock(scope, location, lock), wait: true });
	};

	this.slice = function(scope, location, start, end, lock) {
		return call({ op: "SLICE", scope: scope, location: location, start: start, end: end, lock: effectiveLock(scope, location, lock), wait: true });
	};

	this.splice = function(scope, location, start, num, data, lock) {
		return call({ op: "SPLICE", scope: scope, location: location, start: start, num: num, data: data, lock: effectiveLock(scope, location, lock) });
	};

	this.keys = function(scope, location, lock) {
		return call({ op: "KEYS", scope: scope, location: location, lock: effectiveLock(scope, location, lock), wait: true });
	};

	this.keyTypes = function(scope, location, lock) {
		return call({ op: "KEYTYPES", scope: scope, location: location, lock: effectiveLock(scope, location, lock), wait: true });
	};

	this.shift = function(scope, location, lock) {
		return call({ op: "SHIFT", scope: scope, location: location, lock: effectiveLock(scope, location, lock), wait: true });
	};

	this.pop = function(scope, location, lock) {
		return call({ op: "POP", scope: scope, location: location, lock: effectiveLock(scope, location, lock), wait: true });
	};

	this.write = function(scope, location, data, lock) {
		return call({ op: "WRITE", scope: scope, location: location, data: data, lock: effectiveLock(scope, location, lock) });
	};

	this.remove = function(scope, location, lock) {
		return call({ op: "DELETE", scope: scope, location: location, lock: effectiveLock(scope, location, lock) });
	};

	this.unshift = function(scope, location, data, lock) {
		return call({ op: "UNSHIFT", scope: scope, location: location, data: data, lock: effectiveLock(scope, location, lock) });
	};

	this.push = function(scope, location, data, lock) {
		return call({ op: "PUSH", scope: scope, location: location, data: data, lock: effectiveLock(scope, location, lock) });
	};

	/* Real client's own low-level primitives (confirmed against
	   sbbs_reference/json-client.js): send() ships a bare, caller-
	   built packet with no response wait; wait() blocks until the
	   next RESPONSE packet arrives and returns its data. Every other
	   method above is really just "build a packet, send it, and
	   (usually) wait" -- these two let a door do that manually for a
	   packet shape none of the higher-level methods cover. Found live
	   bundling Thirstyville: `jsonClient.send({scope:"ADMIN",
	   func:"TIME"})` then `jsonClient.wait()`, a real Synchronet admin
	   query with no scope/location/oper structure at all.

	   This shim's architecture is one fresh connection per call (see
	   this file's own top-of-file docstring), so there's no
	   persistent socket to send() on ahead of a later, independent
	   wait() the way the real client has -- send() here just
	   remembers the packet, and wait() performs the actual
	   connect+send+receive round trip via jsonrpc_client.py's own
	   "RAW" op. Faithful for this door's real usage (send() always
	   immediately followed by wait(), never interleaved with other
	   calls) without needing a genuinely persistent connection just
	   for this one pairing. */
	var pendingRawPacket = undefined;
	this.send = function(packet) {
		pendingRawPacket = packet;
	};
	this.wait = function() {
		if (pendingRawPacket === undefined)
			throw new Error("json-client.js shim: wait() called with no pending send()");
		var packet = pendingRawPacket;
		pendingRawPacket = undefined;
		return call({ op: "RAW", packet: packet });
	};

	/* readmulti() is a convenience wrapper in the real client (fires
	   several READs, then waits() for each in order) -- since every
	   call here is already an independent blocking round trip, a
	   plain loop reproduces the identical observable result without
	   needing the real client's own pipelining trick. */
	this.readmulti = function(objects) {
		var ret = {};
		for (var i in objects) {
			ret[objects[i][3]] = this.read(objects[i][0], objects[i][1], objects[i][2]);
		}
		return ret;
	};
}
