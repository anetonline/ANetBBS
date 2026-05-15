// boss.js - Server Overlord boss fight (multi-phase)

load(js.exec_dir + "botwars_utils.js");
load(js.exec_dir + "loot.js");

if (typeof BW_BossFight === 'undefined') {
    var BW_BossFight = (function() {

        function ServerOverlord(level) {
            this.name = "SERVER OVERLORD";
            this.level = Math.max(100, level + 40);
            this.maxHealth = this.level * 30;
            this.health = this.maxHealth;
            this.attack = this.level * 3;
            this.phase = 1;
            this.env = { latency: 0, empRounds: 0, surgeRounds: 0, latencyRounds: 0 };
        }

        function asciiArt() {
            BotWarsUtils.printColor("\r\n" +
"        .-.      ________      .-.\r\n" +
"       (   )    /  ____  \\    (   )\r\n" +
"        `-'    /__/    \\__\\    `-'\r\n" +
"         |     |  /\\/\\  |      |\r\n" +
"      .-\"\"\"-.  |  \\/\\/  |  .-\"\"\"-.\r\n" +
"     /  .--. \\ |  /__\\  | /  .--. \\\r\n" +
"    /__/    \\_\\|________|/__/    \\_\\", "red");
            BotWarsUtils.printColor("            THE SERVER OVERLORD AWAKENS", "yellow");
        }

        function randomEnvEvent(boss) {
            var roll = Math.random();
            if (roll < 0.12) {
                boss.env.surgeRounds = 1;
                return {type:"power_surge", msg:"A POWER SURGE cracks the datacenter! Healing systems offline for 1 round."};
            } else if (roll < 0.22) {
                boss.env.empRounds = 2;
                return {type:"emp", msg:"An EMP burst pulses: attack modules partially disabled for 2 rounds."};
            } else if (roll < 0.32) {
                boss.env.latency = 25;
                boss.env.latencyRounds = 2;
                return {type:"latency", msg:"A LATENCY STORM surges: actions may fail for 2 rounds."};
            } else if (roll < 0.36) {
                return {type:"blackout", msg:"BLACKOUT: An unstable power spike slams both sides!"};
            }
            return null;
        }

        function applyEnvDecay(boss) {
            if (boss.env.empRounds > 0) boss.env.empRounds--;
            if (boss.env.surgeRounds > 0) boss.env.surgeRounds--;
            if (boss.env.latencyRounds) {
                boss.env.latencyRounds--;
                if (boss.env.latencyRounds <= 0) boss.env.latency = 0;
            }
        }

        function startBossFight(player) {
            asciiArt();
            mswait(500);
            BotWarsUtils.printColor("\nYou stand before the " + "SERVER OVERLORD", "magenta");
            console.print("Press Enter to begin...");
            console.getstr();

            var boss = new ServerOverlord(player.level);
            var round = 1;
            while (boss.health > 0 && player.health > 0 && bbs.online && !js.terminated) {
                clearScreen();
                BotWarsUtils.printColor("\n=== THE BOSS FIGHT - ROUND " + round + " ===", "red");
                BotWarsUtils.printColor("Boss: " + boss.name + "  Phase: " + boss.phase + "  HP: " + boss.health + "/" + boss.maxHealth, "yellow");
                BotWarsUtils.printColor("You: " + player.name + "  HP: " + player.health + "/" + player.maxHealth + "  Level: " + player.level, "cyan");

                if (boss.phase === 1 && boss.health <= boss.maxHealth * 0.66) {
                    boss.phase = 2;
                    boss.attack = Math.floor(boss.attack * 1.4);
                    BotWarsUtils.printColor("\n[PHASE 2] Firewalls adapt - the Overlord's attack patterns change!", "magenta");
                } else if (boss.phase === 2 && boss.health <= boss.maxHealth * 0.35) {
                    boss.phase = 3;
                    boss.attack = Math.floor(boss.attack * 1.7);
                    BotWarsUtils.printColor("\n[PHASE 3] CORE RAGE - Overlord unleashes lethal subroutines!", "red");
                }

                if (Math.random() < 0.4) {
                    var ev = randomEnvEvent(boss);
                    if (ev) BotWarsUtils.printColor("\n-- " + ev.msg, "yellow");
                }

                var canHeal = boss.env.surgeRounds <= 0;
                var latencyFail = (boss.env.latency && Math.random()*100 < boss.env.latency);

                console.print("\nActions: [A]ttack  ");
                if (canHeal) console.print("[H]eal  ");
                console.print("[S]pecial  [R]etreat: ");
                var action = console.getstr(1);
                if (!action) action = "A";
                action = action.toUpperCase();

                if (latencyFail) {
                    BotWarsUtils.printColor("Your action was delayed by latency and failed!", "red");
                } else {
                    if (action === 'A') {
                        var dmg = Math.floor(Math.random() * (player.hackingSkill || player.firewallSkill) ) + 1;
                        if (boss.env.empRounds > 0) dmg = Math.floor(dmg * 0.5);
                        boss.health -= dmg;
                        BotWarsUtils.printColor("You strike the Overlord for " + dmg + " damage.", "green");
                    } else if (action === 'H' && canHeal) {
                        console.print("Heal amount (gold per HP): ");
                        var amtStr = console.getstr(5);
                        var amt = parseInt(amtStr) || 0;
                        if (amt > 0 && player.gold >= amt) {
                            player.gold -= amt;
                            player.health = Math.min(player.maxHealth, player.health + amt);
                            BotWarsUtils.printColor("You patched yourself for " + amt + " HP.", "green");
                        } else {
                            BotWarsUtils.printColor("Invalid or insufficient gold.", "red");
                        }
                    } else if (action === 'S') {
                        if (!player._specialCooldown) player._specialCooldown = 0;
                        if (player._specialCooldown > 0) {
                            BotWarsUtils.printColor("Special still recharging for " + player._specialCooldown + " rounds.", "red");
                        } else {
                            var dmg = Math.floor((Math.random() * (player.hackingSkill || 10)) + (player.hackingSkill || 10) * 2);
                            if (boss.env.empRounds > 0) dmg = Math.floor(dmg * 0.6);
                            boss.health -= dmg;
                            player._specialCooldown = 3;
                            BotWarsUtils.printColor("SPECIAL HIT! " + dmg + " damage to the Overlord!", "yellow");
                        }
                    } else if (action === 'R') {
                        if (Math.random() < 0.25) {
                            BotWarsUtils.printColor("You narrowly retreat from the Overlord's throne room!", "magenta");
                            player.save();
                            return;
                        } else {
                            BotWarsUtils.printColor("Retreat failed! The Overlord clamps down!", "red");
                        }
                    } else {
                        BotWarsUtils.printColor("Invalid action.", "red");
                    }
                }

                if (boss.health > 0) {
                    if (Math.random() < 0.08) {
                        var blackoutDmg = Math.floor(boss.attack * 0.6);
                        player.health -= blackoutDmg;
                        BotWarsUtils.printColor("BLACKOUT DAMAGE: " + blackoutDmg + " to you!", "red");
                    } else {
                        var dmg = Math.floor(Math.random() * boss.attack / 2) + Math.floor(boss.attack/4);
                        player.health -= dmg;
                        BotWarsUtils.printColor("The Overlord lashes out for " + dmg + " damage!", "red");
                    }
                }

                applyEnvDecay(boss);
                if (player._specialCooldown && player._specialCooldown>0) player._specialCooldown--;

                if (player.health <= 0) {
                    BotWarsUtils.printColor("\nYou were consumed by the Server Overlord.", "red");
                    player.health = 1;
                    player.save();
                    return;
                }
                if (boss.health <= 0) {
                    BotWarsUtils.printColor("\nTHE SERVER OVERLORD HAS BEEN DEFEATED!", "yellow");
                    var exp = Math.floor(player.level * 500);
                    var gold = Math.floor(player.level * 1000);
                    player.experience += exp;
                    player.gold += gold;
                    player.permBonuses.maxHealth = (player.permBonuses.maxHealth || 0) + 50;
                    player.permBonuses.attack = (player.permBonuses.attack || 0) + 10;
                    player.bossVictories = (player.bossVictories || 0) + 1;
                    BotWarsUtils.printColor("Rewards: EXP " + exp + "  Gold " + gold, "green");
                    player.save();
                    return;
                }

                round++;
            }
        }

        return {
            startBossFight: startBossFight
        };
    })();
}