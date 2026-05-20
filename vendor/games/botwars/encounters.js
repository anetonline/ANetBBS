// encounters.js - random encounter engine (bots attack)
load(js.exec_dir + "botwars_utils.js");
load(js.exec_dir + "bot_attacks.js");
load(js.exec_dir + "guilds.js");

if (typeof BW_Encounters === 'undefined') {
    var BW_Encounters = (function(){

        function simulateEncounter(player, threatLevel) {
            var severity = Math.max(1, Math.floor(threatLevel + Math.random()*3));
            var res = BotAttacks.simulateBotAttack(player, severity);
            if (player.health > 0) {
                var exp = Math.floor(severity * 10); // Increased from 5
                BotAttacks.addExperience(player, exp);
                if (player.guildId) {
                    BWGuilds.awardGuildPoints(player.guildId, Math.floor(exp/5), "Repelled bot wave");
                }
            }
            return res;
        }

        return {
            simulateEncounter: simulateEncounter
        };
    })();
}