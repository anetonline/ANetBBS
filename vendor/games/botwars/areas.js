// areas.js - Town/Areas system for Bot Wars

load(js.exec_dir + "botwars_utils.js");
load(js.exec_dir + "shop.js");
load(js.exec_dir + "market.js");
load(js.exec_dir + "bar.js");
load(js.exec_dir + "encounters.js");

if (typeof BW_Areas === 'undefined') {
    var BW_Areas = (function() {

        var AREAS = [
            { id: "downtown", name: "Downtown Market", level: 1, attackRisk: 0.2, shops: ["shop_general","shop_upgrades"], hasBar:true, hasMarket:true },
            { id: "tech_mart", name: "TechMart (Upgrades)", level: 2, attackRisk: 0.35, shops: ["shop_upgrades","shop_parts"], hasBar:false, hasMarket:true },
            { id: "arcade_row", name: "Arcade Row", level: 1, attackRisk: 0.15, shops: [], hasBar:true, hasMarket:false },
            { id: "black_market", name: "Black Market Alley", level: 4, attackRisk: 0.7, shops: ["shop_illicit"], hasBar:false, hasMarket:false },
            { id: "safe_haven", name: "Safe Haven (Low Risk)", level: 1, attackRisk: 0.05, shops: ["shop_storage"], hasBar:false, hasMarket:false }
        ];

        function getArea(id) {
            for (var i=0;i<AREAS.length;i++) if (AREAS[i].id === id) return AREAS[i];
            return null;
        }

        function listAreasForPlayer(player) {
            var out = [];
            for (var i=0;i<AREAS.length;i++) {
                var a = AREAS[i];
                if (player.level >= a.level - 1) out.push(a);
            }
            return out;
        }

        function enterTown(player) {
            while (bbs.online && !js.terminated) {
                clearScreen();
                BotWarsUtils.printColor("=== TOWN: PLACES TO VISIT ===", "magenta");
                var areas = listAreasForPlayer(player);
                for (var i=0;i<areas.length;i++) {
                    var a = areas[i];
                    var risk = Math.round(a.attackRisk*100);
                    BotWarsUtils.printColor("(" + (i+1) + ") " + a.name + "  Lvl:" + a.level + "  Risk:" + risk + "%", "cyan");
                }
                BotWarsUtils.printColor("(0) Return to main menu", "yellow");
                console.print("\nChoice: ");
                var ch = console.getstr(2);
                if (!ch) continue;
                if (ch === '0') return;
                var idx = parseInt(ch)-1;
                if (isNaN(idx) || idx < 0 || idx >= areas.length) { BotWarsUtils.printColor("Invalid selection.", "red"); console.getstr(); continue; }
                visitArea(player, areas[idx]);
            }
        }

        function visitArea(player, area) {
            while (bbs.online && !js.terminated) {
                clearScreen();
                BotWarsUtils.printColor("== " + area.name + " ==", "magenta");
                BotWarsUtils.printColor("Options:", "white");
                BotWarsUtils.printColor("(1) Visit Shops", "green");
                if (area.hasMarket) BotWarsUtils.printColor("(2) Open Marketplace", "green");
                if (area.hasBar) BotWarsUtils.printColor("(3) Go to Bar / Hangout", "green");
                BotWarsUtils.printColor("(4) Wander around (risk of attack: " + Math.round(area.attackRisk*100) + "%)", "red");
                BotWarsUtils.printColor("(0) Leave area", "yellow");
                console.print("\nChoice: ");
                var ch = console.getstr(2);
                if (!ch) continue;
                if (ch === '0') return;
                if (ch === '1') {
                    if (!area.shops || area.shops.length === 0) { BotWarsUtils.printColor("No shops here.", "red"); console.getstr(); continue; }
                    for (var s=0;s<area.shops.length;s++) BotWarsUtils.printColor("(" + (s+1) + ") " + area.shops[s], "cyan");
                    BotWarsUtils.printColor("(0) Back", "yellow");
                    console.print("Choose shop: "); var sc = console.getstr(2);
                    if (!sc) continue;
                    if (sc === '0') continue;
                    var si = parseInt(sc)-1;
                    if (isNaN(si) || si<0 || si>=area.shops.length) { BotWarsUtils.printColor("Invalid.", "red"); console.getstr(); continue; }
                    var shopId = area.shops[si];
                    BW_Shop.visitShop(player, shopId);
                    continue;
                } else if (ch === '2' && area.hasMarket) {
                    BW_Market.openMarket(player, area.id);
                    continue;
                } else if (ch === '3' && area.hasBar) {
                    BW_Bar.enterBar(player, area.id);
                    continue;
                } else if (ch === '4') {
                    var risk = area.attackRisk;
                    var roll = Math.random();
                    if (roll < risk) {
                        var res = BW_Encounters.simulateEncounter(player, area.level);
                        for (var i=0;i<res.log.length;i++) {
                            BotWarsUtils.printColor("Encounter: " + res.log[i].target + " -> " + res.log[i].effect, res.log[i].success ? "red" : "green");
                        }
                        console.print("Press Enter...");
                        console.getstr();
                    } else {
                        BotWarsUtils.printColor("You wander the area and nothing major happens.", "green");
                        if (typeof BotAttacks !== 'undefined' && typeof BotAttacks.addExperience === 'function') {
                            BotAttacks.addExperience(player, 5); // Small XP for exploring
                        }
                        console.getstr();
                    }
                    continue;
                } else {
                    BotWarsUtils.printColor("Invalid choice.", "red");
                }
            }
        }

        return {
            enterTown: enterTown,
            getArea: getArea,
            listAreasForPlayer: listAreasForPlayer
        };
    })();
}