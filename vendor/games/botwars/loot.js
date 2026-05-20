// loot.js - Boss loot tables, rare drops, and guild perks definitions
load(js.exec_dir + "botwars_utils.js");

if (typeof BW_Loot === 'undefined') {
    var BW_Loot = (function() {
        var items = [
            {id:"patch_rare", name:"Rare Security Patch", type:"consumable", value:200},
            {id:"firewall_mod", name:"Firewall Module mkII", type:"module", value:1000},
            {id:"honeypot", name:"Deceptive Honeypot", type:"module", value:800}
        ];

        var guildPerksCatalog = {
            "vault_protection": { name: "Vault Protection", description: "Reduces chance of successful raids against your guild bank.", cost: 2000 },
            "raid_bonus": { name: "Raid Bonus", description: "Members gain small bonus to raid success when attacking other guilds.", cost: 2000 },
            "market_access": { name: "Market Access", description: "Guild shop discounts and marketplace access.", cost: 1500 }
        };

        function randomFromArray(arr) {
            return arr[Math.floor(Math.random()*arr.length)];
        }

        function generateLoot(bossLevel) {
            var gold = Math.floor(bossLevel * (300 + Math.random()*700));
            var chance = Math.random();
            var drop = null;
            if (chance > 0.85) {
                drop = randomFromArray(items);
            } else if (chance > 0.995) {
                drop = {id:"legendary_core", name:"Legendary Core", type:"legendary", value:5000};
            }
            var perkToken = null;
            if (Math.random() > 0.997) {
                var keys = Object.keys(guildPerksCatalog);
                var k = keys[Math.floor(Math.random()*keys.length)];
                perkToken = {perkId: k, spec: guildPerksCatalog[k]};
            }
            return {gold:gold, item:drop, perkToken:perkToken};
        }

        function applyGuildPerk(guildId, perkId) {
            var path = BotWarsUtils.DATA_DIR + "guilds/" + guildId + ".json";
            var g = BotWarsUtils.loadJSON(path);
            if (!g) return {ok:false, msg:"Guild not found"};
            if (!g.perks) g.perks = {};
            if (!guildPerksCatalog[perkId]) return {ok:false, msg:"Invalid perk"};
            if (g.perks[perkId]) return {ok:false, msg:"Perk already unlocked"};
            var cost = guildPerksCatalog[perkId].cost;
            if (g.treasury < cost) return {ok:false, msg:"Not enough in guild treasury"};
            g.treasury -= cost;
            g.perks[perkId] = {appliedAt:(new Date()).toISOString(), info:guildPerksCatalog[perkId]};
            BotWarsUtils.saveJSON(path, g);
            return {ok:true, msg:"Perk applied", perk: g.perks[perkId]};
        }

        return {
            generateLoot: generateLoot,
            guildPerksCatalog: guildPerksCatalog,
            applyGuildPerk: applyGuildPerk
        };
    })();
}