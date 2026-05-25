/**
 * MRCClient — browser WebSocket client for the ANetBBS MRC bridge.
 *
 * Wire format (JSON over WebSocket):
 *   outbound → {type: "join_room"|"send_message"|"server_cmd"|
 *                      "leave_room"|"ping", ...}
 *   inbound  ← {type: "welcome"|"joined"|"mrc_message"|"error"|
 *                      "left"|"room_changed"|"handle_changed"|
 *                      "pong"|"bridge_status", ...}
 */
class MRCClient {
    constructor(wsUrl) {
        this._wsUrl = wsUrl;
        this._ws = null;
        this._handlers = [];
        this._room = null;
        this._handle = null;
        this._pingInterval = null;
    }

    addMessageHandler(fn) {
        this._handlers.push(fn);
    }

    _dispatch(data) {
        for (const fn of this._handlers) {
            try { fn(data); } catch (e) { console.error('MRCClient handler:', e); }
        }
    }

    connect() {
        return new Promise((resolve, reject) => {
            let ws;
            try {
                ws = new WebSocket(this._wsUrl);
            } catch (e) {
                reject(e);
                return;
            }
            this._ws = ws;

            ws.onopen = () => {
                this._startPing();
                resolve();
            };

            ws.onerror = () => {
                reject(new Error('WebSocket connection error'));
            };

            ws.onmessage = (event) => {
                let data;
                try { data = JSON.parse(event.data); }
                catch (e) { return; }

                if (data.type === 'joined') {
                    this._room   = data.room   || this._room;
                    this._handle = data.handle || this._handle;
                } else if (data.type === 'room_changed') {
                    this._room = data.room || this._room;
                } else if (data.type === 'handle_changed') {
                    this._handle = data.new_handle || this._handle;
                }

                this._dispatch(data);
            };

            ws.onclose = (event) => {
                this._stopPing();
                this._dispatch({ type: 'disconnected', code: event.code });
            };
        });
    }

    _send(obj) {
        if (this._ws && this._ws.readyState === WebSocket.OPEN) {
            this._ws.send(JSON.stringify(obj));
        }
    }

    _startPing() {
        this._stopPing();
        this._pingInterval = setInterval(
            () => this._send({ type: 'ping', t: Date.now() }),
            30000
        );
    }

    _stopPing() {
        if (this._pingInterval !== null) {
            clearInterval(this._pingInterval);
            this._pingInterval = null;
        }
    }

    joinRoom(handle, room) {
        this._handle = handle;
        this._room   = (room || 'Lobby').replace(/^#/, '').toLowerCase();
        this._send({ type: 'join_room', handle, room: this._room });
    }

    sendMessage(message) {
        this._send({
            type: 'send_message',
            room: this._room || 'lobby',
            message,
        });
    }

    sendServerCommand(cmd) {
        this._send({ type: 'server_cmd', command: cmd });
    }

    leaveRoom() {
        this._send({ type: 'leave_room' });
    }

    disconnect() {
        this._stopPing();
        if (this._ws) {
            this._ws.close();
            this._ws = null;
        }
    }
}
