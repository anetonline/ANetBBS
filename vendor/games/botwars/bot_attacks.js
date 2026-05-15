/*
  bot_attacks.js - AI attacks, leveling, boss fights, prestige/reincarnation
  Complete, self-contained; does not depend on the Player constructor being present.
  It reads/writes plain JSON files for reincarnation/new run creation so botwars.js's Player.load() will work.
*/

load("sbbsdefs.js");
if (typeof BotWarsUtils === "undefined") {
    try { load(js.exec_dir + "botwars_utils.js"); } catch(e) {}
}

var BotAttacks = (function(){
    var FINAL_BOSS_VICTORIES = 3;
    var BASE_DEVICE_NAMES = ["Router","Firewall","Workstation","Database","IoT-Sensor"];

    function initDevices() {
        var devices = {};
        for (var i=0;i<BASE_DEVICE_NAMES.length;i++) devices[BASE_DEVICE_NAMES[i]] = { integrity:100, compromised:false };
        return devices;
    }

    function getXPForLevel(level) {
        level = Math.max(1, level || 1);
        return Math.floor(100 * Math.pow(1.35, level - 1));
    }

    function addExperience(player, xp) {
        xp = Math.floor(xp||0);
        if (!player) return;
        player.experience = (player.experience||0) + xp;
        var leveled = 0;
        while (player.experience >= getXPForLevel(player.level || 1)) {
            player.experience -= getXPForLevel(player.level || 1);
            player.level = (player.level || 1) + 1;
            leveled++;
            player.maxHealth = (player.maxHealth || 100) + 10; // Increased from 5
            player.health = Math.min(player.maxHealth, player.health || player.maxHealth);
            player.firewallSkill = (player.firewallSkill || 0) + 2; // Increased from 1
            player.hackingSkill = (player.hackingSkill || 0) + 2; // Increased from 1
            player.gold = (player.gold || 0) + (10 * leveled); // Reward gold on level up
        }
        if (leveled > 0) {
            try { 
                BotWarsUtils.printColor("LEVEL UP! You are now level " + player.level + ". Gained +10 gold!", "green");
                player.save(); // Save after level up
            } catch(e) {}
        }
    }

    function simulateAttacksWhileAway(player, hoursAway) {
        hoursAway = Math.max(0, Math.floor(hoursAway||0));
        var events = [];
        if (!player) return events;
        var attempts = Math.min(24, Math.max(1, Math.floor(hoursAway)));
        for (var i=0;i<attempts;i++) {
            var targets = Object.keys(player.devices || {});
            if (!targets.length) break;
            var target = targets[Math.floor(Math.random()*targets.length)];
            var dev = player.devices[target];
            var damage = Math.floor(Math.random()*20 + Math.random()*10);
            dev.integrity = Math.max(0, (dev.integrity||100) - damage);
            if (dev.integrity <= 0) dev.compromised = true;
            events.push({ time:(new Date()).toISOString(), target:target, effect:"Integrity -" + damage + " -> " + dev.integrity, success: dev.compromised });
            if (Math.random() < 0.05 && (player.gold||0) > 0) {
                var s = Math.min(player.gold, Math.floor(Math.random()*20)+5);
                player.gold -= s;
                events.push({ time:(new Date()).toISOString(), target:target, effect:"Stole " + s + " gold", success:true });
            }
        }
        try { player.save(); } catch(e) {}
        return events;
    }

    function simulateBotAttack(player) {
        var log = [];
        if (!player) return { ok:false, log:[] };
        var targets = Object.keys(player.devices || {});
        if (!targets.length) return { ok:false, log:[] };
        var target = targets[Math.floor(Math.random()*targets.length)];
        var dev = player.devices[target];
        var atk = Math.floor(Math.random()*30)+5;
        dev.integrity = Math.max(0, (dev.integrity||100) - atk);
        var comp = false;
        if (dev.integrity <= 0) { dev.compromised = true; comp = true; }
        log.push({ target:target, effect:"Integrity -" + atk + " -> " + dev.integrity, success: comp });
        if (Math.random() < 0.1 && (player.gold||0) > 0) {
            var lost = Math.min(player.gold, Math.floor(Math.random()*10)+1);
            player.gold -= lost;
            log.push({ target:target, effect:"Lost " + lost + " gold", success:true });
        }
        try { player.save(); } catch(e) {}
        return { ok:true, log:log };
    }

    function repairDevice(player, name, costFromGold) {
        if (!player) return { ok:false, msg:"No player" };
        costFromGold = (typeof costFromGold === 'undefined') ? true : !!costFromGold;
        var dev = player.devices && player.devices[name];
        if (!dev) return { ok:false, msg:"Device not found." };
        var cost = Math.max(5, Math.floor((100 - (dev.integrity||0)) * 0.5));
        if (costFromGold) {
            if ((player.gold||0) < cost) return { ok:false, msg:"Not enough gold ("+cost+"g)." };
            player.gold -= cost;
        } else return { ok:false, msg:"Only gold repairs supported." };
        dev.integrity = 100; dev.compromised = false;
        try { player.save(); } catch(e) {}
        return { ok:true, msg:"Repaired "+name+" for "+cost+" gold.", cost:cost };
    }

    // local askHotkey used when botwars.hotkey isn't available
    function askHotkey(prompt) {
        if (prompt) console.print(prompt);
        try {
            if (typeof console.getkey === "function") {
                var k = console.getkey(K_NONE);
                if (!k) return null;
                return String(k).toUpperCase();
            }
        } catch(e){}
        try { var s = console.getstr(2) || ""; return s.length ? s.charAt(0).toUpperCase() : null; } catch(e) { return null; }
    }

    function bossFight(player, bossLevel) {
        bossLevel = Math.max(1, bossLevel || 1);
        var log = [];
        var bossHP = 50 + bossLevel * 30;
        var bossAtk = 5 + Math.floor(bossLevel * 3);
        var bossDef = 2 + Math.floor(bossLevel * 2);

        var pHP = player.maxHealth || 100;
        var pAtk = (player.hackingSkill || 5) * 2;
        var pDef = (player.firewallSkill || 5) * 1;

        log.push("Boss Lv " + bossLevel + " appears! HP:" + bossHP);

        var turn = 0;
        while (bossHP > 0 && pHP > 0 && turn < 200) {
            turn++;
            var action = (pHP < (player.maxHealth||100) * 0.35 && Math.random() < 0.6) ? "heal" : "attack";
            if (action === "attack") {
                var dmg = Math.max(1, pAtk - bossDef + Math.floor(Math.random()*5));
                bossHP -= dmg;
                log.push("You attack for " + dmg + ". Boss HP: " + Math.max(0,bossHP));
            } else {
                var heal = Math.max(5, Math.floor((player.firewallSkill||5) * 1.5));
                pHP = Math.min(player.maxHealth||100, pHP + heal);
                log.push("You repair for " + heal + ". You HP: " + pHP);
            }
            if (bossHP <= 0) break;
            var bdmg = Math.max(1, bossAtk - pDef + Math.floor(Math.random()*6));
            pHP -= bdmg;
            log.push("Boss hits for " + bdmg + ". You HP: " + Math.max(0,pHP));
        }

        var victory = bossHP <= 0 && pHP > 0;
        return { result: victory ? "victory" : "defeat", log:log, bossLevel:bossLevel, playerHPremain: Math.max(0,pHP), bossHPremain: Math.max(0,bossHP) };
    }

    function _readPrestigeDB() {
        return BotWarsUtils.loadJSON(BotWarsUtils.DATA_DIR + "prestige.json") || {};
    }
    function _writePrestigeDB(db) {
        return BotWarsUtils.saveJSON(BotWarsUtils.DATA_DIR + "prestige.json", db);
    }

    // handlePostRunVictory: create a new player JSON object (no dependency on Player() constructor).
    function handlePostRunVictory(player) {
        if (!player) return;
        BotWarsUtils.printColor("\r\n=== RUN COMPLETED ===", "magenta");
        BotWarsUtils.printColor("Choose a permanent retention option:", "cyan");
        BotWarsUtils.printColor("(1) Keep 5% of all relevant stats", "green");
        BotWarsUtils.printColor("(2) Keep 25% of ONE stat", "yellow");
        BotWarsUtils.printColor("(3) Decline", "red");
        var choice = askHotkey("\nChoice: ");
        var perm = player.permBonuses || {};
        perm.maxHealth = perm.maxHealth || 0;
        perm.firewallSkill = perm.firewallSkill || 0;
        perm.hackingSkill = perm.hackingSkill || 0;
        perm.gold = perm.gold || 0;
        if (choice === '1') {
            var addH = Math.max(1, Math.floor((player.maxHealth||100)*0.05));
            var addFW = Math.max(0, Math.floor((player.firewallSkill||0)*0.05));
            var addHK = Math.max(0, Math.floor((player.hackingSkill||0)*0.05));
            var addG = Math.max(0, Math.floor((player.gold||0)*0.05));
            perm.maxHealth += addH; perm.firewallSkill += addFW; perm.hackingSkill += addHK; perm.gold += addG;
            BotWarsUtils.printColor("Permanent: +"+addH+" maxHP, +"+addFW+" FW, +"+addHK+" HK, +"+addG+" gold.", "green");
        } else if (choice === '2') {
            BotWarsUtils.printColor("(1) HP (2) Firewall (3) Hacking (4) Gold", "cyan");
            var sc = askHotkey("\nChoice: ");
            if (sc === '1') { var a = Math.max(1, Math.floor((player.maxHealth||100)*0.25)); perm.maxHealth += a; BotWarsUtils.printColor("Permanent +"+a+" maxHP", "green"); }
            else if (sc === '2') { var a = Math.max(1, Math.floor((player.firewallSkill||0)*0.25)); perm.firewallSkill += a; BotWarsUtils.printColor("Permanent +"+a+" firewall", "green"); }
            else if (sc === '3') { var a = Math.max(1, Math.floor((player.hackingSkill||0)*0.25)); perm.hackingSkill += a; BotWarsUtils.printColor("Permanent +"+a+" hacking", "green"); }
            else if (sc === '4') { var a = Math.max(1, Math.floor((player.gold||0)*0.25)); perm.gold += a; BotWarsUtils.printColor("Permanent +"+a+" gold", "green"); }
            else BotWarsUtils.printColor("No permanent chosen.", "yellow");
        } else BotWarsUtils.printColor("No permanent chosen.", "yellow");

        player.permBonuses = perm;
        player.rebirths = (player.rebirths||0) + 1;

        // Create the new player object as plain JSON for the new run
        var newPlayerObj = {
            name: player.name,
            role: player.role || "Script Kiddie",
            level: 1,
            experience: 0,
            maxHealth: (100) + (perm.maxHealth || 0),
            health: (100) + (perm.maxHealth || 0),
            firewallSkill: (10) + (perm.firewallSkill || 0),
            hackingSkill: (5) + (perm.hackingSkill || 0),
            gold: (300) + (perm.gold || 0),
            bank: 0,
            bankUpkeepPaidDate: null,
            dailyBankFee: player.dailyBankFee || 20,
            permBonuses: perm,
            devices: initDevices(),
            guildId: null,
            bossVictories: 0,
            inventory: {},
            lastLoginDate: null,
            rebirths: player.rebirths
        };

        try {
            var db = _readPrestigeDB(); db.records = db.records || [];
            db.records.push({ name: player.name, date:(new Date()).toISOString(), rebirths:newPlayerObj.rebirths, perm:perm });
            _writePrestigeDB(db);
        } catch(e) {}

        try { BotWarsUtils.saveJSON(BotWarsUtils.SAVE_DIR + newPlayerObj.name + ".json", newPlayerObj); } catch(e) {}
        BotWarsUtils.printColor("You have been reincarnated. Rebirth count: " + newPlayerObj.rebirths, "magenta");
        BotWarsUtils.printColor("Permanent bonuses applied to new run.", "cyan");
        console.print("Press Enter..."); console.getstr();
        return newPlayerObj;
    }

    var BW_BossFight = {
        startBossFight: function(player) {
            var nowDate = BotWarsUtils.nowDateStr();
            if (player.lastBossFightDate === nowDate) {
                BotWarsUtils.printColor("The Server Overlord's lair is sealed for today. Return tomorrow.", "red");
                console.print("Press Enter...");
                console.getstr();
                return { result: "cooldown" };
            }
            var bossLevel = Math.max(1, (player.level||1) + Math.floor(((player.rebirths||0)/2)));
            BotWarsUtils.printColor("Entering Boss Lair... Boss level: " + bossLevel, "magenta");
            var res = bossFight(player, bossLevel);
            try { clear(); } catch(e) {}
            BotWarsUtils.printColor("=== BOSS FIGHT ===", "magenta");
            for (var i=0;i<res.log.length;i++) console.print(res.log[i] + "\r\n");
            if (res.result === "victory") {
                BotWarsUtils.printColor("\r\nYou defeated the boss!", "green");
                var goldGain = 200 + bossLevel * 50;
                player.gold = (player.gold||0) + goldGain;
                var xpGain = 100 + bossLevel * 40;
                addExperience(player, xpGain);
                player.bossVictories = (player.bossVictories||0) + 1;
                BotWarsUtils.printColor("Rewards: " + goldGain + " gold, " + xpGain + " XP", "yellow");
                player.save();
                if ((player.bossVictories||0) >= FINAL_BOSS_VICTORIES) {
                    var newP = handlePostRunVictory(player);
                    player.lastBossFightDate = nowDate;
                    return { result:"victory", newPlayer:newP || null };
                }
                player.lastBossFightDate = nowDate;
                return { result:"victory" };
            } else {
                BotWarsUtils.printColor("\r\nYou were defeated by the boss...", "red");
                var lost = Math.min(player.gold||0, 50 + bossLevel * 10);
                player.gold = Math.max(0, (player.gold||0) - lost);
                player.save();
                BotWarsUtils.printColor("You lost " + lost + " gold.", "red");
                console.print("Press Enter..."); console.getstr();
                player.lastBossFightDate = nowDate;
                return { result:"defeat" };
            }
        }
    };

    return {
        initDevices: initDevices,
        simulateAttacksWhileAway: simulateAttacksWhileAway,
        simulateBotAttack: simulateBotAttack,
        repairDevice: repairDevice,
        addExperience: addExperience,
        getXPForLevel: getXPForLevel,
        _readPrestigeDB: _readPrestigeDB,
        _writePrestigeDB: _writePrestigeDB,
        BW_BossFight: BW_BossFight
    };
})();

try { global.BotAttacks = BotAttacks; } catch(e){}
try { if (typeof BW_BossFight === "undefined") BW_BossFight = BotAttacks.BW_BossFight; } catch(e) {}
