// igms.js - IGM mini-games module (firewall, patch, code-runner)
load(js.exec_dir + "botwars_utils.js");
load(js.exec_dir + "bot_attacks.js");

if (typeof BWIGMs === 'undefined') {
    var BWIGMs = (function() {

        function drawGrid(state) {
            for (var r = 0; r < state.length; r++) {
                var line = "";
                for (var c = 0; c < state[r].length; c++) {
                    line += state[r][c] ? "\x01g[#]\x01n" : "\x01r[ ]\x01n";
                }
                console.print(line + "\r\n");
            }
        }

        function firewallPuzzle(player) {
            clearScreen();
            BotWarsUtils.printColor("=== FIREWALL PUZZLE ===", "magenta");
            BotWarsUtils.printColor("Flip nodes to neutralize the malicious pattern. Goal: turn all nodes OFF (red).", "cyan");

            var size = 5;
            var grid = [];
            for (var i=0;i<size;i++) {
                grid.push([]);
                for (var j=0;j<size;j++) grid[i][j] = Math.random() < 0.5;
            }

            var moves = 0;
            while (bbs.online && !js.terminated) {
                drawGrid(grid);
                BotWarsUtils.printColor("Moves: " + moves, "yellow");
                console.print("Enter coordinate to toggle (row,col) or Q to quit: ");
                var ans = console.getstr(10);
                if (!ans) continue;
                if (ans.toUpperCase() === 'Q') return;
                var parts = ans.split(',');
                if (parts.length !== 2) { BotWarsUtils.printColor("Invalid input.", "red"); continue; }
                var r = parseInt(parts[0]) - 1, c = parseInt(parts[1]) - 1;
                if (isNaN(r) || isNaN(c) || r<0 || c<0 || r>=size || c>=size) { BotWarsUtils.printColor("Bad coords.", "red"); continue; }

                function toggle(rr,cc) { if (rr>=0 && cc>=0 && rr<size && cc<size) grid[rr][cc] = !grid[rr][cc]; }
                toggle(r,c);
                toggle(r-1,c); toggle(r+1,c); toggle(r,c-1); toggle(r,c+1);
                moves++;

                var allOff = true;
                for (var a=0;a<size;a++) for (var b=0;b<size;b++) if (grid[a][b]) allOff = false;
                if (allOff) {
                    BotWarsUtils.printColor("Firewall neutralized in " + moves + " moves!", "green");
                    BotAttacks.addExperience(player, 100);
                    var gold = 50 + Math.floor(Math.random()*150);
                    player.gold += gold;
                    player.save();
                    BotWarsUtils.printColor("You found " + gold + " gold in the logs.", "yellow");
                    console.print("Press Enter...");
                    console.getstr();
                    return;
                }
            }
        }

        function patchDeploy(player) {
            clearScreen();
            BotWarsUtils.printColor("=== PATCH DEPLOY ===", "magenta");
            BotWarsUtils.printColor("Watch the sequence of LED indicators and repeat them correctly.", "cyan");

            var seqLen = 1 + Math.floor(player.level/5) + Math.floor(Math.random()*2);
            var seq = [];
            for (var i=0;i<seqLen;i++) seq.push(Math.floor(Math.random()*4)+1);
            for (var i=0;i<seq.length;i++) {
                BotWarsUtils.printColor("Indicator: " + seq[i], "yellow");
                mswait(450);
                console.clear();
                mswait(200);
            }
            console.print("Enter sequence (numbers without spaces): ");
            var ans = console.getstr(30);
            if (!ans) { BotWarsUtils.printColor("No input.", "red"); return; }
            var ok = true;
            if (ans.length !== seq.length) ok = false;
            for (var j=0;j<seq.length && ok;j++) if (parseInt(ans[j]) !== seq[j]) ok = false;
            if (ok) {
                BotAttacks.addExperience(player, 50);
                var gold = 25 + Math.floor(Math.random()*75);
                player.gold += gold;
                player.save();
                BotWarsUtils.printColor("Patch deployed perfectly! Reward: " + gold + " gold.", "green");
            } else {
                BotWarsUtils.printColor("Patch failed. Rollbacks initiated.", "red");
            }
            console.print("Press Enter...");
            console.getstr();
        }

        function codeRunner(player) {
            clearScreen();
            BotWarsUtils.printColor("=== CODE RUNNER ===", "magenta");
            BotWarsUtils.printColor("Type the code exactly as shown. Speed and accuracy rewarded.", "cyan");

            var snippets = [
                "rm -rf /tmp/cache",
                "ssh root@192.168.0.1 'service restart'",
                "patch --apply security_fix.diff",
                "sudo ./deploy.sh --force"
            ];
            var snippet = snippets[Math.floor(Math.random()*snippets.length)];
            console.print("\r\n" + snippet + "\r\n");
            var start = (new Date()).getTime();
            console.print("\nType now: ");
            var typed = console.getstr(snippet.length + 20);
            var elapsed = ((new Date()).getTime() - start)/1000;
            var accuracy = 0;
            for (var i=0;i<snippet.length;i++) {
                if (typed && typed[i] === snippet[i]) accuracy++;
            }
            accuracy = Math.floor((accuracy / snippet.length) * 100);
            if (typed === snippet && elapsed < 8) {
                BotAttacks.addExperience(player, 40);
                var gold = 200;
                player.gold += gold;
                player.save();
                BotWarsUtils.printColor("Perfect! Fast and accurate. +" + gold + " gold.", "green");
            } else {
                BotWarsUtils.printColor("Result -> Accuracy: " + accuracy + "%  Time: " + elapsed.toFixed(2) + "s", "yellow");
                var gold = Math.floor(accuracy/10) * 10;
                player.gold += gold;
                player.save();
                BotWarsUtils.printColor("You receive " + gold + " gold.", "green");
            }
            console.print("Press Enter...");
            console.getstr();
        }

        return {
            firewallPuzzle: firewallPuzzle,
            patchDeploy: patchDeploy,
            codeRunner: codeRunner
        };
    })();
}