// ─── Simulation: one call per in-game day ──────────────────────────────────

function recomputeUtilities(state) {
  // Reset, then flood coverage out from every power/water/service/townhall
  // source. O(sources * radius^2) — trivially fast at this grid size.
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      state.grid[y][x].powered = false;
      state.grid[y][x].watered = false;
    }
  }
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      if (t.type === 'power') {
        forEachInRadius(x, y, UTIL_RADIUS, (nx, ny) => { state.grid[ny][nx].powered = true; });
      } else if (t.type === 'water') {
        forEachInRadius(x, y, UTIL_RADIUS, (nx, ny) => { state.grid[ny][nx].watered = true; });
      } else if (t.type === 'townhall') {
        forEachInRadius(x, y, TOWNHALL_RADIUS, (nx, ny) => {
          state.grid[ny][nx].powered = true;
          state.grid[ny][nx].watered = true;
        });
      }
    }
  }
}

// Sum of happiness contributions from service buildings + decorations,
// minus factory penalties, at a given tile.
function localHappinessBonus(state, x, y) {
  let bonus = 0;
  const t = tileAt(state, x, y);
  forEachInRadius(x, y, SERVICE_RADIUS, (nx, ny) => {
    const s = state.grid[ny][nx];
    if (s.type === 'clinic') bonus += 3;
    else if (s.type === 'school') bonus += 3;
    else if (s.type === 'library') bonus += 2;
    else if (s.type === 'park') bonus += 2;
    else if (s.type === 'statue') bonus += 4;
    else if (s.type === 'townhall') bonus += 1;
    else if (s.type === 'police') bonus += 2;
    else if (s.type === 'firestation') bonus += 1;
  });
  // University/Stadium have a bigger campus footprint than the other
  // service buildings, so they get their own (larger) radius pass.
  forEachInRadius(x, y, BIG_SERVICE_RADIUS, (nx, ny) => {
    const s = state.grid[ny][nx];
    if (s.type === 'university') bonus += 5;
    else if (s.type === 'stadium') bonus += 6;
  });
  forEachInRadius(x, y, 1, (nx, ny) => {
    const s = state.grid[ny][nx];
    if (s.type === 'tree') bonus += 1;
    if (s.type === 'factory') bonus -= 2;
  });
  return bonus;
}

// Whether (x,y) is within a police/fire station's coverage radius.
// Radius comes from TOOL_RADIUS (data.js) -- the same lookup render.js
// uses for the hover-coverage preview -- rather than a hardcoded
// constant, so the preview shown before you place a building and the
// real in-simulation coverage can never silently diverge again. (They
// did, briefly: this used to hardcode CRIME_RADIUS for every
// buildingType, including 'firestation', which meant Fire Station's
// real disaster-coverage radius was quietly following Police's radius
// -- widened to 7 for the vandalism fix -- while its own description
// and hover preview still said 5, the moment those two radii stopped
// being equal by coincidence.)
function hasCoverage(state, x, y, buildingType) {
  const radius = TOOL_RADIUS[buildingType] || SERVICE_RADIUS;
  let covered = false;
  forEachInRadius(x, y, radius, (nx, ny) => {
    if (state.grid[ny][nx].type === buildingType) covered = true;
  });
  return covered;
}

// Whole-map count of a given building type -- used for the town-wide
// crime dampening factor (see localCrimeScore). Cheap enough to call once
// per day (not once per tile).
function countBuildingType(state, type) {
  let n = 0;
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      if (state.grid[y][x].type === type) n++;
    }
  }
  return n;
}

// Crime score (0-100ish) for a house tile: higher with unemployment and low
// happiness, reduced sharply by nearby police coverage.
function localCrimeScore(state, x, y, employmentRatio, policeCount) {
  let score = (1 - employmentRatio) * 55 + Math.max(0, 55 - state.happiness) * 0.6;
  if (hasCoverage(state, x, y, 'police')) score *= 0.35;
  // Town-wide dampening: every Police Station built anywhere in town also
  // shaves a bit off baseline crime pressure, not just the local-coverage
  // multiplier above -- so building more stations visibly helps even for
  // houses outside any single station's radius. Reported live: "vandalism
  // ... continues even after adding multiple stations" -- without this, a
  // 2nd/3rd station only ever helped the specific houses it happened to
  // cover, which in a spread-out town can look like it does nothing at
  // all. Floors at 40% of baseline so stations still aren't a complete
  // cure by themselves -- local coverage remains the bigger lever.
  const townWideFactor = Math.max(0.4, 1 - (policeCount || 0) * 0.12);
  score *= townWideFactor;
  return Math.max(0, Math.min(100, score));
}

// Land desirability (0-100) for ANY tile, including vacant grass — meant to
// help the player scout good building spots, SimCity-style, not just react
// after the fact. Boosted by nearby parks/decor/civic/education buildings,
// hurt by nearby factories and heavy traffic.
function computeLandValue(state, x, y) {
  let value = 40;
  forEachInRadius(x, y, SERVICE_RADIUS, (nx, ny) => {
    const s = state.grid[ny][nx];
    if (s.type === 'park') value += 4;
    else if (s.type === 'tree') value += 1;
    else if (s.type === 'statue') value += 6;
    else if (s.type === 'clinic') value += 2;
    else if (s.type === 'school') value += 2;
    else if (s.type === 'library') value += 2;
    else if (s.type === 'townhall') value += 3;
    else if (s.type === 'factory') value -= 5;
  });
  forEachInRadius(x, y, BIG_SERVICE_RADIUS, (nx, ny) => {
    const s = state.grid[ny][nx];
    if (s.type === 'university') value += 5;
    else if (s.type === 'stadium') value += 6;
  });
  let maxTraffic = 0;
  forEachInRadius(x, y, 2, (nx, ny) => {
    const s = state.grid[ny][nx];
    if (s.type === 'road') maxTraffic = Math.max(maxTraffic, s.trafficLoad || 0);
  });
  if (maxTraffic >= TRAFFIC_HEAVY) value -= 10;
  else if (maxTraffic >= TRAFFIC_LIGHT) value -= 4;
  return Math.max(0, Math.min(100, Math.round(value)));
}

// Recomputes the three whole-map overlay metrics (land value, crime
// pressure, nearby-traffic) used both by the info panel and by the
// Land Value/Crime/Traffic view-mode overlays in render.js. Computed for
// EVERY tile (not just built-on ones) so the overlays double as a "where
// should I build next" scouting tool, not just a status report on
// existing buildings. Must run after recomputeTraffic() (needs
// up-to-date trafficLoad) but doesn't otherwise depend on ordering.
function computeTileOverlays(state, employmentRatio) {
  const policeCount = countBuildingType(state, 'police');
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      t.landValue = computeLandValue(state, x, y);
      t.crimeScore = localCrimeScore(state, x, y, employmentRatio, policeCount);
      let maxTraffic = 0;
      forEachInRadius(x, y, 2, (nx, ny) => {
        const s = state.grid[ny][nx];
        if (s.type === 'road') maxTraffic = Math.max(maxTraffic, s.trafficLoad || 0);
      });
      t.trafficNear = maxTraffic;
    }
  }
}

// Recompute per-road-tile commute "load" — a rough heuristic, not real
// pathfinding: each road tile sums nearby population + nearby jobs within
// TRAFFIC_RADIUS. Heavier load tints the road (see render.js) and dings
// happiness for houses next to congested roads.
function recomputeTraffic(state) {
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      if (t.type !== 'road') { t.trafficLoad = 0; continue; }
      let load = 0;
      forEachInRadius(x, y, TRAFFIC_RADIUS, (nx, ny) => {
        const s = state.grid[ny][nx];
        if (s.type === 'house') load += s.population * 0.6;
        else if (s.type === 'shop' || s.type === 'factory') load += s.jobs * 0.5;
      });
      t.trafficLoad = load;
    }
  }
}

// One random disaster roll per day. Chance scales up with factory count and
// low happiness (neglect); fire severity is reduced by Fire Station coverage.
function rollDisaster(state, addLog) {
  let factoryCount = 0;
  const developed = [];
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      if (t.type === 'factory') factoryCount++;
      if (t.type === 'house' || t.type === 'shop' || t.type === 'factory') developed.push([x, y]);
    }
  }
  if (developed.length === 0) return;

  const chance = DISASTER_BASE_CHANCE * (1 + factoryCount * 0.15) *
                 (1 + Math.max(0, 40 - state.happiness) * 0.01);
  if (Math.random() > chance) return;

  const roll = Math.random();
  if (roll < 0.45) {
    // Fire: pick a random developed tile, weighted toward ones without fire coverage.
    const candidates = developed.filter(([x, y]) => !hasCoverage(state, x, y, 'firestation'));
    const pool = candidates.length > 0 ? candidates : developed;
    const [x, y] = pool[Math.floor(Math.random() * pool.length)];
    const covered = hasCoverage(state, x, y, 'firestation');
    const def = BUILDINGS[state.grid[y][x].type];
    if (covered && Math.random() < 0.7) {
      state.cash -= Math.round(def.cost * 0.1);
      addLog(`🔥 A fire broke out near a ${def.name}, but the Fire Station had it contained quickly.`, 'warning');
    } else {
      state.grid[y][x] = makeEmptyTile();
      state.cash -= Math.round(def.cost * 0.15);
      addLog(`🔥 Fire destroyed a ${def.name}! ${covered ? '' : 'A nearby Fire Station could have helped.'}`, 'warning');
    }
  } else if (roll < 0.75) {
    state.stormDaysLeft = (state.stormDaysLeft || 0) + 3;
    addLog('⛈ A storm rolled through Meadowlark Valley — happiness will dip for a few days.', 'warning');
  } else {
    state.droughtDaysLeft = (state.droughtDaysLeft || 0) + 6;
    addLog('☀ A drought has begun — farms will grow more slowly until it passes.', 'warning');
  }
}

// One random vandalism roll per day, separate from disasters — smaller,
// more frequent, and specifically tied to the crime/police system rather
// than fire/weather. Gated behind the same population milestone Police
// Stations unlock at (BUILDINGS.police.unlock) -- previously this rolled
// from day 1 regardless of population, so vandalism could (and did) start
// well before a player was even allowed to build the one thing that
// counters it. Reported live: "vandalism starts way before you can get a
// police station."
function rollCrimeEvent(state, employmentRatio, addLog) {
  if (state.population < BUILDINGS.police.unlock) return;
  const houses = [];
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      if (t.type === 'house' && t.population > 0) houses.push([x, y]);
    }
  }
  if (houses.length === 0) return;
  const [x, y] = houses[Math.floor(Math.random() * houses.length)];
  const policeCount = countBuildingType(state, 'police');
  const crime = localCrimeScore(state, x, y, employmentRatio, policeCount);
  if (Math.random() * 100 > crime * 0.12) return; // most days, nothing happens
  const loss = 20 + Math.floor(Math.random() * 40);
  state.cash -= loss;
  addLog(`🚨 A vandalism incident cost the town $${loss}. ${hasCoverage(state, x, y, 'police') ? '' : 'A Police Station nearby would help.'}`, 'warning');
}

function recomputeUnlocks(state) {
  state.unlocked = new Set([0]);
  for (const key in BUILDINGS) {
    if (BUILDINGS[key].unlock <= state.population) state.unlocked.add(BUILDINGS[key].unlock);
  }
}

function checkMilestones(state, addLog) {
  for (const m of MILESTONES) {
    if (state.population >= m.pop && !state.milestonesSeen.has(m.pop)) {
      state.milestonesSeen.add(m.pop);
      addLog(m.msg, 'milestone');
    }
  }
}

// Returns the total number of jobs available town-wide (serviced, road-
// connected shop/factory/mall tiles only) -- NOT how many are filled;
// employment/unemployment is derived elsewhere by comparing this against
// population (see employmentRatio in simulateDay()).
function countJobs(state) {
  let jobs = 0;
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      if ((t.type === 'shop' || t.type === 'factory' || t.type === 'mall') &&
          t.powered && t.watered && isRoadAdjacent(state, x, y)) {
        jobs += t.jobs;
      }
    }
  }
  return jobs;
}

function simulateDay(state, addLog) {
  recomputeUtilities(state);
  recomputeTraffic(state);

  state.day += 1;
  if (state.day % DAYS_PER_SEASON === 0) {
    state.season = (state.season + 1) % SEASONS.length;
    addLog(`A new season begins: ${SEASONS[state.season]}.`, 'season');
  }

  // Drought/storm are temporary effects from rollDisaster(), decayed here.
  const droughtActive = (state.droughtDaysLeft || 0) > 0;
  if (state.droughtDaysLeft > 0) state.droughtDaysLeft--;
  if (state.stormDaysLeft > 0) state.stormDaysLeft--;

  // ── Population growth in serviced, road-connected houses ──
  let totalPop = 0;
  const jobsAvailable = countJobs(state);

  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      if (t.type !== 'house') continue;

      const cap = BUILDINGS.house.levelCap[t.level - 1] || BUILDINGS.house.levelCap[0];
      const serviced = t.powered && t.watered && isRoadAdjacent(state, x, y);

      if (serviced && t.population < cap) {
        // Growth rate scales gently with happiness; schools/universities speed it up.
        let rate = 4 + Math.max(0, state.happiness - 40) * 0.15;
        let schoolNearby = false, universityNearby = false;
        forEachInRadius(x, y, SERVICE_RADIUS, (nx, ny) => {
          if (state.grid[ny][nx].type === 'school') schoolNearby = true;
        });
        forEachInRadius(x, y, BIG_SERVICE_RADIUS, (nx, ny) => {
          if (state.grid[ny][nx].type === 'university') universityNearby = true;
        });
        if (schoolNearby) rate *= 1.4;
        if (universityNearby) rate *= 1.3;
        t.growth += rate;
        if (t.growth >= 100) {
          t.growth = 0;
          t.population += 1;
        }
      } else if (!serviced && t.population > 0) {
        // Slow decline if utilities lost (e.g. power plant bulldozed).
        t.growth = 0;
        if (Math.random() < 0.15) t.population -= 1;
      }

      // Level up once population + happiness support it.
      if (t.level === 1 && t.population >= cap && state.population >= 50 && state.happiness >= 55) {
        t.level = 2; t.population = Math.min(t.population, BUILDINGS.house.levelCap[1]);
      } else if (t.level === 2 && t.population >= BUILDINGS.house.levelCap[1] &&
                 state.population >= 150 && state.happiness >= 65) {
        t.level = 3;
      }

      totalPop += t.population;
    }
  }

  // Shop/factory/mall level-ups once town is big enough (cosmetic + more jobs).
  // Mall's own threshold is higher since it doesn't unlock until pop 100
  // (shop/factory's 75 would fire instantly on placement otherwise).
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      if ((t.type === 'shop' || t.type === 'factory') && t.level === 1 && state.population >= 75) {
        t.level = 2;
      }
      if (t.type === 'mall' && t.level === 1 && state.population >= 200) {
        t.level = 2;
      }
      if (t.type === 'shop') t.jobs = BUILDINGS.shop.levelJobs[t.level - 1] || BUILDINGS.shop.levelJobs[0];
      if (t.type === 'factory') t.jobs = BUILDINGS.factory.levelJobs[t.level - 1] || BUILDINGS.factory.levelJobs[0];
      if (t.type === 'mall') t.jobs = BUILDINGS.mall.levelJobs[t.level - 1] || BUILDINGS.mall.levelJobs[0];
    }
  }

  state.population = totalPop;
  state.totalPopEver = Math.max(state.totalPopEver, totalPop);
  recomputeUnlocks(state);
  checkMilestones(state, addLog);

  // ── Farm growth (no utilities needed; droughts slow it to every other day) ──
  const farmGrows = !droughtActive || (state.day % 2 === 0);
  if (farmGrows) {
    for (let y = 0; y < GRID_H; y++) {
      for (let x = 0; x < GRID_W; x++) {
        const t = state.grid[y][x];
        if (t.type === 'farm' && t.farmStage < BUILDINGS.farm.growDays) {
          t.farmStage += 1;
        }
      }
    }
  }
  // A farm that just turned ripe THIS tick gets auto-harvested the same
  // day if it has a farmer -- run after the growth step above, not before.
  resolveFarmerHarvests(state, addLog);

  // ── Economy: tax income vs upkeep ──
  const employmentRatio = totalPop > 0 ? Math.min(1, jobsAvailable / Math.max(1, totalPop)) : 1;

  // Land value (+ crime/traffic overlays) recomputed for every tile now
  // that traffic/population/jobs are all current for today.
  computeTileOverlays(state, employmentRatio);

  // Tax income is per-tile now, weighted by that tile's land value, rather
  // than one flat town-wide formula off total population — a well-placed
  // house (near parks, away from factories/traffic) earns more tax than
  // an identical one in a rough neighborhood. Commercial/industrial zones
  // (shop/factory/mall) also contribute directly via their jobs count —
  // previously their descriptions claimed to "boost tax income" but the
  // formula never actually referenced them at all.
  let taxIncome = 0;
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      const lvMult = LAND_VALUE_TAX_MIN_MULT +
        (t.landValue / 100) * (LAND_VALUE_TAX_MAX_MULT - LAND_VALUE_TAX_MIN_MULT);
      if (t.type === 'house' && t.population > 0) {
        taxIncome += t.population * (state.taxRate / 100) * RESIDENTIAL_TAX_PER_POP * lvMult;
      } else if ((t.type === 'shop' || t.type === 'factory' || t.type === 'mall') &&
                 t.powered && t.watered && isRoadAdjacent(state, x, y)) {
        taxIncome += t.jobs * (state.taxRate / 100) * COMMERCIAL_TAX_PER_JOB * lvMult;
      }
    }
  }
  taxIncome = Math.round(taxIncome);

  let upkeep = 0;
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      const def = BUILDINGS[t.type];
      if (def && def.upkeep) upkeep += def.upkeep;
    }
  }
  // Flat civic subsidy (see TOWNHALL_SUBSIDY, data.js) -- always present,
  // so a struggling town's cash trends toward recoverable rather than an
  // ever-deepening negative spiral with no income source at all.
  state.cash += taxIncome - upkeep + TOWNHALL_SUBSIDY;

  // ── Happiness: services, employment, tax burden, utilities gaps, traffic ──
  let happinessSum = 0, servicedHouses = 0;
  const stormActive = (state.stormDaysLeft || 0) > 0;
  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      if (t.type !== 'house' || t.population === 0) continue;
      servicedHouses++;
      let h = 55;
      h += localHappinessBonus(state, x, y);
      h += (t.landValue - 40) * 0.15; // desirable neighborhoods -> happier residents
      if (!t.powered) h -= 15;
      if (!t.watered) h -= 15;
      h -= Math.max(0, state.taxRate - 10) * 1.2;
      h += (employmentRatio - 0.5) * 20;
      if (stormActive) h -= 10;
      // Heavy nearby traffic is an annoyance, not a disaster — small penalty.
      let maxNearbyTraffic = 0;
      forEachInRadius(x, y, 1, (nx, ny) => {
        const s = state.grid[ny][nx];
        if (s.type === 'road') maxNearbyTraffic = Math.max(maxNearbyTraffic, s.trafficLoad || 0);
      });
      if (maxNearbyTraffic >= TRAFFIC_HEAVY) h -= 8;
      else if (maxNearbyTraffic >= TRAFFIC_LIGHT) h -= 3;
      t.happinessLocal = Math.max(0, Math.min(100, h));
      happinessSum += t.happinessLocal;
    }
  }
  const targetHappiness = servicedHouses > 0 ? happinessSum / servicedHouses : 70;
  // Smooth toward target so it doesn't jitter wildly day to day.
  state.happiness = Math.round(state.happiness + (targetHappiness - state.happiness) * 0.3);
  state.happiness = Math.max(0, Math.min(100, state.happiness));

  if (state.cash < 0 && Math.random() < 0.3) {
    addLog('The treasury is running dry — raise taxes or slow down construction.', 'warning');
  }

  rollDisaster(state, addLog);
  rollCrimeEvent(state, employmentRatio, addLog);

  recordHistory(state);
  resyncNpcs(state);
}

// One snapshot per simulated day for the Stats graph panel — capped at a
// rolling window so a very long-running town doesn't grow this array
// forever (only the trend over the last ~200 days is useful to look at
// anyway, and it's saved/loaded with everything else in state.history).
function recordHistory(state) {
  state.history.push({
    day: state.day,
    population: state.population,
    cash: state.cash,
    happiness: state.happiness
  });
  if (state.history.length > HISTORY_MAX_DAYS) {
    state.history.splice(0, state.history.length - HISTORY_MAX_DAYS);
  }
}

function harvestFarm(state, x, y, addLog, farmerName) {
  const t = tileAt(state, x, y);
  if (!t || t.type !== 'farm' || t.farmStage < BUILDINGS.farm.growDays) return false;
  const bonus = state.season === 1 ? 1.2 : (state.season === 3 ? 0.8 : 1); // summer bonus, winter penalty
  const amount = Math.round(BUILDINGS.farm.yield * bonus);
  state.cash += amount;
  t.farmStage = 0;
  if (farmerName) {
    addLog(`🌾 ${farmerName} harvested a farm for +$${amount}.`, 'harvest');
  } else {
    addLog(`Harvested a farm for +$${amount}.`, 'harvest');
  }
  return true;
}

// Farms with an assigned farmer (an npc whose jobX/jobY points at this
// tile -- see pickJobFor()/spawnNpc() in npc.js, and the `farm` entry's
// baseJobs:1 in data.js) harvest themselves automatically the day they
// ripen, crediting cash without the player needing to click. An
// unstaffed farm (no population/npcs yet, or every farmer already
// working a different plot) just sits ripe and waiting for a manual
// click, exactly like every farm did before this feature existed.
function resolveFarmerHarvests(state, addLog) {
  for (const npc of npcs) {
    if (npc.jobX == null || npc.jobY == null) continue;
    const t = tileAt(state, npc.jobX, npc.jobY);
    if (t && t.type === 'farm' && t.farmStage >= BUILDINGS.farm.growDays) {
      harvestFarm(state, npc.jobX, npc.jobY, addLog, npc.name);
    }
  }
}
