// map.js - world map and travel system
// NOTE: map.js no longer loads areas.js to avoid circular load. It expects BW_Areas to already be available.
load(js.exec_dir + "botwars_utils.js");

if (typeof BW_Map === 'undefined') {
    var BW_Map = (function(){
        function showMap(player) {
            clearScreen();
            BotWarsUtils.printColor("=== WORLD MAP ===", "magenta");
            var areas = ["downtown","tech_mart","arcade_row","black_market","safe_haven"];
            for (var i=0;i<areas.length;i++) {
                var a = (typeof BW_Areas !== 'undefined') ? BW_Areas.getArea(areas[i]) : null;
                if (!a) continue;
                BotWarsUtils.printColor("(" + (i+1) + ") " + a.name + " - Lvl req: " + a.level + "  Risk:" + Math.round(a.attackRisk*100) + "%", "cyan");
            }
            BotWarsUtils.printColor("(0) Back", "yellow");
            console.print("\nChoice: "); var ch = console.getstr(2);
            if (!ch) return;
            if (ch === '0') return;
            var idx = parseInt(ch)-1;
            if (isNaN(idx) || idx < 0 || idx >= areas.length) return;
            var area = BW_Areas.getArea(areas[idx]);
            if (!area) return;
            var cost = Math.max(10, Math.floor(area.level * 20));
            BotWarsUtils.printColor("Travel to " + area.name + " will cost " + cost + " gold. Proceed? [Y/N]", "yellow");
            var yn = console.getstr(1);
            if (yn && yn.toUpperCase() === 'Y') {
                if (player.gold >= cost) {
                    player.gold -= cost;
                    player.save();
                    BW_Areas.visitArea(player, area);
                } else BotWarsUtils.printColor("Not enough gold.", "red");
            }
        }

        function travelTo(player, areaId) {
            var area = (typeof BW_Areas !== 'undefined') ? BW_Areas.getArea(areaId) : null;
            if (!area) { BotWarsUtils.printColor("Unknown area.", "red"); return; }
            var cost = Math.max(10, Math.floor(area.level * 20));
            if (player.gold < cost) { BotWarsUtils.printColor("Not enough gold.", "red"); return; }
            player.gold -= cost; player.save();
            BW_Areas.visitArea(player, area);
        }

        return {
            showMap: showMap,
            travelTo: travelTo
        };
    })();
}