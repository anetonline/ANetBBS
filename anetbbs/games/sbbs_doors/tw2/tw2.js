// ANetBBS patch: upstream uses an exception-fileName trick to derive
// the directory of the running script (`throw barfitty.barf` triggers
// a ReferenceError whose .fileName points at this file). That breaks
// under the Node compat path because the concatenated script lives in
// a temp dir, not next to tw2's own files. Use the compat-provided
// `js.startup_dir` (set to game.synchronet_exec_dir) when present —
// fall back to the original fileName trick for real Synchronet jsexec.
//
// Top-level `var X = ...` in real Synchronet automatically goes on the
// global object; under Node's CommonJS wrap each `var` is module-local
// and invisible to subsequently load()'d files. Republish to globalThis
// explicitly so players.js / sectors.js / etc. can see startup_path,
// Commodities, Settings, player, sector, initialized, exit_tw2, on_at,
// db, i — the eight cross-file vars upstream tw2 expects to be global.
var startup_path =
    (typeof js !== 'undefined' && js && js.startup_dir)
    || (typeof js !== 'undefined' && js && js.exec_dir)
    || (function(){
        try { throw new Error('startup_path probe'); }
        catch(e) {
            var p = (e.fileName || '.')
                .replace(/[\/\\][^\/\\]*$/,'');
            return p;
        }
       })();
if (typeof backslash === 'function') startup_path = backslash(startup_path);
if (typeof globalThis !== 'undefined') globalThis.startup_path = startup_path;

var on_at=time();
if (typeof globalThis !== 'undefined') globalThis.on_at = on_at;
var i;
if (typeof globalThis !== 'undefined') globalThis.i = i;

var Commodities=[
	{
		 name:"Ore"
		,abbr:"Ore"
		,disp:"Ore......."
		,price:10
	}
	,{
		 name:"Organics"
		,abbr:"Org"
		,disp:"Organics.."
		,price:20
	}
	,{
		 name:"Equipment"
		,abbr:"Equ"
		,disp:"Equipment."
		,price:35
	}
];
// Publish cross-file shared vars to globalThis. Under real Synchronet
// these are all globals automatically; under Node's CommonJS wrap
// every `var` would be module-local without this hop.
if (typeof globalThis !== 'undefined') {
	globalThis.Commodities = Commodities;
}
var Settings;
var player=null;
var sector=null;
var initialized=null;
var exit_tw2=false;
if (typeof globalThis !== 'undefined') {
	globalThis.Settings = undefined;
	globalThis.player = null;
	globalThis.sector = null;
	globalThis.initialized = null;
	globalThis.exit_tw2 = false;
	globalThis.LOCK_WRITE = 2;
	globalThis.LOCK_READ  = 1;
}

load("json-client.js");
load(startup_path+"filename.js");
load(fname("gamesettings.js"));
var db;
// Mirror the global LOCK_* values back into module-local vars so the
// rest of this file's `LOCK_READ` references resolve. (Loaded modules
// hit globalThis.LOCK_READ via the scope chain; this file is wrapped.)
var LOCK_WRITE = globalThis.LOCK_WRITE;
var LOCK_READ  = globalThis.LOCK_READ;

// GameSettings's constructor instantiates the JSONClient and binds it
// to the global `db`. Under runInThisContext that ends up on globalThis;
// under our concatenated entry script the assignment lands module-local.
// Read it back from globalThis for both possibilities, then re-export.
Settings = new GameSettings();
if (typeof globalThis !== 'undefined') {
	globalThis.Settings = Settings;
	if (typeof globalThis.db === 'undefined' && typeof db !== 'undefined') {
		globalThis.db = db;
	}
}
db = globalThis.db;
if(db==undefined) {
	alert("ERROR: Configuration invalid");
	exit(1);
}

load(fname("ports.js"));
load(fname("planets.js"));
load(fname("teams.js"));
load(fname("sectors.js"));
load(fname("maint.js"));
load(fname("players.js"));
load(fname("messages.js"));
load(fname("computer.js"));
load(fname("input.js"));
load(fname("editor.js"));

function Menu(sector)
{
	var refresh;
	/* 22000 */
	while(1) {
		refresh=true;
		console.crlf();
		if(player.TurnsLeft==10 || player.TurnsLeft < 6) {
			console.attributes="HM";
			console.writeln("You have " + player.TurnsLeft + " turns left.");
		}
		console.attributes="HC";
		console.print("Command (?=Help)? ");
		var valid=new Array('A','C','D','E','F','G','I','L','M','P','Q','T','Z','?');
		var i;
		for(i=0; i<sector.Warps.length; i++) {
			if(sector.Warps[i]>0)
				valid.push(sector.Warps[i].toString());
		}
		var inp=InputFunc(valid);
		switch(inp) {
			case '':
				console.writeln("? = Help");
				break;
			case 'A':
				/* 25000 */
				console.writeln("<Attack>");
				AttackPlayer();
				break;
			case 'C':
				/* 33640 */
				console.writeln("<Computer>");
				ComputerMenu();
				sector=db.read(Settings.DB,'sectors.'+player.Sector,LOCK_READ);
				DisplaySector(sector,player.Sector,false,'main.ans');
				refresh=false;
				break;
			case 'D':
				console.writeln("<Display>");
				sector=db.read(Settings.DB,'sectors.'+player.Sector,LOCK_READ);
				DisplaySector(sector,player.Sector,false,'main.ans');
				refresh=false;
				continue;
			case 'E':
				if(user.level < 90)
					break;
				console.writeln("<TW Editor>");
				console.print("Do you wish to use the editor? Y/N [N] ");
				if(InputFunc(['Y','N'])=='Y') {
					console.writeln("Running Tradewars ][ Editor...");
					Editor();
				}
				break;
			case 'F':
				/* 24000 */
				console.writeln("<Drop/Take Fighters>");
				DropFighters();
				break;
			case 'G':
				/* 27500 */
				console.writeln("<Gamble>");
				PlayerGamble();
				break;
			case 'I':
				console.writeln("<Info>");
				PlayerInfo(player.Record);
				break;
			case 'L':
				/* 31000 */
				console.writeln("<Land/Create planet>");
				LandOnPlanet();
				break;
			case 'M':
				/* 23000 */
				console.writeln("<Move>");
				if(PlayerMove())
					return;
				break;
			case 'P':
				console.writeln("<Port>");
				DockAtPort();
				break;
			case 'T':
				/* 32799 */
				console.attributes="HW";
				console.writeln("<Team menu>");
				TeamMenu();
				sector=db.read(Settings.DB,'sectors.'+player.Sector,LOCK_READ);
				DisplaySector(sector,player.Sector,false,'main.ans');
				refresh=false;
				break;
			case '?':
				console.attributes="C";
				console.writeln("<Help>");
				console.crlf();
				if(user.settings&USER_ANSI) {
					sector=db.read(Settings.DB,'sectors.'+player.Sector,LOCK_READ);
					DisplaySector(sector,player.Sector,true,'main.ans');
					refresh=false;
				}
				else
					console.printfile(fname("main.asc"));
				break;

			case 'Z':
				console.writeln("<Instructions>");
				Instructions();
				break;
			case 'Q':
				console.attributes="W";
				console.writeln("<Quit>");
				console.attributes="W";
				console.print("Are you sure (Y/N)? ");
				if(InputFunc(['Y','N'])=='Y') {
					exit_tw2=true;
					// ANetBBS: also publish to globalThis. Menu lives in
					// tw2.js (Node wraps the entry in CommonJS), so this
					// assignment hits the module-local var. The main loop
					// below resyncs `exit_tw2 = globalThis.exit_tw2` each
					// iteration to pick up sectors.js's setter (which runs
					// under runInThisContext and DOES land on globalThis).
					// Without the explicit publish, that resync overwrites
					// our true back to globalThis's stale false → loop
					// never exits → "Quit" returns to main menu.
					globalThis.exit_tw2=true;
					return;
				}
				break;
			default:
				if(inp.search(/^[0-9]*$/)!=-1) {
					if(MoveTo(parseInt(inp)))
						return;
				}
				break;
		}
		if(refresh)
			sector=db.read(Settings.DB,'sectors.'+player.Sector,LOCK_READ);
	}
}

function do_exit()
{
	if(player != undefined) {
		if(db.status(Settings.DB,'players').lock!=undefined)
			db.unlock(Settings.DB,'players');
		if(db.status(Settings.DB,'players.'+player.Record).lock!=undefined)
			db.unlock(Settings.DB,'players.'+player.Record);
		player.Online=false;
		if(player.Ported || player.Landed) {
			if(db.status(Settings.DB,'sectors').lock!=undefined)
				db.unlock(Settings.DB,'sectors');
			if(db.status(Settings.DB,'sectors.'+player.Sector).lock!=undefined)
				db.unlock(Settings.DB,'sectors.'+player.Sector);
			var sector=db.read(Settings.DB,'sectors.'+player.Sector,LOCK_READ);
			if(player.Ported) {
				console.writeln("Leaving the port...");
				player.Ported=false;
				if(db.status(Settings.DB,'ports').lock!=undefined);
					db.unlock(Settings.DB,'ports');
				if(db.status(Settings.DB,'ports.'+sector.Port).lock!=undefined);
					db.unlock(Settings.DB,'ports.'+sector.Port);
				db.lock(Settings.DB,'ports.'+sector.Port,LOCK_WRITE);
				port=db.read(Settings.DB,'ports.'+sector.Port);
				port.OccupiedBy=0;
				db.write(Settings.DB,'ports.'+sector.Port,port);
				db.unlock(Settings.DB,'ports.'+sector.Port);
			}
			if(player.Landed) {
				console.writeln("Launching from planet...");
				player.Landed=false;
				if(db.status(Settings.DB,'planets').lock!=undefined);
					db.unlock(Settings.DB,'planets');
				if(db.status(Settings.DB,'planets.'+sector.Planet).lock!=undefined);
					db.unlock(Settings.DB,'planets.'+sector.Planet);
				db.lock(Settings.DB,'planets.'+sector.Planet,LOCK_WRITE);
				var planet=db.read(Settings.DB,'planets.'+sector.Planet);
				planet.OccupiedCount--;
				db.write(Settings.DB,'planets.'+sector.Planet,planet);
				db.unlock(Settings.DB,'planets.'+sector.Planet);
			}
		}
		player.TimeUsed += time()-on_at;
		if(player.Put != undefined)
			player.Put();
	}
	console.writeln("Returning to Door monitor...");
	if(initialized != undefined) {
		TWRank();
	}
}

function Instructions()
{
	console.print("Do you want instructions (Y/N) [N]? ");
	if(InputFunc(['Y','N'])=='Y') {
		console.crlf();
		console.printfile(fname("twinstr.doc"), P_CPM_EOF);
	}
}

// NOTE: Caller needs to save now...
function LockedProduction(place)
{
	var newupd=time();
	var diffdays=(newupd-place.LastUpdate)/86400;

	if(diffdays>10)
		diffdays=10;
	for(i=0; i<Commodities.length; i++) {
		if(diffdays > 0) {
			place.Commodities[i] += place.Production[i]*diffdays;
			if(place.Commodities[i] > place.Production[i]*10)
				place.Commodities[i] = place.Production[i]*10;
		}
	}
	place.LastUpdate=newupd;
}

function Production(place)
{
	var newupd=time();
	var diffdays=(newupd-place.LastUpdate)/86400;

	if(diffdays>10)
		diffdays=10;
	for(i=0; i<Commodities.length; i++) {
		if(diffdays > 0) {
			place.Commodities[i] += place.Production[i]*diffdays;
			if(place.Commodities[i] > place.Production[i]*10)
				place.Commodities[i] = place.Production[i]*10;
		}
	}
	place.LastUpdate=newupd;
	place.Put();
}

function ShowOpeng()
{
	var len=db.read(Settings.DB,'twopeng.length',LOCK_READ);
	var i;
	var msg;

	// TODO: Only show "new" stuff from here...
	for(i=0; i<len; i++) {
		msg=db.read(Settings.DB,'twopeng.'+i,LOCK_READ);
		console.writeln(msg.Message);
		console.crlf();
	}
	return len;
}

function main()
{
	var today=strftime("%Y:%m:%d");

try {
	js.on_exit("do_exit()");

	console.attributes="C";
	console.crlf();
	console.crlf();
	console.center("Trade Wars (v.ii)");
	console.center("By Chris Sherrick (PTL)");
	console.center("Copyright 1986 Chris Sherrick");
	console.crlf();
	console.center(system.name);
	console.center("Sysop  "+system.operator);
	console.crlf();
	console.crlf();
	initialized = ShowOpeng();

	/*
	 * ANetBBS patch: bundle the upstream big-bang into the door itself.
	 * Real Synchronet ships a separate twint500.js setup tool that the
	 * sysop runs once via uifc. Single-BBS users shouldn't have to know
	 * about that — auto-init on first launch when the universe doesn't
	 * exist yet (initialized == undefined). The init sequence matches
	 * twint500.js:173-179 exactly:
	 *   ResetAllPlayers / ResetAllPlanets / ResetAllMessages
	 *   InitializeTeams / InitializeSectors / InitializePorts /
	 *   InitializeCabal
	 * sector_map.js and ports_map.js — the prebuilt universe adjacency
	 * tables — are only loaded by twint500 in upstream, so we load them
	 * here too. InitializeSectors and InitializeCabal both read
	 * sector_map[]; InitializePorts iterates ports_init[].
	 */
	if(initialized == undefined) {
		console.attributes="HG";
		console.writeln("");
		console.writeln("First launch — generating universe...");
		console.attributes="N";
		// Coalesce ~700 init writes into one disk flush. See json-client.js
		// for the bulk-mode policy: writes are kept in memory until
		// _endBulk, then flushed in a single pass. If we die mid-init the
		// scope is just absent and the next launch re-runs the same
		// idempotent build.
		if (db && db._beginBulk) db._beginBulk();
		try {
			load(fname("sector_map.js"));
			load(fname("ports_map.js"));
			ResetAllPlayers();
			ResetAllPlanets();
			ResetAllMessages();
			InitializeTeams();
			InitializeSectors();
			InitializePorts();
			InitializeCabal();
			db.write(Settings.DB,'twopeng',[],LOCK_WRITE);
			if (db && db._endBulk) db._endBulk();
			initialized = ShowOpeng();
			console.writeln("Universe ready. Press a key to continue.");
			console.pause();
		}
		catch(_initerr) {
			// Bail out of bulk mode without flushing — leave the scope
			// absent so the next launch retries cleanly.
			if (db && db._bulk) db._bulk = 0;
			console.attributes="R";
			console.writeln("Universe init failed: " + _initerr);
			console.writeln("Sysop: delete the tw2 db dir "
			               +"($ANETBBS_TW2_DB_DIR, default "
			               +"<install>/data/sbbs_doors/tw2/db/) "
			               +"and re-launch to retry.");
			console.pause();
			exit(0);
		}
	}

	/* Run maintenance */
	if(Settings.MaintLastRan < today) {
		RunMaint();
	}
	console.attributes="W";
	console.writeln("Initializing...");
	console.writeln("Searching my records for your name.");
	if(!LoadPlayer()) {
		console.pause();
		exit(0);
	}
	// ANetBBS patch: LoadPlayer runs in players.js's runInThisContext
	// scope where assignments to `player` land on globalThis. The entry
	// script's module-local `var player` (top of file) stays null.
	// Sync back so this function's references see the loaded record.
	// Same for `sector` and `exit_tw2` updated by EnterSector/Menu.
	player = globalThis.player;

	console.pause();
	while(player && player.KilledBy==0 && exit_tw2==false) {
		if(EnterSector()) {
			sector = globalThis.sector;
			if(CheckSector())
				Menu(sector);
		}
		// Re-sync each iteration in case Menu() or Killing logic in
		// the loaded modules updated player / exit_tw2 on globalThis.
		player = globalThis.player;
		exit_tw2 = globalThis.exit_tw2;
	}
}
catch (e) {
	log(e);
	log(e && e.stack ? e.stack : e.toSource());
	// ANetBBS: also print to stderr so the test harness sees the trace.
	try {
		process.stderr.write('\n--- tw2 caught ---\n' + (e && e.stack ? e.stack : e) + '\n');
	} catch(_) {}
	throw(e);
}
}

// ANetBBS patch: tw2.js is the entry script (Node wraps it in a CommonJS
// module), so its top-level `function` defs are module-local. The other
// .js files loaded via load() use vm.runInThisContext which DOES put
// their top-level on globalThis — so they can't see tw2.js's functions
// without this hop. Publish the ones referenced cross-file.
if (typeof globalThis !== 'undefined') {
	globalThis.Instructions = Instructions;
	globalThis.LockedProduction = LockedProduction;
}

main();
