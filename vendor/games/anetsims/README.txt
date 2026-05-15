===============================================================================
                       A - N E T   S I M S   v 1.0
                  A BBS Sysop Simulation Game in CP437 ANSI
                       by StingRay - A-Net Online BBS
===============================================================================
  Build your BBS from a single-line 300-baud XT into a 16-node T1
  powerhouse.  Buy hardware, install doors and CD-ROMs, sign up for
  FidoNet and the Internet, hack other sysops (real players - saved
  profiles on your system) for warez and cash, and keep the FBI off
  your back.

  Pure CP437 ANSI, Door32.sys, double-buffered differential renderer.
  Windows 32-bit and Linux 64-bit binaries included.
===============================================================================

-------------------------------------------------------------------------------
  G A M E   L O O P
-------------------------------------------------------------------------------

  You start with:

    * XT 8088, 640 KB RAM, 20 MB hard drive, 300 baud modem, CGA monitor
    * Spitfire BBS, no networks, one phone line, one node
    * $1,500 starting cash

  Each "Run the BBS" day advances time by one BBS day and simulates a
  day of callers based on your rig, your software library, and your
  networks.  Monthly bills hit on day 1 of every BBS month for Fidonet,
  Internet, and phone-line rental.

  Turn cap: 30 BBS days per real calendar day by default (configurable
  in the sysop config to 10, 20, 30, 50, 75, or unlimited 100).  You
  come back the next day with a fresh pool of turns.

  Win condition: Fame 5, $100,000+ cash, 25,000+ callers, near-top CPU
  and modem tiers, and max FidoNet + Internet.  You can keep playing
  for records after winning.

-------------------------------------------------------------------------------
  M A I N   C O N S O L E
-------------------------------------------------------------------------------

  Top chrome bar: persistent Day / Cash / Rep / Heat stats
  Inside the box:
      Your BBS + node count + user count
      Prominent CASH / DAY / REP / HEAT / FAME / TURNS strip

      [Hardware] panel:   CPU, RAM, HDD + %used, Modem, Monitor,
                          peripherals count, doors count, CD-ROMs,
                          hardware health
      [Networks] panel:   FidoNet tier, Internet tier, warez files,
                          nodes, callers yesterday + total, hack skill,
                          fame stars

      [Command Menu]:
          [1] Run the BBS (advance 1 day)
          [2] Computer Store
          [3] Software Store
          [4] Phone Company (networks)
          [5] Hack the Underground
          [6] View Statistics
          [7] Rename your BBS
          [8] Buy another node
          [9] Save game
          [Q] Save and quit

-------------------------------------------------------------------------------
  C O M P U T E R   S T O R E   -   "Byte Me Computing"
-------------------------------------------------------------------------------

  Hardware you can buy:

    CPUs         XT 8088 | 286 | 386SX | 386DX | 486SX | 486DX2 |
                 Pentium 90 | P166 MMX | Pentium II 300 | Pentium III
                 600
    RAM          640K | 1M | 4M | 8M | 16M | 32M | 64M | 128M | 256M
    HDDs         20M | 40M | 120M | 340M | 500M | 1G | 2G | 4G | 10G |
                 40G
    Modems       300 | 1200 | 2400 | 9600 | 14.4K | 28.8K | 33.6K |
                 56K | ISDN | T1 leased
    Monitors     CGA | EGA | VGA | SVGA | 17" Trinitron
    Peripherals  Sound Blaster, tape backup, flatbed scanner,
                 CD-ROM drive, UPS battery, dot-matrix printer

  Faster CPUs require more RAM (the catalog warns you with NEEDS RAM).

-------------------------------------------------------------------------------
  S O F T W A R E   S T O R E   -   "Big John's Software Emporium"
-------------------------------------------------------------------------------

  Doors & Games (16 titles):
    LORD, TradeWars 2002, Usurper, BRE, Global Wars, Food Fight,
    Pimp Wars, Planets TEOS, Red Dragon Quest III, Operation Overkill
    II, Legend of the Red Dragon II, Swords of Chaos, Murder Motel,
    Fall of Atlantis, BBS Wordle, Elderwood.

  CD-ROM Collections (9 titles):
    Cica Shareware, Walnut Creek GIFs, Night Owl, Id DOOM WADs,
    Fractint, Slackware 0.99, MP3 Rockers, Sci-Fi Mags, Adult Vol.7.
    Requires a CD-ROM drive.

  BBS Software Packages (15 titles):
    Spitfire, Synchronet, Mystic BBS, Wildcat!, WWIV, Searchlight,
    Renegade, A-NetBBS, Telegard, TriBBS, Ezycom, Maximus/CBCS,
    ProBoard, RemoteAccess, PCBoard.

  Protection Software (10 titles):
    McAfee, Norton, Personal Firewall, ZoneAlarm, VPN Tunnel, PGP,
    IDS, Offsite Backups, Legal Defence Fund, Hardened BBS Shell.
    Each cuts the attack frequency and damage you take from hacks.

  Repair Protection:
    Protection items lose health with every attack.  Repair costs scale
    with how broken they are - cheap for minor wear, serious money if
    an item is in the red.

-------------------------------------------------------------------------------
  P H O N E   C O M P A N Y
-------------------------------------------------------------------------------

  FidoNet tiers:  Not connected | Point | Node | Hub | Zone Coordinator
  Internet tiers: None | UUCP Email | Usenet+FTP | 56K dedicated | T1

  One-time signup fee + monthly billing auto-deducted every 30 days of
  play.  Each network tier bumps your caller count and reputation.

-------------------------------------------------------------------------------
  T H E   P I R A T E   C O V E
-------------------------------------------------------------------------------

  Eight corporate targets of escalating difficulty:

    Local Kid's BBS     diff 15   $50-$200
    Used-Car Dealer     diff 25   $120-$500
    University Server   diff 40   $300-$1.2K
    MegaSoft Research   diff 55   $800-$2.5K
    InterTel Corp       diff 65   $1.5K-$4.2K
    Dept. of Records    diff 75   $3K-$7.5K
    US Military Annex   diff 85   $6K-$15K
    Globotech DC        diff 92   $12K-$32K

  [P] Hack Other Sysops:
      Scans data/sysop_*.dat on the host and lets you hack every OTHER
      real sysop who has played this game on your system.  Difficulty
      is derived from THEIR installed protection, BBS software, fame,
      and hack skill.  A successful hack subtracts cash and warez from
      their save file and marks "you were hacked" on their next login.

  [D] Dark Market (viruses / hack kits):
      Password lists, wardialers, script kits, rootkits, trojan
      builders, zero-day exploit packs.  Buying them bumps your hack
      skill / warez library at the cost of raising your heat.

  Hack mini-game:
      Four reels of rolling digits.  Press SPACE to lock each reel on
      the TARGET digit.  Miss three times and the trace catches you.

-------------------------------------------------------------------------------
  R A N D O M   E V E N T S
-------------------------------------------------------------------------------

  Every "Run the BBS" may trigger an event.  Probability and damage
  scale with your player level and your installed protection:

    *  FBI raid (when heat > 70 and level >= 3)
    *  Hardware failure (when HW health is low)
    *  Rival sysop retaliation hack - blocked by your protection
       stack, otherwise damage is scaled down proportionally
    *  Virus outbreak - same anti-virus mitigation
    *  User donation
    *  Hacker magazine practice (+1 hack skill)
    *  FidoNet stranger warez drop
    *  FidoNews praise
    *  VIP caller (plays the modem handshake animation)

  Protection software BOTH lowers the bad-event roll window AND rolls
  a dice save when an attack triggers.  A fully-hardened sysop sees
  maybe 2-3% of days with damage, vs 25-30% bare-metal.

-------------------------------------------------------------------------------
  S Y S O P   C O N F I G
-------------------------------------------------------------------------------

  For BBS sysops only - run from the server's shell:

      Linux:    ./anetsims --config
      Windows:  anetsims.exe --config    (or --cfg  or -c)

  The config tool is ONLY reachable from the command line - the title
  menu that BBS callers see never exposes a [5] Config option, so your
  users can't wander into sysop controls regardless of what BBS comm
  mode the door is running in (socket / stdin+stdout / local).

  Config menu:
      [1] Rename BBS
      [2] Rename Sysop name
      [3] Change turns per day (10/20/30/50/75/100)
      [4] Change difficulty (Easy / Normal / Hard)
      [5] User / profile editor - list every saved sysop, delete one
      [6] Reset current BBS (wipes back to fresh)
      [7] Export scores.ans + scores.asc on demand
      [8] Dot Matrix Printer Log (see below)

  The sysop config edits the profile belonging to the local account
  ($USER on Linux, %USERNAME% on Windows).  To edit another sysop's
  profile, run the door once with their dropfile, exit, then run
  --config.

-------------------------------------------------------------------------------
  D O T   M A T R I X   P R I N T E R
-------------------------------------------------------------------------------

  Four report options in the config menu:

      [1] Daily log      -> data/log_day.ans + log_day.asc
      [2] Weekly log     -> data/log_week.ans + log_week.asc
      [3] Monthly log    -> data/log_month.ans + log_month.asc
      [4] All-time log   -> data/log_alltime.ans + log_alltime.asc

  Each report shows sysop / BBS / day / cash / rep / heat / fame /
  callers / hardware / networks / hack-skill stats plus a timestamp.

  When you pick a period, an OKI MICROLINE 320 dot-matrix printer is
  drawn on-screen with the print head sliding back and forth and the
  fanfold paper filling line by line with a typewriter cursor.  Runs
  about 8 seconds regardless of how quickly the file saves, for pure
  1980s vibes.

  The .ans files use CP437 block chars for tractor feed holes and
  perforated edges - drop them into your BBS menu system as a
  DISPLAY file.  The .asc files are plain-ASCII equivalents for
  terminals that don't do CP437.

-------------------------------------------------------------------------------
  S A V E S   &   S C O R E S
-------------------------------------------------------------------------------

  Profiles:        data/sysop_<name>.dat     (one per user)
  Leaderboard:     data/topsysops.dat        (binary)
  Score exports:   data/scores.ans + scores.asc
                   (auto-written whenever the leaderboard changes)
  Log exports:     data/log_day[.ans|.asc], log_week, log_month,
                   log_alltime  (written by the dot matrix printer)

  The leaderboard dedupes per sysop - renaming your BBS mid-game
  doesn't create duplicate rows.

-------------------------------------------------------------------------------
  H A C K I N G   O T H E R   L O C A L   P L A Y E R S
-------------------------------------------------------------------------------

  The "[P] Hack Other Sysops" target list is built by scanning every
  data/sysop_*.dat file on the host.  If two sysops on the same BBS
  both play A-Net SIMS, they can hack each other's save files:

      1. You select a rival from the list (shows their user name,
         BBS title, fame level, protection count, computed difficulty,
         and steal amount).
      2. You run the lock-the-reels mini-game.
      3. On a success:
          - Cash and warez are DEDUCTED from the target's save file
          - The target's "pending_attacks" counter is incremented
          - You gain the cash + warez + hack skill + 1 rep
      4. On failure the rival traces you: rep -3, heat + some

  When the hacked player next logs in, they see a "Someone hacked
  you" modal listing how many attacks were run while they were away,
  and the damage is already applied to their save.

-------------------------------------------------------------------------------
  B U I L D   F R O M   S O U R C E
-------------------------------------------------------------------------------

    Linux 64-bit:    ./build_linux.sh     -> build/anetsims
    Windows 32-bit:  ./build_mingw.sh     -> build/anetsims.exe


-------------------------------------------------------------------------------
  R U N   U N D E R   B B S
-------------------------------------------------------------------------------

  Synchronet:    anetsims %f       (DOOR32.SYS) (Windows-socket) (Linux-standard no echo)
  Mystic:        anetsims %Pdoor32.sys     (D3) 
  (With Mystic or any other BBS software that does not start from the door directory, you
   will need to make a bat file to go to the door game directory first)
 
  								
  ENiGMA 1/2:    cmd: anetsims
                 dropFileType: DOOR32
  Local test:    ./anetsims --local  or anetsims.exe --local     (no dropfile)

-------------------------------------------------------------------------------
  F I L E S
-------------------------------------------------------------------------------

    src/anetsims.c          whole door source in one file
    build/anetsims          Linux binary (prebuilt)
    build/anetsims.exe      Windows binary (prebuilt)
    build_linux.sh          Linux build script
    build_mingw.sh          Windows cross-compile script
    readme.txt              this file
    file_id.diz             BBS file-listing description
    data/                   runtime - created on first play

-------------------------------------------------------------------------------
  C R E D I T S
-------------------------------------------------------------------------------

  Concept, code, art:     StingRay - A-Net Online BBS

  BBS connections:
      telnet://bbs.a-net.online:1337
      ssh://bbs.a-net.online:1338
  Web:  https://a-net.online

  Happy Sysopping.  May your HDD be large and your heat be low.
===============================================================================
