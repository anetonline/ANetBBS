// Standalone IGM: Firewall Puzzle (can be run directly)
load(js.exec_dir + "botwars_utils.js");
load(js.exec_dir + "igms.js");

(function run() {
    BotWarsUtils.ensureSaveDir();
    BotWarsUtils.printColor("=== FIREWALL PUZZLE (IGM) ===", "magenta");
    var size = 5;
    var grid = [];
    for (var r=0;r<size;r++) {
        grid[r] = [];
        for (var c=0;c<size;c++) grid[r][c] = Math.random() < 0.5;
    }

    function drawGrid() {
        clearScreen();
        BotWarsUtils.printColor("Firewall status (green = patched, red = infected):", "cyan");
        for (var r=0;r<size;r++) {
            var line = "";
            for (var c=0;c<size;c++) {
                line += grid[r][c] ? "\x01g[#]\x01n" : "\x01r[ ]\x01n";
            }
            console.print(line + "\r\n");
        }
    }

    var moves = 0;
    while (bbs.online && !js.terminated) {
        drawGrid();
        BotWarsUtils.printColor("Moves: " + moves, "yellow");
        console.print("Enter row,col to toggle or Q to exit: ");
        var s = console.getstr(10);
        if (!s) continue;
        if (s.toUpperCase() === 'Q') break;
        var parts = s.split(' ');
        if (parts.length !== 2) { BotWarsUtils.printColor("Bad input.", "red"); continue; }
        var rr = parseInt(parts[0])-1, cc = parseInt(parts[1])-1;
        if (isNaN(rr) || isNaN(cc) || rr<0 || cc<0 || rr>=size || cc>=size) { BotWarsUtils.printColor("Bad coords.", "red"); continue; }
        function toggle(r,c){ if (r>=0 && c>=0 && r<size && c<size) grid[r][c] = !grid[r][c]; }
        toggle(rr,cc); toggle(rr-1,cc); toggle(rr+1,cc); toggle(rr,cc-1); toggle(rr,cc+1);
        moves++;
        var allOff = true;
        for (var a=0;a<size;a++) for (var b=0;b<size;b++) if (grid[a][b]) allOff=false;
        if (allOff) {
            BotWarsUtils.printColor("Firewall neutralized in " + moves + " moves!", "green");
            break;
        }
    }
    BotWarsUtils.printColor("Exiting Firewall Puzzle.", "magenta");
})();