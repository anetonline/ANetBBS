// market.js - Player marketplace for listings and buying/selling
load(js.exec_dir + "botwars_utils.js");

if (typeof BW_Market === 'undefined') {
    var BW_Market = (function(){
        var MARKET_FILE = BotWarsUtils.DATA_DIR + "market.json";

        function loadMarket() {
            var db = BotWarsUtils.loadJSON(MARKET_FILE) || { listings: [] };
            if (!db.listings) db.listings = [];
            return db;
        }
        function saveMarket(db) { BotWarsUtils.saveJSON(MARKET_FILE, db); }

        function showListings(areaFilter) {
            var db = loadMarket();
            var out = [];
            for (var i=0;i<db.listings.length;i++) {
                var l = db.listings[i];
                if (!areaFilter || l.area === areaFilter) out.push(l);
            }
            return out;
        }

        function openMarket(player, areaId) {
            while (bbs.online && !js.terminated) {
                clearScreen();
                BotWarsUtils.printColor("=== MARKETPLACE (" + (areaId||"global") + ") ===", "magenta");
                var listings = showListings(areaId);
                if (listings.length === 0) BotWarsUtils.printColor("No listings currently.", "yellow");
                for (var i=0;i<listings.length;i++) {
                    var l = listings[i];
                    BotWarsUtils.printColor("(" + (i+1) + ") " + l.item + " x" + l.qty + "  Price:" + l.price + "g  Seller:" + l.seller + " (id:" + l.id + ")", "cyan");
                }
                BotWarsUtils.printColor("(C)reate listing  (B)uy listing  (R)emove my listing  (0)Back", "yellow");
                console.print("\nChoice: ");
                var ch = console.getstr(3);
                if (!ch) continue;
                if (ch === '0') return;
                if (ch.toUpperCase() === 'C') {
                    createListingUI(player, areaId);
                    continue;
                } else if (ch.toUpperCase() === 'B') {
                    console.print("Enter listing number to buy: ");
                    var idx = parseInt(console.getstr(3)) - 1;
                    if (isNaN(idx) || idx < 0 || idx >= listings.length) { BotWarsUtils.printColor("Invalid.", "red"); console.getstr(); continue; }
                    buyListing(player, listings[idx]);
                    continue;
                } else if (ch.toUpperCase() === 'R') {
                    removeListingUI(player);
                    continue;
                } else {
                    BotWarsUtils.printColor("Invalid choice.", "red");
                }
            }
        }

        function createListingUI(player, areaId) {
            if (!player.inventory || Object.keys(player.inventory).length === 0) { BotWarsUtils.printColor("You have nothing to list.", "red"); console.getstr(); return; }
            clearScreen();
            BotWarsUtils.printColor("Your inventory:", "cyan");
            var keys = Object.keys(player.inventory);
            for (var i=0;i<keys.length;i++) BotWarsUtils.printColor("(" + (i+1) + ") " + keys[i] + " x" + player.inventory[keys[i]], "yellow");
            console.print("Choose item to list: ");
            var idx = parseInt(console.getstr(3)) - 1;
            if (isNaN(idx) || idx < 0 || idx >= keys.length) { BotWarsUtils.printColor("Invalid.", "red"); console.getstr(); return; }
            var id = keys[idx];
            console.print("Quantity to list: ");
            var qty = parseInt(console.getstr(5)) || 0;
            if (qty <= 0 || qty > player.inventory[id]) { BotWarsUtils.printColor("Invalid qty.", "red"); console.getstr(); return; }
            console.print("Price per unit: ");
            var price = parseInt(console.getstr(8)) || 0;
            if (price <= 0) { BotWarsUtils.printColor("Invalid price.", "red"); console.getstr(); return; }
            var db = loadMarket();
            var listing = {
                id: "L" + (new Date()).getTime(),
                seller: player.name,
                item: id,
                qty: qty,
                price: price,
                area: areaId || "global",
                created: (new Date()).toISOString()
            };
            db.listings.push(listing);
            player.inventory[id] -= qty;
            if (player.inventory[id] <= 0) delete player.inventory[id];
            player.save();
            saveMarket(db);
            BotWarsUtils.printColor("Listing created.", "green");
            console.getstr();
        }

        function buyListing(player, listing) {
            var total = listing.price * listing.qty;
            if (player.gold < total) { BotWarsUtils.printColor("Not enough gold.", "red"); console.getstr(); return; }
            var db = loadMarket();
            var found = -1;
            for (var i=0;i<db.listings.length;i++) if (db.listings[i].id === listing.id) { found = i; break; }
            if (found === -1) { BotWarsUtils.printColor("Listing gone.", "red"); console.getstr(); return; }
            player.gold -= total;
            player.inventory = player.inventory || {};
            player.inventory[listing.item] = (player.inventory[listing.item] || 0) + listing.qty;
            player.save();
            var seller = null;
            try { seller = (typeof Player !== 'undefined') ? Player.load(listing.seller) : null; } catch(e) { seller = null; }
            if (seller) {
                seller.gold += total;
                seller.save();
                BotWarsUtils.sendMessageToPlayer(listing.seller, "Market", "Your listing " + listing.item + " sold for " + total + " gold.");
            } else {
                BotWarsUtils.sendMessageToPlayer(listing.seller, "Market", "Your listing " + listing.item + " sold for " + total + " gold. (Not online)");
            }
            db.listings.splice(found,1);
            saveMarket(db);
            BotWarsUtils.printColor("Purchase complete.", "green");
            console.getstr();
        }

        function removeListingUI(player) {
            var db = loadMarket();
            var my = db.listings.filter(function(l){ return l.seller === player.name; });
            if (my.length === 0) { BotWarsUtils.printColor("You have no listings.", "red"); console.getstr(); return; }
            for (var i=0;i<my.length;i++) BotWarsUtils.printColor("(" + (i+1) + ") " + my[i].item + " x" + my[i].qty + " - " + my[i].price + "g", "cyan");
            console.print("Which listing to remove: ");
            var idx = parseInt(console.getstr(3)) - 1;
            if (isNaN(idx) || idx < 0 || idx >= my.length) { BotWarsUtils.printColor("Invalid.", "red"); console.getstr(); return; }
            var id = my[idx].item, qty = my[idx].qty;
            player.inventory = player.inventory || {};
            player.inventory[id] = (player.inventory[id]||0) + qty;
            player.save();
            for (var j=0;j<db.listings.length;j++) if (db.listings[j].id === my[idx].id) { db.listings.splice(j,1); break; }
            saveMarket(db);
            BotWarsUtils.printColor("Listing removed and items returned.", "green");
            console.getstr();
        }

        return {
            openMarket: openMarket
        };
    })();
}