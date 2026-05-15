// Bar

load(js.exec_dir + "botwars_utils.js");
load(js.exec_dir + "bot_attacks.js");

if (typeof BW_Bar === 'undefined') {
var BW_Bar = (function(){
    var AREA_BBS = BotWarsUtils.DATA_DIR + "area_bulletins.json";

    function loadBulletins() {
        var d = BotWarsUtils.loadJSON(AREA_BBS) || {};
        if (!d.posts) d.posts = {};
        return d;
    }
    function saveBulletins(d) { BotWarsUtils.saveJSON(AREA_BBS, d); }

    function postBulletin(areaId, author, text) {
        var db = loadBulletins();
        if (!db.posts[areaId]) db.posts[areaId] = [];
        db.posts[areaId].push({ author: author, text: text, date:(new Date()).toISOString() });
        saveBulletins(db);
    }

    function readBulletins(areaId) {
        var db = loadBulletins();
        return db.posts[areaId] || [];
    }

    function showColorCheatSheet() {
        console.clear();
        BotWarsUtils.printColor("=== COLOR CHEAT SHEET ===", "magenta");
        BotWarsUtils.printColor("Use |<num> to change text color. Examples below (|n resets):", "cyan");
        var names = ["Black","Red","Green","Yellow","Blue","Magenta","Cyan","White"];
        for (var i=0;i<8;i++) {
            var code = "|" + (i<10 ? ("0"+i).slice(-2) : i);
            var sample = code + names[i] + "|n";
            console.print(BotWarsUtils.parsePipeColorCodes(sample) + "  ");
            BotWarsUtils.printColor("(" + code + ") " + names[i], "faint");
        }
        BotWarsUtils.printColor("\nBright variants:", "cyan");
        for (var j=8;j<16;j++) {
            var code2 = "|" + j;
            var baseIdx = j % 8;
            var sample2 = code2 + "Bright " + names[baseIdx] + "|n";
            console.print(BotWarsUtils.parsePipeColorCodes(sample2) + "  ");
            BotWarsUtils.printColor("(" + code2 + ") Bright " + names[baseIdx], "faint");
        }
        BotWarsUtils.printColor("\nNotes:", "magenta");
        BotWarsUtils.printColor("- Use '|n' to reset to normal color.", "faint");
        console.print("\r\nPress Enter to return...");
        console.getstr();
    }

    function enterBar(player, areaId) {
        while (bbs.online && !js.terminated) {
            console.clear();
            BotWarsUtils.printColor("=== THE HACKER'S BAR ===", "magenta");
            BotWarsUtils.printColor("(1) Buy Drink (15g) - heals 15 HP", "green");
            BotWarsUtils.printColor("(2) Read Bulletin Board", "cyan");
            BotWarsUtils.printColor("(3) Post to Board (supports |<num> color codes, e.g. |15)", "yellow");
            BotWarsUtils.printColor("(4) Gossip (rumor) - 5g for a hint about events", "cyan");
            BotWarsUtils.printColor("(5) Color Cheat Sheet", "cyan");
            BotWarsUtils.printColor("(0) Leave Bar", "magenta");
            console.print("\nChoice: ");
            var ch = console.getstr(2);
            if (!ch) continue;
            if (ch === '0') return;
            if (ch === '1') {
                if (player.gold >= 15) {
                    player.gold -= 15;
                    player.health = Math.min(player.maxHealth, player.health + 15);
                    BotAttacks.addExperience(player, 10);
                    player.save();
                    BotWarsUtils.printColor("You feel refreshed.", "green");
                } else BotWarsUtils.printColor("Not enough gold.", "red");
                console.getstr();
            } else if (ch === '2') {
                var posts = readBulletins(areaId);
                console.clear();
                BotWarsUtils.printColor("=== BULLETIN BOARD ===", "magenta");
                if (posts.length === 0) BotWarsUtils.printColor("No posts yet.", "yellow");
                for (var i=0;i<posts.length;i++) {
                    var p = posts[i];
                    BotWarsUtils.printColor(p.date + " - " + p.author, "cyan");
                    var rendered = BotWarsUtils.parsePipeColorCodes(p.text || "");
                    console.print(rendered + "\x01n\r\n");
                    BotWarsUtils.printColor("---", "faint");
                }
                console.print("Press Enter..."); console.getstr();
            } else if (ch === '3') {
                // Frame-based input: hides codes, shows colored text live
                var rawMsg = BotWarsUtils.getColoredInput("Message (use |<num> color codes, max 60 chars)", 60);
                if (!rawMsg || rawMsg.trim().length === 0) {
                    BotWarsUtils.printColor("No message entered. Cancelled.", "red");
                    console.getstr();
                    continue;
                }
                // preview & confirm
                console.clear();
                BotWarsUtils.printColor("=== MESSAGE PREVIEW ===", "magenta");
                console.print(BotWarsUtils.parsePipeColorCodes(rawMsg) + "\x01n\r\n");
                BotWarsUtils.printColor("\nPost this message? [Y]es  [E]dit  [C]ancel", "yellow");
                var resp = console.getstr(1);
                if (!resp) continue;
                resp = resp.toUpperCase();
                if (resp === 'Y') {
                    postBulletin(areaId, player.name, rawMsg);
                    BotAttacks.addExperience(player, 5);
                    player.save();
                    BotWarsUtils.printColor("Posted.", "green");
                    console.getstr();
                } else if (resp === 'E') {
                    continue; // re-enter
                } else {
                    BotWarsUtils.printColor("Post cancelled.", "red");
                    console.getstr();
                }
            } else if (ch === '4') {
                if (player.gold < 5) { BotWarsUtils.printColor("Not enough gold.", "red"); console.getstr(); continue; }
                player.gold -= 5;
                player.save();
                var hint = generateRumor();
                BotWarsUtils.printColor("GOSSIP: " + hint, "yellow");
                BotAttacks.addExperience(player, 5);
                player.save();
                console.getstr();
            } else if (ch === '5') {
                showColorCheatSheet();
            } else BotWarsUtils.printColor("Invalid choice.", "red");
        }
    }

    function generateRumor() {
        var rumors = [
            "Guild Alpha missing upkeep - treasury weak.",
            "Server Overlord patrols stronger near Black Market.",
            "A rare perk token was spotted in TechMart last week.",
            "DDoS storms expected in 2 days.",
            "A player was seen near the vault..."
        ];
        return rumors[Math.floor(Math.random()*rumors.length)];
    }

    return {
        enterBar: enterBar,
        postBulletin: postBulletin,
        readBulletins: readBulletins,
        showColorCheatSheet: showColorCheatSheet
    };
})();
}