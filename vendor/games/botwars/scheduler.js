// scheduler.js - Daily upkeep automation and leaderboard updater (sanitized)
// Writes a full report file under botwars_data/maintenance_reports/ and prints a concise summary to console.

load(js.exec_dir + "botwars_utils.js");
load(js.exec_dir + "guilds.js");
load(js.exec_dir + "leaderboards.js");

(function main() {
    BotWarsUtils.ensureSaveDir();

    var guildDir = BotWarsUtils.DATA_DIR + "guilds/";
    var guildFiles = directory(guildDir + "*.json") || [];
    var reportLines = [];
    var now = new Date();

    reportLines.push("Bot Wars Daily Maintenance Report - " + now.toISOString());
    reportLines.push("");
    reportLines.push("Guild upkeep checks:");

    for (var i = 0; i < guildFiles.length; i++) {
        try {
            var path = guildFiles[i];
            var guild = BotWarsUtils.loadJSON(path);
            if (!guild) continue;

            var result;
            if (!guild.upkeepFeeDaily) guild.upkeepFeeDaily = 500;

            if (!guild.lastUpkeepDate || guild.lastUpkeepDate !== BotWarsUtils.nowDateStr()) {
                if ((guild.treasury||0) >= guild.upkeepFeeDaily) {
                    guild.treasury -= guild.upkeepFeeDaily;
                    guild.lastUpkeepDate = BotWarsUtils.nowDateStr();
                    guild.raidableUntil = null;
                    BotWarsUtils.saveJSON(path, guild);
                    result = "Upkeep paid. Treasury now: " + guild.treasury;
                } else {
                    var until = new Date();
                    until.setHours(until.getHours() + 24);
                    guild.raidableUntil = until.toISOString();
                    BotWarsUtils.saveJSON(path, guild);
                    result = "Insufficient funds. Guild bank is now raidable until " + guild.raidableUntil;
                }

                var leader = guild.leader || (guild.members && guild.members[0]) || "unknown";
                BotWarsUtils.sendMessageToPlayer(leader, "Guild System", "Upkeep result for " + guild.name + ": " + result);
                if (guild.members && guild.members.length) {
                    for (var m = 0; m < guild.members.length; m++) {
                        BotWarsUtils.sendMessageToPlayer(guild.members[m], "Guild System", "Daily upkeep for " + guild.name + ": " + result);
                    }
                }
            } else {
                result = "Already paid today.";
            }

            reportLines.push(guild.name + ": " + result);
        } catch (e) {
            reportLines.push("Error processing a guild during upkeep (check server logs).");
            try { log(LOG_ERR, "BotWars scheduler error processing guild file: " + (e && e.toString ? e.toString() : e)); } catch (x) {}
        }
    }

    reportLines.push("");
    reportLines.push("Leaderboards:");

    try {
        if (typeof Leaderboards !== 'undefined' && typeof Leaderboards.updateLeaderboards === 'function') {
            var lbRes = Leaderboards.updateLeaderboards();
            if (lbRes && lbRes.ok) reportLines.push("Leaderboards updated.");
            else reportLines.push("Leaderboards update attempted.");
        } else {
            reportLines.push("Leaderboards module not available.");
        }
    } catch (e) {
        reportLines.push("Leaderboards update failed (check server logs).");
        try { log(LOG_ERR, "BotWars scheduler leaderboard update failed: " + (e && e.toString ? e.toString() : e)); } catch (x) {}
    }

    // Save the maintenance report
    var reportPathDir = BotWarsUtils.DATA_DIR + "maintenance_reports/";
    try { if (!file_isdir(reportPathDir)) mkdir(reportPathDir); } catch (e) {}
    var reportFileName = "report_" + (new Date()).getTime() + ".txt";
    var reportFullPath = reportPathDir + reportFileName;
    try {
        var f = new File(reportFullPath);
        if (f.open("w")) {
            f.write(reportLines.join("\r\n"));
            f.close();
            reportLines.push("");
            reportLines.push("Maintenance report created.");
        } else {
            reportLines.push("");
            reportLines.push("Failed to save maintenance report (check server permissions).");
        }
    } catch (e) {
        try { if (f && f.isopen) f.close(); } catch (x) {}
        reportLines.push("");
        reportLines.push("Failed to save maintenance report (exception).");
        try { log(LOG_ERR, "BotWars scheduler error saving maintenance report: " + (e && e.toString ? e.toString() : e)); } catch (x) {}
    }

    // Print concise report to console (no internal paths)
    try { console.print("\r\n" + reportLines.join("\r\n") + "\r\n"); } catch (e) { try { log(LOG_ERR, "BotWars scheduler console print failed: " + (e && e.toString ? e.toString() : e)); } catch(x) {} }
})();