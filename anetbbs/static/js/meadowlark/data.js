// ─── Game constants & building/zone definitions ────────────────────────────
// All original content — no copied names, art, or data from any commercial
// game. "Meadowlark Valley" is an original town + farm sim.

const TILE_SIZE = 32;
const GRID_W = 60;
const GRID_H = 45;

// Camera viewport — how many tiles are visible on screen at zoom=1. The
// world (GRID_W x GRID_H) is bigger than this; the camera pans/zooms to
// see the rest. Canvas pixel size is fixed at VIEW_TILES_W/H * TILE_SIZE.
const VIEW_TILES_W = 26;
const VIEW_TILES_H = 18;
const ZOOM_MIN = 0.5, ZOOM_MAX = 2.0;

const TICK_MS = 2600;           // one in-game day per tick at normal speed
const DAYS_PER_SEASON = 20;     // spring/summer/fall/winter
const SEASONS = ['Spring', 'Summer', 'Fall', 'Winter'];
const HISTORY_MAX_DAYS = 200;   // rolling window kept for the Stats graph panel

// Radius (Chebyshev distance) that utility/service buildings cover. No
// roads/wires needed to "connect" a house to a Power Plant/Water Tower --
// coverage is pure proximity, not a network -- which isn't obvious from
// the UI alone (reported live: a player assumed they needed to connect
// them, built houses too far away, and got stuck with unpowered/
// unwatered houses despite having built both). Was 4, which is a fairly
// small 9x9-tile coverage square; widened for a more forgiving radius,
// and see render.js's hover-radius preview for the other half of the fix
// -- showing the coverage circle before you commit cash to a placement.
const UTIL_RADIUS = 6;
const SERVICE_RADIUS = 5;
const BIG_SERVICE_RADIUS = 7; // University/Stadium — bigger "campus" footprint
// Was 3 -- too small in practice. A new player's first few houses very
// easily land outside a 3-tile square around Town Hall, leaving them
// permanently unpowered/unwatered with no visual cue why until they
// notice the red tint (reported live: "the people count is not going up
// so all I do is lose money" -- 4 houses built, none within the old
// radius, no Power Plant/Water Tower ever built, cash spiraled negative
// with zero recovery path). Bigger radius = more forgiving for a
// naturally-spread-out first few builds, not just a perfectly optimized
// dense cluster.
const TOWNHALL_RADIUS = 5;

// Police Station's crime-reduction radius -- deliberately independent of
// SERVICE_RADIUS (the happiness-bonus radius other service buildings use)
// and wider, since crime coverage matters most for the "why isn't my
// Police Station helping" complaint. Declared up here (not down with the
// other crime-tuning constants below) so TOOL_RADIUS can reference it.
const CRIME_RADIUS = 7;

// Maps a build-tool key to the coverage radius it'll have once placed --
// used by render.js to draw a preview outline under the cursor while a
// radius-based tool is selected, so the coverage area is visible BEFORE
// spending cash on a placement rather than only discoverable afterward
// via the red "unpowered" tint.
const TOOL_RADIUS = {
  power: UTIL_RADIUS, water: UTIL_RADIUS,
  clinic: SERVICE_RADIUS, school: SERVICE_RADIUS, library: SERVICE_RADIUS,
  park: SERVICE_RADIUS, police: CRIME_RADIUS, firestation: SERVICE_RADIUS,
  statue: SERVICE_RADIUS,
  university: BIG_SERVICE_RADIUS, stadium: BIG_SERVICE_RADIUS,
};

// Building/zone catalog. `cost` is placement cost, `upkeep` is per-day cash
// drain, `unlock` is the population milestone required (0 = always available).
const BUILDINGS = {
  road: {
    key: 'road', name: 'Road', category: 'infra', cost: 10, upkeep: 0,
    unlock: 0, color: '#6b6459', desc: 'Connects zones. Required for growth.'
  },
  house: {
    key: 'house', name: 'House', category: 'zone', cost: 60, upkeep: 1,
    unlock: 0, color: '#c9a06b',
    desc: 'Residential zone. Grows population when powered, watered, and road-connected.',
    baseCap: 4, levelCap: [4, 9, 16]
  },
  shop: {
    key: 'shop', name: 'Shop', category: 'zone', cost: 90, upkeep: 2,
    unlock: 0, color: '#7fb2c9',
    desc: 'Commercial zone. Provides jobs and boosts tax income.',
    baseJobs: 4, levelJobs: [4, 8, 14]
  },
  farm: {
    key: 'farm', name: 'Farm Plot', category: 'zone', cost: 40, upkeep: 0,
    unlock: 0, color: '#8fae55',
    desc: 'Grows crops over a few days. Employs a farmer who harvests automatically once ripe (or click it yourself if unstaffed). No utilities needed.',
    growDays: 5, yield: 55, baseJobs: 1
  },
  power: {
    // unlock was 10 -- a real chicken-and-egg trap: houses need power to
    // grow population, but the ONLY power source before this was Town
    // Hall's tiny TOWNHALL_RADIUS(3)-tile free bootstrap zone. Any house
    // built even slightly outside that small radius (very easy to do
    // without realizing how small it is) never grows, population never
    // reaches 10, and the Power Plant that would fix it stays locked
    // forever -- reported live as "impossible to get where you can build
    // a power plant." Core infrastructure shouldn't be population-gated
    // content; it's available from the start now, like roads/houses.
    key: 'power', name: 'Power Plant', category: 'service', cost: 400, upkeep: 8,
    unlock: 0, color: '#e0b23d',
    desc: `Supplies power in a ${UTIL_RADIUS}-tile radius.`
  },
  water: {
    key: 'water', name: 'Water Tower', category: 'service', cost: 320, upkeep: 5,
    unlock: 0, color: '#4f8fc0',
    desc: `Supplies water in a ${UTIL_RADIUS}-tile radius.`
  },
  factory: {
    key: 'factory', name: 'Factory', category: 'zone', cost: 260, upkeep: 6,
    unlock: 20, color: '#a37a5b',
    desc: 'Industrial zone. Many jobs, strong tax income, mild happiness penalty nearby.',
    baseJobs: 10, levelJobs: [10, 18]
  },
  mall: {
    key: 'mall', name: 'Mall', category: 'zone', cost: 380, upkeep: 8,
    unlock: 100, color: '#c98fb2',
    desc: 'Big commercial zone. Lots of jobs and strong tax income, needs power/water/road like any other zone.',
    baseJobs: 20, levelJobs: [20, 30]
  },
  clinic: {
    key: 'clinic', name: 'Clinic', category: 'service', cost: 300, upkeep: 6,
    unlock: 30, color: '#e37b7b',
    desc: `Boosts happiness in a ${SERVICE_RADIUS}-tile radius.`
  },
  library: {
    key: 'library', name: 'Library', category: 'service', cost: 220, upkeep: 4,
    unlock: 75, color: '#7b9ac9',
    desc: `Boosts happiness in a ${SERVICE_RADIUS}-tile radius.`
  },
  school: {
    key: 'school', name: 'School', category: 'service', cost: 380, upkeep: 7,
    unlock: 50, color: '#8a7bc9',
    desc: `Boosts happiness and growth rate in a ${SERVICE_RADIUS}-tile radius.`
  },
  university: {
    key: 'university', name: 'University', category: 'service', cost: 650, upkeep: 12,
    unlock: 150, color: '#5a4a9c',
    desc: `A bigger campus — strong happiness and growth-rate boost in a ${BIG_SERVICE_RADIUS}-tile radius.`
  },
  park: {
    key: 'park', name: 'Park', category: 'service', cost: 120, upkeep: 2,
    unlock: 50, color: '#5fae6e',
    desc: `Boosts happiness in a ${SERVICE_RADIUS}-tile radius.`
  },
  police: {
    key: 'police', name: 'Police Station', category: 'service', cost: 340, upkeep: 6,
    unlock: 30, color: '#4f6fa0',
    desc: `Lowers crime in a ${CRIME_RADIUS}-tile radius, plus a small town-wide reduction for every station you build.`
  },
  firestation: {
    key: 'firestation', name: 'Fire Station', category: 'service', cost: 340, upkeep: 6,
    unlock: 30, color: '#b0453f',
    desc: `Lowers fire risk in a ${SERVICE_RADIUS}-tile radius.`
  },
  tree: {
    key: 'tree', name: 'Tree', category: 'deco', cost: 15, upkeep: 0,
    unlock: 0, color: '#3f8b4a',
    desc: 'Small happiness boost to adjacent tiles. Purely decorative otherwise.'
  },
  statue: {
    key: 'statue', name: 'Monument', category: 'deco', cost: 500, upkeep: 3,
    unlock: 100, color: '#b7a97a',
    desc: 'A grand monument. Big happiness boost, town pride.'
  },
  stadium: {
    key: 'stadium', name: 'Stadium', category: 'deco', cost: 800, upkeep: 12,
    unlock: 200, color: '#4f9c6a',
    desc: `A grand civic stadium. Very big happiness boost across a ${BIG_SERVICE_RADIUS}-tile radius.`
  },
  townhall: {
    key: 'townhall', name: 'Town Hall', category: 'civic', cost: 0, upkeep: 0,
    unlock: 0, color: '#c04f4f',
    desc: `The heart of town. Small free power/water/happiness radius (${TOWNHALL_RADIUS} tiles).`
  }
};

const MILESTONES = [
  // Power Plant/Water Tower used to unlock here -- they're available from
  // the start now (see the `power` BUILDINGS entry's comment), so this is
  // just a flavor-text milestone rather than an unlock announcement.
  { pop: 10, msg: 'Population 10 — Meadowlark Valley is growing!' },
  { pop: 20, msg: 'Population 20 — Factories unlocked!' },
  { pop: 30, msg: 'Population 30 — Clinics, Police Stations, and Fire Stations unlocked!' },
  { pop: 50, msg: 'Population 50 — Schools and Parks unlocked! Houses can now grow to Level 2.' },
  { pop: 75, msg: 'Population 75 — Libraries unlocked! Shops and Factories can now grow to Level 2!' },
  { pop: 100, msg: 'Population 100 — Monuments and Malls unlocked! Meadowlark Valley is a real town now.' },
  { pop: 150, msg: 'Population 150 — Universities unlocked! Houses can now grow to Level 3. Thriving Town achieved!' },
  { pop: 200, msg: 'Population 200 — Stadiums unlocked! Malls can now grow to Level 2.' },
  { pop: 250, msg: 'Population 250 — Legendary Valley status! You built something special.' }
];

// Number-key shortcuts for the most commonly used tools — 0 is Bulldoze
// (not a real BUILDINGS entry, handled specially by the keydown listener
// in main.js) so it reads left-to-right on a keyboard the same way the
// build menu categories are ordered (zones, infra, service, deco).
const KEY_TOOL_MAP = {
  '1': 'road', '2': 'house', '3': 'shop', '4': 'farm',
  '5': 'power', '6': 'water', '7': 'factory', '8': 'park',
  '9': 'tree', '0': 'bulldoze'
};

const TAX_MIN = 0, TAX_MAX = 25, TAX_DEFAULT = 9;
// Was 4.2/2.0 -- a real balance bug, not a misunderstanding: a
// modestly-serviced mid-size town (police+fire+clinic+school+park, a
// couple shops) ran a steady net loss at the DEFAULT tax rate (9%), and
// even at 0% tax, and only turned break-even around 15%+ -- meaning the
// tax slider's bottom two-thirds of its range weren't real options for
// any town with real services, and max tax (25%) was the only way to not
// slowly go bankrupt. Reported live: "you have to have the taxes set to
// max or you will always go bankrupt." Buffed both rates ~1.75-1.8x so a
// well-run town breaks roughly even at the DEFAULT rate, comfortably
// profits at higher rates, and 0% tax is still a real (if harsh)
// austerity choice rather than the only two options being "max tax" or
// "eventual bankruptcy regardless of rate." Verified against a
// representative early/mid/large town's actual upkeep totals, not just
// spot-checked.
const RESIDENTIAL_TAX_PER_POP = 7.5;
const COMMERCIAL_TAX_PER_JOB = 3.5; // shops/factories/malls: previously described as
                                    // "boosts tax income" but never actually did —
                                    // only residential population fed the tax formula.
// Land value scales tax revenue +/-30% per tile (see computeLandValue in simulation.js).
const LAND_VALUE_TAX_MIN_MULT = 0.7, LAND_VALUE_TAX_MAX_MULT = 1.3;

// Small flat daily civic income, always present (Town Hall can't be
// bulldozed) regardless of population/tax revenue -- a safety net so a
// rough start (e.g. houses built out of Power Plant/Water Tower range,
// population stuck at 0, zero tax income) can't spiral into a permanent,
// unrecoverable negative-cash dead end with literally no way to ever earn
// money again. Reported live: a real player's town sat at population 0,
// tax maxed at 25% (which does nothing with no residents), cash steadily
// draining with no path back except starting over. Covers a handful of
// early houses' upkeep on its own and gives a stuck town a slow but real
// way to recover without the player needing to figure that out.
const TOWNHALL_SUBSIDY = 15;

// ── Crime / traffic / disaster tuning ───────────────────────────────────
// CRIME_RADIUS is declared up near TOOL_RADIUS (needed there too).
const TRAFFIC_RADIUS = 3;          // how far a house/job's commute "load" reaches onto nearby roads
const TRAFFIC_LIGHT = 6, TRAFFIC_HEAVY = 14; // load thresholds for tint + happiness penalty

const DISASTER_TYPES = {
  fire:    { label: 'Fire',    color: '#e0603d' },
  storm:   { label: 'Storm',   color: '#6f8fae' },
  drought: { label: 'Drought', color: '#c9a04f' }
};
const DISASTER_BASE_CHANCE = 0.006; // per day, before modifiers

