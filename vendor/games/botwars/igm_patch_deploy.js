// Standalone IGM: Patch Deploy
load(js.exec_dir + "botwars_utils.js");
load(js.exec_dir + "igms.js");

(function run() {
    BotWarsUtils.printColor("=== PATCH DEPLOY (IGM) ===", "magenta");
    var seqLen = 3 + Math.floor(Math.random()*3);
    var seq = [];
    for (var i=0;i<seqLen;i++) seq.push(Math.floor(Math.random()*4)+1);
    BotWarsUtils.printColor("Watch the sequence:", "cyan");
    for (var j=0;j<seq.length;j++) {
        BotWarsUtils.printColor("Indicator: " + seq[j], "yellow");
        mswait(550);
        console.clear();
        mswait(200);
    }
    console.print("Enter sequence (e.g., 123): ");
    var s = console.getstr(30);
    if (!s) { BotWarsUtils.printColor("No input.", "red"); return; }
    var ok = true;
    if (s.length !== seq.length) ok = false;
    for (var k=0;k<seq.length && ok;k++) if (parseInt(s[k]) !== seq[k]) ok=false;
    if (ok) BotWarsUtils.printColor("Patch deployed successfully!", "green"); else BotWarsUtils.printColor("Deployment failed.", "red");
})();