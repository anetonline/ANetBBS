// ANetBBS drop-in for Synchronet's json-client.js.
//
// Upstream json-client connects to Synchronet's tcp json service. For a
// single-BBS install we don't need a network service — the entire game
// state for one xtrn lives in one JSON file (db/<scope>.json). One Node
// process owns it at a time, so locks are no-ops.
//
// API surface (only the methods tw2 actually calls):
//   db.read(scope, key, lock)            — dotted path lookup
//   db.write(scope, key, value, lock)    — dotted path set + persist
//   db.push(scope, key, value, lock)     — array.push to dotted path + save
//   db.pop(scope, key, lock)             — array.pop + save
//   db.shift(scope, key, lock)           — array.shift + save
//   db.unshift(scope, key, value, lock)  — array.unshift + save
//   db.lock(scope, key, mode)            — no-op, returns true
//   db.unlock(scope, key)                — no-op
//   db.status / db.who / db.readmulti    — stubs
//
// Key format mirrors upstream: dot-separated path, e.g. "sectors.85.Fighters"
// drills into sectors[85].Fighters. The special suffix ".length" on an array
// returns the array length (tw2 uses this to count records).

// `require` inside runInThisContext is the Synchronet-style require()
// from the compat shim, which does load() — not Node's CommonJS one.
// The compat shim exposes Node's real require as `_node_require` at
// globalThis scope before defining its own require, so reach for that.
var _path = _node_require('path');
var _fs = _node_require('fs');

function JSONClient(dbroot) {
    // Upstream tw2 passes (Server, Port) — the TCP json-service hostname
    // and port. We're file-backed, so dbroot becomes the directory holding
    // <scope>.json files.
    //
    // ANetBBS override: the bundled door source tree lives under
    // /home/stingray/anetbbs/anetbbs/games/sbbs_doors/tw2/ (owned by
    // stingray), but the anetbbs-web service runs as uid 998. Writing
    // runtime state into the source tree requires a chown that does not
    // survive update.sh. door_runner.py exports ANETBBS_TW2_DB_DIR pointing
    // at {install}/data/sbbs_doors/tw2/db/ (writable by the runtime user
    // and persistent across upgrades). Honor that override; fall back to
    // the caller-supplied dbroot for tests and out-of-BBS use.
    var envDir = (typeof process !== 'undefined' && process.env
                  && process.env.ANETBBS_TW2_DB_DIR);
    this._dbroot = envDir || dbroot || '.';
    this._cache = {};   // { scope: parsed-object }
    this._dirty = {};   // { scope: bool }
}

JSONClient.prototype._file = function(scope) {
    return _path.join(this._dbroot, scope + '.json');
};

JSONClient.prototype._load = function(scope) {
    if (this._cache[scope] !== undefined) return this._cache[scope];
    var f = this._file(scope);
    try {
        var raw = _fs.readFileSync(f, 'utf8');
        this._cache[scope] = JSON.parse(raw);
    } catch (e) {
        if (e.code !== 'ENOENT') {
            // File exists but couldn't be parsed — log so sysop can diagnose.
            process.stderr.write('[tw2 json-client] WARNING: failed to load '
                + f + ': ' + e.message + '\n');
        }
        this._cache[scope] = {};
    }
    return this._cache[scope];
};

// Synchronous flush. Used by the deferred saver below and by process-exit.
JSONClient.prototype._saveNow = function(scope) {
    if (!this._dirty[scope]) return;
    var f = this._file(scope);
    // mkdir errors used to be silently swallowed here, which turned a
    // permission problem on the dbroot into a misleading downstream
    // "ENOENT: open <tmp>" from the writeFileSync that followed. Surface
    // anything other than EEXIST so the failure is diagnosable.
    try {
        _fs.mkdirSync(_path.dirname(f), { recursive: true });
    } catch (mkErr) {
        if (mkErr && mkErr.code !== 'EEXIST') {
            throw new Error('json-client: cannot create db dir '
                + _path.dirname(f) + ' (' + mkErr.code + ': '
                + mkErr.message + ')');
        }
    }
    var tmp = f + '.tmp';
    // Compact JSON (no indenting) keeps the file ~30% smaller and the
    // stringify step ~5x faster. Each push during universe-init rewrites
    // the full scope file; size matters.
    _fs.writeFileSync(tmp, JSON.stringify(this._cache[scope]));
    _fs.renameSync(tmp, f);
    this._dirty[scope] = false;
};

// Save policy:
//   - tw2's main() is an infinite synchronous loop running at the top level
//     of runInThisContext(). Top-level never returns to libuv, so neither
//     setImmediate nor process.on('exit') is reachable as a flush trigger.
//   - Default: synchronous flush. Every gameplay write hits disk so a hard
//     kill never loses player state.
//   - Universe init is ~700 writes in one burst (~140KB file rewritten each
//     push). On a slow disk that's a minute of IO. Wrap such bursts in
//     db._beginBulk() / db._endBulk() — writes during the bulk window only
//     mark dirty, and a single _saveAll() at the end coalesces them into
//     one file write. If we crash mid-bulk, on next launch the scope is
//     simply absent and tw2 re-runs the same init from scratch (idempotent).
JSONClient.prototype._beginBulk = function() { this._bulk = (this._bulk || 0) + 1; };
JSONClient.prototype._endBulk = function() {
    if (this._bulk) this._bulk--;
    if (!this._bulk) this._saveAll();
};
JSONClient.prototype._saveAll = function() {
    for (var s in this._dirty) if (this._dirty[s]) this._saveNow(s);
};
JSONClient.prototype._save = function(scope) {
    if (this._bulk) return;   // bulk window — mark-dirty only
    this._saveNow(scope);
};

JSONClient.prototype._splitKey = function(key) {
    if (key === undefined || key === null || key === '') return [];
    return String(key).split('.');
};

// Navigate to the parent container of the final path segment. Returns
// { parent: <obj-or-array>, key: <final-segment-string> }, or undefined
// if any intermediate segment is missing.
JSONClient.prototype._walk = function(scope, parts, create) {
    var cur = this._load(scope);
    if (!parts.length) return { parent: this._cache, key: scope };
    for (var i = 0; i < parts.length - 1; i++) {
        var seg = parts[i];
        if (cur === null || cur === undefined) return undefined;
        // Numeric segment on array uses index; on object uses string key
        if (Array.isArray(cur) && /^\d+$/.test(seg)) {
            if (cur[Number(seg)] === undefined) {
                if (create) cur[Number(seg)] = {};
                else return undefined;
            }
            cur = cur[Number(seg)];
        } else {
            if (cur[seg] === undefined) {
                if (create) cur[seg] = {};
                else return undefined;
            }
            cur = cur[seg];
        }
    }
    return { parent: cur, key: parts[parts.length - 1] };
};

JSONClient.prototype.read = function(scope, key, lock) {
    var parts = this._splitKey(key);
    if (!parts.length) return this._load(scope);
    // Special: ".length" tail on an array returns the array length.
    // Return undefined (NOT 0) when the parent doesn't exist — tw2's
    // ShowOpeng uses `initialized = ShowOpeng()` which returns its `len`
    // local; subsequent `if (initialized == undefined)` would be false
    // for 0, false for the auto-init path, and the universe never gets
    // built. Real Synchronet's json service returns undefined for
    // missing keys, including missing-array .length probes.
    if (parts[parts.length - 1] === 'length') {
        var pre = parts.slice(0, -1);
        var walked = pre.length ? this._walk(scope, pre.concat(['__final__']), false)
                                : { parent: this._load(scope), key: '__final__' };
        if (!walked) return undefined;
        var container = pre.length ? walked.parent : this._load(scope);
        return Array.isArray(container) ? container.length : undefined;
    }
    var node = this._walk(scope, parts, false);
    if (!node) return undefined;
    var p = node.parent;
    if (Array.isArray(p) && /^\d+$/.test(node.key)) return p[Number(node.key)];
    return p ? p[node.key] : undefined;
};

JSONClient.prototype.write = function(scope, key, value, lock) {
    var parts = this._splitKey(key);
    // Upstream Synchronet tw2 calls db.write(scope, key) with no value after
    // mutating a cached object in-place, as a "flush this key" signal. Treat
    // that as mark-dirty-and-save; assigning undefined would make
    // JSON.stringify silently drop the key from the file.
    if (value === undefined && parts.length) {
        this._dirty[scope] = true;
        this._save(scope);
        return true;
    }
    if (!parts.length) {
        this._cache[scope] = value;
    } else {
        var node = this._walk(scope, parts, true);
        if (!node) return false;
        if (Array.isArray(node.parent) && /^\d+$/.test(node.key)) {
            node.parent[Number(node.key)] = value;
        } else {
            node.parent[node.key] = value;
        }
    }
    this._dirty[scope] = true;
    this._save(scope);
    return true;
};

JSONClient.prototype.push = function(scope, key, value, lock) {
    var arr = this.read(scope, key);
    if (!Array.isArray(arr)) {
        // Initialize the slot to [] then push.
        this.write(scope, key, []);
        arr = this.read(scope, key);
    }
    arr.push(value);
    this._dirty[scope] = true;
    this._save(scope);
    return arr.length;
};
JSONClient.prototype.pop = function(scope, key, lock) {
    var arr = this.read(scope, key);
    if (!Array.isArray(arr)) return undefined;
    var v = arr.pop();
    this._dirty[scope] = true;
    this._save(scope);
    return v;
};
JSONClient.prototype.shift = function(scope, key, lock) {
    var arr = this.read(scope, key);
    if (!Array.isArray(arr)) return undefined;
    var v = arr.shift();
    this._dirty[scope] = true;
    this._save(scope);
    return v;
};
JSONClient.prototype.unshift = function(scope, key, value, lock) {
    var arr = this.read(scope, key);
    if (!Array.isArray(arr)) {
        this.write(scope, key, []);
        arr = this.read(scope, key);
    }
    arr.unshift(value);
    this._dirty[scope] = true;
    this._save(scope);
    return arr.length;
};

// Single-node BBS, single-process owner of the JSON file → no contention.
JSONClient.prototype.lock = function(scope, key, mode) { return true; };
JSONClient.prototype.unlock = function(scope, key) { return true; };

// Synchronet's json service exposes a status/who/readmulti for monitoring.
// Doors occasionally poll these — return inert defaults.
JSONClient.prototype.status = function() { return { ok: true, clients: 1 }; };
JSONClient.prototype.who = function() { return []; };
// Synchronet's readmulti takes an array of [scope, key, lock, outname]
// tuples (4-arg per row) and returns an object keyed by `outname`:
//     reads = [[DB,'ports.5',LOCK_READ,'port'], [DB,'planets.3',LOCK_READ,'planet']];
//     sd = db.readmulti(reads);  // -> {port: {...}, planet: {...}}
// tw2's sectors.js builds reads[] and pulls sd.port.Name / sd.planet.Name.
// Also accept the older (scope, keys[], lock) form for callers that use it.
JSONClient.prototype.readmulti = function(arg1, arg2, arg3) {
    var out = {};
    var self = this;
    if (Array.isArray(arg1) && arg1.length && Array.isArray(arg1[0])) {
        // Tuple-of-tuples form.
        arg1.forEach(function (row) {
            var scope = row[0], key = row[1], lock = row[2], name = row[3];
            out[name || key] = self.read(scope, key, lock);
        });
        return out;
    }
    // Legacy form: readmulti(scope, [keys], lock)
    (arg2 || []).forEach(function (k) { out[k] = self.read(arg1, k, arg3); });
    return out;
};

// Lock constants the upstream json-client exports.
var LOCK_NONE  = 0;
var LOCK_READ  = 1;
var LOCK_WRITE = 2;
var LOCK_UNLOCK = 3;

// Make the constants visible to scripts that load this file via load().
// vm.runInThisContext puts top-level `var` on globalThis, but be explicit
// for safety.
if (typeof globalThis !== 'undefined') {
    globalThis.JSONClient = JSONClient;
    globalThis.LOCK_NONE = LOCK_NONE;
    globalThis.LOCK_READ = LOCK_READ;
    globalThis.LOCK_WRITE = LOCK_WRITE;
    globalThis.LOCK_UNLOCK = LOCK_UNLOCK;
}

// Flush any pending writes on process exit so an early termination
// (door errors, kill signals) doesn't lose state. Node's default SIGTERM
// behaviour kills without firing 'exit' handlers, so promote SIGTERM/SIGINT
// into a clean process.exit which triggers the registered handler. The BBS
// daemon will send SIGTERM if it ever has to kill a stuck door, and we
// don't want that to corrupt the universe.
if (typeof process !== 'undefined' && process.on) {
    process.on('exit', function () {
        if (!globalThis.db || !globalThis.db._dirty) return;
        for (var scope in globalThis.db._dirty) {
            try { globalThis.db._saveNow(scope); } catch (e) {}
        }
    });
    process.on('SIGTERM', function () { try { process.exit(0); } catch (e) {} });
    process.on('SIGINT',  function () { try { process.exit(0); } catch (e) {} });
}

// tw2.js does `db = new JSONClient(...)` itself — we don't auto-create
// the singleton here. Just expose the class.
