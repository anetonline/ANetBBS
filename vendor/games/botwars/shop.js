// shop.js - Shop system (device upgrades, consumables, selling items)
load(js.exec_dir + "botwars_utils.js");
load(js.exec_dir + "bot_attacks.js");

if (typeof BW_Shop === 'undefined') {
    var BW_Shop = (function(){
        var SHOPS = {
            shop_general: {
                name: "General Store",
                items: [
                    { id:"patch_common", name:"Common Patch", type:"consumable", desc:"Repairs device integrity +50", price:50 },
                    { id:"repair_kit", name:"Repair Kit", type:"consumable", desc:"Repairs device partially", price:80 },
                    { id:"drink", name:"Energy Drink", type:"consumable", desc:"Heals 20 HP", price:15 }
                ]
            },
            shop_upgrades: {
                name: "TechMart Upgrades",
                items: [
                    { id:"fw_module_1", name:"Firewall Module Mk I", type:"upgrade", desc:"+2 Firewall skill", price:300 },
                    { id:"antivirus_1", name:"Antivirus Suite", type:"upgrade", desc:"+2 Hacking defense", price:250 }
                ]
            },
            shop_parts: {
                name: "Parts & Modules",
                items: [
                    { id:"honeypot_module", name:"Honeypot Module", type:"module", desc:"Automated lure for bots", price:900 }
                ]
            },
            shop_illicit: {
                name: "Black Market Vendors",
                items: [
                    { id:"exploit_kit", name:"Exploit Toolkit", type:"illicit", desc:"+5 attack (risky)", price:1500 }
                ]
            },
            shop_storage: {
                name: "Storage & Supplies",
                items: [
                    { id:"storage_rack", name:"Storage Rack", type:"utility", desc:"Extra inventory slot (future)", price:100 }
                ]
            }
        };

        function visitShop(player, shopId) {
            var shop = SHOPS[shopId];
            if (!shop) { BotWarsUtils.printColor("Shop not found.", "red"); return; }
            while (bbs.online && !js.terminated) {
                clearScreen();
                BotWarsUtils.printColor("=== " + shop.name + " ===", "magenta");
                for (var i=0;i<shop.items.length;i++) {
                    var it = shop.items[i];
                    BotWarsUtils.printColor("(" + (i+1) + ") " + it.name + " - " + it.price + "g  " + it.desc, "cyan");
                }
                BotWarsUtils.printColor("(S)ell items to shop", "yellow");
                BotWarsUtils.printColor("(0) Leave shop", "yellow");
                console.print("\nChoice: ");
                var ch = console.getstr(3);
                if (!ch) continue;
                if (ch === '0') return;
                if (ch.toUpperCase() === 'S') {
                    sellToShop(player, shop);
                    continue;
                }
                var idx = parseInt(ch)-1;
                if (isNaN(idx) || idx < 0 || idx >= shop.items.length) { BotWarsUtils.printColor("Invalid choice.", "red"); continue; }
                buyItem(player, shop.items[idx]);
            }
        }

        function buyItem(player, item) {
            if (player.gold < item.price) { BotWarsUtils.printColor("You don't have enough gold.", "red"); console.getstr(); return; }
            player.gold -= item.price;
            player.inventory = player.inventory || {};
            player.inventory[item.id] = (player.inventory[item.id] || 0) + 1;
            if (item.id === "drink") {
                player.health = Math.min(player.maxHealth, player.health + 20);
                BotAttacks.addExperience(player, 5);
            } else if (item.type === "upgrade") {
                if (item.id === "fw_module_1") player.firewallSkill += 2;
                if (item.id === "antivirus_1") player.firewallSkill += 1;
                BotAttacks.addExperience(player, 20);
            }
            player.save();
            BotWarsUtils.printColor("Purchase successful.", "green");
            console.getstr();
        }

        function sellToShop(player, shop) {
            if (!player.inventory || Object.keys(player.inventory).length === 0) { BotWarsUtils.printColor("You have nothing to sell.", "red"); console.getstr(); return; }
            clearScreen();
            BotWarsUtils.printColor("Your inventory:", "cyan");
            var keys = Object.keys(player.inventory);
            for (var i=0;i<keys.length;i++) {
                var id = keys[i];
                var qty = player.inventory[id];
                BotWarsUtils.printColor("(" + (i+1) + ") " + id + " x" + qty, "yellow");
            }
            BotWarsUtils.printColor("(0) Back", "yellow");
            console.print("Sell which item? ");
            var ch = console.getstr(3);
            if (!ch) return;
            if (ch === '0') return;
            var idx = parseInt(ch)-1;
            if (isNaN(idx) || idx < 0 || idx >= keys.length) { BotWarsUtils.printColor("Invalid.", "red"); console.getstr(); return; }
            var sel = keys[idx];
            var qty = player.inventory[sel];
            console.print("Amount to sell (max " + qty + "): ");
            var amt = parseInt(console.getstr(5))||0;
            if (amt <= 0 || amt > qty) { BotWarsUtils.printColor("Invalid amount.", "red"); console.getstr(); return; }
            var basePrice = 50;
            var pricePer = basePrice * 0.4;
            var total = Math.floor(pricePer * amt);
            player.inventory[sel] -= amt;
            if (player.inventory[sel] <= 0) delete player.inventory[sel];
            player.gold += total;
            player.save();
            BotWarsUtils.printColor("Sold for " + total + " gold.", "green");
            console.getstr();
        }

        return {
            visitShop: visitShop
        };
    })();
}