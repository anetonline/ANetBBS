// ─── ANetDarkForces: level maps ──────────────────────────────────────────
// Grids are built PROGRAMMATICALLY (makeGrid + wallRect/wallRing helpers)
// rather than hand-typed ASCII-art strings -- a real 2D array of fixed
// width from the start can't drift out of sync row-to-row the way manual
// character-counting across 20-35-column string literals reliably did
// (caught during validation: several hand-typed rows were off by one or
// two characters, silently shifting every wall after the mistake).
//
// Coordinates in spawn lists are GRID cells (not world units) -- converted
// to world-pixel centers by gridToWorld().

function makeGrid(w, h, borderType = 1) {
  const grid = [];
  for (let y = 0; y < h; y++) grid.push(new Array(w).fill(0));
  for (let x = 0; x < w; x++) { grid[0][x] = borderType; grid[h - 1][x] = borderType; }
  for (let y = 0; y < h; y++) { grid[y][0] = borderType; grid[y][w - 1] = borderType; }
  return { grid, w, h };
}

// Fills a solid rectangle (inclusive coords) with a wall type.
function wallRect(level, x1, y1, x2, y2, type) {
  for (let y = y1; y <= y2; y++) {
    for (let x = x1; x <= x2; x++) level.grid[y][x] = type;
  }
}

// Outlines a rectangle (inclusive coords) with a wall type, leaving the
// interior as floor -- shelving units / server racks / display islands.
function wallRing(level, x1, y1, x2, y2, type) {
  for (let x = x1; x <= x2; x++) { level.grid[y1][x] = type; level.grid[y2][x] = type; }
  for (let y = y1; y <= y2; y++) { level.grid[y][x1] = type; level.grid[y][x2] = type; }
}

// Carves a floor opening (doorway) into an existing wall run.
function opening(level, x1, y1, x2, y2) {
  for (let y = y1; y <= y2; y++) {
    for (let x = x1; x <= x2; x++) level.grid[y][x] = 0;
  }
}

function validateLevel(level) {
  const { grid, w, h } = level;
  for (const row of grid) {
    if (row.length !== w) throw new Error(`Level "${level.name}": row length ${row.length} != declared width ${w}`);
  }
  for (let x = 0; x < w; x++) {
    if (grid[0][x] === 0 || grid[h - 1][x] === 0) throw new Error(`Level "${level.name}": gap in top/bottom border at column ${x}`);
  }
  for (let y = 0; y < h; y++) {
    if (grid[y][0] === 0 || grid[y][w - 1] === 0) throw new Error(`Level "${level.name}": gap in left/right border at row ${y}`);
  }
  const checkPt = (label, gx, gy) => {
    const ix = Math.floor(gx), iy = Math.floor(gy);
    if (iy < 0 || iy >= h || ix < 0 || ix >= w) throw new Error(`Level "${level.name}": ${label} out of bounds (${gx},${gy})`);
    if (grid[iy][ix] !== 0) throw new Error(`Level "${level.name}": ${label} lands on a wall (${gx},${gy}) type=${grid[iy][ix]}`);
  };
  // Entities with a physical collision radius (player, enemies) need real
  // clearance around their spawn point, not just a clear center tile -- a
  // center tile that's technically floor but within ~a third of a tile of
  // a wall still leaves the entity's own collision box overlapping that
  // wall from the instant it spawns. This is exactly how enemies ended up
  // visibly embedded in walls in testing: a valid center point, but no
  // clearance check around it.
  const CLEARANCE = 0.35; // grid units -- matches the TILE*0.3 world-unit collision radius used in entities.js, with a small safety margin
  const checkClearance = (label, gx, gy) => {
    checkPt(label, gx, gy);
    for (const [dx, dy] of [[-CLEARANCE, -CLEARANCE], [CLEARANCE, -CLEARANCE], [-CLEARANCE, CLEARANCE], [CLEARANCE, CLEARANCE]]) {
      const ix = Math.floor(gx + dx), iy = Math.floor(gy + dy);
      if (iy < 0 || iy >= h || ix < 0 || ix >= w || grid[iy][ix] !== 0) {
        throw new Error(`Level "${level.name}": ${label} at (${gx},${gy}) has no collision clearance -- a corner of its bounding box lands on a wall/border`);
      }
    }
  };
  checkClearance('playerStart', level.playerStart.gx, level.playerStart.gy);
  checkPt('exit', level.exit.gx, level.exit.gy); // proximity trigger only, no rigid body
  for (const e of level.enemies) checkClearance(`enemy:${e.type}@${e.gx},${e.gy}`, e.gx, e.gy);
  for (const p of level.pickups) checkPt(`pickup:${p.type}@${p.gx},${p.gy}`, p.gx, p.gy); // proximity trigger only
  for (const b of level.barrels || []) checkPt(`barrel@${b.gx},${b.gy}`, b.gx, b.gy); // no rigid collision, proximity-only like pickups

  // Cache the true secret count HERE, at pristine grid state (right after
  // construction, before any gameplay could have opened one) -- computing
  // it lazily on first display call would risk running after a secret's
  // already been found, undercounting the total forever after.
  level._secretsTotal = countCellsOfType(grid, SECRET_TYPE);
}

// Grid coordinates here are CONTINUOUS units matching the raycaster's own
// posX = worldX/TILE convention (integer = cell boundary) -- not discrete
// cell indices. Spawn coords in this file use values like 1.5 to already
// mean "centered in cell 1" (validateLevel's Math.floor(gx) check relies
// on this), so converting to world units is a plain scale, NOT a
// scale-then-add-half-a-tile: that extra offset would shift every spawn
// by half a tile from where it was validated to be wall-free.
function gridToWorld(gx, gy) {
  return { x: gx * TILE, y: gy * TILE };
}

function countCellsOfType(grid, type) {
  let count = 0;
  for (const row of grid) for (const cell of row) if (cell === type) count++;
  return count;
}

// Secrets-found is derived from live grid state rather than a separately
// tracked counter -- a counter would desync the moment a level reloads
// (e.g. on respawn), since the grid's already-opened secrets persist but
// a naive counter would reset to 0. `level._secretsTotal` is cached by
// validateLevel() at construction time (see above), so this is always
// comparing against the true pristine count.
function getSecretsProgress(level) {
  const remaining = countCellsOfType(level.grid, SECRET_TYPE);
  return { found: level._secretsTotal - remaining, total: level._secretsTotal };
}

function buildLevels() {
  const levels = [];

  // ── Level 1: ByteMart Warehouse (32x13) ──────────────────────────────
  // Widened east of the original 18-wide floor into a checkout counter
  // room and a break room, both gated by their own doors -- the original
  // warehouse floor is untouched (still the same validated layout), the
  // exit just now sits past two more rooms instead of at the old edge.
  {
    // Height extended 13->15 solely to fit a secret alcove below the
    // original floor -- appended rows only, nothing existing shifts.
    const { grid, w, h } = makeGrid(32, 15);
    const level = {
      name: 'ByteMart Warehouse',
      intro: "First stop: ByteMart's back warehouse. Word is the Dark Forces have been stockpiling parts here since they hit the place last month. Get in, take back what's ours, get out.",
      grid, w, h,
      playerStart: { gx: 1.5, gy: 1.5, angle: 0 },
      exit: { gx: 29, gy: 7 },
      enemies: [
        { type: 'scalper', gx: 4, gy: 3.5 },
        { type: 'scalper', gx: 5, gy: 7 },
        { type: 'goon', gx: 14, gy: 8 },
        { type: 'scalper', gx: 9, gy: 10 },
        { type: 'guard', gx: 14, gy: 4 },
        { type: 'guard', gx: 21, gy: 4 },
        { type: 'scalper', gx: 28, gy: 5 },
        { type: 'goon', gx: 28, gy: 8 },
        { type: 'turret', gx: 22, gy: 4 },
      ],
      pickups: [
        { type: 'health_small', gx: 2, gy: 10 },
        { type: 'ammo_shells', gx: 9, gy: 1 },
        { type: 'part', gx: 13, gy: 10 },
        { type: 'part', gx: 5, gy: 10 },
        { type: 'weapon_static', gx: 16, gy: 5 },
        { type: 'weapon_packetspray', gx: 9, gy: 6 },
        { type: 'ammo_rounds', gx: 12, gy: 7 },
        { type: 'health_small', gx: 19, gy: 6 },
        { type: 'part', gx: 23, gy: 4 },
        { type: 'armor', gx: 29, gy: 5 },
        { type: 'ammo_shells', gx: 27, gy: 8 },
        { type: 'ammo_cores', gx: 9, gy: 13 }, // behind the secret wall below
      ],
      barrels: [
        { gx: 19, gy: 4 },
        { gx: 29, gy: 7 },
      ],
    };
    wallRing(level, 2, 2, 6, 5, 3);
    opening(level, 4, 2, 4, 2);
    level.grid[2][4] = DOOR_TYPE; // security door into the first shelving block
    wallRing(level, 8, 2, 12, 5, 3);
    opening(level, 10, 2, 10, 2);
    level.grid[2][10] = DOOR_TYPE;
    wallRect(level, 4, 8, 9, 8, 4);
    opening(level, 6, 8, 7, 8);
    level.grid[8][6] = DOOR_TYPE;
    level.grid[8][7] = DOOR_TYPE;
    wallRing(level, 11, 9, 15, 11, 6);
    opening(level, 13, 9, 13, 9);
    level.grid[9][13] = DOOR_TYPE;
    // Checkout counter room -- new.
    wallRing(level, 18, 2, 24, 8, 2);
    opening(level, 21, 2, 21, 2);
    level.grid[2][21] = DOOR_TYPE;
    wallRect(level, 20, 5, 22, 5, 2); // counter divider, cover only
    // Break room -- new, holds the level exit.
    wallRing(level, 26, 3, 30, 9, 6);
    opening(level, 28, 3, 28, 3);
    level.grid[3][28] = DOOR_TYPE;
    // Table, cover only -- split with a gap at x=28 so it doesn't seal the
    // room into two disconnected halves (a full-width obstacle did exactly
    // that in this same room on the first pass).
    level.grid[6][27] = 3;
    level.grid[6][29] = 3;
    // Secret alcove: reseal what was the original bottom border (old
    // h=13 meant row 12 was the border) as solid wall again, except for
    // one SECRET_TYPE cell -- the new real border is now row 14, and row
    // 13 is the alcove's floor, reachable only by finding the secret.
    wallRect(level, 1, 12, 30, 12, 1);
    level.grid[12][9] = SECRET_TYPE;
    validateLevel(level);
    levels.push(level);
  }

  // ── Level 2: Overclock Alley Pawn Shop (24x14) ───────────────────────
  {
    const { grid, w, h } = makeGrid(24, 14);
    const level = {
      name: 'Overclock Alley Pawn Shop',
      intro: 'The pawn shop on Overclock Alley. Cramped aisles, more guards. They know we\'re coming after ByteMart.',
      grid, w, h,
      playerStart: { gx: 1.5, gy: 1.5, angle: 0 },
      exit: { gx: 21.5, gy: 1.5 },
      enemies: [
        { type: 'guard', gx: 11.5, gy: 7.5 },
        { type: 'scalper', gx: 6, gy: 3 },
        { type: 'scalper', gx: 18, gy: 3 },
        { type: 'goon', gx: 11, gy: 10.5 },
        { type: 'guard', gx: 20, gy: 10 },
        { type: 'scalper', gx: 3, gy: 10 },
        { type: 'drone', gx: 5.5, gy: 3 },
        { type: 'drone', gx: 17.5, gy: 3 },
      ],
      pickups: [
        { type: 'health_large', gx: 11, gy: 8 },
        { type: 'armor', gx: 2, gy: 6 },
        { type: 'ammo_shells', gx: 21, gy: 6 },
        { type: 'part', gx: 11, gy: 12 },
        { type: 'part', gx: 2, gy: 12 },
        { type: 'weapon_emp', gx: 20, gy: 12 },
        { type: 'ammo_rounds', gx: 16, gy: 6 },
      ],
      barrels: [
        { gx: 15, gy: 10 },
      ],
    };
    // Aisle shelving (5 short shelf blocks across the top third).
    for (let i = 0; i < 5; i++) {
      const x = 3 + i * 4;
      wallRect(level, x, 2, x + 1, 4, 2);
    }
    // Central shop counter (ring, one doorway -- now a security door).
    wallRing(level, 9, 6, 14, 9, 2);
    opening(level, 11, 6, 12, 6);
    level.grid[6][11] = DOOR_TYPE;
    level.grid[6][12] = DOOR_TYPE;
    // Back-room shelf row -- also now behind a door, not just a gap.
    wallRect(level, 3, 11, 20, 11, 3);
    opening(level, 11, 11, 12, 11);
    level.grid[11][11] = DOOR_TYPE;
    level.grid[11][12] = DOOR_TYPE;
    validateLevel(level);
    levels.push(level);
  }

  // ── NetCafe Hideout (22x12) ────────────────────────────────────────
  // A pacing breather: smaller, tighter, fewer enemies. A defunct
  // internet cafe the Dark Forces turned into a stakeout den -- a row of
  // open-fronted terminal booths (single-column stubs, not enclosed
  // rings, so there's nothing here that can seal itself shut) leading to
  // a small back office.
  {
    const { grid, w, h } = makeGrid(22, 12);
    const level = {
      name: 'NetCafe Hideout',
      intro: "A gutted internet cafe two blocks from ByteMart -- the terminals are long dead, but the Dark Forces have been using the back office as a stakeout post. Smaller job. Doesn't mean smaller trouble.",
      grid, w, h,
      playerStart: { gx: 2, gy: 2, angle: 0 },
      exit: { gx: 18, gy: 8 },
      enemies: [
        { type: 'scalper', gx: 4, gy: 6 },
        { type: 'scalper', gx: 9, gy: 7 },
        { type: 'goon', gx: 13, gy: 6 },
        { type: 'guard', gx: 17, gy: 7 },
      ],
      pickups: [
        { type: 'health_small', gx: 3, gy: 8 },
        { type: 'ammo_shells', gx: 5, gy: 8 },
        { type: 'health_large', gx: 10, gy: 3 },
        { type: 'part', gx: 4, gy: 8 },
        { type: 'part', gx: 17, gy: 6 },
        { type: 'ammo_cells', gx: 17, gy: 8 },
      ],
    };
    // Terminal booth stubs -- thin partial partitions, open on three sides.
    wallRect(level, 5, 2, 5, 3, 2);
    wallRect(level, 8, 2, 8, 3, 2);
    wallRect(level, 11, 2, 11, 3, 2);
    wallRect(level, 14, 2, 14, 3, 2);
    // Back office.
    wallRing(level, 15, 5, 19, 9, 1);
    opening(level, 15, 7, 15, 7);
    level.grid[7][15] = DOOR_TYPE;
    // Old snack counter, now hosting a repurposed ammo dispenser.
    level.grid[9][3] = AMMO_STATION_TYPE;
    validateLevel(level);
    levels.push(level);
  }

  // ── Level 3: CircuitSide Outlet (28x16) ──────────────────────────────
  // Height extended 13->16 solely to fit a locked vault room below the
  // original floor -- appended rows only, nothing existing shifts.
  {
    const { grid, w, h } = makeGrid(28, 16);
    const level = {
      name: 'CircuitSide Outlet',
      intro: "Bigger store, bigger crew. CircuitSide's showroom floor is wide open — no cover, no mercy. Techs are running EMP crowd control in here.",
      grid, w, h,
      playerStart: { gx: 1.5, gy: 1.5, angle: 0 },
      exit: { gx: 26.5, gy: 11.5 },
      enemies: [
        { type: 'tech', gx: 5, gy: 6 },
        { type: 'tech', gx: 21, gy: 6 },
        { type: 'goon', gx: 13, gy: 1.5 },
        { type: 'goon', gx: 14, gy: 11 },
        { type: 'guard', gx: 8, gy: 11 },
        { type: 'guard', gx: 19, gy: 1.5 },
        { type: 'scalper', gx: 12, gy: 6 },
        { type: 'scalper', gx: 16, gy: 6 },
        { type: 'turret', gx: 14, gy: 6 },
        { type: 'shieldtech', gx: 13, gy: 14 },
      ],
      pickups: [
        { type: 'health_large', gx: 13, gy: 6 },
        { type: 'armor', gx: 2, gy: 11 },
        { type: 'ammo_cells', gx: 25, gy: 1.5 },
        { type: 'ammo_shells', gx: 2, gy: 1.5 },
        { type: 'part', gx: 13, gy: 1.5 },
        { type: 'part', gx: 13, gy: 11 },
        { type: 'weapon_overclock', gx: 25, gy: 11 },
        { type: 'weapon_debugger', gx: 25, gy: 5 },
        { type: 'ammo_scopes', gx: 25, gy: 6 },
        { type: 'key_red', gx: 2, gy: 9 },
        { type: 'health_large', gx: 11, gy: 14 },
        { type: 'ammo_cores', gx: 15, gy: 14 },
      ],
      barrels: [
        { gx: 9, gy: 6 },
        { gx: 19, gy: 9 },
      ],
    };
    // 4 display-island blocks, upper row.
    for (let i = 0; i < 4; i++) {
      const x = 3 + i * 6;
      wallRing(level, x, 2, x + 2, 4, 1);
    }
    // 4 display-island blocks, lower row (server-rack themed).
    for (let i = 0; i < 4; i++) {
      const x = 3 + i * 6;
      wallRing(level, x, 7, x + 2, 9, 4);
    }
    // A small secured supply room in the previously-empty far corner -- this
    // level used to be nothing but open floor around decorative islands
    // with no actual rooms, the clearest case of "just one open area."
    // Placed at x=24-26 specifically to clear the 4th display island's own
    // right wall at x=23 -- putting the door there instead would open
    // straight into that island's sealed (opening-less) interior.
    wallRing(level, 24, 4, 26, 7, 1);
    opening(level, 24, 5, 24, 5);
    level.grid[5][24] = DOOR_TYPE;
    // Locked vault room -- reseal the old bottom border (row 12) except a
    // single LOCKED_DOOR_TYPE entrance, guarded inside by the shieldtech.
    // The red key is out on the open floor (see pickups above), reachable
    // well before the player could ever reach this door.
    wallRect(level, 1, 12, 26, 12, 1);
    level.grid[12][13] = LOCKED_DOOR_TYPE;
    validateLevel(level);
    level.lockedDoors = { '13,12': 'red' };
    levels.push(level);
  }

  // ── TechBarn Superstore (36x16) ───────────────────────────────────────
  // A real big-box layout: checkout-lane stubs near the entrance, 5
  // parallel aisle shelves (open-ended single columns, walkable around
  // top or bottom -- deliberately not rings, so nothing here can seal a
  // pocket shut), and a customer service desk room holding the exit.
  {
    const { grid, w, h } = makeGrid(36, 16);
    const level = {
      name: 'TechBarn Superstore',
      intro: "TechBarn's flagship store -- the Dark Forces practically run their whole retail-front operation out of here. Long aisles, lots of angles for an ambush. Watch the endcaps.",
      grid, w, h,
      playerStart: { gx: 2, gy: 2, angle: 0 },
      exit: { gx: 32, gy: 7 },
      enemies: [
        { type: 'scalper', gx: 3, gy: 8 },
        { type: 'scalper', gx: 8.5, gy: 3 },
        { type: 'goon', gx: 8, gy: 13 },
        { type: 'guard', gx: 13, gy: 8 },
        { type: 'tech', gx: 13, gy: 13 },
        { type: 'goon', gx: 18, gy: 8 },
        { type: 'guard', gx: 23, gy: 8 },
        { type: 'scalper', gx: 27, gy: 3 },
        { type: 'guard', gx: 31, gy: 7 },
        { type: 'drone', gx: 12.5, gy: 8 },
        { type: 'drone', gx: 22.5, gy: 8 },
        { type: 'shieldtech', gx: 32, gy: 5 },
      ],
      pickups: [
        { type: 'health_small', gx: 3, gy: 12 },
        { type: 'ammo_shells', gx: 5, gy: 3 },
        { type: 'armor', gx: 14.5, gy: 3 },
        { type: 'ammo_rounds', gx: 18, gy: 3 },
        { type: 'health_large', gx: 23, gy: 3 },
        { type: 'part', gx: 8, gy: 8 },
        { type: 'part', gx: 18, gy: 13 },
        { type: 'ammo_scopes', gx: 23, gy: 13 },
        { type: 'health_large', gx: 30, gy: 4 },
      ],
      barrels: [
        { gx: 3, gy: 8 },
        { gx: 27, gy: 8 },
      ],
    };
    // Checkout-lane stubs near the entrance.
    for (let i = 0; i < 4; i++) {
      const x = 3 + i * 3;
      wallRect(level, x, 2, x + 1, 4, 2);
    }
    // 5 open-ended aisle shelves.
    for (let i = 0; i < 5; i++) {
      const x = 5 + i * 5;
      wallRect(level, x, 6, x, 11, 3);
    }
    // Customer service desk, holds the exit.
    wallRing(level, 29, 3, 33, 9, 2);
    opening(level, 29, 6, 29, 6);
    level.grid[6][29] = DOOR_TYPE;
    // Ammo dispenser tucked in the checkout lanes near the entrance.
    level.grid[4][4] = AMMO_STATION_TYPE;
    validateLevel(level);
    levels.push(level);
  }

  // ── Level 4: The Mainframe Megastore (34x13) ─────────────────────────
  {
    const { grid, w, h } = makeGrid(34, 13);
    const level = {
      name: 'The Mainframe Megastore',
      intro: "The Mainframe Megastore — the Dark Forces' regional distribution hub. Every part they've stolen this year is stacked in here somewhere. Bigger store, bigger crew, and this is only the halfway point.",
      grid, w, h,
      playerStart: { gx: 1.5, gy: 1.5, angle: 0 },
      exit: { gx: 32.5, gy: 11.5 },
      enemies: [
        { type: 'guard', gx: 3, gy: 5.5 },
        { type: 'guard', gx: 27.5, gy: 5.5 },
        { type: 'tech', gx: 16.5, gy: 5.5 },
        { type: 'goon', gx: 3, gy: 9.5 },
        { type: 'goon', gx: 29, gy: 9.5 },
        { type: 'scalper', gx: 10, gy: 9.5 },
        { type: 'scalper', gx: 21, gy: 9.5 },
        { type: 'scalper', gx: 16, gy: 9.5 },
        { type: 'guard', gx: 16.5, gy: 1.5 },
        { type: 'turret', gx: 16.5, gy: 3.5 },
      ],
      pickups: [
        { type: 'health_large', gx: 1.5, gy: 10.5 },
        { type: 'health_large', gx: 32.5, gy: 1.5 },
        { type: 'armor', gx: 16.5, gy: 10.5 },
        { type: 'ammo_cores', gx: 8, gy: 1.5 },
        { type: 'ammo_cells', gx: 25, gy: 1.5 },
        { type: 'part', gx: 4, gy: 3 },
        { type: 'part', gx: 29, gy: 5.5 },
        { type: 'part', gx: 16.5, gy: 5.5 },
        { type: 'ammo_rounds', gx: 8, gy: 9 },
        { type: 'ammo_scopes', gx: 25, gy: 9 },
        { type: 'key_blue', gx: 1.5, gy: 9 },
      ],
      barrels: [
        { gx: 11, gy: 9.5 },
        { gx: 22, gy: 5.5 },
      ],
    };
    // Three big storage-room blocks (outer ring wall with inner shelf ring),
    // each one's server core behind a door -- the MIDDLE room's is a
    // locked vault door (blue key, placed out on the open floor well
    // before the player could reach here) instead of a plain security door.
    const rooms = [[2, 2, 10, 8], [12, 2, 21, 8], [23, 2, 31, 8]];
    let lockedCellKey = null;
    rooms.forEach(([x1, y1, x2, y2], i) => {
      wallRing(level, x1, y1, x2, y2, 1);
      const cx1 = x1 + 3, cy1 = y1 + 2, cx2 = x2 - 3, cy2 = y2 - 2;
      wallRing(level, cx1, cy1, cx2, cy2, 5);
      opening(level, x1, Math.floor((y1 + y2) / 2), x1, Math.floor((y1 + y2) / 2)); // west doorway
      opening(level, x2, Math.floor((y1 + y2) / 2), x2, Math.floor((y1 + y2) / 2)); // east doorway
      const innerDoorX = Math.floor((cx1 + cx2) / 2), innerDoorY = cy1;
      opening(level, innerDoorX, innerDoorY, innerDoorX, innerDoorY); // enter inner ring
      if (i === 1) {
        level.grid[innerDoorY][innerDoorX] = LOCKED_DOOR_TYPE;
        lockedCellKey = innerDoorX + ',' + innerDoorY;
      } else {
        level.grid[innerDoorY][innerDoorX] = DOOR_TYPE;
      }
    });
    // Bottom aisle divider (loading-dock strip) with a center gap.
    wallRect(level, 2, 10, 31, 10, 3);
    opening(level, 16, 10, 17, 10);
    validateLevel(level);
    level.lockedDoors = { [lockedCellKey]: 'blue' };
    levels.push(level);
  }

  // ── Level 5: Datastream Distribution Depot (32x14) ───────────────────
  // A real room-and-corridor level: three sealed bay dividers, one door
  // through each, rather than open floor with decorative islands.
  {
    const { grid, w, h } = makeGrid(32, 14);
    const level = {
      name: 'Datastream Distribution Depot',
      intro: "Past the Megastore, into the depot that actually moves the Dark Forces' stolen hardware around the city. Sealed bay doors between every warehouse block -- expect a fight every time one opens.",
      grid, w, h,
      playerStart: { gx: 2.5, gy: 2.5, angle: 0 },
      exit: { gx: 28, gy: 10 },
      enemies: [
        { type: 'scalper', gx: 4, gy: 8 },
        { type: 'goon', gx: 12, gy: 4 },
        { type: 'tech', gx: 12, gy: 10 },
        { type: 'guard', gx: 19, gy: 3 },
        { type: 'guard', gx: 21, gy: 10 },
        { type: 'scalper', gx: 19, gy: 9 },
        { type: 'tech', gx: 27, gy: 3 },
        { type: 'guard', gx: 27, gy: 10 },
        { type: 'drone', gx: 20, gy: 10 },
      ],
      pickups: [
        { type: 'health_small', gx: 3, gy: 5 },
        { type: 'armor', gx: 6, gy: 9 },
        { type: 'ammo_shells', gx: 11, gy: 2 },
        { type: 'ammo_cells', gx: 13, gy: 11 },
        { type: 'part', gx: 20, gy: 3 },
        { type: 'ammo_rounds', gx: 22, gy: 3 },
        { type: 'health_large', gx: 26, gy: 6 },
        { type: 'part', gx: 29, gy: 10 },
        { type: 'ammo_cores', gx: 29, gy: 2 },
      ],
      barrels: [
        { gx: 4, gy: 4 },
      ],
    };
    wallRect(level, 8, 1, 8, 12, 2);
    opening(level, 8, 3, 8, 3);
    level.grid[3][8] = DOOR_TYPE;
    wallRect(level, 16, 1, 16, 12, 6);
    opening(level, 16, 9, 16, 9);
    level.grid[9][16] = DOOR_TYPE;
    wallRect(level, 24, 1, 24, 12, 4);
    opening(level, 24, 5, 24, 5);
    level.grid[5][24] = DOOR_TYPE;
    // Ammo dispenser near the second bay door.
    level.grid[2][17] = AMMO_STATION_TYPE;
    // Sealed decorative shelving for cover -- deliberately no opening into
    // these, they're obstacles to shoot around, not rooms to enter.
    wallRing(level, 11, 6, 13, 8, 3);
    wallRing(level, 19, 5, 21, 7, 4);
    wallRing(level, 27, 6, 29, 8, 1);
    validateLevel(level);
    levels.push(level);
  }

  // ── The Chip Foundry (30x16) ────────────────────────────────────────
  // An industrial manufacturing floor. Machinery blocks are open-ended
  // (single/double-column obstacles, walkable around either end), same
  // safe pattern as TechBarn's aisles -- plus two small enclosed rooms,
  // each behind its own door.
  {
    const { grid, w, h } = makeGrid(30, 16);
    const level = {
      name: 'The Chip Foundry',
      intro: "The foundry that stamps out half the counterfeit parts flooding the district. Heavier crew than anything so far -- this is where the Dark Forces actually make their money.",
      grid, w, h,
      playerStart: { gx: 2, gy: 2, angle: 0 },
      exit: { gx: 26, gy: 12 },
      enemies: [
        { type: 'guard', gx: 3, gy: 4 },
        { type: 'tech', gx: 10, gy: 4 },
        { type: 'scalper', gx: 10, gy: 9 },
        { type: 'guard', gx: 18, gy: 4 },
        { type: 'tech', gx: 18, gy: 9 },
        { type: 'goon', gx: 25, gy: 4 },
        { type: 'goon', gx: 25, gy: 9 },
        { type: 'guard', gx: 17, gy: 13 },
        { type: 'tech', gx: 4, gy: 12.5 },
        { type: 'guard', gx: 24.5, gy: 12.5 },
        { type: 'shieldtech', gx: 12, gy: 9 },
        { type: 'turret', gx: 14, gy: 2.5 },
      ],
      pickups: [
        { type: 'health_large', gx: 3, gy: 7 },
        { type: 'armor', gx: 10, gy: 6 },
        { type: 'ammo_cells', gx: 18, gy: 6 },
        { type: 'ammo_scopes', gx: 25, gy: 6 },
        { type: 'health_small', gx: 14, gy: 4 },
        { type: 'ammo_cores', gx: 12, gy: 10 },
        { type: 'part', gx: 5, gy: 12 },
        { type: 'ammo_rounds', gx: 10, gy: 13 },
        { type: 'part', gx: 23, gy: 12 },
      ],
      // Clustered close together on purpose -- one detonation chain-reacts
      // into the other two, a classic "shoot the barrels" set-piece.
      barrels: [
        { gx: 24, gy: 3 },
        { gx: 26, gy: 3 },
        { gx: 25, gy: 5 },
      ],
    };
    wallRect(level, 6, 3, 7, 10, 4);
    wallRect(level, 14, 5, 15, 12, 4);
    wallRect(level, 21, 2, 22, 9, 4);
    // Control room.
    wallRing(level, 2, 11, 6, 13, 1);
    opening(level, 4, 11, 4, 11);
    level.grid[11][4] = DOOR_TYPE;
    // Parts storage, holds the exit.
    wallRing(level, 22, 11, 27, 13, 1);
    opening(level, 24, 11, 24, 11);
    level.grid[11][24] = DOOR_TYPE;
    // Ammo dispenser near the entrance.
    level.grid[2][4] = AMMO_STATION_TYPE;
    validateLevel(level);
    levels.push(level);
  }

  // ── Level 6: The Server Farm (28x18) ───────────────────────────────────
  // Two full-width dividers force an S-shaped path (door near the left
  // wall, then door near the right wall) instead of a straight line --
  // the last gauntlet before the boss. Width extended 26->28 solely to
  // fit a secret alcove east of the original floor -- appended column
  // only, nothing existing shifts.
  {
    const { grid, w, h } = makeGrid(28, 18);
    const level = {
      name: 'The Server Farm',
      intro: "The Dark Forces' server farm — rows of racked hardware humming in the dark, and the last thing standing between you and the Middleman himself. No more open floors. Every room here was built to slow you down.",
      grid, w, h,
      playerStart: { gx: 2, gy: 2, angle: 0 },
      exit: { gx: 22, gy: 15 },
      enemies: [
        { type: 'scalper', gx: 5, gy: 3 },
        { type: 'scalper', gx: 10, gy: 3 },
        { type: 'goon', gx: 16, gy: 3 },
        { type: 'tech', gx: 20, gy: 3 },
        { type: 'guard', gx: 4, gy: 9 },
        { type: 'guard', gx: 12, gy: 9 },
        { type: 'tech', gx: 18, gy: 9 },
        { type: 'scalper', gx: 22, gy: 9 },
        { type: 'guard', gx: 6, gy: 15 },
        { type: 'guard', gx: 14, gy: 15 },
        { type: 'tech', gx: 20, gy: 14 },
        { type: 'turret', gx: 8, gy: 3 },
        { type: 'shieldtech', gx: 10, gy: 15 },
      ],
      pickups: [
        { type: 'health_small', gx: 3, gy: 4 },
        { type: 'ammo_shells', gx: 15, gy: 3 },
        { type: 'health_large', gx: 22, gy: 4 },
        { type: 'armor', gx: 7, gy: 9 },
        { type: 'ammo_rounds', gx: 16, gy: 9 },
        { type: 'part', gx: 4, gy: 10 },
        { type: 'ammo_scopes', gx: 2, gy: 15 },
        { type: 'health_large', gx: 10, gy: 14 },
        { type: 'ammo_cores', gx: 19, gy: 15 },
        { type: 'part', gx: 22, gy: 13 },
        { type: 'armor', gx: 26, gy: 9 }, // behind the secret wall below
      ],
      barrels: [
        { gx: 13, gy: 10 },
        { gx: 15, gy: 10 },
      ],
    };
    wallRect(level, 1, 6, 24, 6, 4);
    opening(level, 6, 6, 6, 6);
    level.grid[6][6] = DOOR_TYPE;
    wallRect(level, 1, 12, 24, 12, 5);
    opening(level, 18, 12, 18, 12);
    level.grid[12][18] = DOOR_TYPE;
    // Sealed cover obstacles, one per room segment (kept clear of the
    // divider doors' columns -- a ring's edge sitting on a door's column
    // would seal the room shut right behind its own door).
    wallRing(level, 12, 2, 14, 4, 4);
    wallRing(level, 8, 8, 10, 10, 3);
    // Ammo dispenser near the second divider door.
    level.grid[11][19] = AMMO_STATION_TYPE;
    // Secret alcove: reseal what was the original right border (old
    // w=26 meant column 25 was the border) as solid wall again, except
    // one SECRET_TYPE cell -- the new real border is now column 27, and
    // column 26 is the alcove's floor.
    wallRect(level, 25, 1, 25, 16, 1);
    level.grid[9][25] = SECRET_TYPE;
    validateLevel(level);
    levels.push(level);
  }

  // ── Level 7: HQ Showroom, boss (23x12) ─────────────────────────────────
  // Width extended 20->23 solely to fit a locked gold vault east of the
  // original showroom -- appended columns only, nothing existing shifts.
  {
    const { grid, w, h } = makeGrid(23, 12);
    const level = {
      name: "The Middleman's Showroom",
      intro: "The penthouse showroom. Marble floors, gold-plated cable trays, and one very smug crime boss who thinks he owns every computer store in the city. Time to change his mind.",
      grid, w, h,
      playerStart: { gx: 1.5, gy: 1.5, angle: 0 },
      exit: { gx: 10, gy: 1.5 },
      enemies: [
        { type: 'boss_middleman', gx: 10, gy: 6.5 },
        { type: 'guard', gx: 4, gy: 8 },
        { type: 'guard', gx: 16, gy: 8 },
      ],
      pickups: [
        { type: 'health_large', gx: 3, gy: 3 },
        { type: 'health_large', gx: 16, gy: 3 },
        { type: 'ammo_cores', gx: 10, gy: 3 },
        { type: 'armor', gx: 4, gy: 8 },
        { type: 'armor', gx: 15, gy: 8 },
        { type: 'key_gold', gx: 1.5, gy: 6 },
        { type: 'health_large', gx: 20.5, gy: 1.5 }, // behind the gold vault door
        { type: 'ammo_cores', gx: 20.5, gy: 2.5 },
        { type: 'ammo_scopes', gx: 20.5, gy: 3.5 },
      ],
      barrels: [
        { gx: 5, gy: 8 },
        { gx: 15, gy: 8 },
      ],
      isFinalLevel: true,
    };
    wallRing(level, 2, 2, 17, 9, 2);
    opening(level, 9, 2, 10, 2);
    opening(level, 9, 9, 10, 9);
    wallRing(level, 8, 5, 11, 7, 5);
    opening(level, 9, 7, 10, 7);
    level.grid[7][9] = DOOR_TYPE; // boss chamber security door
    level.grid[7][10] = DOOR_TYPE;
    // Gold vault: reseal what was the original right border (old w=20
    // meant column 19 was the border) as solid wall again, except one
    // LOCKED_DOOR_TYPE entrance at row 1 (the already-open top corridor
    // that wraps around the outer ring) -- new real border is now column
    // 22. Capped at row 4 so the vault is a tidy 3-row room, not the full
    // height of the level.
    wallRect(level, 19, 1, 19, 10, 1);
    level.grid[1][19] = LOCKED_DOOR_TYPE;
    wallRect(level, 20, 4, 21, 4, 1);
    validateLevel(level);
    level.lockedDoors = { '19,1': 'gold' };
    levels.push(level);
  }

  return levels;
}

const LEVELS = buildLevels();
