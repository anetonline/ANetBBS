// messaging2.js - area bulletin and improved messaging helpers
load(js.exec_dir + "botwars_utils.js");
load(js.exec_dir + "bar.js");

if (typeof BW_Messaging === 'undefined') {
    var BW_Messaging = (function(){
        function postAreaMessage(areaId, author, text) {
            if (typeof BW_Bar !== 'undefined') BW_Bar.postBulletin(areaId, author, text);
            else BotWarsUtils.printColor("Area bulletin not available.", "red");
        }
        function getInbox(name) {
            var db = BotWarsUtils.loadJSON(BotWarsUtils.MESSAGE_DB) || {};
            return db[name] || [];
        }
        function sendToAllInArea(areaId, from, text) {
            if (typeof BW_Bar !== 'undefined') BW_Bar.postBulletin(areaId, from, text);
        }

        return {
            postAreaMessage: postAreaMessage,
            getInbox: getInbox,
            sendToAllInArea: sendToAllInArea
        };
    })();
}