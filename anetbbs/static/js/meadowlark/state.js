// ─── Game state, tile helpers, save/load ───────────────────────────────────

const SAVE_SLOTS = 3;

function makeEmptyTile() {
  return {
    type: 'grass',      // grass | road | house | shop | farm | power | water |
                         // factory | clinic | school | park | tree | statue | townhall
    level: 1,
    population: 0,      // house: current residents
    jobs: 0,             // shop/factory: jobs available (derived from level)
    growth: 0,           // house: progress toward next resident (0-100)
    farmStage: 0,        // farm: 0 = empty/growing, growDays = ripe
    plantedDay: 0,
    powered: false,
    watered: false,
    happinessLocal: 0,   // cached per-tile happiness contribution (debug/info panel)
    // These three are only ever fully recomputed by computeTileOverlays()
    // (once per simulated day), but need a sane non-undefined default from
    // the moment a tile exists — the Land Value/Crime/Traffic view-mode
    // overlays (render.js) and the info panel can read a tile before the
    // very first simulated day tick has ever run.
    landValue: 40,       // 0-100, neutral baseline
    crimeScore: 0,
    trafficNear: 0
  };
}

function newGameState() {
  const grid = [];
  for (let y = 0; y < GRID_H; y++) {
    const row = [];
    for (let x = 0; x < GRID_W; x++) row.push(makeEmptyTile());
    grid.push(row);
  }
  // Seed a town hall near the center.
  const cx = Math.floor(GRID_W / 2), cy = Math.floor(GRID_H / 2);
  grid[cy][cx] = Object.assign(makeEmptyTile(), { type: 'townhall' });

  return {
    grid,
    cash: 1500,
    day: 1,
    season: 0,           // index into SEASONS
    population: 0,
    happiness: 70,        // 0-100
    taxRate: TAX_DEFAULT,
    speed: 1,             // 0 = paused, 1 = normal, 2 = fast, 4 = very fast
    unlocked: new Set([0]), // populated properly by recomputeUnlocks()
    milestonesSeen: new Set(),
    selectedTool: 'road',
    // View mode is UI/session state, not saved (matches selectedTool's own
    // precedent below) -- always resets to 'normal' on load.
    viewMode: 'normal',   // 'normal' | 'landvalue' | 'crime' | 'traffic'
    totalPopEver: 0,
    stormDaysLeft: 0,
    droughtDaysLeft: 0,
    log: [],              // recent event/toast history
    // Rolling per-day snapshots for the Stats graph panel (ui.js/render.js),
    // capped at HISTORY_MAX_DAYS entries — see recordHistory() in
    // simulation.js. Saved/loaded like everything else above so a
    // reloaded town's graph isn't just a blank slate.
    history: []
  };
}

function tileAt(state, x, y) {
  if (x < 0 || y < 0 || x >= GRID_W || y >= GRID_H) return null;
  return state.grid[y][x];
}

function forEachInRadius(cx, cy, radius, fn) {
  for (let y = Math.max(0, cy - radius); y <= Math.min(GRID_H - 1, cy + radius); y++) {
    for (let x = Math.max(0, cx - radius); x <= Math.min(GRID_W - 1, cx + radius); x++) {
      fn(x, y);
    }
  }
}

function isRoadAdjacent(state, x, y) {
  const deltas = [[1, 0], [-1, 0], [0, 1], [0, -1]];
  for (const [dx, dy] of deltas) {
    const t = tileAt(state, x + dx, y + dy);
    if (t && t.type === 'road') return true;
  }
  return false;
}

// ── Save / Load ─────────────────────────────────────────────────────────
function serializeState(state) {
  return JSON.stringify({
    grid: state.grid,
    cash: state.cash,
    day: state.day,
    season: state.season,
    population: state.population,
    happiness: state.happiness,
    taxRate: state.taxRate,
    speed: state.speed,
    unlocked: Array.from(state.unlocked),
    milestonesSeen: Array.from(state.milestonesSeen),
    totalPopEver: state.totalPopEver,
    stormDaysLeft: state.stormDaysLeft,
    droughtDaysLeft: state.droughtDaysLeft,
    history: state.history
  });
}

// Migrate a saved grid to the current GRID_W/GRID_H if the map size has
// changed since the save was written (e.g. an older version's smaller
// world). Copies the old content into the center of a fresh, correctly
// sized grid rather than discarding it or crashing on out-of-bounds access.
function migrateGrid(oldGrid) {
  const oldH = oldGrid.length, oldW = oldGrid[0] ? oldGrid[0].length : 0;
  if (oldH === GRID_H && oldW === GRID_W) return oldGrid;

  const grid = [];
  for (let y = 0; y < GRID_H; y++) {
    const row = [];
    for (let x = 0; x < GRID_W; x++) row.push(makeEmptyTile());
    grid.push(row);
  }
  const offsetX = Math.floor((GRID_W - oldW) / 2);
  const offsetY = Math.floor((GRID_H - oldH) / 2);
  for (let y = 0; y < oldH; y++) {
    for (let x = 0; x < oldW; x++) {
      const nx = x + offsetX, ny = y + offsetY;
      if (nx >= 0 && nx < GRID_W && ny >= 0 && ny < GRID_H) {
        grid[ny][nx] = oldGrid[y][x];
      }
    }
  }
  // If the old town somehow had no Town Hall in range after the copy
  // (shouldn't normally happen — it can't be bulldozed — but a corrupted
  // or hand-edited save shouldn't be able to soft-lock the game), make
  // sure exactly one exists.
  let hasTownHall = false;
  for (let y = 0; y < GRID_H && !hasTownHall; y++) {
    for (let x = 0; x < GRID_W; x++) {
      if (grid[y][x].type === 'townhall') { hasTownHall = true; break; }
    }
  }
  if (!hasTownHall) {
    grid[Math.floor(GRID_H / 2)][Math.floor(GRID_W / 2)] = Object.assign(makeEmptyTile(), { type: 'townhall' });
  }
  return grid;
}

function deserializeState(json) {
  const data = JSON.parse(json);
  const state = newGameState();
  state.grid = migrateGrid(data.grid);
  // Farms predating the "farmers auto-harvest" feature were saved with
  // jobs:0 (the field didn't exist yet) -- backfill so existing towns'
  // farms get a farmer next time population/npcs resync, instead of
  // silently staying unstaffed until the player bulldozes and rebuilds.
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      if (t.type === 'farm' && t.jobs !== BUILDINGS.farm.baseJobs) t.jobs = BUILDINGS.farm.baseJobs;
    }
  }
  state.cash = data.cash;
  state.day = data.day;
  state.season = data.season || 0;
  state.population = data.population;
  state.happiness = data.happiness;
  state.taxRate = data.taxRate;
  state.speed = data.speed ?? 1;
  state.unlocked = new Set(data.unlocked || [0]);
  state.milestonesSeen = new Set(data.milestonesSeen || []);
  state.totalPopEver = data.totalPopEver || 0;
  state.stormDaysLeft = data.stormDaysLeft || 0;
  state.droughtDaysLeft = data.droughtDaysLeft || 0;
  state.history = data.history || []; // older saves predate this field
  return state;
}

// Save/load now goes through anetbbs/web/meadowlark.py's per-user API
// (tied to the logged-in ANetBBS account) instead of localStorage --
// serializeState()/deserializeState() above are UNCHANGED, only the
// storage backend moved. hasSave()/getSaveSummary() stay SYNCHRONOUS
// (reading a small client-side cache populated by fetchSaveSummaries())
// so the rest of the game's code — renderSlotPicker() in particular —
// didn't need to become async-aware itself; only the actual save/load
// calls (and the one-time startup fetch) are async.
//
// No export/import here on purpose: those were file-based conveniences
// from the localStorage era. Once a save is tied to your account, "export
// to a text file, re-import it" doesn't serve a real purpose it didn't
// already have (see js/main.js's bootstrap for the removed callers).
let _saveSummaries = {}; // slot -> {empty, day, population, season, updated_at}

function _csrfToken() {
  const m = document.querySelector('meta[name="csrf-token"]');
  return m ? m.getAttribute('content') : '';
}

async function fetchSaveSummaries() {
  try {
    const res = await fetch('/games/meadowlark/saves', { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    _saveSummaries = {};
    for (const s of data.saves) _saveSummaries[s.slot] = s;
  } catch (e) {
    console.error('fetchSaveSummaries failed', e);
  }
}

function hasSave(slot = 1) {
  const s = _saveSummaries[slot];
  return !!s && !s.empty;
}

// Cheap peek at a slot's day/population/season from the cached summary --
// just enough for the save-slot picker UI label. Returns null if the
// slot is empty or its summary hasn't been fetched yet.
function getSaveSummary(slot) {
  const s = _saveSummaries[slot];
  if (!s || s.empty) return null;
  return { day: s.day, population: s.population, season: s.season };
}

async function saveGame(state, slot = 1) {
  try {
    const res = await fetch(`/games/meadowlark/state/${slot}`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _csrfToken() },
      body: JSON.stringify({
        state_json: serializeState(state),
        day: state.day, population: state.population, season: state.season
      })
    });
    if (!res.ok) return false;
    await fetchSaveSummaries();
    return true;
  } catch (e) {
    console.error('Save failed', e);
    return false;
  }
}

async function loadGame(slot = 1) {
  try {
    const res = await fetch(`/games/meadowlark/state/${slot}`, { credentials: 'same-origin' });
    if (!res.ok) return null;
    const data = await res.json();
    return deserializeState(data.state_json);
  } catch (e) {
    console.error('Load failed', e);
    return null;
  }
}
