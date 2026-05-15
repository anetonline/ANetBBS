// leaderboards.js - compute & show leaderboards (sanitized; no filesystem paths printed)

load(js.exec_dir + "botwars_utils.js");

if (typeof Leaderboards === "undefined") {
    var Leaderboards = (function(){
        var LB_PATH = BotWarsUtils.DATA_DIR + "leaderboards.json";

        function computeGuildLeaderboard(){
            var dir = BotWarsUtils.DATA_DIR + "guilds/";
            var files = directory(dir + "*.json") || [];
            var list = [];
            for(var i=0;i<files.length;i++){
                var g = BotWarsUtils.loadJSON(files[i]);
                if(!g) continue;
                list.push({ id:g.id, name:g.name, warPoints:g.warPoints||0, treasury:g.treasury||0, members:(g.members||[]).length });
            }
            list.sort(function(a,b){ return b.warPoints - a.warPoints; });
            return list;
        }

        function computePlayerPrestigeBoard(){
            var files = directory(BotWarsUtils.SAVE_DIR + "*.json") || [];
            var list = [];
            for(var i=0;i<files.length;i++){
                var p = BotWarsUtils.loadJSON(files[i]);
                if(!p) continue;
                var prestige = 0;
                prestige += (p.bossVictories||0) * 1000;
                prestige += ((p.permBonuses && p.permBonuses.maxHealth) || 0) + ((p.permBonuses && p.permBonuses.firewall) || 0) + ((p.permBonuses && p.permBonuses.hacking) || 0);
                prestige += (p.level||0) * 10;
                list.push({ name:p.name, level:p.level||0, prestige:prestige, bossVictories:p.bossVictories||0 });
            }
            list.sort(function(a,b){ return b.prestige - a.prestige; });
            return list;
        }

        function updateLeaderboards(){
            BotWarsUtils.ensureSaveDir();
            var data = { updated:(new Date()).toISOString(), guilds: computeGuildLeaderboard(), players: computePlayerPrestigeBoard() };
            BotWarsUtils.saveJSON(LB_PATH, data);
            return { ok:true, msg:"Leaderboards updated." };
        }

        function getLeaderboards(){
            var d = BotWarsUtils.loadJSON(LB_PATH);
            if(!d){ updateLeaderboards(); d = BotWarsUtils.loadJSON(LB_PATH); }
            return d;
        }

        function showLeaderboards(){
            var d = getLeaderboards();
            clearScreen();
            BotWarsUtils.printColor("=== GUILD WAR POINTS ===","magenta");
            for(var i=0;i<Math.min(10,(d.guilds||[]).length);i++){
                var g = d.guilds[i];
                BotWarsUtils.printColor((i+1)+". "+g.name+" - Points: "+g.warPoints+" - Treasury: "+g.treasury+" - Members: "+g.members, "yellow");
            }
            BotWarsUtils.printColor("\r\n=== PLAYER PRESTIGE ===","magenta");
            for(var j=0;j<Math.min(10,(d.players||[]).length);j++){
                var p = d.players[j];
                BotWarsUtils.printColor((j+1)+". "+p.name+" - Prestige: "+p.prestige+" - Level: "+p.level+" - Bosses: "+p.bossVictories, "cyan");
            }
            console.print("\r\nPress Enter...");
            console.getstr();
        }

        return { updateLeaderboards:updateLeaderboards, getLeaderboards:getLeaderboards, showLeaderboards:showLeaderboards };
    })();
}