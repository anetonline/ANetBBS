// Standalone IGM: Code Runner
load(js.exec_dir + "botwars_utils.js");
load(js.exec_dir + "igms.js");

(function run() {
    BotWarsUtils.printColor("=== CODE RUNNER (IGM) ===", "magenta");
    var snippets = [
        "rm -rf /tmp/cache",
        "ssh admin@10.0.0.1 'restart'",
        "patch --apply fix_security.diff",
        "sudo ./deploy.sh --force"
    ];
    var snippet = snippets[Math.floor(Math.random()*snippets.length)];
    console.print("\r\n" + snippet + "\r\n");
    console.print("Type it exactly: ");
    var start = (new Date()).getTime();
    var typed = console.getstr(snippet.length + 60);
    var elapsed = ((new Date()).getTime() - start)/1000;
    if (typed === snippet && elapsed < 8) BotWarsUtils.printColor("Perfect! Fast and accurate.", "green");
    else BotWarsUtils.printColor("Result -> Time: " + elapsed.toFixed(2) + "s  Accuracy may vary.", "yellow");
})();