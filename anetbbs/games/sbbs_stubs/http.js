/* ANetBBS-authored replacement for Synchronet's real http.js.

   WHY THIS EXISTS: the real http.js (preserved verbatim at
   anetbbs/games/sbbs_reference/http.js for reference) does its actual
   network I/O via `new Socket(SOCK_STREAM)` (or `ConnectedSocket`),
   then calls real synchronous methods on it (`.connect()`, `.send()`,
   `.recvline()`, `.recv()`). Node has no synchronous TCP primitive --
   the exact same gap already solved for json-client.js -- so a real
   `Socket` object was never going to be reproducible here either, for
   the same reasons documented in that file's own docstring (large
   surface area, open-ended semantics, for a payoff that in practice
   reduces to "run this one library file").

   Unlike json-client.js, http.js's own structure conveniently
   separates "build the request" (SetupGet/SetupPost/AddDefaultHeaders/
   AddExtraHeaders/BasicAuth -- pure string/array logic, zero Socket
   dependency) from "parse the response" (ReadStatus/ReadHeaders/
   ReadBody -- pure regex/string logic that only calls
   `this.sock.recvline()`/`this.sock.recv()`, never touches Socket
   internals directly) from "do the actual I/O" (SendRequest, the only
   method that constructs and touches a real Socket). That split means
   only SendRequest needs replacing: HTTPRequest.prototype's *entire*
   real body below (constructor, AddDefaultHeaders, AddExtraHeaders,
   SetupGet, SetupPost, ReadStatus, ReadHeaders, ReadBody, ReadResponse,
   BasicAuth, Get, Post, Head) is the REAL Synchronet source, copied
   unmodified from the vendored reference -- only SendRequest is
   ANetBBS-authored.

   The replacement SendRequest shells out to anetbbs/games/http_client.py
   (via execFileSync, the same subprocess-per-call pattern already
   established for jsonrpc_client.py) to perform ONE complete,
   synchronous request/response round trip, then wraps the raw response
   bytes in a trivial fake "socket" object exposing just the two methods
   ReadStatus/ReadHeaders/ReadBody actually call (`recvline`/`recv`),
   replaying the pre-fetched bytes instead of reading a live connection.
   Real Synchronet's http.js always sends "Connection: close" and an
   HTTP/1.0 request line (see AddDefaultHeaders() below, unmodified),
   so the server is expected to close the connection once the response
   is fully sent -- http_client.py reads until EOF, which is the
   correct way to capture a complete response, not a simplification.

   This means every door that only touches the documented HTTPRequest
   public method surface (Get/Post/Head, the realistic case) keeps
   working completely unmodified. */

require('sockdefs.js', 'SOCK_STREAM');
require('url.js', 'URL');

function HTTPRequest(username,password,extra_headers,recv_timeout)
{
	/* request properties */
	this.request_headers = undefined;
	this.referer = undefined;
	this.base = undefined;
	this.url = undefined;
	this.body = undefined;

	this.extra_headers = extra_headers;
	this.username=username;
	this.password=password;
	this.user_agent='SYNXv0.1';
	this.follow_redirects = 0;
	this.recv_timeout = recv_timeout || 60;

	this.status = { ok: 200, created: 201, accepted: 202, no_content: 204 };
}

HTTPRequest.prototype.AddDefaultHeaders=function(){
	// General Headers
	this.request_headers.push("Connection: close");
	if(js.global.client != undefined)
		this.request_headers.push(
			"Via: "+client.protocol.toString().toLowerCase()+"/1.0 "+system.name);
	// Request Headers
	//this.request_headers.push("Accept: text/html,application/xhtml+xml,application/xml,text/*,*/*;q=0.9,*/*;q=0.8;q=0.7;q=0.6");
	this.request_headers.push("Accept: text/*,*/*;q=0.9");
	this.request_headers.push(
		"Accept-Charset: ISO-8859-13,Latin-9,ISO-8859-15,ISO-8859-1,UTF-8;q=0.5,*;q=0.1");
	this.request_headers.push("Accept-Encoding: ");
	this.request_headers.push("Host: "+this.url.host);
	if(this.referer != undefined)
		this.request_headers.push("Referer: "+this.referer);
	this.request_headers.push("User-Agent: "+this.user_agent);
};

HTTPRequest.prototype.AddExtraHeaders = function () {
	if (typeof this.extra_headers !== 'object') return;
	Object.keys(this.extra_headers).forEach(
		function (e) {
			this.request_headers.push(e + ': ' + this.extra_headers[e]);
		}, this
	);
};

HTTPRequest.prototype.SetupGet=function(url, referer, base) {
	this.referer=referer;
	this.base=base;
	this.url=new URL(url, this.base);
	if(this.url.scheme!='http' && this.url.scheme!='https')
		throw new Error("Unknown scheme! '"+this.url.scheme+"' in url:" + url);
	if(this.url.path=='')
		this.url.path='/';
	this.request="GET "+this.url.request_path+" HTTP/1.0";
	this.request_headers=[];
	this.AddDefaultHeaders();
	this.AddExtraHeaders();
};

HTTPRequest.prototype.SetupPost=function(url, referer, base, data, content_type) {
	if (content_type === undefined)
		content_type = 'application/x-www-form-urlencoded';
	this.referer=referer;
	this.base=base;
	this.url=new URL(url, this.base);
	if(this.url.scheme!='http' && this.url.scheme!='https')
		throw new Error("Unknown scheme! '"+this.url.scheme+"' in url: " + url);
	if(this.url.path=='')
		this.url.path='/';
	this.request="POST "+this.url.request_path+" HTTP/1.0";
	this.request_headers=[];
	this.AddDefaultHeaders();
	this.AddExtraHeaders();
	this.body=data;
	this.request_headers.push("Content-Type: "+content_type);
	this.request_headers.push("Content-Length: "+data.length);
};

function pythonExe() {
	return process.env.ANETBBS_JSONRPC_CLI_PYTHON || 'python3';
}
function httpCliPath() {
	var p = process.env.ANETBBS_HTTP_CLI_PATH;
	if (!p)
		throw new Error("ANETBBS_HTTP_CLI_PATH not set -- http.js shim can't locate http_client.py");
	return p;
}

/* Trivial replay "socket" -- ReadStatus/ReadHeaders/ReadBody (real,
   unmodified below) only ever call recvline()/recv() on this.sock, so
   this only needs to implement exactly those two methods against a
   buffer that's already fully in memory (http_client.py already did
   the real blocking network read). Matches the real Socket methods'
   signatures (maxlen/timeout args) without needing to honor timeout
   semantics at all -- there's no live connection left to time out on. */
function _ReplaySocket(rawResponse) {
	this._buf = rawResponse;
	this._pos = 0;
}
_ReplaySocket.prototype.recvline = function (maxlen, timeout) {
	if (this._pos >= this._buf.length) return null;
	var nl = this._buf.indexOf('\n', this._pos);
	var end = (nl === -1) ? this._buf.length : nl;
	var line = this._buf.slice(this._pos, end);
	if (line.charAt(line.length - 1) === '\r') line = line.slice(0, -1);
	this._pos = (nl === -1) ? this._buf.length : nl + 1;
	if (typeof maxlen === 'number' && line.length > maxlen)
		line = line.slice(0, maxlen);
	return line;
};
_ReplaySocket.prototype.recv = function (len, timeout) {
	if (this._pos >= this._buf.length) return null;
	var n = (typeof len === 'number' && len > 0) ? len : this._buf.length - this._pos;
	var chunk = this._buf.slice(this._pos, this._pos + n);
	this._pos += chunk.length;
	return chunk;
};
_ReplaySocket.prototype.close = function () {};

HTTPRequest.prototype.SendRequest=function() {
	if (this.sock != undefined)
		this.sock.close();
	var port = this.url.port ? this.url.port : (this.url.scheme=='http' ? 80 : 443);
	var reqArgs = {
		host: this.url.host,
		port: port,
		scheme: this.url.scheme,
		request_line: this.request,
		headers: this.request_headers,
		body: this.body,
		timeout: this.recv_timeout
	};
	var cp = _node_require('child_process');
	var out, result;
	try {
		out = cp.execFileSync(pythonExe(), [httpCliPath()], {
			input: JSON.stringify(reqArgs),
			encoding: 'utf8',
			timeout: (this.recv_timeout + 5) * 1000
		});
	} catch (e) {
		out = (e && typeof e.stdout === 'string') ? e.stdout : null;
		if (!out) {
			throw new Error(format("Unable to connect to %s:%u", this.url.host, port));
		}
	}
	try {
		result = JSON.parse(out);
	} catch (e) {
		throw new Error("http.js shim: subprocess produced invalid JSON: " + out);
	}
	if (!result.ok)
		throw new Error(result.error || format("Unable to connect to %s:%u", this.url.host, port));
	this.sock = new _ReplaySocket(result.response);
};

HTTPRequest.prototype.ReadStatus=function() {
	this.status_line=this.sock.recvline(4096, this.recv_timeout);
	if(this.status_line==null)
		throw new Error("Unable to read status");
	var m = this.status_line.match(/^HTTP\/[0-9]+\.[0-9]+ ([0-9]{3})/);
	if (m === null)
		throw new Error("Unable to parse status line '"+this.status_line+"'");
	this.response_code = parseInt(m[1], 10);
};

HTTPRequest.prototype.ReadHeaders=function() {
	var header='';
	var m;
	this.response_headers=[];
	this.response_headers_parsed={};

	for(;;) {
		header=this.sock.recvline(4096, this.recv_timeout);
		if(header==null)
			throw new Error("Unable to receive headers");
		if(header=='')
			return;
		this.response_headers.push(header);
		m=header.match(/^Content-length:\s+([0-9]+)$/i);
		if(m!=null)
			this.contentlength=parseInt(m[1]);
		m = header.match(/^(.*?):\s*(.*?)\s*$/);
		if (m) {
			if (this.response_headers_parsed[m[1]] == undefined)
				this.response_headers_parsed[m[1]] = [];
			this.response_headers_parsed[m[1]].push(m[2]);
			var lc = m[1].toLowerCase();
			if (lc !== m[1]) {
				if (this.response_headers_parsed[lc] == undefined)
					this.response_headers_parsed[lc] = [];
				this.response_headers_parsed[lc].push(m[2]);
			}
		}
	}
};

HTTPRequest.prototype.ReadBody=function() {
	var ch;
	var lastlen=0;
	var len=this.contentlength;
	if(len==undefined)
		len=1024;

	this.body='';
	while((ch=this.sock.recv(len, this.recv_timeout))!=null && ch != '') {
		this.body += ch.toString();
		len -= ch.length;
		if(len < 1)
			len=1024;
		js.flatten_string(this.body);
	}
};

HTTPRequest.prototype.ReadResponse=function() {
	this.ReadStatus();
	this.ReadHeaders();
	this.ReadBody();
};

HTTPRequest.prototype.BasicAuth=function(username,password) {
	if(username && password) {
		this.username=username;
		this.password=password;
	}
	if(this.username && this.password) {
		var auth = base64_encode(this.username + ":" + this.password);
		this.request_headers.push("Authorization: Basic " + auth);
	}
};

HTTPRequest.prototype.Get=function(url, referer, base) {
	this.SetupGet(url,referer,base);
	this.BasicAuth();
	this.SendRequest();
	this.ReadResponse();
	if ([301, 302, 307, 308].indexOf(this.response_code) > -1
		&& this.follow_redirects > 0
		&& this.response_headers_parsed.location
		&& this.response_headers_parsed.location.length
	) {
		this.follow_redirects--;
		return this.Get(this.response_headers_parsed.location[0], this.url.url, this.url.url);
	}
	return(this.body);
};

HTTPRequest.prototype.Post=function(url, data, referer, base, content_type) {
	this.SetupPost(url,referer,base,data, content_type);
	this.BasicAuth();
	this.SendRequest();
	this.ReadResponse();
	return(this.body);
};

HTTPRequest.prototype.Head=function(url, referer, base) {
	var i;
	var m;
	var ret={};

	this.SetupGet(url,referer,base);
	this.request = this.request.replace(/^GET/, 'HEAD');
	this.BasicAuth();
	this.SendRequest();
	this.ReadResponse();
	for(i in this.response_headers) {
		m = this.response_headers[i].match(/^(.*?):\s*(.*?)\s*$/);
		if (m) {
			if (ret[m[1]] == undefined)
				ret[m[1]] = [];
			ret[m[1]].push(m[2]);
		}
	}
	return(ret);
};
