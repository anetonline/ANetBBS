// ─── ANetDarkForces: constants & content definitions ────────────────────────
// All original content — no copied names, art, sounds, level layouts, or
// mechanics from Doom, Duke Nukem 3D, Wolfenstein 3D, or any other
// commercial game. First-person raycasting engine in the spirit of that
// genre (textured walls, billboarded sprite enemies), built from scratch.
//
// Premise: a crime syndicate calling itself the Dark Forces has been
// raiding and hoarding computer stores, trying to starve out independent
// BBSes (like this one) by cutting off the supply of hardware parts. You
// play a sysop who suits up and raids their hideouts to reclaim parts and
// keep the board alive. Nerd-meets-action-hero, not sci-fi/horror.

const TILE = 64; // world units per grid cell (also the procedural texture size)
const FOV = Math.PI / 3; // 60 degrees
const RENDER_W = 640, RENDER_H = 400; // internal render resolution (canvas is scaled up via CSS)
const NUM_RAYS = RENDER_W; // one ray per column — no supersampling, keeps it fast on plain 2D canvas
const MAX_DEPTH = TILE * 20;
const HALF_FOV = FOV / 2;
const ANGLE_STEP = FOV / NUM_RAYS;

// Speed constants are all in TILES PER SECOND -- the one consistent unit
// convention used everywhere in this codebase (enemy .speed in data.js,
// projectile .speed, these player constants): actual per-frame movement
// is always `speed * TILE * dt` (dt = real elapsed seconds since last
// frame), standard physics integration. Nothing here is a "per frame at
// 60fps" magic number that needs its own separate scaling logic anywhere
// else.
const MOVE_SPEED = 3.2;
const STRAFE_SPEED = 2.8;
const TURN_SPEED = 2.4;      // radians/sec via keyboard
const PLAYER_RADIUS = 18;    // world units, for wall collision
const SPRINT_MULT = 1.6;     // Shift-held speed multiplier

// Jump has no real z-axis effect (this is a flat-height raycaster, same
// genre limitation classic Wolfenstein/Doom-style engines have) -- it's a
// cosmetic camera hop for feedback only, not a platforming mechanic.
const JUMP_BOB_DURATION = 0.45; // seconds
const JUMP_BOB_HEIGHT = 26;     // screen pixels at the peak

// ── Wall types (procedurally textured — see textures.js) ──
const WALL_TYPES = {
  1: { name: 'Cinderblock', base: '#7a7266', mortar: '#5a5448' },
  2: { name: 'Steel Shutter', base: '#5f6870', mortar: '#3a4148' },
  3: { name: 'Shelving Unit', base: '#8a5a3a', mortar: '#5a3a20' },
  4: { name: 'Server Rack', base: '#2a3a4a', mortar: '#1a2430' },
  5: { name: 'Neon Sign Wall', base: '#3a2050', mortar: '#e05fd0' },
  6: { name: 'Loading Dock', base: '#6a6050', mortar: '#3a3428' },
  7: { name: 'Security Door', base: '#4a3a1a', mortar: '#e0b23d' },
  8: { name: 'Reinforced Panel', base: '#7a7266', mortar: '#5a5448' }, // secret wall -- texture is aliased to Cinderblock in textures.js so it's visually indistinguishable
  9: { name: 'Ammo Dispenser', base: '#1c3a4a', mortar: '#5fd6ff' },
  10: { name: 'Vault Door', base: '#3a1a1a', mortar: '#ff4f4f' },
};
const DOOR_TYPE = 7; // treated as a normal wall by collision/raycasting until interactWithDoor() opens it (see entities.js)
const SECRET_TYPE = 8; // functions exactly like a door, but disguised and never shown as an obvious "this opens" hazard-stripe panel
const AMMO_STATION_TYPE = 9; // interactable, never opens -- tops up ammo instead (see interactWithAmmoStation in entities.js)
const LOCKED_DOOR_TYPE = 10; // like DOOR_TYPE, but interactWithDoor() requires a matching key in player.keys first

// Ammo stations top each owned ammo type UP TO these thresholds (never
// down) -- a "fill up if low" station rather than a flat bonus, so
// revisiting one you've already topped off does nothing.
const AMMO_REFILL_THRESHOLDS = { shells: 12, cells: 6, cores: 3, rounds: 40, scopes: 6 };
const AMMO_STATION_COOLDOWN = 25; // seconds before the same station can be used again

// Hard ammo caps -- without these, stacking enough pickups/dispenser
// visits lets any ammo type grow unbounded, which undercuts the actual
// resource-management tension the genre depends on. All comfortably
// above the refill-station thresholds above.
const AMMO_MAX = { shells: 40, cells: 20, cores: 10, rounds: 120, scopes: 15 };

// How long (seconds) an enemy keeps chasing/attacking its last known
// target position after losing direct line of sight, before giving up
// and reverting to idle -- without this, anything that's ever spotted
// you tracks your live position forever, through walls, which reads as
// artificial rather than a real patrol/guard reacting to a threat.
const ENEMY_MEMORY_DURATION = 5;

// Kamikaze drones telegraph their detonation instead of exploding the
// instant they're in range -- a beep-and-arm window gives the player a
// real (if brief) chance to retreat out of blast radius, matching how
// proximity-triggered hazards read as fair in the genre rather than a
// cheap instant-death gotcha.
const KAMIKAZE_ARM_DURATION = 0.5;

// Barrel chain-reaction blast stats, named instead of inlined magic
// numbers in entities.js's explodeBarrel().
const BARREL_EXPLOSION_DAMAGE = 45;
const BARREL_EXPLOSION_RADIUS_MULT = 2; // world radius = TILE * this

// ── Weapon viewmodel (on-screen gun) tuning ──
const WEAPON_BOB_SPEED = 8;      // radians/sec of bob-cycle advance while moving
const WEAPON_BOB_AMOUNT_Y = 10;  // px vertical bob at full stride
const WEAPON_BOB_AMOUNT_X = 6;   // px horizontal sway at full stride
const WEAPON_KICK_DURATION = 0.12; // seconds the recoil kick takes to settle
const WEAPON_KICK_HEIGHT = 14;     // px upward kick on fire

// Footstep cadence, seconds between steps at normal speed (scaled down
// by SPRINT_MULT while sprinting so footfalls speed up realistically).
const FOOTSTEP_INTERVAL = 0.38;

// ── RPG progression ──
const XP_PER_LEVEL = [0, 40, 100, 180, 280, 400, 540, 700, 880, 1080]; // cumulative XP needed for level N (index = level-1)
const LEVEL_UP_HEALTH_BONUS = 15;
const LEVEL_UP_DAMAGE_MULT = 0.08; // +8% weapon damage per level

// ── Weapons (original names, retro-hacker-hero flavor) ──
const WEAPONS = {
  solder: {
    key: 'solder', name: 'Solder Gun', kind: 'hitscan',
    damage: 8, fireRate: 0.14, spread: 0.02, ammoType: null, // infinite -- starting weapon
    range: TILE * 10, sfx: 'shootLight',
    desc: 'Your trusty starting sidearm. Rapid-fire, unlimited charge.'
  },
  static: {
    key: 'static', name: 'Static Shotgun', kind: 'hitscan-spread',
    damage: 7, pellets: 6, fireRate: 0.7, spread: 0.14, ammoType: 'shells',
    range: TILE * 4.5, sfx: 'shootHeavy',
    desc: 'Wide discharge cone. Devastating up close, useless at range.'
  },
  emp: {
    key: 'emp', name: 'EMP Launcher', kind: 'projectile',
    damage: 26, splashRadius: TILE * 1.6, speed: 9, fireRate: 0.9, ammoType: 'cells',
    range: TILE * 12, sfx: 'shootEmp',
    desc: 'Slow-moving charge, big splash. Watch your distance.'
  },
  overclock: {
    key: 'overclock', name: 'Overclock Cannon', kind: 'projectile',
    damage: 55, splashRadius: TILE * 2.2, speed: 7, fireRate: 1.3, ammoType: 'cores',
    range: TILE * 14, sfx: 'shootUltimate',
    desc: 'The good stuff. Rare ammo, room-clearing payoff.'
  },
  multitool: {
    key: 'multitool', name: 'Multitool', kind: 'melee',
    damage: 16, fireRate: 0.45, range: TILE * 1.15, ammoType: null, // infinite -- starting backup weapon
    sfx: 'meleeSwing',
    desc: 'Your trusty wrench. Never runs dry, never scores from across the room.'
  },
  packetspray: {
    key: 'packetspray', name: 'Packet Spray', kind: 'hitscan',
    damage: 5, fireRate: 0.08, spread: 0.05, ammoType: 'rounds',
    range: TILE * 7, sfx: 'shootSmg',
    desc: 'High rate of fire, low damage per hit. Spray and pray.'
  },
  debugger: {
    key: 'debugger', name: 'Long-Range Debugger', kind: 'hitscan',
    damage: 42, fireRate: 1.1, spread: 0.006, ammoType: 'scopes',
    range: TILE * 18, sfx: 'shootSniper',
    desc: 'One shot, one bug fixed. Slow to cycle, brutal at range.'
  },
};
const WEAPON_ORDER = ['solder', 'multitool', 'packetspray', 'static', 'debugger', 'emp', 'overclock'];

// ── Enemies (original names — cartoonish goons, not gore) ──
const ENEMY_TYPES = {
  scalper: {
    key: 'scalper', name: 'Scalper', hp: 18, speed: 1.6, xp: 8,
    kind: 'melee', damage: 6, attackRange: TILE * 0.7, attackRate: 0.9,
    color: '#c9a04f', sightRange: TILE * 8,
    desc: 'Fast, weak, swarms in packs.'
  },
  goon: {
    key: 'goon', name: 'Store Goon', hp: 34, speed: 1.1, xp: 14,
    kind: 'ranged', damage: 9, attackRange: TILE * 7, attackRate: 1.4,
    projectileSpeed: 7, color: '#8a5a5a', sightRange: TILE * 9,
    desc: 'Keeps its distance, plinks you with a stun-gun.'
  },
  guard: {
    key: 'guard', name: 'Security Guard', hp: 60, speed: 0.85, xp: 22,
    kind: 'melee', damage: 14, attackRange: TILE * 0.8, attackRate: 1.1,
    color: '#4f6fa0', sightRange: TILE * 7,
    desc: 'Tanky. Hits hard up close.'
  },
  tech: {
    key: 'tech', name: 'Rogue Tech', hp: 40, speed: 1.0, xp: 18,
    kind: 'ranged', damage: 12, attackRange: TILE * 8, attackRate: 1.7,
    projectileSpeed: 8, color: '#5aa06a', sightRange: TILE * 10,
    desc: 'Erratic movement, ranged EMP pops.'
  },
  drone: {
    key: 'drone', name: 'Overclocked Drone', hp: 12, speed: 2.6, xp: 10,
    kind: 'kamikaze', damage: 22, attackRange: TILE * 0.7, attackRate: 999,
    splashRadius: TILE * 1.3, color: '#e0603d', sightRange: TILE * 9,
    desc: 'Malfunctioning delivery bot. Rushes you and detonates on contact -- keep your distance or outrun it.'
  },
  turret: {
    key: 'turret', name: 'Security Camera Turret', hp: 30, speed: 0, xp: 16,
    kind: 'turret', damage: 10, attackRange: TILE * 10, attackRate: 1.2,
    projectileSpeed: 9, color: '#c9c0b0', sightRange: TILE * 11,
    desc: 'Wall-mounted and immobile, but it never misses a beat once it spots you.'
  },
  shieldtech: {
    key: 'shieldtech', name: 'Riot Tech', hp: 90, speed: 0.6, xp: 30,
    kind: 'melee', damage: 16, attackRange: TILE * 0.85, attackRate: 1.3,
    color: '#5a6a7a', sightRange: TILE * 7, frontalDamageReduction: 0.7,
    desc: 'Riot shield blocks most damage head-on. Get behind it.'
  },
  boss_middleman: {
    key: 'boss_middleman', name: 'The Middleman', hp: 420, speed: 0.7, xp: 300,
    kind: 'boss', damage: 20, attackRange: TILE * 6, attackRate: 1.0,
    projectileSpeed: 8, color: '#c04f4f', sightRange: TILE * 20,
    desc: 'Big-box-store baron. Owns every store you just raided.',
    isBoss: true
  },
};

// ── Pickups ──
const PICKUP_TYPES = {
  health_small: { name: 'Spare Battery', kind: 'health', amount: 15, color: '#5fae6e' },
  health_large: { name: 'UPS Battery Pack', kind: 'health', amount: 35, color: '#5fae6e' },
  armor: { name: 'Static Vest', kind: 'armor', amount: 25, color: '#7b9ac9' },
  ammo_shells: { name: 'Discharge Cells', kind: 'ammo', ammoType: 'shells', amount: 8, color: '#e0b23d' },
  ammo_cells: { name: 'EMP Cells', kind: 'ammo', ammoType: 'cells', amount: 4, color: '#e0b23d' },
  ammo_cores: { name: 'Overclock Cores', kind: 'ammo', ammoType: 'cores', amount: 2, color: '#e0b23d' },
  ammo_rounds: { name: 'Data Rounds', kind: 'ammo', ammoType: 'rounds', amount: 30, color: '#e0b23d' },
  ammo_scopes: { name: 'Scope Charges', kind: 'ammo', ammoType: 'scopes', amount: 5, color: '#e0b23d' },
  part: { name: 'BBS Part', kind: 'part', color: '#e05fd0' }, // narrative/objective pickup, see LEVELS
  weapon_static: { name: 'Static Shotgun', kind: 'weapon', weapon: 'static', color: '#c9c0b0' },
  weapon_emp: { name: 'EMP Launcher', kind: 'weapon', weapon: 'emp', color: '#c9c0b0' },
  weapon_overclock: { name: 'Overclock Cannon', kind: 'weapon', weapon: 'overclock', color: '#c9c0b0' },
  weapon_packetspray: { name: 'Packet Spray', kind: 'weapon', weapon: 'packetspray', color: '#c9c0b0' },
  weapon_debugger: { name: 'Long-Range Debugger', kind: 'weapon', weapon: 'debugger', color: '#c9c0b0' },
  key_red: { name: 'Red Access Card', kind: 'key', keyId: 'red', color: '#ff4f4f' },
  key_blue: { name: 'Blue Access Card', kind: 'key', keyId: 'blue', color: '#5fd6ff' },
  key_gold: { name: 'Gold Access Card', kind: 'key', keyId: 'gold', color: '#e0b23d' },
};

const PLAYER_START_STATE = {
  hp: 100, maxHp: 100, armor: 0, maxArmor: 100,
  level: 1, xp: 0,
  weapons: ['solder', 'multitool'],
  currentWeapon: 'solder',
  ammo: { shells: 0, cells: 0, cores: 0, rounds: 0, scopes: 0 },
  parts: 0,
  keys: [],
};
