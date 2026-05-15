/*
  botwars_utils.js - Utilities for Bot Wars (fixed cross-platform paths)
  - Save/load JSON
  - Directory helpers
  - ANSI printing helpers (printANS)
  - Colored console helpers (printColor, bannerScary)
  - parsePipeColorCodes (support for |nn color tokens)
  - getColoredInput() (safe fallback)
  - sendMessageToPlayer
  - format utility
  - ensureSaveDir, DATA_DIR, SAVE_DIR
*/

load("sbbsdefs.js");

var BotWarsUtils = (function(){
    // Normalize exec_dir to use forward slashes and ensure trailing slash.
    var EXEC_DIR = (typeof js !== "undefined" && js.exec_dir) ? js.exec_dir : "./";
    // Replace any backslashes with forward slashes (portable)
    EXEC_DIR = String(EXEC_DIR).replace(/\\/g, "/");
    if (EXEC_DIR.slice(-1) !== "/") EXEC_DIR += "/";

    var DATA_DIR = EXEC_DIR + "botwars_data/";
    var SAVE_DIR = EXEC_DIR + "botwars_saves/";
    var MESSAGE_DB = DATA_DIR + "messages.json";

    function ensureDir(p) {
        try { if (!file_isdir(p)) mkdir(p); } catch(e) {}
    }

    function ensureSaveDir(){
        ensureDir(DATA_DIR);
        ensureDir(SAVE_DIR);
        ensureDir(DATA_DIR + "guilds/");
        ensureDir(DATA_DIR + "maintenance_reports/");
        ensureDir(DATA_DIR + "leaderboards/");
    }

    function nowDateStr(){
        var d=new Date();
        var y=d.getFullYear(), m=("0"+(d.getMonth()+1)).slice(-2), day=("0"+d.getDate()).slice(-2);
        return y + "-" + m + "-" + day;
    }

    function saveJSON(path,obj){
        var f = null;
        try {
            f = new File(path);
            if(!f.open("w")) return false;
            f.write(JSON.stringify(obj, null, 2));
            f.close();
            return true;
        } catch(e) {
            try { if (f && f.isopen) f.close(); } catch(x) {}
            return false;
        }
    }

    function loadJSON(path){
        var f = null;
        try {
            if (!file_exists(path)) return null;
            f = new File(path);
            if (!f.open("r")) return null;
            var txt = f.readAll().join("");
            f.close();
            return JSON.parse(txt || "null");
        } catch(e) {
            try { if (f && f.isopen) f.close(); } catch(x) {}
            return null;
        }
    }

    function printColor(text, color) {
        var map = {
            black: "\x01k", red: "\x01r", green: "\x01g", yellow: "\x01y",
            blue: "\x01b", magenta: "\x01m", cyan: "\x01c", white: "\x01w",
            bright: "\x01h", normal: "\x01n", faint: "\x01n"
        };
        var prefix = (map[color] || "");
        try { console.print(prefix + text + "\x01n\r\n"); } catch(e) { try { console.print(text + "\r\n"); } catch(x){} }
    }

    function bannerScary(){
        var art = [
"  .-. .-.  .-. .-. .-. .-. .-. .-. .-. .-.",
" ( B ¦ O )( T ¦ W ¦ A ¦ R ¦ S ) (  -  )",
"  `-' `-'  `-' `-' `-' `-' `-' `-' `-' `-'",
"",
"  :::  D E F E N D   Y O U R   C I R C U I T S :::",
""
        ];
        try { console.print("\x01m\x01h" + art.join("\r\n") + "\x01n\r\n"); } catch(e) { try { console.print(art.join("\r\n") + "\r\n"); } catch(x) {} }
    }

    // Support |nn → ctrl-A color codes and ¦ (broken bar) replacement
    function parsePipeColorCodes(text){
        if(!text || typeof text !== "string") return text || "";
        var t = text.replace(/\u00A6/g, "|");
        var base = {0:"\x01k",1:"\x01r",2:"\x01g",3:"\x01y",4:"\x01b",5:"\x01m",6:"\x01c",7:"\x01w"};
        t = t.replace(/\|([0-9]{1,2})/g, function(_,n){
            var v = parseInt(n,10);
            if(isNaN(v)) return "|" + n;
            var bright = v >= 8;
            if (bright) v = v - 8;
            var esc = base.hasOwnProperty(v) ? base[v] : "";
            if (bright) esc = esc + "\x01h";
            return esc;
        });
        t = t.replace(/\|n/g, "\x01n");
        return t;
    }

    // printANS: try console.printfile, fallback to binary read+console.write/print
    function printANS(name, waitForKey) {
        waitForKey = (typeof waitForKey === "undefined") ? true : !!waitForKey;
        if (!name) return false;
        var candidates = [
            EXEC_DIR + name,
            EXEC_DIR + "art/" + name,
            EXEC_DIR + "art\\" + name
        ];
        var chosen = null;
        for (var i=0;i<candidates.length;i++) if (file_exists(candidates[i])) { chosen = candidates[i]; break; }
        if (!chosen) return false;
        try {
            if (typeof console.printfile === "function") {
                console.clear(); console.home();
                console.printfile(chosen);
                if (waitForKey) { console.print("\r\nPress any key to continue..."); console.getkey(K_NONE); }
                return true;
            }
        } catch (e) {}
        var f = null;
        try {
            f = new File(chosen);
            if (!f.open("rb")) return false;
            var content = f.read();
            f.close();
            console.clear(); console.home();
            if (typeof console.write === "function") try { console.write(content); } catch(e) { console.print(content); }
            else console.print(content);
            if (waitForKey) { console.print("\r\nPress any key to continue..."); console.getkey(K_NONE); }
            return true;
        } catch(e) { try { if (f && f.isopen) f.close(); } catch(x) {} return false; }
    }

    // getColoredInput: safe fallback to console.getstr if Frame/inkey unavailable.
    function getColoredInput(promptText, maxLen) {
        maxLen = maxLen || 60;
        try {
            if (typeof Frame !== "function" || typeof console.inkey !== "function") {
                if (promptText) console.print("\r\n" + promptText + "\r\n");
                return console.getstr(maxLen) || "";
            }
            // Minimal safe Frame implementation (not required for basic game)
            console.print("\r\n" + (promptText || "") + "\r\n");
            return console.getstr(maxLen) || "";
        } catch (e) {
            try { return console.getstr(maxLen) || ""; } catch(x) { return ""; }
        }
    }

    function format(){
        var args = Array.prototype.slice.call(arguments);
        var fmt = args.shift() || "";
        return fmt.replace(/%[sdj%]/g, function(x){
            if (x === "%%") return "%";
            if (args.length === 0) return x;
            var a = args.shift();
            if (x === "%s") return String(a);
            if (x === "%d") return Number(a);
            if (x === "%j") try { return JSON.stringify(a); } catch(e) { return String(a); }
            return x;
        });
    }

    function sendMessageToPlayer(playerName, from, text) {
        try {
            var db = loadJSON(MESSAGE_DB) || {};
            db.messages = db.messages || [];
            db.messages.push({ to: playerName, from: from, text: text, date: (new Date()).toISOString() });
            saveJSON(MESSAGE_DB, db);
            return true;
        } catch(e) { return false; }
    }

    function listSaveFiles(pattern) {
        try { return directory(pattern) || []; } catch(e) { return []; }
    }

    return {
        EXEC_DIR: EXEC_DIR,
        DATA_DIR: DATA_DIR,
        SAVE_DIR: SAVE_DIR,
        MESSAGE_DB: MESSAGE_DB,
        ensureSaveDir: ensureSaveDir,
        nowDateStr: nowDateStr,
        saveJSON: saveJSON,
        loadJSON: loadJSON,
        printColor: printColor,
        bannerScary: bannerScary,
        parsePipeColorCodes: parsePipeColorCodes,
        printANS: printANS,
        getColoredInput: getColoredInput,
        sendMessageToPlayer: sendMessageToPlayer,
        format: format,
        listSaveFiles: listSaveFiles
    };
})();

try { global.BotWarsUtils = BotWarsUtils; } catch(e) {}