// Guilds module for Bot Wars
// Exposes BWGuilds with guild management functions
load(js.exec_dir + "botwars_utils.js");

if (typeof BWGuilds === 'undefined') {
    var BWGuilds = (function() {
        BotWarsUtils.ensureSaveDir();
        var GUILD_DIR = BotWarsUtils.DATA_DIR + "guilds/";

        function ensureGuildDir() {
            try {
                if (!file_isdir(GUILD_DIR)) mkdir(GUILD_DIR);
            } catch (e) {}
        }
        ensureGuildDir();

        function guildPath(id) {
            return GUILD_DIR + id + ".json";
        }

        function generateGuildId(name) {
            return "g_" + (new Date()).getTime().toString(36) + "_" + name.replace(/\s+/g,'').toLowerCase();
        }

        function createGuild(name, leaderName) {
            var id = generateGuildId(name);
            var guild = {
                id: id,
                name: name,
                leader: leaderName,
                members: [leaderName],
                created: (new Date()).toISOString(),
                treasury: 0,
                upkeepFeeDaily: 500,
                lastUpkeepDate: null,
                atWarWith: [],
                warHistory: [],
                warPoints: 0,
                raidableUntil: null,
                perks: {}
            };
            BotWarsUtils.saveJSON(guildPath(id), guild);
            return guild;
        }

        function loadGuild(id) {
            var path = guildPath(id);
            if (!file_exists(path)) return null;
            return BotWarsUtils.loadJSON(path);
        }

        function saveGuild(guild) {
            return BotWarsUtils.saveJSON(guildPath(guild.id), guild);
        }

        function findGuildByName(name) {
            var files = directory(GUILD_DIR + "*.json") || [];
            for (var i=0;i<files.length;i++) {
                var g = BotWarsUtils.loadJSON(files[i]);
                if (g && g.name.toLowerCase() === name.toLowerCase()) return g;
            }
            return null;
        }

        function joinGuild(guildId, playerName) {
            var g = loadGuild(guildId);
            if (!g) return {ok:false, msg:"Guild not found."};
            if (g.members.indexOf(playerName) !== -1) return {ok:false, msg:"Already a member."};
            g.members.push(playerName);
            saveGuild(g);
            return {ok:true, guild:g};
        }

        function leaveGuild(guildId, playerName) {
            var g = loadGuild(guildId);
            if (!g) return {ok:false, msg:"Guild not found."};
            var idx = g.members.indexOf(playerName);
            if (idx === -1) return {ok:false, msg:"Not a member."};
            g.members.splice(idx,1);
            if (g.leader === playerName && g.members.length>0) {
                g.leader = g.members[0];
            } else if (g.members.length === 0) {
                try { file_remove(guildPath(g.id)); } catch(e){}
                return {ok:true, msg:"Guild disbanded."};
            }
            saveGuild(g);
            return {ok:true, guild:g};
        }

        function depositToGuild(guildId, player, amount) {
            if (amount <= 0) return {ok:false, msg:"Invalid amount."};
            if ((player.bank || 0) < amount && (player.gold || 0) < amount) return {ok:false, msg:"Not enough funds."};
            var g = loadGuild(guildId);
            if (!g) return {ok:false, msg:"Guild not found."};
            if ((player.bank || 0) >= amount) {
                player.bank -= amount;
            } else {
                var rem = amount - Math.max(0, player.bank || 0);
                player.bank = 0;
                if ((player.gold || 0) >= rem) player.gold -= rem;
                else return {ok:false, msg:"Not enough funds."};
            }
            g.treasury += amount;
            saveGuild(g);
            player.save();
            return {ok:true, newTreasury:g.treasury};
        }

        function withdrawFromGuild(guildId, player, amount) {
            var g = loadGuild(guildId);
            if (!g) return {ok:false, msg:"Guild not found."};
            if (g.leader !== player.name) return {ok:false, msg:"Only the guild leader can withdraw."};
            if (amount <= 0 || amount > g.treasury) return {ok:false, msg:"Invalid amount."};
            g.treasury -= amount;
            player.gold += amount;
            saveGuild(g);
            player.save();
            return {ok:true, newTreasury:g.treasury};
        }

        function payGuildUpkeep(guildId) {
            var g = loadGuild(guildId);
            if (!g) return {ok:false, msg:"Guild not found."};
            if (g.treasury >= g.upkeepFeeDaily) {
                g.treasury -= g.upkeepFeeDaily;
                g.lastUpkeepDate = BotWarsUtils.nowDateStr();
                g.raidableUntil = null;
                saveGuild(g);
                return {ok:true, msg:"Upkeep paid."};
            } else {
                var until = new Date();
                until.setDate(until.getDate() + 1);
                g.raidableUntil = until.toISOString();
                saveGuild(g);
                return {ok:false, msg:"Insufficient treasury: guild bank is now raidable until " + g.raidableUntil};
            }
        }

        function declareGuildWar(attackerId, defenderId, reason) {
            var a = loadGuild(attackerId);
            var b = loadGuild(defenderId);
            if (!a || !b) return {ok:false, msg:"Guild not found."};
            var war = {
                id: "war_" + (new Date()).getTime(),
                attacker: attackerId,
                defender: defenderId,
                start: (new Date()).toISOString(),
                status: "active",
                attackerPoints: 0,
                defenderPoints: 0,
                log: [],
                reason: reason || ""
            };
            a.warHistory = a.warHistory || [];
            b.warHistory = b.warHistory || [];
            a.warHistory.push(war);
            b.warHistory.push(war);
            a.atWarWith.push(defenderId);
            b.atWarWith.push(attackerId);
            saveGuild(a);
            saveGuild(b);
            return {ok:true, war:war};
        }

        function awardGuildPoints(guildId, points, note) {
            var g = loadGuild(guildId);
            if (!g) return {ok:false, msg:"Guild not found."};
            g.warPoints = (g.warPoints || 0) + points;
            g.warHistory = g.warHistory || [];
            g.warHistory.push({ts:(new Date()).toISOString(), points:points, note:note});
            saveGuild(g);
            return {ok:true, warPoints:g.warPoints};
        }

        function raidGuild(attackerGuildId, targetGuildId, attackerPlayer) {
            var target = loadGuild(targetGuildId);
            var attackerGuild = loadGuild(attackerGuildId);
            if (!target || !attackerGuild) return {ok:false, msg:"Guild not found."};
            if (!target.raidableUntil || new Date() > new Date(target.raidableUntil)) {
                var penalty = Math.min( Math.floor(attackerPlayer.gold * 0.2), 200 );
                attackerPlayer.gold = Math.max(0, attackerPlayer.gold - penalty);
                attackerPlayer.save();
                return {ok:false, msg:"Raid attempt failed and you were fined " + penalty + " gold."};
            }
            var baseStolen = Math.min(target.treasury, Math.floor(Math.random() * 500) + 100);
            target.treasury -= baseStolen;
            attackerGuild.treasury += Math.floor(baseStolen * 0.5);
            var personalGain = Math.floor(baseStolen * 0.5);
            attackerPlayer.gold += personalGain;
            saveGuild(target);
            saveGuild(attackerGuild);
            attackerPlayer.save();
            return {ok:true, stolen:baseStolen, personal:personalGain, guildGain:Math.floor(baseStolen*0.5)};
        }

        return {
            createGuild: createGuild,
            loadGuild: loadGuild,
            saveGuild: saveGuild,
            findGuildByName: findGuildByName,
            joinGuild: joinGuild,
            leaveGuild: leaveGuild,
            depositToGuild: depositToGuild,
            withdrawFromGuild: withdrawFromGuild,
            payGuildUpkeep: payGuildUpkeep,
            declareGuildWar: declareGuildWar,
            awardGuildPoints: awardGuildPoints,
            raidGuild: raidGuild
        };
    })();
}