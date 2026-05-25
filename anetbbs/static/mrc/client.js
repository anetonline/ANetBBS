/**
 * MRC Bridge WebSocket Client + helpers
 *
 * Includes:
 * - MRCClient websocket wrapper
 * - window.MRCClient export for inline scripts
 * - Pipe color code renderer (|00..|15)
 * - Simple slash-command normalization helper
 *
 * - The 140 limit is enforced in the UI (index.html) against the *typed portion* only.
 *   For chat messages, the effective limit is MRC_MAX_LEN - handle_overhead (from server).
 *   For DMs, the effective limit is MRC_MAX_LEN - dm_overhead (from server).
 *   For actions, the effective limit is MRC_MAX_LEN (no handle prefix on wire).
 * - This file intentionally does NOT enforce message length at the raw wire level
 *   (that is done server-side by _truncate_wire_message).
 */

function escapeHtml(s) {
    return String(s)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

const PIPE_COLORS = {
    '00': '#000000', '01': '#0000aa', '02': '#00aa00', '03': '#00aaaa',
    '04': '#aa0000', '05': '#aa00aa', '06': '#aa5500', '07': '#aaaaaa',
    '08': '#555555', '09': '#5555ff', '10': '#55ff55', '11': '#55ffff',
    '12': '#ff5555', '13': '#ff55ff', '14': '#ffff55', '15': '#ffffff'
};

function renderPipeCodesToHtml(text) {
    if (text === null || text === undefined) return '';
    text = String(text).replaceAll('\u0001', '');

    let out = '';
    let i = 0;
    let curColor = null;

    while (i < text.length) {
        if (text[i] === '|' && i + 2 < text.length) {
            const code = text.slice(i + 1, i + 3);
            if (PIPE_COLORS[code]) {
                curColor = PIPE_COLORS[code];
                i += 3;
                continue;
            }
        }
        const ch = text[i];
        if (curColor) out += `<span style="color:${curColor}">${escapeHtml(ch)}</span>`;
        else out += escapeHtml(ch);
        i += 1;
    }

    return out;
}

function normalizeSlashCommand(input) {
    const raw = (input || '').trim();
    if (!raw.startsWith('/')) return null;

    const rest = raw.slice(1).trim();
    if (!rest) return null;

    const spaceIdx = rest.indexOf(' ');
    const verb = (spaceIdx === -1 ? rest : rest.slice(0, spaceIdx)).trim();
    const args = (spaceIdx === -1 ? '' : rest.slice(spaceIdx + 1)).trim();

    if (!verb) return null;

    if (verb.toLowerCase() === 'join') return args ? `JOIN ${args}` : 'JOIN';
    if (verb.toLowerCase() === 'ctcp') return args ? `CTCP ${args}` : 'CTCP';

    return args ? `${verb.toUpperCase()} ${args}` : verb.toUpperCase();
}

class MRCClient {
    constructor(wsUrl, opts = {}) {
        this.wsUrl = wsUrl;
        this.ws = null;
        this.connected = false;
        this.handle = null;
        this.room = null;
        this.messageHandlers = [];

        this._shouldReconnect = true;
        this._reconnectAttempt = 0;
        this._maxReconnectDelayMs = opts.maxReconnectDelayMs || 30000;
        this._baseReconnectDelayMs = opts.baseReconnectDelayMs || 1000;

        // Remembered for auto-rejoin after a reconnect
        this._lastHandle = null;
        this._lastRoom   = null;

        this._connectingPromise = null;
        this._reconnectTimer    = null;
    }

    connect() {
        if (this._connectingPromise) return this._connectingPromise;

        this._connectingPromise = new Promise((resolve, reject) => {
            let ws;
            try {
                ws = new WebSocket(this.wsUrl);
            } catch (e) {
                this._connectingPromise = null;
                reject(e);
                return;
            }

            this.ws = ws;

            ws.onopen = () => {
                this.connected = true;
                this._reconnectAttempt = 0;
                this._connectingPromise = null;
                resolve(true);
                // NOTE: no "return done" here — that was the original bug
            };

            ws.onerror = (error) => {
                if (this._connectingPromise) {
                    this._connectingPromise = null;
                    reject(error);
                }
            };

            ws.onclose = () => {
                this.connected = false;
                this._connectingPromise = null;
                this.notifyHandlers({ type: 'disconnected', message: 'Disconnected from server' });
                if (this._shouldReconnect) this._scheduleReconnect();
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleMessage(data);
                } catch (e) {}
            };
        });

        return this._connectingPromise;
    }

    _scheduleReconnect() {
        // Guard: never schedule more than one timer at a time
        if (this._reconnectTimer) return;

        this._reconnectAttempt += 1;
        const delay = Math.min(
            this._baseReconnectDelayMs * Math.pow(2, this._reconnectAttempt - 1),
            this._maxReconnectDelayMs
        );

        // Tell the UI we are about to reconnect
        this.notifyHandlers({ type: 'reconnecting', attempt: this._reconnectAttempt, delayMs: delay });

        this._reconnectTimer = setTimeout(async () => {
            this._reconnectTimer = null;
            if (!this._shouldReconnect) return;
            if (this.connected) return;

            try {
                await this.connect();
                // Connection restored — if we were in a room, rejoin automatically
                if (this._lastHandle && this._lastRoom) {
                    this.notifyHandlers({ type: 'rejoining', handle: this._lastHandle, room: this._lastRoom });
                    this.joinRoom(this._lastHandle, this._lastRoom);
                }
            } catch (e) {
                // ws.onclose will fire again and schedule the next attempt
            }
        }, delay);
    }

    handleMessage(data) {
        if (data && data.type === 'joined') {
            this.handle = data.handle;
            this.room   = data.room;
            // Remember for auto-rejoin
            this._lastHandle = data.handle;
            this._lastRoom   = data.room;
        } else if (data && data.type === 'left') {
            this.handle = null;
            this.room   = null;
            // Intentional leave — clear auto-rejoin state
            this._lastHandle = null;
            this._lastRoom   = null;
        } else if (data && data.type === 'handle_changed') {
            this.handle      = data.new_handle;
            this._lastHandle = data.new_handle;
        } else if (data && data.type === 'room_changed') {
            this.room      = data.room;
            this._lastRoom = data.room;
        }

        this.notifyHandlers(data);
    }

    addMessageHandler(handler) {
        this.messageHandlers.push(handler);
    }

    notifyHandlers(data) {
        this.messageHandlers.forEach(h => {
            try { h(data); } catch (e) {}
        });
    }

    joinRoom(handle, room = 'Lobby') {
        if (!this.connected || !this.ws) {
            this.notifyHandlers({ type: 'error', message: 'Not connected to bridge yet.' });
            return;
        }
        this.ws.send(JSON.stringify({ type: 'join_room', handle, room }));
    }

    sendMessage(message) {
        if (!this.connected || !this.ws) {
            this.notifyHandlers({ type: 'error', message: 'Not connected to bridge.' });
            return;
        }
        if (!this.handle || !this.room) {
            this.notifyHandlers({ type: 'error', message: 'Not joined to a room.' });
            return;
        }
        this.ws.send(JSON.stringify({ type: 'send_message', message }));
    }

    sendServerCommand(command) {
        if (!this.connected || !this.ws) {
            this.notifyHandlers({ type: 'error', message: 'Not connected to bridge.' });
            return;
        }
        const normalized = (typeof command === 'string' && command.trim().startsWith('/'))
            ? normalizeSlashCommand(command)
            : command;
        this.ws.send(JSON.stringify({ type: 'server_cmd', command: normalized || command }));
    }

    sendDirectMessage(to_user, message) {
        if (!this.connected || !this.ws) {
            this.notifyHandlers({ type: 'error', message: 'Not connected to bridge.' });
            return;
        }
        this.ws.send(JSON.stringify({ type: 'direct_message', to_user, message }));
    }

    async leaveRoom() {
        if (!this.connected || !this.ws) return;
        // Explicit leave — don't auto-rejoin after this
        this._lastHandle = null;
        this._lastRoom   = null;
        this.ws.send(JSON.stringify({ type: 'leave_room' }));
        await new Promise(resolve => setTimeout(resolve, 250));
    }

    disconnect(opts = {}) {
        const graceful = (opts && Object.prototype.hasOwnProperty.call(opts, 'graceful'))
            ? !!opts.graceful
            : true;
        this._shouldReconnect = false;
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        if (this.ws) {
            this.ws.close();
            this.ws = null;
            this.connected = false;
        }
    }
}

if (typeof window !== 'undefined') {
    window.MRCClient = MRCClient;
    window.renderPipeCodesToHtml = renderPipeCodesToHtml;
    window.normalizeSlashCommand = normalizeSlashCommand;
    window.escapeHtml = escapeHtml;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { MRCClient, renderPipeCodesToHtml, normalizeSlashCommand, escapeHtml };
}
