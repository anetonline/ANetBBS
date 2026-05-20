/*
  botwars.js - By; StingRay A-Net Online BBS
  bbs.a-net.online - Telnet: 1337 SSH: 1338 RLogin: 1339
*/

load("sbbsdefs.js");

// -------------------- Safe loader --------------------
function silentLoad(path) {
    try {
        if (file_exists(path)) load(path);
    } catch (e) {
        try { log(LOG_ERR, "botwars: failed to load " + path + ": " + (e && e.toString ? e.toString() : e)); } catch(x) {}
    }
}

// Load utilities and optional modules (silently)
silentLoad(js.exec_dir + "botwars_utils.js");   // REQUIRED
silentLoad(js.exec_dir + "bot_attacks.js");
silentLoad(js.exec_dir + "leaderboards.js");
silentLoad(js.exec_dir + "guilds.js");
silentLoad(js.exec_dir + "boss.js");
silentLoad(js.exec_dir + "igms.js");
silentLoad(js.exec_dir + "bw_bank.js");
silentLoad(js.exec_dir + "shop.js");
silentLoad(js.exec_dir + "market.js");
silentLoad(js.exec_dir + "bar.js");
silentLoad(js.exec_dir + "encounters.js");
silentLoad(js.exec_dir + "areas.js");
silentLoad(js.exec_dir + "map.js");
silentLoad(js.exec_dir + "messaging2.js");
silentLoad(js.exec_dir + "scheduler.js");

// -------------------- Globals and config --------------------
var MAIN_ANS_NAME = "botwarmain.ans";
var INTRO_ANS_NAME = "botwarintro.ans";

var TEXT_MENU_LINES = [
    "(1) Devices / Defend",
    "(2) Guild Hall",
    "(3) Boss Lair",
    "(4) Town / Places",
    "(5) Bank",
    "(6) Leaderboards",
    "(7) Arcades",
    "(8) Save & Quit"
];

// -------------------- Small helpers --------------------
function clearScreen() { try { console.clear(); } catch (e) {} }
function mswait(ms) { try { msleep(ms); } catch (e) {} }
function safeStrLen(s) { try { return console.strlen(s); } catch (e) { return (s || "").length; } }
function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

// Hotkey single-key input with fallback to getstr
function hotkey(prompt) {
    if (prompt) console.print(prompt);
    try {
        if (typeof console.getkey === "function") {
            var k = console.getkey(K_NONE);
            if (!k) return null;
            var s = String(k);
            if (!s) return null;
            return s.toUpperCase();
        }
    } catch (e) {}
    try {
        var s2 = console.getstr(2) || "";
        if (!s2) return null;
        return s2.charAt(0).toUpperCase();
    } catch (e) { return null; }
}

// Input line helper
function inputLine(prompt, maxLen) {
    maxLen = maxLen || 80;
    if (prompt) console.print(prompt);
    try { return (console.getstr(maxLen) || "").trim(); } catch (e) { return ""; }
}

// Scores.ANS helper using Leaderboards data
function createScoresANS(player) {
    try {
        var execDir = (typeof js !== "undefined" && js.exec_dir) ? js.exec_dir : "./";
        var outPath = execDir + "scores.ans";

        // Try to gather player saves if possible to compute top scores
        var saveDir = (typeof BotWarsUtils !== "undefined" && BotWarsUtils.SAVE_DIR) ? BotWarsUtils.SAVE_DIR : null;
        var allPlayers = [];

        if (saveDir && file_exists(saveDir)) {
            try {
                var files = directory(saveDir + "*.json") || [];
                for (var i = 0; i < files.length; i++) {
                    try {
                        var obj = BotWarsUtils.loadJSON(files[i]);
                        if (!obj || !obj.name) continue;
                        allPlayers.push(obj);
                    } catch (e) {}
                }
            } catch (e) {}
        }

        // Sort by experience, then level, then bossVictories, then gold
        allPlayers.sort(function(a,b){
            var ax = a.experience||0, bx = b.experience||0;
            if (bx !== ax) return bx - ax;
            var al = a.level||0, bl = b.level||0;
            if (bl !== al) return bl - al;
            var ab = a.bossVictories||0, bb = b.bossVictories||0;
            if (bb !== ab) return bb - ab;
            var ag = a.gold||0, bg = b.gold||0;
            return bg - ag;
        });

        var lines = [];
        lines.push("=== BOT WARS - SCORES ===");
        lines.push("Generated: " + (new Date()).toISOString());
        lines.push("");
        lines.push(" Top Players ");
        lines.push("------------------------------");

        var maxRows = Math.min(10, allPlayers.length);
        if (maxRows === 0) {
            lines.push("No saved players found.");
        } else {
            for (var r = 0; r < maxRows; r++) {
                var pp = allPlayers[r];
                lines.push(
                    (r+1) + ". " + (pp.name || "unknown")
                    + "  Lv:" + (pp.level || 0)
                    + "  Exp:" + (pp.experience || 0)
                    + "  Gold:" + (pp.gold || 0)
                    + "  Bosses:" + (pp.bossVictories || 0)
                );
            }
        }

        lines.push("");
        lines.push(" Your Current Character ");
        lines.push("------------------------------");
        try {
            lines.push("Name: " + (player.name || "guest"));
            lines.push("Role: " + (player.role || "Script Kiddie"));
            lines.push("Level: " + (player.level || 0) + "  Experience: " + (player.experience || 0));
            lines.push("Gold: " + (player.gold || 0) + "  Bank: " + (player.bank || 0));
            lines.push("Firewall: " + (player.firewallSkill || 0) + "  Hacking: " + (player.hackingSkill || 0));
            lines.push("Boss victories: " + (player.bossVictories || 0));
        } catch (e) {}

        lines.push("");
        lines.push("End of scores.");

        var content = lines.join("\r\n") + "\r\n";

        var f = new File(outPath);
        if (f.open("wb")) {
            try {
                f.write(content);
            } finally {
                try { f.close(); } catch (e) {}
            }
            return { ok: true, path: outPath };
        } else {
            return { ok: false, msg: "open_failed", path: outPath };
        }
    } catch (e) {
        return { ok: false, msg: (e && e.toString ? e.toString() : e) };
    }
}

// Try to print ANS: prefer BotWarsUtils.printANS if present, otherwise search common paths
function tryPrintANSByName(name, pauseAfter) {
    pauseAfter = !!pauseAfter;
    try {
        if (typeof BotWarsUtils !== "undefined" && typeof BotWarsUtils.printANS === "function")
            return BotWarsUtils.printANS(name, pauseAfter);
    } catch (e) {}
    var candidates = [ js.exec_dir + name, js.exec_dir + "art/" + name, js.exec_dir + "art\\" + name ];
    for (var i = 0; i < candidates.length; i++) {
        try {
            var p = candidates[i];
            if (!p || !file_exists(p)) continue;
            if (typeof console.printfile === "function") {
                try { console.printfile(p); if (pauseAfter) { console.print("\r\nPress any key..."); console.getkey(K_NONE); } return true; } catch (e) {}
            }
            var f = new File(p);
            if (f.open("rb")) {
                try {
                    var content = f.read();
                    f.close();
                    if (typeof console.write === "function") try { console.write(content); } catch(e) { console.print(content); }
                    else console.print(content);
                    if (pauseAfter) { console.print("\r\nPress any key..."); console.getkey(K_NONE); }
                    return true;
                } catch (e) { try { f.close(); } catch (x) {} }
            }
        } catch (e) {}
    }
    return false;
}

function autoDetectANSHeight(path) {
    var f = null;
    try {
        if (!file_exists(path)) return null;
        f = new File(path);
        if (!f.open("r")) return null;
        var cnt = 0;
        while (f.readln() !== null) cnt++;
        f.close();
        return cnt || null;
    } catch (e) {
        try { if (f && f.isopen) f.close(); } catch (x) {}
        return null;
    }
}

// -------------------- Status lines --------------------
function buildStatusLines(player) {
    player = player || {};
    var name = player.name || "guest";
    var role = player.role || "Script Kiddie";
    var lvl = player.level || 1;
    var gold = player.gold || 0;
    var bank = player.bank || 0;
    var fw = player.firewallSkill || 0;
    var hk = player.hackingSkill || 0;
    var compromised = 0;
    try { compromised = Object.keys(player.devices || {}).filter(function (d) { return player.devices[d].compromised; }).length; } catch (e) { compromised = 0; }

    var line1 = ""
        + "\x01m\x01hPlayer:\x01n " + "\x01c\x01h" + name + "\x01n"
        + "  " + "\x01mRole:\x01n " + "\x01w\x01h" + role + "\x01n"
        + "  " + "\x01y\x01hLv:\x01n " + "\x01y\x01h" + lvl + "\x01n"
        + "  " + "\x01yGold:\x01n " + "\x01y\x01h" + gold + "\x01n"
        + "  " + "\x01cBank:\x01n " + "\x01c\x01h" + bank + "\x01n";

    var devColor = compromised > 0 ? "\x01r\x01h" : "\x01g\x01h";
    var line2 = ""
        + "\x01c\x01hFirewall:\x01n " + "\x01c\x01h" + fw + "\x01n"
        + "  " + "\x01g\x01hHacking:\x01n " + "\x01g\x01h" + hk + "\x01n"
        + "  " + devColor + "Devices compromised:\x01n " + devColor + compromised + "\x01n";

    return { line1: line1, line2: line2 };
}

// -------------------- Player model --------------------
function Player(name) {
    this.name = name;
    this.role = "Script Kiddie";
    this.level = 1;
    this.experience = 0;
    this.maxHealth = 100;
    this.health = 100;
    this.firewallSkill = 10;
    this.hackingSkill = 5;
    this.gold = 300;
    this.bank = 0;
    this.bankUpkeepPaidDate = null;
    this.dailyBankFee = 20;
    this.permBonuses = { maxHealth:0, firewall:0, hacking:0, gold:0 };
    this.devices = (typeof BotAttacks !== 'undefined' && BotAttacks.initDevices) ? BotAttacks.initDevices() : {};
    this.guildId = null;
    this.bossVictories = 0;
    this.inventory = {};
    this.lastLoginDate = null;
    this.rebirths = 0;
    this.lastBossFightDate = null;
}
Player.prototype.save = function () {
    try { BotWarsUtils.ensureSaveDir(); } catch (e) {}
    try { BotWarsUtils.saveJSON(BotWarsUtils.SAVE_DIR + this.name + ".json", this); } catch (e) {}
};
Player.load = function (name) {
    try {
        var path = BotWarsUtils.SAVE_DIR + name + ".json";
        if (!file_exists(path)) return null;
        var obj = BotWarsUtils.loadJSON(path);
        if (!obj) return null;
        var p = new Player(obj.name || name);
        for (var k in obj) if (obj.hasOwnProperty(k)) p[k] = obj[k];
        if (!p.devices && typeof BotAttacks !== 'undefined') p.devices = BotAttacks.initDevices();
        return p;
    } catch (e) { return null; }
};
Player.prototype.bankIsProtected = function () {
    try { return this.bankUpkeepPaidDate === BotWarsUtils.nowDateStr(); } catch (e) { return false; }
};
Player.prototype.payBankUpkeep = function () {
    try {
        var today = BotWarsUtils.nowDateStr();
        if (this.bank <= 0) return { ok: false, msg: "No funds to protect." };
        if (this.bankUpkeepPaidDate === today) return { ok: true, msg: "Already paid." };
        var fee = this.dailyBankFee || 20;
        if (this.gold >= fee) {
            this.gold -= fee;
            this.bankUpkeepPaidDate = today;
            this.save();
            return { ok: true, msg: "Bank protected for today." };
        }
        return { ok: false, msg: "Not enough gold." };
    } catch (e) { return { ok: false, msg: "Error" }; }
};

// -------------------- Device Management --------------------
function deviceMenu(player) {
    while (bbs.online && !js.terminated) {
        clearScreen();
        BotWarsUtils.printColor("=== DEVICE MANAGEMENT ===", "magenta");
        BotWarsUtils.printColor("(1) View Devices", "cyan");
        BotWarsUtils.printColor("(2) Auto-Scan ALL Devices", "green");
        BotWarsUtils.printColor("(3) Repair a Device", "yellow");
        BotWarsUtils.printColor("(4) Repair ALL Devices", "yellow");
        BotWarsUtils.printColor("(5) Simulate Attack", "red");
        BotWarsUtils.printColor("(0) Back", "magenta");

        var k = hotkey("\nChoice: ");
        if (!k) continue;
        if (k === '0') return;

        if (k === '1') {
            clearScreen();
            BotWarsUtils.printColor("Device list:", "cyan");
            var devs = player.devices || {};
            var any = false;
            for (var name in devs) {
                any = true;
                var d = devs[name];
                var status = d.compromised ? "COMPROMISED" : "OK";
                var color = d.compromised ? "red" : "green";
                BotWarsUtils.printColor(BotWarsUtils.format("%s - Integrity: %d%% - %s", name, d.integrity || 0, status), color);
            }
            if (!any) BotWarsUtils.printColor("No devices configured.", "yellow");
            console.print("Press Enter..."); console.getstr();
        }
        else if (k === '2') {
            BotWarsUtils.printColor("Running auto-scan...", "cyan");
            mswait(400);
            var found = [];
            var keys = Object.keys(player.devices || {});
            for (var i=0;i<keys.length;i++) {
                if (Math.random() < 0.25) {
                    found.push(keys[i]);
                    var dv = player.devices[keys[i]];
                    dv.integrity = Math.max(0, (dv.integrity || 100) - (Math.floor(Math.random() * 12) + 3));
                    if (dv.integrity <= 0) dv.compromised = true;
                }
            }
            player.save();
            if (found.length) BotWarsUtils.printColor("Scan found issues: " + found.join(", "), "yellow");
            else BotWarsUtils.printColor("No immediate threats detected.", "green");
            console.print("Press Enter..."); console.getstr();
        }
        else if (k === '3') {
            console.print("Device name to repair: ");
            var devName = console.getstr(40) || "";
            if (!devName) { BotWarsUtils.printColor("No device specified.", "red"); console.getstr(); continue; }
            var dev = (player.devices || {})[devName];
            if (!dev) { BotWarsUtils.printColor("Device not found.", "red"); console.getstr(); continue; }
            var cost = Math.max(5, Math.floor((100 - (dev.integrity || 0)) * 0.5));
            BotWarsUtils.printColor("Repair " + devName + " for " + cost + " gold? (Y/N)", "yellow");
            var yn = console.getstr(1) || "";
            if (yn.toUpperCase() === "Y") {
                if ((player.gold || 0) >= cost) {
                    player.gold -= cost;
                    dev.integrity = 100;
                    dev.compromised = false;
                    player.save();
                    BotWarsUtils.printColor("Repaired " + devName + ".", "green");
                } else BotWarsUtils.printColor("Insufficient gold.", "red");
            } else BotWarsUtils.printColor("Repair cancelled.", "yellow");
            console.print("Press Enter..."); console.getstr();
        }
        else if (k === '4') {
            var keys2 = Object.keys(player.devices || {});
            if (!keys2.length) { BotWarsUtils.printColor("No devices to repair.", "yellow"); console.getstr(); continue; }
            var total = 0;
            for (var j=0;j<keys2.length;j++) {
                var dv = player.devices[keys2[j]];
                total += Math.max(5, Math.floor((100 - (dv.integrity || 0)) * 0.5));
            }
            BotWarsUtils.printColor("Repair ALL devices for " + total + " gold? (Y/N)", "yellow");
            var y = console.getstr(1) || "";
            if (y.toUpperCase() === "Y") {
                if ((player.gold || 0) >= total) {
                    for (var k3=0;k3<keys2.length;k3++) {
                        player.devices[keys2[k3]].integrity = 100;
                        player.devices[keys2[k3]].compromised = false;
                    }
                    player.gold -= total;
                    player.save();
                    BotWarsUtils.printColor("All devices repaired.", "green");
                } else BotWarsUtils.printColor("Insufficient gold.", "red");
            } else BotWarsUtils.printColor("Cancelled.", "yellow");
            console.print("Press Enter..."); console.getstr();
        }
        else if (k === '5') {
            try {
                if (typeof BotAttacks !== 'undefined' && typeof BotAttacks.simulateBotAttack === 'function') {
                    var out = BotAttacks.simulateBotAttack(player);
                    if (out && out.log) {
                        for (var li=0; li<out.log.length; li++) {
                            var e = out.log[li];
                            BotWarsUtils.printColor(e.target + ": " + e.effect, e.success ? "red" : "yellow");
                        }
                    } else BotWarsUtils.printColor("Attack simulated.", "yellow");
                } else {
                    var keys3 = Object.keys(player.devices || {});
                    if (!keys3.length) BotWarsUtils.printColor("No devices to attack.", "yellow");
                    else {
                        var t = keys3[Math.floor(Math.random()*keys3.length)];
                        var dv2 = player.devices[t];
                        var dmg = Math.floor(Math.random()*20)+5;
                        dv2.integrity = Math.max(0, (dv2.integrity || 100) - dmg);
                        if (dv2.integrity <= 0) dv2.compromised = true;
                        player.save();
                        BotWarsUtils.printColor("Attack hit " + t + " for " + dmg + " dmg. Now " + dv2.integrity + "%", "red");
                    }
                }
            } catch(e) { BotWarsUtils.printColor("Simulation error: " + e, "red"); }
            console.print("Press Enter..."); console.getstr();
        }
        else {
            BotWarsUtils.printColor("Invalid selection.", "red");
            console.getstr();
        }
    }
}

// -------------------- Quick device viewer --------------------
function showDevices(player) {
    clearScreen();
    BotWarsUtils.printColor("=== DEVICE STATUS ===", "magenta");
    var any = false;
    for (var d in player.devices) {
        any = true;
        var dev = player.devices[d];
        BotWarsUtils.printColor(BotWarsUtils.format(" %s - Integrity: %d%% - %s", d, dev.integrity || 0, dev.compromised ? "COMPROMISED" : "OK"), dev.compromised ? "red" : "green");
    }
    if (!any) BotWarsUtils.printColor("No devices configured.", "yellow");
    console.print("Press Enter..."); console.getstr();
}

// -------------------- Guild Hall --------------------
function guildHallMenu(player) {
    if (typeof BWGuilds === 'undefined') {
        BotWarsUtils.printColor("Guild system not available.", "red");
        console.getstr();
        return;
    }
    if (typeof BWGuilds.enter === "function") {
        try { BWGuilds.enter(player); return; } catch (e) {}
    }
    while (bbs.online && !js.terminated) {
        clearScreen();
        BotWarsUtils.printColor("=== GUILD HALL ===", "magenta");
        BotWarsUtils.printColor("(1) Create Guild", "green");
        BotWarsUtils.printColor("(2) View My Guild Info", "cyan");
        BotWarsUtils.printColor("(3) Join Guild by Name", "yellow");
        BotWarsUtils.printColor("(4) Leave Guild", "yellow");
        BotWarsUtils.printColor("(5) Deposit to Guild Treasury", "green");
        BotWarsUtils.printColor("(6) Withdraw from Guild (Leader only)", "red");
        BotWarsUtils.printColor("(7) List All Guilds", "cyan");
        BotWarsUtils.printColor("(0) Back", "magenta");
        var k = hotkey("\nChoice: ");
        if (!k) continue;
        if (k === '0') return;
        if (k === '1') {
            console.print("Guild name: "); var gname = console.getstr(60) || "";
            if (!gname.trim()) { BotWarsUtils.printColor("Invalid name.", "red"); console.getstr(); continue; }
            var created = BWGuilds.createGuild(gname.trim(), player.name);
            if (created && created.id) { player.guildId = created.id; player.save(); BotWarsUtils.printColor("Guild created and you are leader.", "green"); }
            else BotWarsUtils.printColor("Failed to create guild.", "red");
            console.getstr();
        } else if (k === '2') {
            if (!player.guildId) { BotWarsUtils.printColor("You are not in a guild.", "yellow"); console.getstr(); continue; }
            var g = BWGuilds.loadGuild(player.guildId);
            if (!g) { BotWarsUtils.printColor("Guild not found.", "red"); console.getstr(); continue; }
            clearScreen();
            BotWarsUtils.printColor("Guild: " + g.name + " (" + g.id + ")", "magenta");
            BotWarsUtils.printColor("Leader: " + g.leader, "cyan");
            BotWarsUtils.printColor("Members (" + (g.members ? g.members.length : 0) + "): " + ((g.members && g.members.join(", ")) || ""), "white");
            BotWarsUtils.printColor("Treasury: " + (g.treasury || 0) + " gold", "yellow");
            BotWarsUtils.printColor("War Points: " + (g.warPoints || 0), "yellow");
            BotWarsUtils.printColor("Raidable Until: " + (g.raidableUntil || "Not raidable"), "cyan");
            BotWarsUtils.printColor("Perks: " + (g.perks ? JSON.stringify(g.perks) : "{}"), "faint");
            console.print("\r\nPress Enter..."); console.getstr();
        } else if (k === '3') {
            console.print("Guild name to join: "); var nameToJoin = console.getstr(60) || "";
            if (!nameToJoin.trim()) { BotWarsUtils.printColor("Invalid.", "red"); console.getstr(); continue; }
            var found = BWGuilds.findGuildByName(nameToJoin.trim());
            if (!found) { BotWarsUtils.printColor("Guild not found.", "red"); console.getstr(); continue; }
            var res = BWGuilds.joinGuild(found.id, player.name);
            if (res && res.ok) { player.guildId = found.id; player.save(); BotWarsUtils.printColor("Joined guild: " + found.name, "green"); }
            else BotWarsUtils.printColor("Unable to join: " + (res && res.msg ? res.msg : "unknown"), "red");
            console.getstr();
        } else if (k === '4') {
            if (!player.guildId) { BotWarsUtils.printColor("You are not in a guild.", "yellow"); console.getstr(); continue; }
            var res = BWGuilds.leaveGuild(player.guildId, player.name);
            if (res && res.ok) { player.guildId = null; player.save(); BotWarsUtils.printColor("You left the guild.", "green"); }
            else BotWarsUtils.printColor("Unable to leave: " + (res && res.msg ? res.msg : "unknown"), "red");
            console.getstr();
        } else if (k === '5') {
            if (!player.guildId) { BotWarsUtils.printColor("You are not in a guild.", "yellow"); console.getstr(); continue; }
            console.print("Amount to deposit to guild treasury: "); var amt = parseInt(console.getstr(12)) || 0;
            if (amt <= 0) { BotWarsUtils.printColor("Invalid amount.", "red"); console.getstr(); continue; }
            var res = BWGuilds.depositToGuild(player.guildId, player, amt);
            if (res && res.ok) BotWarsUtils.printColor("Deposited. New treasury: " + res.newTreasury, "green");
            else BotWarsUtils.printColor("Deposit failed: " + (res && res.msg ? res.msg : "unknown"), "red");
            console.getstr();
        } else if (k === '6') {
            if (!player.guildId) { BotWarsUtils.printColor("You are not in a guild.", "yellow"); console.getstr(); continue; }
            var gobj = BWGuilds.loadGuild(player.guildId);
            if (!gobj) { BotWarsUtils.printColor("Guild not found.", "red"); console.getstr(); continue; }
            if (gobj.leader !== player.name) { BotWarsUtils.printColor("Only the guild leader may withdraw.", "red"); console.getstr(); continue; }
            console.print("Amount to withdraw from guild treasury: "); var wamt = parseInt(console.getstr(12)) || 0;
            if (wamt <= 0) { BotWarsUtils.printColor("Invalid amount.", "red"); console.getstr(); continue; }
            var wres = BWGuilds.withdrawFromGuild(player.guildId, player, wamt);
            if (wres && wres.ok) BotWarsUtils.printColor("Withdraw successful. New treasury: " + wres.newTreasury, "green");
            else BotWarsUtils.printColor("Withdraw failed: " + (wres && wres.msg ? wres.msg : "unknown"), "red");
            console.getstr();
        } else if (k === '7') {
            clearScreen(); BotWarsUtils.printColor("=== ALL GUILDS ===", "magenta");
            try {
                var gdir = BotWarsUtils.DATA_DIR + "guilds/";
                var files = directory(gdir + "*.json") || [];
                if (!files || files.length === 0) BotWarsUtils.printColor("No guilds found.", "yellow");
                for (var i = 0; i < files.length; i++) {
                    var gg = BotWarsUtils.loadJSON(files[i]); if (!gg) continue;
                    BotWarsUtils.printColor("- " + gg.name + " (Leader: " + gg.leader + ") Members: " + ((gg.members && gg.members.length) || 0) + "  Treasury: " + (gg.treasury || 0), "cyan");
                }
            } catch (e) { BotWarsUtils.printColor("Unable to list guilds.", "red"); }
            console.print("\r\nPress Enter..."); console.getstr();
        } else {
            BotWarsUtils.printColor("Invalid selection.", "red");
            console.getstr();
        }
    }
}

// -------------------- Bank menu, arcades, bossLair, main loop --------------------
function bankMenu(player) {
    try { if (typeof BW_Bank !== 'undefined' && typeof BW_Bank.enter === 'function') { BW_Bank.enter(player); return; } } catch (e) {}
    while (bbs.online && !js.terminated) {
        clearScreen();
        BotWarsUtils.printColor("=== BANK & SAFE ===", "yellow");
        BotWarsUtils.printColor("On hand: " + (player.gold || 0) + "  Bank: " + (player.bank || 0) + (player.bankIsProtected && player.bankIsProtected() ? " (Protected Today)" : " (UNPROTECTED)"), "cyan");
        BotWarsUtils.printColor("(1) Deposit to Bank", "green");
        BotWarsUtils.printColor("(2) Withdraw from Bank", "green");
        BotWarsUtils.printColor("(3) Pay Daily Bank Fee (" + (player.dailyBankFee || 20) + " gold) to protect bank", "yellow");
        BotWarsUtils.printColor("(4) Attempt to Steal from Player", "red");
        BotWarsUtils.printColor("(5) Back", "magenta");
        var k = hotkey("\nChoice: ");
        if (!k) continue;
        if (k === '5') return;
        if (k === '1') {
            console.print("Amount to deposit: "); var amt = parseInt(console.getstr(12)) || 0;
            if (amt <= 0) { BotWarsUtils.printColor("Invalid amount.", "red"); console.getstr(); continue; }
            if ((player.gold || 0) >= amt) { player.gold -= amt; player.bank = (player.bank || 0) + amt; player.save(); BotWarsUtils.printColor("Deposited " + amt + " gold.", "green"); }
            else BotWarsUtils.printColor("Not enough gold.", "red");
            console.getstr();
        } else if (k === '2') {
            console.print("Amount to withdraw: "); var w = parseInt(console.getstr(12)) || 0;
            if (w <= 0) { BotWarsUtils.printColor("Invalid amount.", "red"); console.getstr(); continue; }
            if ((player.bank || 0) >= w) { player.bank -= w; player.gold = (player.gold || 0) + w; player.save(); BotWarsUtils.printColor("Withdrew " + w + " gold.", "green"); }
            else BotWarsUtils.printColor("Insufficient bank funds.", "red");
            console.getstr();
        } else if (k === '3') {
            var res = player.payBankUpkeep ? player.payBankUpkeep() : null;
            if (res) { if (res.ok) BotWarsUtils.printColor(res.msg, "green"); else BotWarsUtils.printColor(res.msg, "red"); }
            else { var fee = player.dailyBankFee || 20; if ((player.gold || 0) >= fee) { player.gold -= fee; player.bankUpkeepPaidDate = BotWarsUtils.nowDateStr(); player.save(); BotWarsUtils.printColor("Bank protected for today.", "green"); } else BotWarsUtils.printColor("Not enough gold.", "red"); }
            console.getstr();
        } else if (k === '4') {
            console.print("Target player name to attempt steal: "); var targ = console.getstr(40) || "";
            if (!targ.trim()) { BotWarsUtils.printColor("Invalid target.", "red"); console.getstr(); continue; }
            var sr = attemptStealFromPlayer(player, targ.trim());
            if (sr && sr.ok) BotWarsUtils.printColor("Steal successful: " + sr.stolen + " gold.", "green");
            else BotWarsUtils.printColor("Steal failed: " + (sr && sr.msg ? sr.msg : "unknown"), "red");
            console.getstr();
        } else { BotWarsUtils.printColor("Invalid choice.", "red"); console.getstr(); }
    }
}

function arcades(player) {
    try { if (typeof BWIGMs !== 'undefined' && typeof BWIGMs.enter === 'function') { BWIGMs.enter(player); return; } } catch (e) {}
    while (bbs.online && !js.terminated) {
        clearScreen();
        BotWarsUtils.printColor("=== ARCADES ===", "yellow");
        BotWarsUtils.printColor("1) Firewall Puzzle", "green");
        BotWarsUtils.printColor("2) Patch Deploy", "green");
        BotWarsUtils.printColor("3) Code Runner", "green");
        BotWarsUtils.printColor("4) Back", "magenta");
        var k = hotkey("\nChoice: ");
        if (!k) continue;
        if (k === '4') return;
        if (k === '1') {
            if (typeof BWIGMs !== 'undefined' && typeof BWIGMs.firewallPuzzle === 'function') BWIGMs.firewallPuzzle(player);
            else {
                clearScreen(); BotWarsUtils.printColor("FIREWALL PUZZLE (placeholder)", "cyan");
                BotWarsUtils.printColor("Patch 3 vulnerabilities (enter 1,2,3).", "faint");
                var ok = true;
                for (var i=1;i<=3;i++){ console.print("Patch #" + i + ": "); var a = console.getstr(2)||""; if (a.trim() !== String(i)) ok = false; }
                if (ok) BotWarsUtils.printColor("Patched successfully!", "green"); else BotWarsUtils.printColor("Patch failed.", "red");
                console.print("Press Enter..."); console.getstr();
            }
        } else if (k === '2') {
            if (typeof BWIGMs !== 'undefined' && typeof BWIGMs.patchDeploy === 'function') BWIGMs.patchDeploy(player);
            else {
                clearScreen(); BotWarsUtils.printColor("PATCH DEPLOY (placeholder)", "cyan");
                console.print("Deploy now? [Y/N]: "); var yn = console.getstr(1) || "";
                if (yn.toUpperCase() === "Y") { if (Math.random() < 0.8) BotWarsUtils.printColor("Deploy succeeded.", "green"); else BotWarsUtils.printColor("Deploy failed.", "red"); }
                else BotWarsUtils.printColor("Cancelled.", "yellow");
                console.print("Press Enter..."); console.getstr();
            }
        } else if (k === '3') {
            if (typeof BWIGMs !== 'undefined' && typeof BWIGMs.codeRunner === 'function') BWIGMs.codeRunner(player);
            else {
                clearScreen(); BotWarsUtils.printColor("CODE RUNNER (placeholder)", "cyan");
                BotWarsUtils.printColor("Type exactly: let x = 42;", "faint");
                console.print("Input: "); var got = console.getstr(80) || "";
                if (got === "let x = 42;") { BotWarsUtils.printColor("Success! +10 gold", "green"); player.gold = (player.gold||0) + 10; player.save(); }
                else BotWarsUtils.printColor("Compile error.", "red");
                console.print("Press Enter..."); console.getstr();
            }
        } else { BotWarsUtils.printColor("Invalid selection.", "red"); console.getstr(); }
    }
}

// -------------------- bossLair wrapper --------------------
function bossLair(player) {
    if ((player.level || 1) < 5) {
        BotWarsUtils.printColor("The Server Overlord's lair is too dangerous. Must be at least level 5.", "red");
        console.print("Press Enter...");
        console.getstr();
        return;
    }
    try {
        if (typeof BotAttacks !== 'undefined' && BotAttacks.BW_BossFight && typeof BotAttacks.BW_BossFight.startBossFight === 'function') {
            var res = BotAttacks.BW_BossFight.startBossFight(player);
            if (res && res.newPlayer && res.newPlayer.name) {
                try {
                    var np = Player.load(res.newPlayer.name);
                    if (np) {
                        for (var k in player) if (player.hasOwnProperty(k)) delete player[k];
                        for (var k2 in np) player[k2] = np[k2];
                    }
                } catch (e) {}
            }
            return;
        }
    } catch (e) {}
    BotWarsUtils.printColor("Boss Lair unavailable.", "red");
    console.print("Press Enter..."); console.getstr();
}

// -------------------- displayMainMenuAndStatus --------------------
function displayMainMenuAndStatus(player) {
    clearScreen();

    var printedANS = false;
    try {
        printedANS = tryPrintANSByName(MAIN_ANS_NAME, false);
        if (!printedANS) {
            try { if (typeof BotWarsUtils !== "undefined" && typeof BotWarsUtils.bannerScary === "function") { BotWarsUtils.bannerScary(); printedANS = true; } } catch(e) {}
        }
    } catch (e) { /* ignore */ }

    // Always print status overlay
    var status = buildStatusLines(player);
    try {
        console.print("\r\n");
        console.print(status.line1 + "\r\n");
        console.print(status.line2 + "\r\n");
    } catch (e) {
        try { console.print(status.line1.replace(/\x01./g,"") + "\r\n"); console.print(status.line2.replace(/\x01./g,"") + "\r\n"); } catch(x) {}
    }

    // Only print textual menu lines if ANS/banner was NOT printed
    if (!printedANS) {
        console.print("\r\n");
        for (var i=0;i<TEXT_MENU_LINES.length;i++) console.print(TEXT_MENU_LINES[i] + "\r\n");
    }
}

// -------------------- Main --------------------
function main() {
    try { BotWarsUtils.ensureSaveDir(); } catch (e) {}

    try { tryPrintANSByName(INTRO_ANS_NAME, true); } catch (e) {}

    var name = (typeof user !== 'undefined' && user && user.alias) ? user.alias : ("guest" + Math.floor(Math.random()*1000));
    var player = Player.load(name);
    if (!player) {
        BotWarsUtils.printColor("Creating new character: " + name, "yellow");
        player = new Player(name);
        var roles = ['Network Admin','Security Researcher','Bot Hunter','Engineer','Script Kiddie'];
        BotWarsUtils.printColor("Choose role: 1) Network Admin  2) Security Researcher", "cyan");
        BotWarsUtils.printColor("3) Bot Hunter  4) Engineer  5) Script Kiddie", "cyan");
        console.print("Choice: ");
        var ch = console.getstr(2) || "";
        var r = Math.max(1, Math.min(5, parseInt(ch) || 5));
        player.role = roles[r - 1];
        player.save();
    }

    // simulate attacks while away
    try {
        if (player.lastLoginDate && typeof BotAttacks !== 'undefined' && typeof BotAttacks.simulateAttacksWhileAway === 'function') {
            var last = new Date(player.lastLoginDate);
            var now = new Date();
            var hoursAway = Math.floor((now - last) / (1000*60*60));
            if (hoursAway >= 3) {
                var res = BotAttacks.simulateAttacksWhileAway(player, hoursAway);
                BotWarsUtils.printColor("");
                BotWarsUtils.printColor("While you were away your devices were attacked " + (res && res.length ? res.length : 0) + " times.", "red");
                console.getstr();
            }
        }
    } catch (e) {}

    player.lastLoginDate = (new Date()).toISOString();
    player.save();

    while (bbs.online && !js.terminated) {
        clearScreen();
        displayMainMenuAndStatus(player);

        console.print("\r\nChoice: ");
        var k = hotkey("");
        if (!k) continue;

        if (k === '1') deviceMenu(player);
        else if (k === '2') guildHallMenu(player);
        else if (k === '3') bossLair(player);
        else if (k === '4') {
            try {
                if (typeof BW_Areas !== 'undefined' && typeof BW_Areas.enterTown === 'function') BW_Areas.enterTown(player);
                else { BotWarsUtils.printColor("Town/Places unavailable.", "red"); console.getstr(); }
            } catch (e) { BotWarsUtils.printColor("Town error.", "red"); console.getstr(); }
        } else if (k === '5') bankMenu(player);
        else if (k === '6') {
            try {
                if (typeof Leaderboards !== 'undefined' && typeof Leaderboards.showLeaderboards === 'function')
                    Leaderboards.showLeaderboards();
                else
                    BotWarsUtils.printColor("Leaderboards unavailable.", "red");
            } catch(e) { BotWarsUtils.printColor("Leaderboards error.", "red"); }
        }
        else if (k === '7') arcades(player);
        else if (k === '8') {
            try { player.save(); } catch (e) {}
                BotWarsUtils.printColor("Game Saved. Goodbye!", "red");
            try {
                createScoresANS(player);
            } catch (e) {
            }
            return;
        }
        else { BotWarsUtils.printColor("Invalid choice.", "red"); console.getstr(); }
    }
}

// Run when executed directly
if (typeof module === 'undefined' || !module.exports) {
    try { main(); } catch (err) { try { log(LOG_ERR, "botwars runtime error: " + (err && err.toString ? err.toString() : err)); } catch(e) {} }
}

function attemptStealFromPlayer(stealer, targetName) {
    try {
        var target = Player.load(targetName);
        if (!target || target.name === stealer.name) return { ok: false, msg: "Invalid target." };
        var success = Math.random() < 0.3 && target.bank > 0 && !target.bankIsProtected();
        if (success) {
            var stolen = Math.min(target.bank, Math.floor(Math.random() * 100) + 10);
            target.bank -= stolen;
            stealer.gold += stolen;
            target.save();
            stealer.save();
            return { ok: true, stolen: stolen };
        } else return { ok: false, msg: "Steal failed." };
    } catch (e) { return { ok: false, msg: "Error" }; }
}