# Bot Wars — Guilds, Server Overlord Boss, and IGM Mini‑Games - Don't let the Bots win!! We can't let AI take over the world, we need your help!

# By; Sting Ray - A-Net Online BBS 
# bbs.a-net.online  -  Telnet: 1337, SSH: 1338, RLogin:1339  -  http://a-net.fyi  -  https://a-net-online.lol

Welcome to A-Net Online's 'Bot Wars — a Synchronet door game that puts you in charge of a small network of devices, defends against persistent bot attacks, fights a shifting multi‑phase Server Overlord boss, joins or founds guilds, and plays IGM mini‑games for rewards.

This README explains how to install, configure and run Bot Wars for end users and sysops on a Synchronet BBS. It documents features, the file layout, installation steps, runtime configuration, and basic troubleshooting.

- Install location example(recommended): `/sbbs/xtrn/botwars`
- SCFG external program (run command): `?botwars.js`

---

## What's new in this update (user-level summary)

Main new systems and improvements:

- Full Guild/Clan system
  - Create/join/leave guilds, deposit/withdraw from guild treasury
  - Guild upkeep and raidability (if upkeep not paid)
  - Guild leader actions (withdraw, manage treasury)
- Server Overlord boss
  - Multi‑phase boss fight with environmental modifiers (power surges, EMP bursts, latency storms)
  - Boss phases change rules mid‑battle for extra challenge
  - Boss rewards and XP; boss victories can unlock prestige / reincarnation options
- IGM Mini‑Games (IGMs) implemented as modular functions
  - Firewall Puzzle — tile flipping logic puzzle
  - Patch Deploy — timed sequence reaction challenge
  - Code Runner — typing/accuracy challenge
- Persistent save format
  - Per player JSON save files in `botwars_saves/`
  - Guild files in `botwars_data/guilds/`
- Usability and safety
  - ANSI art support (art files under `art/`)
  - Safe, sanitized scheduler + leaderboards integration
  - Defensive code and fallbacks when optional modules are missing

---

## Files added / updated

Place the following files into the install directory (`/sbbs/xtrn/botwars`):

- `botwars.js` — main game entrypoint (loads utilities and modules)
- `botwars_utils.js` — utilities (JSON save/load, ANSI helpers, colored printing)
- `bot_attacks.js` — bot attack and boss integration (recommended)
- `guilds.js` — guild/clan implementation
- `boss.js` — Server Overlord boss logic
- `igms.js` — mini‑games module (Firewall Puzzle, Patch Deploy, Code Runner)
- `leaderboards.js` — leaderboards computations and display (optional)
- `scheduler.js` — daily maintenance runner (optional; sanitized)
- `botwars_config.json` — configuration (fees, boss tuning, file paths)
- `art/botwarintro.ans` — optional intro ANSI
- `art/botwarmain.ans` — optional main menu ANSI
- `botwars_saves/` — directory (created automatically) for player JSON files
- `botwars_data/` — directory for guilds, leaderboards, and reports

Note: Some optional modules (shop, bar, areas, etc.) may be referenced by the main script but can be safely missing; the game provides fallbacks.

---

## Installation (sysop)

1. Create install directory:
   - Recommended path: `/sbbs/xtrn/botwars`
   - Example:
     ```
     mkdir -p /sbbs/xtrn/botwars
     ```

2. Copy files into the directory:
   - Copy all `.js`, `.json`, `.ans` and `art/` files into `/sbbs/xtrn/botwars`.
   - Ensure subdirectories like `art/` exist and contain the ANS files.

3. File permissions:
   - Ensure the BBS process user (usually `scfg`/`sbbs`-owned) can read and write:
     ```
     chown -R sbbs:sbbs /sbbs/xtrn/botwars
     chmod -R 750 /sbbs/xtrn/botwars
     ```

4. Confirm directories writable:
   - `botwars_saves/` and `botwars_data/` must be writable by the BBS process. The scripts will create them automatically if BotWarsUtils is present.

5. Configure in Synchronet SCFG — External Programs
   - Add a new Door/External Program record and set:
     - Description: `Bot Wars`
     - Command: `?botwars.js`

- SCFG - External Programs - Online Programs (Doors)


                         Bot Wars 
╠══╠════════════════════════════════════════════════════════╣
║  │Name                       Bot Wars                     ║
║  │Internal Code              BOTWARS                      ║
║  │Start-up Directory         ../xtrn/botwars              ║
║  │Command Line               ?botwars.js                  ║
║  │Clean-up Command Line                                   ║
║  │Execution Cost             None                         ║
║  │Access Requirements                                     ║
║  │Execution Requirements                                  ║
║  │Multiple Concurrent Users  Yes                          ║
║  │I/O Method                 FOSSIL or UART               ║
║  │Native Executable          No                           ║
║  │Use Shell or New Context   No                           ║
║  │Modify User Data           No                           ║
║  │Execute on Event           No                           ║
║  │Pause After Execution      No                           ║
║  │Disable Local Display      No                           ║
║  │BBS Drop File Type         None                         ║


6. Optional: schedule daily maintenance
   - `scheduler.js` can be run by a Synchronet event or a cron job. The scheduler updates leaderboards and executes upkeep checks:
     - Example Synchronet event or cron: run `javascript /sbbs/xtrn/botwars/scheduler.js`

---

## Configuration (`botwars_config.json`)

A single JSON file contains configurable values. Example (defaults):

```json
{
  "game_name": "Bot Wars",
  "version": "0.2.0",
  "bank": {
    "daily_fee": 20,
    "raid_penalty_pct": 0.2
  },
  "guild": {
    "default_upkeep": 500,
    "raid_window_hours": 24
  },
  "boss": {
    "base_level_offset": 40,
    "phase_thresholds": [0.66, 0.35]
  }
}
 

