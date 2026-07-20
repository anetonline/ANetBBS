// ─── Villager NPCs: home/work commute schedule + mood ──────────────────────
// Not a full per-person needs-meter simulation (no hunger/energy/social
// stats) — each villager has a home, optionally a job, follows a simple
// daily commute (BFS-pathed along the road network, not random wandering),
// and shows a small mood indicator derived from their home's happiness and
// employment status. That's a meaningful step up from pure decoration
// without pretending to be a full agent-based life sim.

const MAX_NPCS = 60;
const NPC_SPEED = 0.05; // tiles per animation frame (~3 tiles/sec at 60fps)
const SKIN_TONES = ['#e8b98a', '#c98f5f', '#8a5a3a', '#f2d0a4', '#6b4530'];
const SHIRT_COLORS = ['#c0524f', '#4f7fc0', '#4fae6e', '#c0a04f', '#8a5ac0', '#c06fa0', '#5fa0ae'];

// Original given/family names for villager flavor text — not drawn from
// any real person or copied name list, just simple invented syllables.
const NPC_FIRST_NAMES = [
  'Wren', 'Bramble', 'Oat', 'Sage', 'Fern', 'Clover', 'Hazel', 'Reed',
  'Marigold', 'Juniper', 'Thistle', 'Poppy', 'Basil', 'Rowan', 'Elm',
  'Birch', 'Cricket', 'Dandelion', 'Maple', 'Willow', 'Barley', 'Plum',
  'Sorrel', 'Flax', 'Moss', 'Heather', 'Aster', 'Linden', 'Yarrow', 'Quill'
];
const NPC_LAST_NAMES = [
  'Meadowlight', 'Cobblestone', 'Furrow', 'Hollyhock', 'Brambleton',
  'Millwright', 'Oakhurst', 'Larkspur', 'Cornwell', 'Haybury',
  'Winnow', 'Thatcher', 'Fallowfield', 'Brookbend', 'Pasturely',
  'Stonebrook', 'Wickfield', 'Grainger', 'Sunmeadow', 'Ashgrove'
];
function randomNpcName() {
  const f = NPC_FIRST_NAMES[Math.floor(Math.random() * NPC_FIRST_NAMES.length)];
  const l = NPC_LAST_NAMES[Math.floor(Math.random() * NPC_LAST_NAMES.length)];
  return `${f} ${l}`;
}

// Commute schedule as fractions of one in-game day.
const PHASE_TO_WORK_END = 0.42;
const PHASE_AT_WORK_END = 0.55;
const PHASE_TO_HOME_END = 0.92;

let npcs = [];

// Hit-test for click-to-inspect: nearest NPC within maxDist WORLD pixels
// of the given world-pixel point (caller already applied camera zoom/pan
// to get here — see screenToWorldPx in main.js), or null if none close
// enough. Picks the closest one rather than the first match so tightly
// clustered villagers (e.g. several idling near the same house) resolve
// sensibly instead of whichever happened to be first in the array.
function findNpcNear(worldPx, worldPy, maxDist = 11) {
  let best = null, bestDist = maxDist;
  for (const npc of npcs) {
    const px = npc.x * TILE_SIZE + TILE_SIZE / 2;
    const py = npc.y * TILE_SIZE + TILE_SIZE / 2;
    const d = Math.hypot(worldPx - px, worldPy - py);
    if (d <= bestDist) { best = npc; bestDist = d; }
  }
  return best;
}

function randomRoadTileNear(state, hx, hy, maxSearch = 8) {
  const visited = new Set([`${hx},${hy}`]);
  let frontier = [[hx, hy]];
  for (let depth = 0; depth < maxSearch; depth++) {
    const next = [];
    for (const [x, y] of frontier) {
      const t = tileAt(state, x, y);
      if (t && t.type === 'road') return [x, y];
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const nx = x + dx, ny = y + dy, key = `${nx},${ny}`;
        if (!visited.has(key) && tileAt(state, nx, ny)) {
          visited.add(key);
          next.push([nx, ny]);
        }
      }
    }
    frontier = next;
  }
  return null;
}

// BFS over road tiles only, from one road tile to another. Capped so a big
// disconnected map can't cause a slow search; returns null if unreachable
// (or too far) rather than hanging.
function findRoadPath(state, x0, y0, x1, y1, maxExplore = 1500) {
  if (x0 === x1 && y0 === y1) return [[x0, y0]];
  const startKey = `${x0},${y0}`;
  const cameFrom = new Map([[startKey, null]]);
  let frontier = [[x0, y0]];
  let explored = 0;
  while (frontier.length > 0 && explored < maxExplore) {
    const next = [];
    for (const [x, y] of frontier) {
      explored++;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const nx = x + dx, ny = y + dy, key = `${nx},${ny}`;
        if (cameFrom.has(key)) continue;
        const t = tileAt(state, nx, ny);
        if (!t || t.type !== 'road') continue;
        cameFrom.set(key, `${x},${y}`);
        if (nx === x1 && ny === y1) {
          // Reconstruct path.
          const path = [[nx, ny]];
          let cur = key;
          while (cameFrom.get(cur) !== null) {
            cur = cameFrom.get(cur);
            const [cx, cy] = cur.split(',').map(Number);
            path.push([cx, cy]);
          }
          return path.reverse();
        }
        next.push([nx, ny]);
      }
    }
    frontier = next;
  }
  return null;
}

function pickJobFor(state, usedJobCounts) {
  const jobs = [];
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      // Farms are workplaces too (a "farmer" -- see resolveFarmerHarvests()
      // in simulation.js) but, like their own build description says,
      // need no power/water -- unlike shop/factory/mall, which do.
      const isWorkplace =
        ((t.type === 'shop' || t.type === 'factory' || t.type === 'mall') && t.powered && t.watered) ||
        t.type === 'farm';
      if (isWorkplace && t.jobs > 0) {
        const key = `${x},${y}`;
        const used = usedJobCounts.get(key) || 0;
        if (used < t.jobs) jobs.push([x, y]);
      }
    }
  }
  if (jobs.length === 0) return null;
  return jobs[Math.floor(Math.random() * jobs.length)];
}

function moodFor(state, homeX, homeY, hasJob) {
  const home = tileAt(state, homeX, homeY);
  const h = home ? (home.happinessLocal || 55) : 55;
  if (!hasJob && h < 60) return 'sad';
  if (h >= 65) return 'happy';
  if (h >= 40) return 'neutral';
  return 'sad';
}

function spawnNpc(state, homeX, homeY, usedJobCounts) {
  const homeRoad = randomRoadTileNear(state, homeX, homeY);
  if (!homeRoad) return null;
  const job = pickJobFor(state, usedJobCounts);
  let jobRoad = null;
  if (job) {
    jobRoad = randomRoadTileNear(state, job[0], job[1]);
    if (jobRoad) {
      const key = `${job[0]},${job[1]}`;
      usedJobCounts.set(key, (usedJobCounts.get(key) || 0) + 1);
    }
  }
  return {
    name: randomNpcName(),
    x: homeRoad[0], y: homeRoad[1],
    targetX: homeRoad[0], targetY: homeRoad[1],
    homeX, homeY, homeRoadX: homeRoad[0], homeRoadY: homeRoad[1],
    jobRoadX: jobRoad ? jobRoad[0] : null, jobRoadY: jobRoad ? jobRoad[1] : null,
    // The actual job TILE (not just the road tile alongside it) -- only
    // needed so simulateDay() can find which farm (if any) this npc
    // works and auto-harvest it once ripe. Shop/factory/mall jobs don't
    // need this (their contribution is counted by scanning the grid
    // directly in countJobs(), not by walking the npc list).
    // Tied to jobRoad (not just `job`) so this always agrees with hasJob
    // below -- a job tile that pickJobFor() found but that has no
    // reachable road within randomRoadTileNear()'s search radius (e.g. a
    // farm dropped somewhere isolated) should count as no job at all,
    // not a job with no commute: otherwise resolveFarmerHarvests() would
    // still auto-harvest for an npc everything ELSE in the game (mood,
    // commute, the town-log/employment story) treats as jobless.
    jobX: jobRoad ? job[0] : null, jobY: jobRoad ? job[1] : null,
    hasJob: !!jobRoad,
    path: [], pathIndex: 0,
    phase: 'atHome', // atHome | toWork | atWork | toHome
    mood: moodFor(state, homeX, homeY, !!jobRoad),
    skin: SKIN_TONES[Math.floor(Math.random() * SKIN_TONES.length)],
    shirt: SHIRT_COLORS[Math.floor(Math.random() * SHIRT_COLORS.length)],
    walkPhase: Math.random() * Math.PI * 2,
    idleTicks: Math.floor(Math.random() * 40)
  };
}

// Rebuild the NPC population to roughly match current town population,
// capped at MAX_NPCS. Called after each simulation day-tick, which also
// refreshes job assignments and mood (home happiness may have changed).
function resyncNpcs(state) {
  const desired = Math.min(MAX_NPCS, Math.ceil(state.population / 2));
  const usedJobCounts = new Map();
  // Seed with EXISTING npcs' job assignments -- without this, a job
  // slot's real capacity (t.jobs) was only checked against spawns made
  // in THIS SAME call, not against npcs already actively working there
  // from a previous call. Population growth across multiple days could
  // then quietly double-book a job: most visible on a Farm Plot (jobs:1)
  // where a 2nd/3rd "farmer" NPC could end up pointing at an
  // already-staffed tile (resolveFarmerHarvests() in simulation.js
  // happens to no-op the redundant harvest since the first one already
  // reset farmStage, but the extra NPCs still wastefully cluster on one
  // tile while other zones nearby sit unstaffed).
  for (const npc of npcs) {
    if (npc.jobX == null || npc.jobY == null) continue;
    const key = `${npc.jobX},${npc.jobY}`;
    usedJobCounts.set(key, (usedJobCounts.get(key) || 0) + 1);
  }

  // Refresh mood for existing NPCs — home happiness may have changed.
  for (const npc of npcs) {
    npc.mood = moodFor(state, npc.homeX, npc.homeY, npc.hasJob);
  }

  if (npcs.length < desired) {
    const homes = [];
    for (let y = 0; y < GRID_H; y++) {
      for (let x = 0; x < GRID_W; x++) {
        const t = state.grid[y][x];
        if (t.type === 'house' && t.population > 0) homes.push([x, y]);
      }
    }
    if (homes.length === 0) return;
    while (npcs.length < desired) {
      const [hx, hy] = homes[Math.floor(Math.random() * homes.length)];
      const n = spawnNpc(state, hx, hy, usedJobCounts);
      if (n) npcs.push(n);
      else break;
    }
  } else if (npcs.length > desired) {
    npcs.length = desired;
  }
}

function startCommute(state, npc, destX, destY) {
  const path = findRoadPath(state, Math.round(npc.x), Math.round(npc.y), destX, destY);
  if (path && path.length > 1) {
    npc.path = path;
    npc.pathIndex = 1; // index 0 is current position
    npc.targetX = path[1][0];
    npc.targetY = path[1][1];
  } else {
    npc.path = [];
  }
}

function pickWanderTarget(state, npc) {
  const cx = Math.round(npc.x), cy = Math.round(npc.y);
  const options = [];
  for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    const t = tileAt(state, cx + dx, cy + dy);
    if (t && t.type === 'road') options.push([cx + dx, cy + dy]);
  }
  if (options.length === 0) return;
  const [nx, ny] = options[Math.floor(Math.random() * options.length)];
  npc.targetX = nx;
  npc.targetY = ny;
}

function updateNpcPhase(state, npc, dayFraction) {
  const wantPhase =
    dayFraction < PHASE_TO_WORK_END ? (npc.hasJob ? 'toWork' : 'atHome') :
    dayFraction < PHASE_AT_WORK_END ? (npc.hasJob ? 'atWork' : 'atHome') :
    dayFraction < PHASE_TO_HOME_END ? (npc.hasJob ? 'toHome' : 'atHome') :
    'atHome';

  if (wantPhase === npc.phase) return;
  npc.phase = wantPhase;

  if (wantPhase === 'toWork' && npc.hasJob) {
    startCommute(state, npc, npc.jobRoadX, npc.jobRoadY);
  } else if (wantPhase === 'toHome') {
    startCommute(state, npc, npc.homeRoadX, npc.homeRoadY);
  }
}

function advanceAlongPath(npc) {
  if (npc.path.length > 0 && npc.pathIndex < npc.path.length) {
    const [nx, ny] = npc.path[npc.pathIndex];
    npc.targetX = nx;
    npc.targetY = ny;
    return true;
  }
  return false;
}

function updateNpcs(state, dt, dayFraction) {
  for (const npc of npcs) {
    updateNpcPhase(state, npc, dayFraction);

    const dx = npc.targetX - npc.x, dy = npc.targetY - npc.y;
    const dist = Math.hypot(dx, dy);
    if (dist < 0.03) {
      npc.x = npc.targetX; npc.y = npc.targetY;
      if (npc.path.length > 0 && npc.pathIndex < npc.path.length - 1) {
        npc.pathIndex++;
        advanceAlongPath(npc);
      } else if (npc.path.length > 0) {
        npc.path = []; // arrived at destination
      } else if (npc.phase === 'atHome' || npc.phase === 'atWork') {
        // Idle wandering near home/work.
        if (npc.idleTicks > 0) npc.idleTicks--;
        else { pickWanderTarget(state, npc); npc.idleTicks = Math.floor(Math.random() * 50) + 20; }
      }
    } else {
      const step = NPC_SPEED * dt;
      const ratio = Math.min(1, step / dist);
      npc.x += dx * ratio;
      npc.y += dy * ratio;
      npc.walkPhase += dt * 0.25;
    }
  }
}
