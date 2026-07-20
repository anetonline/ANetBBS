// ─── Canvas rendering — original procedural pixel-art ──────────────────────
// Everything is drawn with primitive shapes at draw time; no external image
// assets. Palette is a warm, cozy retro-pixel style.

let hoverTile = null; // {x,y} or null, set by ui.js on mousemove

const MINIMAP_SCALE = 2.5; // world pixel scale-down factor for the minimap

// Whole-map overview: one flat-colored rect per tile (reusing each
// building's own catalog `color` — no separate minimap palette to keep in
// sync) plus a white rectangle showing the current camera viewport.
// Deliberately cheap/flat, not trying to mirror the detailed in-world art.
function renderMinimap(ctx, state, camera, mainCanvasW, mainCanvasH) {
  const cw = ctx.canvas.width, ch = ctx.canvas.height;
  ctx.fillStyle = '#1a160f';
  ctx.fillRect(0, 0, cw, ch);

  for (let y = 0; y < GRID_H; y++) {
    for (let x = 0; x < GRID_W; x++) {
      const t = state.grid[y][x];
      let color;
      if (t.type === 'grass') color = (x + y) % 2 === 0 ? '#3a5a2e' : '#33512a';
      else {
        const def = BUILDINGS[t.type];
        color = def ? def.color : '#3a5a2e';
      }
      ctx.fillStyle = color;
      ctx.fillRect(x * MINIMAP_SCALE, y * MINIMAP_SCALE, MINIMAP_SCALE, MINIMAP_SCALE);
    }
  }

  // Viewport rectangle — world-pixel camera rect scaled down to minimap space.
  const vx = (camera.x / TILE_SIZE) * MINIMAP_SCALE;
  const vy = (camera.y / TILE_SIZE) * MINIMAP_SCALE;
  const vw = (mainCanvasW / camera.zoom / TILE_SIZE) * MINIMAP_SCALE;
  const vh = (mainCanvasH / camera.zoom / TILE_SIZE) * MINIMAP_SCALE;
  ctx.strokeStyle = 'rgba(255,255,255,0.9)';
  ctx.lineWidth = 1.5;
  ctx.strokeRect(vx, vy, vw, vh);
}

function drawGrass(ctx, px, py, variant) {
  ctx.fillStyle = variant % 7 === 0 ? '#7cae52' : '#8fc45f';
  ctx.fillRect(px, py, TILE_SIZE, TILE_SIZE);
  // A few darker blades for texture, deterministic per-tile so it doesn't
  // flicker frame to frame.
  ctx.fillStyle = 'rgba(0,0,0,0.06)';
  const seed = variant * 2654435761 % 7;
  for (let i = 0; i < 3; i++) {
    const ox = ((seed + i * 5) % TILE_SIZE);
    const oy = ((seed * 3 + i * 9) % TILE_SIZE);
    ctx.fillRect(px + ox, py + oy, 2, 2);
  }
}

function drawRoad(ctx, px, py, tile, x, y, grid) {
  ctx.fillStyle = '#6b6459';
  ctx.fillRect(px, py, TILE_SIZE, TILE_SIZE);

  // Center-line marking follows whichever neighbors are actually roads —
  // straight, turning, T-junction, or 4-way crossing — instead of always
  // drawing a fixed left-right stripe regardless of orientation.
  const isRoad = (nx, ny) => {
    if (nx < 0 || ny < 0 || ny >= grid.length || nx >= grid[0].length) return false;
    return grid[ny][nx].type === 'road';
  };
  const up = isRoad(x, y - 1), down = isRoad(x, y + 1);
  const left = isRoad(x - 1, y), right = isRoad(x + 1, y);
  const cx = px + TILE_SIZE / 2, cy = py + TILE_SIZE / 2, lineW = 3;

  ctx.fillStyle = '#8a8175';
  if (left)  ctx.fillRect(px, cy - lineW / 2, TILE_SIZE / 2, lineW);
  if (right) ctx.fillRect(cx, cy - lineW / 2, TILE_SIZE / 2 + 1, lineW);
  if (up)    ctx.fillRect(cx - lineW / 2, py, lineW, TILE_SIZE / 2);
  if (down)  ctx.fillRect(cx - lineW / 2, cy, lineW, TILE_SIZE / 2 + 1);
  if (!left && !right && !up && !down) {
    // Isolated road stub — still show a small center mark.
    ctx.fillRect(cx - lineW / 2, cy - lineW / 2, lineW, lineW);
  }

  const load = tile.trafficLoad || 0;
  if (load >= TRAFFIC_LIGHT) {
    const heavy = load >= TRAFFIC_HEAVY;
    ctx.fillStyle = heavy ? 'rgba(224,96,61,0.45)' : 'rgba(224,178,61,0.35)';
    ctx.fillRect(px, py, TILE_SIZE, TILE_SIZE);
  }
}

function drawRoofBox(ctx, px, py, wallColor, roofColor, w = TILE_SIZE - 6, h = 18) {
  const x = px + (TILE_SIZE - w) / 2;
  const y = py + TILE_SIZE - h - 3;
  ctx.fillStyle = wallColor;
  ctx.fillRect(x, y, w, h);
  // Roof (triangle)
  ctx.fillStyle = roofColor;
  ctx.beginPath();
  ctx.moveTo(x - 2, y);
  ctx.lineTo(x + w / 2, y - 12);
  ctx.lineTo(x + w + 2, y);
  ctx.closePath();
  ctx.fill();
  // Windows
  ctx.fillStyle = 'rgba(255,240,180,0.8)';
  ctx.fillRect(x + 4, y + 5, 5, 5);
  ctx.fillRect(x + w - 9, y + 5, 5, 5);
}

function drawHouse(ctx, px, py, tile) {
  drawGrass(ctx, px, py, px + py);
  const wallColors = ['#c9a06b', '#c98a6b', '#a3906b'];
  const roofColors = ['#8a5a3a', '#7a4a3a', '#6a5030'];
  const lvl = tile.level - 1;
  const w = 18 + lvl * 4, h = 16 + lvl * 3;
  drawRoofBox(ctx, px, py, wallColors[lvl] || wallColors[0], roofColors[lvl] || roofColors[0], w, h);
  if (tile.level >= 2) {
    // second chimney/extension for bigger houses
    ctx.fillStyle = '#7a4a3a';
    ctx.fillRect(px + TILE_SIZE - 10, py + TILE_SIZE - h - 12, 4, 8);
  }
  if (!tile.powered || !tile.watered) {
    ctx.fillStyle = 'rgba(200,60,60,0.35)';
    ctx.fillRect(px, py, TILE_SIZE, TILE_SIZE);
  }
}

function drawShop(ctx, px, py, tile) {
  drawGrass(ctx, px, py, px + py);
  drawRoofBox(ctx, px, py, '#7fb2c9', '#3a6a80', 22 + tile.level * 3, 17);
  // Awning stripe
  ctx.fillStyle = '#c0524f';
  const x = px + (TILE_SIZE - (22 + tile.level * 3)) / 2;
  const y = py + TILE_SIZE - 17 - 3;
  ctx.fillRect(x - 1, y + 3, 22 + tile.level * 3 + 2, 3);
  if (!tile.powered || !tile.watered) {
    ctx.fillStyle = 'rgba(200,60,60,0.35)';
    ctx.fillRect(px, py, TILE_SIZE, TILE_SIZE);
  }
}

function drawFactory(ctx, px, py, tile) {
  drawGrass(ctx, px, py, px + py);
  const w = 24, h = 16;
  const x = px + (TILE_SIZE - w) / 2, y = py + TILE_SIZE - h - 3;
  ctx.fillStyle = '#8a7a6b';
  ctx.fillRect(x, y, w, h);
  ctx.fillStyle = '#6a5a4b';
  ctx.fillRect(x + 3, y - 14, 5, 14); // smokestack
  ctx.fillStyle = 'rgba(120,120,120,0.5)';
  ctx.beginPath();
  ctx.arc(x + 5, y - 18, 4, 0, Math.PI * 2);
  ctx.fill();
  if (!tile.powered || !tile.watered) {
    ctx.fillStyle = 'rgba(200,60,60,0.35)';
    ctx.fillRect(px, py, TILE_SIZE, TILE_SIZE);
  }
}

function drawFarm(ctx, px, py, tile) {
  ctx.fillStyle = '#8a6b3f';
  ctx.fillRect(px, py, TILE_SIZE, TILE_SIZE);
  // furrow rows
  ctx.fillStyle = '#77592f';
  for (let i = 4; i < TILE_SIZE; i += 6) ctx.fillRect(px, py + i, TILE_SIZE, 2);
  const progress = tile.farmStage / BUILDINGS.farm.growDays;
  if (progress > 0) {
    ctx.fillStyle = progress >= 1 ? '#e0c23d' : '#8fc45f';
    const sprouts = Math.ceil(progress * 6);
    for (let i = 0; i < sprouts; i++) {
      const cx = px + 4 + (i % 3) * 9;
      const cy = py + 6 + Math.floor(i / 3) * 12;
      ctx.beginPath();
      ctx.arc(cx, cy, progress >= 1 ? 4 : 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  if (progress >= 1) {
    // ripe glow
    ctx.strokeStyle = 'rgba(255,230,120,0.9)';
    ctx.lineWidth = 2;
    ctx.strokeRect(px + 2, py + 2, TILE_SIZE - 4, TILE_SIZE - 4);
  }
}

function drawUtility(ctx, px, py, tile, kind) {
  drawGrass(ctx, px, py, px + py);
  if (kind === 'power') {
    ctx.fillStyle = '#3a3a3a';
    ctx.fillRect(px + 13, py + 4, 6, 24);
    ctx.strokeStyle = '#e0b23d';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(px + 8, py + 12); ctx.lineTo(px + TILE_SIZE - 8, py + 12);
    ctx.moveTo(px + 8, py + 20); ctx.lineTo(px + TILE_SIZE - 8, py + 20);
    ctx.stroke();
    ctx.fillStyle = '#f2d060';
    ctx.beginPath();
    ctx.arc(px + 16, py + 6, 3, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.fillStyle = '#4f8fc0';
    ctx.beginPath();
    ctx.arc(px + 16, py + 14, 11, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#3a3a3a';
    ctx.fillRect(px + 13, py + 22, 6, 8);
    ctx.fillRect(px + 8, py + 28, 16, 2);
  }
}

function drawService(ctx, px, py, tile, kind) {
  drawGrass(ctx, px, py, px + py);
  const map = {
    clinic:      { wall: '#e8e0da', roof: '#e37b7b', symbol: '+' },
    school:      { wall: '#e0d8c0', roof: '#8a7bc9', symbol: 'A' },
    library:     { wall: '#e0dfd0', roof: '#7b9ac9', symbol: 'B' },
    university:  { wall: '#d8d4e8', roof: '#5a4a9c', symbol: 'U' },
    police:      { wall: '#d8dce0', roof: '#4f6fa0', symbol: '★' },
    firestation: { wall: '#e0d0c8', roof: '#b0453f', symbol: '▲' },
    park:        { wall: null,       roof: null,      symbol: null }
  };
  const c = map[kind];
  if (kind === 'park') {
    ctx.fillStyle = '#5fae6e';
    ctx.beginPath(); ctx.arc(px + 10, py + 16, 8, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(px + 22, py + 12, 7, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#6a4a30';
    ctx.fillRect(px + 15, py + 20, 3, 8);
    return;
  }
  // University gets a slightly bigger campus-y footprint than the other
  // single-room service buildings, for visual hierarchy.
  const w = kind === 'university' ? 26 : 22, h = kind === 'university' ? 19 : 16;
  drawRoofBox(ctx, px, py, c.wall, c.roof, w, h);
  if (kind === 'university') {
    // A second small wing, like a campus annex.
    ctx.fillStyle = c.wall;
    ctx.fillRect(px + TILE_SIZE - 9, py + TILE_SIZE - h + 2, 5, h - 5);
  }
  ctx.fillStyle = c.roof;
  ctx.font = 'bold 10px monospace';
  ctx.textAlign = 'center';
  ctx.fillText(c.symbol, px + TILE_SIZE / 2, py + TILE_SIZE - 6);
}

function drawMall(ctx, px, py, tile) {
  drawGrass(ctx, px, py, px + py);
  const w = 26 + tile.level * 2, h = 18;
  const x = px + (TILE_SIZE - w) / 2, y = py + TILE_SIZE - h - 3;
  // Flat modern roofline (not the peaked cottage roof) — a wide low
  // block with a row of glass-front windows, to read as "big box store"
  // rather than another house/shop silhouette.
  ctx.fillStyle = '#e0d0e0';
  ctx.fillRect(x, y, w, h);
  ctx.fillStyle = '#c98fb2';
  ctx.fillRect(x - 2, y - 4, w + 4, 5); // flat overhang roof
  ctx.fillStyle = 'rgba(160,220,255,0.75)';
  const winCount = 3 + tile.level;
  const winW = (w - 6) / winCount - 2;
  for (let i = 0; i < winCount; i++) {
    ctx.fillRect(x + 3 + i * (winW + 2), y + 6, winW, h - 10);
  }
  if (!tile.powered || !tile.watered) {
    ctx.fillStyle = 'rgba(200,60,60,0.35)';
    ctx.fillRect(px, py, TILE_SIZE, TILE_SIZE);
  }
}

function drawStadium(ctx, px, py) {
  drawGrass(ctx, px, py, px + py);
  // Oval bowl shape with tiered "seating" rings, plus 4 corner floodlights.
  ctx.fillStyle = '#9c9080';
  ctx.beginPath();
  ctx.ellipse(px + TILE_SIZE / 2, py + TILE_SIZE / 2 + 2, 15, 11, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#4f9c6a';
  ctx.beginPath();
  ctx.ellipse(px + TILE_SIZE / 2, py + TILE_SIZE / 2 + 2, 9, 6, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = 'rgba(255,255,255,0.6)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(px + TILE_SIZE / 2, py + TILE_SIZE / 2 - 4);
  ctx.lineTo(px + TILE_SIZE / 2, py + TILE_SIZE / 2 + 8);
  ctx.stroke();
  ctx.fillStyle = '#3a3a3a';
  const lights = [[px + 4, py + 5], [px + TILE_SIZE - 4, py + 5],
                  [px + 4, py + TILE_SIZE - 5], [px + TILE_SIZE - 4, py + TILE_SIZE - 5]];
  for (const [lx, ly] of lights) {
    ctx.fillRect(lx - 1, ly - 4, 2, 6);
    ctx.fillStyle = '#f2e060';
    ctx.beginPath(); ctx.arc(lx, ly - 5, 1.6, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#3a3a3a';
  }
}

function drawStatue(ctx, px, py) {
  drawGrass(ctx, px, py, px + py);
  ctx.fillStyle = '#8a8265';
  ctx.fillRect(px + 10, py + 22, 12, 6);
  ctx.fillStyle = '#b7a97a';
  ctx.fillRect(px + 13, py + 8, 6, 16);
  ctx.beginPath();
  ctx.arc(px + 16, py + 6, 5, 0, Math.PI * 2);
  ctx.fill();
}

function drawTree(ctx, px, py) {
  drawGrass(ctx, px, py, px + py);
  ctx.fillStyle = '#6a4a30';
  ctx.fillRect(px + 14, py + 18, 4, 10);
  ctx.fillStyle = '#3f8b4a';
  ctx.beginPath(); ctx.arc(px + 16, py + 12, 10, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#4fae5e';
  ctx.beginPath(); ctx.arc(px + 12, py + 9, 6, 0, Math.PI * 2); ctx.fill();
}

function drawTownHall(ctx, px, py) {
  drawGrass(ctx, px, py, px + py);
  drawRoofBox(ctx, px, py, '#c9c0b0', '#c04f4f', 26, 20);
  ctx.fillStyle = '#c04f4f';
  ctx.fillRect(px + TILE_SIZE / 2 - 2, py + TILE_SIZE - 30, 4, 8); // little flag pole
  ctx.fillStyle = '#e0e0e0';
  ctx.beginPath(); ctx.moveTo(px + TILE_SIZE / 2 + 2, py + TILE_SIZE - 30);
  ctx.lineTo(px + TILE_SIZE / 2 + 9, py + TILE_SIZE - 27);
  ctx.lineTo(px + TILE_SIZE / 2 + 2, py + TILE_SIZE - 24);
  ctx.fill();
}

function drawTile(ctx, x, y, tile, grid) {
  const px = x * TILE_SIZE, py = y * TILE_SIZE;
  switch (tile.type) {
    case 'grass':    drawGrass(ctx, px, py, x * 31 + y * 17); break;
    case 'road':     drawRoad(ctx, px, py, tile, x, y, grid); break;
    case 'house':    drawHouse(ctx, px, py, tile); break;
    case 'shop':     drawShop(ctx, px, py, tile); break;
    case 'mall':     drawMall(ctx, px, py, tile); break;
    case 'factory':  drawFactory(ctx, px, py, tile); break;
    case 'farm':     drawFarm(ctx, px, py, tile); break;
    case 'power':    drawUtility(ctx, px, py, tile, 'power'); break;
    case 'water':    drawUtility(ctx, px, py, tile, 'water'); break;
    case 'clinic':   drawService(ctx, px, py, tile, 'clinic'); break;
    case 'school':   drawService(ctx, px, py, tile, 'school'); break;
    case 'library':  drawService(ctx, px, py, tile, 'library'); break;
    case 'university': drawService(ctx, px, py, tile, 'university'); break;
    case 'park':     drawService(ctx, px, py, tile, 'park'); break;
    case 'police':   drawService(ctx, px, py, tile, 'police'); break;
    case 'firestation': drawService(ctx, px, py, tile, 'firestation'); break;
    case 'stadium':  drawStadium(ctx, px, py); break;
    case 'tree':     drawTree(ctx, px, py); break;
    case 'statue':   drawStatue(ctx, px, py); break;
    case 'townhall': drawTownHall(ctx, px, py); break;
    default:         drawGrass(ctx, px, py, x * 31 + y * 17);
  }
}

function drawNpc(ctx, npc) {
  const px = npc.x * TILE_SIZE + TILE_SIZE / 2;
  const py = npc.y * TILE_SIZE + TILE_SIZE / 2;
  const bob = Math.sin(npc.walkPhase * 6) * 1.5;
  // shadow
  ctx.fillStyle = 'rgba(0,0,0,0.2)';
  ctx.beginPath();
  ctx.ellipse(px, py + 7, 5, 2, 0, 0, Math.PI * 2);
  ctx.fill();
  // legs (tiny alternating steps)
  ctx.fillStyle = '#3a3a3a';
  const strideL = Math.sin(npc.walkPhase * 6);
  ctx.fillRect(px - 3, py + 2 + Math.max(0, strideL), 2, 5);
  ctx.fillRect(px + 1, py + 2 + Math.max(0, -strideL), 2, 5);
  // body
  ctx.fillStyle = npc.shirt;
  ctx.fillRect(px - 4, py - 6 + bob * 0.2, 8, 8);
  // head
  ctx.fillStyle = npc.skin;
  ctx.beginPath();
  ctx.arc(px, py - 9 + bob * 0.2, 4, 0, Math.PI * 2);
  ctx.fill();
  // mood dot — small, subtle, reflects home happiness + employment status
  const moodColor = npc.mood === 'happy' ? '#7fd67f' : npc.mood === 'sad' ? '#d67f7f' : '#d6c67f';
  ctx.fillStyle = moodColor;
  ctx.beginPath();
  ctx.arc(px + 4, py - 13 + bob * 0.2, 1.6, 0, Math.PI * 2);
  ctx.fill();
}

function drawHoverHighlight(ctx, state) {
  if (!hoverTile) return;
  const { x, y } = hoverTile;
  if (x < 0 || y < 0 || x >= GRID_W || y >= GRID_H) return;

  // Coverage-radius preview for radius-based tools (Power Plant, Water
  // Tower, every service building) -- coverage is pure proximity, no
  // roads/wires needed to "connect" a house to it, which isn't obvious
  // from the UI otherwise (reported live: a player assumed a connection
  // was required, built too far away, and got stuck with unpowered
  // houses despite having built both). Drawn BEHIND the tile-select
  // outline so the single-tile highlight still reads clearly on top.
  const radius = state && TOOL_RADIUS[state.selectedTool];
  if (radius) {
    const rx = (x - radius) * TILE_SIZE, ry = (y - radius) * TILE_SIZE;
    const rsize = (radius * 2 + 1) * TILE_SIZE;
    ctx.fillStyle = 'rgba(255,220,120,0.10)';
    ctx.fillRect(rx, ry, rsize, rsize);
    ctx.strokeStyle = 'rgba(255,220,120,0.6)';
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(rx, ry, rsize, rsize);
    ctx.setLineDash([]);
  }

  ctx.strokeStyle = 'rgba(255,255,255,0.85)';
  ctx.lineWidth = 2;
  ctx.strokeRect(x * TILE_SIZE + 1, y * TILE_SIZE + 1, TILE_SIZE - 2, TILE_SIZE - 2);
}

// Day/night + seasonal tint overlay drawn on top of everything.
// NOTE: previously this also pulsed a dark overlay in/out once per
// simulated day via cos(dayFraction*2pi) — since one "day" is one real-time
// tick (2.6s at normal speed, faster at higher speeds), that produced a
// distracting full dim->bright->dim flicker every single tick instead of a
// subtle effect. dayFraction is now only used to drive NPC home/work
// schedules (see npc.js), not screen darkening. Only the season tint
// (which changes once per season, ~20 days) remains here.
function drawAmbientOverlay(ctx, state, dayFraction, cw, ch) {
  const seasonTints = ['rgba(140,220,140,0.04)', 'rgba(255,230,120,0.04)',
                       'rgba(230,140,60,0.07)', 'rgba(180,210,255,0.10)'];
  ctx.fillStyle = seasonTints[state.season] || 'transparent';
  ctx.fillRect(0, 0, cw, ch);
}

// ── Weather particles: rain (active storms) and snow (Winter) ──────────────
// Screen-space, not world-space — deliberately not tied to camera pan/zoom
// (matches how real screen-space weather effects work in this genre: it's
// weather over the *view*, not physical objects in the world that would
// need per-tile simulation). Re-seeded whenever the active kind changes
// (storm starts/ends, season changes) or the canvas is resized.
let weatherParticles = [];
let weatherKind = null;
let weatherCanvasW = 0, weatherCanvasH = 0;

function seedWeather(kind, cw, ch) {
  weatherKind = kind;
  weatherCanvasW = cw; weatherCanvasH = ch;
  const count = kind === 'rain' ? 90 : 55;
  weatherParticles = [];
  for (let i = 0; i < count; i++) {
    weatherParticles.push({
      x: Math.random() * cw,
      y: Math.random() * ch,
      speed: kind === 'rain' ? 9 + Math.random() * 5 : 1 + Math.random() * 1.2,
      drift: kind === 'snow' ? (Math.random() - 0.5) * 0.6 : 0,
      len: kind === 'rain' ? 10 + Math.random() * 8 : 0,
      size: kind === 'snow' ? 1.5 + Math.random() * 2 : 0,
      phase: Math.random() * Math.PI * 2
    });
  }
}

function updateAndDrawWeather(ctx, state, cw, ch, dt) {
  const stormActive = (state.stormDaysLeft || 0) > 0;
  const isWinter = SEASONS[state.season] === 'Winter';
  const kind = stormActive ? 'rain' : (isWinter ? 'snow' : null);

  if (!kind) { weatherParticles = []; weatherKind = null; return; }
  if (kind !== weatherKind || cw !== weatherCanvasW || ch !== weatherCanvasH) {
    seedWeather(kind, cw, ch);
  }

  if (kind === 'rain') {
    ctx.strokeStyle = 'rgba(180,200,230,0.5)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (const p of weatherParticles) {
      p.y += p.speed * dt;
      p.x -= p.speed * 0.25 * dt; // slight wind-blown angle
      if (p.y > ch) { p.y = -p.len; p.x = Math.random() * cw; }
      if (p.x < 0) p.x = cw;
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(p.x + p.len * 0.25, p.y + p.len);
    }
    ctx.stroke();
  } else {
    ctx.fillStyle = 'rgba(255,255,255,0.85)';
    for (const p of weatherParticles) {
      p.y += p.speed * dt;
      p.phase += 0.02 * dt;
      p.x += Math.sin(p.phase) * 0.3 + p.drift * dt * 0.3;
      if (p.y > ch) { p.y = -p.size; p.x = Math.random() * cw; }
      if (p.x < 0) p.x = cw; else if (p.x > cw) p.x = 0;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

// SimCity-style "data view" overlays: Land Value (green = desirable, red =
// undesirable), Crime (only meaningful on house tiles — everything else is
// left untinted), Traffic (nearby road congestion, every tile). Values are
// cached per-tile by computeTileOverlays() (simulation.js), refreshed once
// per simulated day; default to a neutral baseline (see makeEmptyTile())
// so this never sees undefined/NaN even before the first day-tick, or on
// an old save that predates these fields.
function drawViewModeOverlay(ctx, state, startX, endX, startY, endY) {
  for (let y = startY; y <= endY; y++) {
    for (let x = startX; x <= endX; x++) {
      const t = state.grid[y][x];
      const px = x * TILE_SIZE, py = y * TILE_SIZE;
      let color = null;

      if (state.viewMode === 'landvalue') {
        const v = t.landValue ?? 40;
        // 0 -> red, 50 -> yellow, 100 -> green
        color = v < 50
          ? `rgba(200,${Math.round(60 + v * 3.2)},50,0.42)`
          : `rgba(${Math.round(220 - (v - 50) * 3.2)},190,50,0.42)`;
      } else if (state.viewMode === 'crime') {
        if (t.type !== 'house') continue;
        const c = t.crimeScore ?? 0;
        if (c < 5) continue; // don't bother tinting essentially-safe houses
        color = `rgba(200,50,50,${Math.min(0.55, 0.1 + c / 100 * 0.5)})`;
      } else if (state.viewMode === 'traffic') {
        const load = t.trafficNear ?? 0;
        if (load < TRAFFIC_LIGHT) continue;
        const heavy = load >= TRAFFIC_HEAVY;
        color = heavy ? 'rgba(224,96,61,0.5)' : 'rgba(224,178,61,0.4)';
      }

      if (color) {
        ctx.fillStyle = color;
        ctx.fillRect(px, py, TILE_SIZE, TILE_SIZE);
      }
    }
  }
}

// `camera` = { x, y, zoom } — x/y are the world-pixel coords of the
// viewport's top-left corner. Only tiles within the visible world-rect are
// drawn (the map is bigger than the screen now), and NPCs outside that
// rect are skipped too since they're cheap to filter and drawing them
// off-screen would be pure waste.
function renderFrame(ctx, state, dayFraction, camera, dt = 1) {
  const cw = ctx.canvas.width, ch = ctx.canvas.height;
  ctx.clearRect(0, 0, cw, ch);

  ctx.save();
  ctx.scale(camera.zoom, camera.zoom);
  ctx.translate(-camera.x, -camera.y);

  const viewWorldW = cw / camera.zoom, viewWorldH = ch / camera.zoom;
  const startX = Math.max(0, Math.floor(camera.x / TILE_SIZE));
  const endX = Math.min(GRID_W - 1, Math.ceil((camera.x + viewWorldW) / TILE_SIZE));
  const startY = Math.max(0, Math.floor(camera.y / TILE_SIZE));
  const endY = Math.min(GRID_H - 1, Math.ceil((camera.y + viewWorldH) / TILE_SIZE));

  for (let y = startY; y <= endY; y++) {
    for (let x = startX; x <= endX; x++) drawTile(ctx, x, y, state.grid[y][x], state.grid);
  }
  if (state.viewMode && state.viewMode !== 'normal') {
    drawViewModeOverlay(ctx, state, startX, endX, startY, endY);
  }
  for (const npc of npcs) {
    if (npc.x >= startX - 1 && npc.x <= endX + 1 && npc.y >= startY - 1 && npc.y <= endY + 1) {
      drawNpc(ctx, npc);
    }
  }
  drawHoverHighlight(ctx, state);
  ctx.restore();

  // Screen-space overlays (not affected by zoom/pan).
  drawAmbientOverlay(ctx, state, dayFraction, cw, ch);
  updateAndDrawWeather(ctx, state, cw, ch, dt);
}

// ── Stats modal chart: population/cash/happiness over time ─────────────────
// Each series is normalized to ITS OWN min/max (not a shared scale) since
// population/cash/happiness live on wildly different ranges — this is a
// trend chart (shape over time), not a chart meant for reading absolute
// cross-series values off the same axis.
function renderStatsChart(ctx, history) {
  const cw = ctx.canvas.width, ch = ctx.canvas.height;
  ctx.clearRect(0, 0, cw, ch);
  ctx.fillStyle = '#201c16';
  ctx.fillRect(0, 0, cw, ch);

  if (!history || history.length < 2) {
    ctx.fillStyle = '#b8ac94';
    ctx.font = '14px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Not enough history yet — keep playing!', cw / 2, ch / 2);
    return;
  }

  const padL = 10, padR = 10, padT = 14, padB = 14;
  const plotW = cw - padL - padR, plotH = ch - padT - padB;

  // Faint horizontal gridlines.
  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padT + (plotH * i) / 4;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(cw - padR, y); ctx.stroke();
  }

  function drawSeries(key, color) {
    const values = history.map(h => h[key]);
    let min = Math.min(...values), max = Math.max(...values);
    if (min === max) { min -= 1; max += 1; } // avoid a flat divide-by-zero line
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = padL + (plotW * i) / (values.length - 1);
      const y = padT + plotH - ((v - min) / (max - min)) * plotH;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  drawSeries('population', '#5fae6e');
  drawSeries('cash', '#e0b23d');
  drawSeries('happiness', '#7fb2c9');

  ctx.fillStyle = '#b8ac94';
  ctx.font = '11px sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText(`Day ${history[0].day}`, padL, ch - 2);
  ctx.textAlign = 'right';
  ctx.fillText(`Day ${history[history.length - 1].day}`, cw - padR, ch - 2);
}
