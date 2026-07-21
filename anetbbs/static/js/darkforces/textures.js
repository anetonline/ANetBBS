// ─── ANetDarkForces: procedural textures ─────────────────────────────────
// Every wall/sprite texture is drawn with primitive canvas shapes at
// startup into small offscreen canvases, then sampled by the raycaster —
// no external image assets, matching this project's zero-dependency
// philosophy (same approach Meadowlark Valley uses for its pixel art).

const WALL_TEXTURES = {}; // wall type number -> offscreen canvas

function makeTextureCanvas(size = TILE) {
  const c = document.createElement('canvas');
  c.width = size; c.height = size;
  return c;
}

function buildWallTexture(type, def) {
  const c = makeTextureCanvas();
  const ctx = c.getContext('2d');
  const s = c.width;
  ctx.fillStyle = def.base;
  ctx.fillRect(0, 0, s, s);

  switch (type) {
    case 1: { // Cinderblock — brick grid
      ctx.strokeStyle = def.mortar;
      ctx.lineWidth = 2;
      const rows = 4, cols = 4;
      for (let r = 0; r <= rows; r++) {
        const y = (r / rows) * s;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(s, y); ctx.stroke();
      }
      for (let r = 0; r < rows; r++) {
        const offset = (r % 2) * (s / cols / 2);
        for (let cI = -1; cI <= cols; cI++) {
          const x = offset + (cI / cols) * s;
          const y0 = (r / rows) * s, y1 = ((r + 1) / rows) * s;
          ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y1); ctx.stroke();
        }
      }
      break;
    }
    case 2: { // Steel Shutter — horizontal ridges
      ctx.fillStyle = def.mortar;
      for (let y = 0; y < s; y += 8) ctx.fillRect(0, y, s, 3);
      ctx.strokeStyle = 'rgba(255,255,255,0.15)';
      ctx.lineWidth = 1;
      for (let y = 2; y < s; y += 8) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(s, y); ctx.stroke(); }
      break;
    }
    case 3: { // Shelving Unit — wood grain + shelf lines
      ctx.strokeStyle = 'rgba(0,0,0,0.12)';
      for (let i = 0; i < 10; i++) {
        ctx.beginPath();
        ctx.moveTo(0, (i / 10) * s + Math.sin(i) * 3);
        ctx.lineTo(s, (i / 10) * s + Math.cos(i) * 3);
        ctx.stroke();
      }
      ctx.fillStyle = def.mortar;
      for (let y = 0; y < s; y += s / 3) ctx.fillRect(0, y, s, 4);
      break;
    }
    case 4: { // Server Rack — dark panel with slots + status lights
      ctx.fillStyle = def.mortar;
      for (let y = 4; y < s; y += 10) ctx.fillRect(4, y, s - 8, 6);
      const lightColors = ['#4fae6e', '#e0b23d', '#c0524f'];
      for (let y = 4; y < s; y += 10) {
        for (let i = 0; i < 2; i++) {
          ctx.fillStyle = lightColors[(y + i * 5) % lightColors.length];
          ctx.fillRect(s - 14 + i * 6, y + 2, 3, 2);
        }
      }
      break;
    }
    case 5: { // Neon Sign Wall — dark purple with pink neon grid glow
      ctx.strokeStyle = def.mortar;
      ctx.lineWidth = 2;
      ctx.shadowColor = def.mortar;
      ctx.shadowBlur = 4;
      ctx.beginPath();
      ctx.moveTo(0, s * 0.3); ctx.lineTo(s, s * 0.3);
      ctx.moveTo(0, s * 0.7); ctx.lineTo(s, s * 0.7);
      ctx.moveTo(s * 0.5, 0); ctx.lineTo(s * 0.5, s);
      ctx.stroke();
      ctx.shadowBlur = 0;
      break;
    }
    case 6: { // Loading Dock — corrugated horizontal stripes, warm tone
      ctx.fillStyle = def.mortar;
      for (let y = 0; y < s; y += 6) ctx.fillRect(0, y, s, 2);
      break;
    }
    case 7: { // Security Door — diagonal hazard stripes + a center keypad light
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, s, s); ctx.clip();
      ctx.strokeStyle = def.mortar;
      ctx.lineWidth = 6;
      for (let x = -s; x < s * 2; x += 16) {
        ctx.beginPath(); ctx.moveTo(x, s); ctx.lineTo(x + s, 0); ctx.stroke();
      }
      ctx.restore();
      ctx.fillStyle = '#1c1a16';
      ctx.fillRect(s * 0.38, s * 0.42, s * 0.24, s * 0.16);
      ctx.fillStyle = '#5fae6e';
      ctx.fillRect(s * 0.44, s * 0.47, s * 0.12, s * 0.06);
      break;
    }
    case 9: { // Ammo Dispenser — vending-machine panel, deliberately eye-catching (unlike a secret, this one WANTS to be found)
      ctx.fillStyle = def.mortar;
      ctx.fillRect(s * 0.15, s * 0.1, s * 0.7, s * 0.15);
      ctx.strokeStyle = def.mortar;
      ctx.lineWidth = 2;
      ctx.strokeRect(s * 0.2, s * 0.3, s * 0.6, s * 0.5);
      ctx.fillStyle = '#0a1c24';
      ctx.fillRect(s * 0.25, s * 0.35, s * 0.5, s * 0.3);
      ctx.fillStyle = def.mortar;
      ctx.fillRect(s * 0.3, s * 0.7, s * 0.4, s * 0.08);
      break;
    }
    case 10: { // Vault Door — vertical bars (distinct from Security Door's diagonal stripes) + a lock icon
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, s, s); ctx.clip();
      ctx.strokeStyle = def.mortar;
      ctx.lineWidth = 4;
      for (let x = 6; x < s; x += 20) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, s); ctx.stroke(); }
      ctx.restore();
      ctx.fillStyle = '#1c1a16';
      ctx.beginPath(); ctx.arc(s * 0.5, s * 0.5, s * 0.13, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = def.mortar;
      ctx.lineWidth = 3;
      ctx.stroke();
      break;
    }
    default: {
      ctx.strokeStyle = def.mortar;
      ctx.strokeRect(2, 2, s - 4, s - 4);
    }
  }
  // Subtle vertical shading gradient on every texture, for a bit of depth.
  const grad = ctx.createLinearGradient(0, 0, 0, s);
  grad.addColorStop(0, 'rgba(255,255,255,0.06)');
  grad.addColorStop(1, 'rgba(0,0,0,0.12)');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, s, s);
  return c;
}

function buildAllWallTextures() {
  for (const key in WALL_TYPES) {
    WALL_TEXTURES[key] = buildWallTexture(parseInt(key, 10), WALL_TYPES[key]);
  }
  // Secret walls are meant to be found by shooting/interacting, not by
  // eye -- alias their texture directly to Cinderblock's canvas so
  // there's genuinely no visual tell.
  WALL_TEXTURES[SECRET_TYPE] = WALL_TEXTURES[1];
}

// ── Sprite textures: enemies, pickups ── (the on-screen weapon viewmodel
// is drawn fresh each frame in raycaster.js instead of cached here, since
// it bobs/kicks dynamically rather than being a static texture)
// Rank-and-file enemies (scalper/goon/guard/tech) are drawn as readable
// human silhouettes -- tapered torso, a face (eyes read at a glance far
// better than a flat blob), and a small gear flourish per type so they're
// tellable apart from across a room. Every shape gets a dark outline
// stroke, which is what actually fixes them "melting into" a similarly
// dark wall texture behind them (that was a readability problem, not a
// collision one -- the collision/embedding bug is fixed separately in
// levels.js's clearance-checked spawn points). The boss is deliberately
// NOT human -- a jagged, oversized, glowing-eyed mass, per the mixed
// human-mooks/monstrous-boss art direction.
const SPRITE_TEXTURES = {}; // key -> canvas

function drawHumanoidSprite(ctx, s, def) {
  const cx = s / 2;
  const bodyColor = def.color;
  const headColor = '#e8b98a';
  const outline = 'rgba(8,6,10,0.7)';
  ctx.lineJoin = 'round';

  const fillStroke = (drawFn, color, lw) => {
    ctx.beginPath();
    drawFn();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = outline;
    ctx.lineWidth = lw || 2;
    ctx.stroke();
  };

  // legs (slight stance, not a straight block)
  fillStroke(() => ctx.rect(cx - s * 0.15, s * 0.76, s * 0.11, s * 0.22), '#232025');
  fillStroke(() => ctx.rect(cx + s * 0.04, s * 0.76, s * 0.11, s * 0.22), '#232025');
  // torso, tapered at the waist -- reads more human than a flat rectangle
  fillStroke(() => {
    ctx.moveTo(cx - s * 0.24, s * 0.42);
    ctx.lineTo(cx + s * 0.24, s * 0.42);
    ctx.lineTo(cx + s * 0.19, s * 0.80);
    ctx.lineTo(cx - s * 0.19, s * 0.80);
    ctx.closePath();
  }, bodyColor);
  // arms
  fillStroke(() => ctx.rect(cx - s * 0.34, s * 0.46, s * 0.11, s * 0.3), bodyColor);
  fillStroke(() => ctx.rect(cx + s * 0.23, s * 0.46, s * 0.11, s * 0.3), bodyColor);
  // head
  fillStroke(() => ctx.arc(cx, s * 0.27, s * 0.15, 0, Math.PI * 2), headColor);
  // eyes -- the single biggest legibility upgrade over a blank head circle
  ctx.fillStyle = '#241c18';
  ctx.beginPath(); ctx.arc(cx - s * 0.05, s * 0.26, s * 0.018, 0, Math.PI * 2); ctx.fill();
  ctx.beginPath(); ctx.arc(cx + s * 0.05, s * 0.26, s * 0.018, 0, Math.PI * 2); ctx.fill();
  // weapon stub
  ctx.fillStyle = '#2c2c2c';
  ctx.strokeStyle = outline;
  ctx.lineWidth = 1.5;
  ctx.fillRect(cx + s * 0.18, s * 0.52, s * 0.24, s * 0.06);
  ctx.strokeRect(cx + s * 0.18, s * 0.52, s * 0.24, s * 0.06);

  // per-type gear silhouette, so each enemy is tellable apart at a glance
  switch (def.key) {
    case 'scalper': // ballcap -- fast, scrappy
      fillStroke(() => {
        ctx.moveTo(cx - s * 0.17, s * 0.19); ctx.lineTo(cx + s * 0.17, s * 0.19);
        ctx.lineTo(cx + s * 0.13, s * 0.09); ctx.lineTo(cx - s * 0.13, s * 0.09);
      }, '#3a2f1a', 1.5);
      break;
    case 'goon': // bulky jacket collar
      fillStroke(() => ctx.rect(cx - s * 0.23, s * 0.40, s * 0.46, s * 0.07), '#4a2a2a', 1.5);
      break;
    case 'guard': // helmet dome + chest badge
      fillStroke(() => ctx.arc(cx, s * 0.24, s * 0.175, Math.PI, 0, true), '#2f3f55', 1.5);
      ctx.fillStyle = '#e0b23d';
      ctx.beginPath(); ctx.arc(cx, s * 0.52, s * 0.028, 0, Math.PI * 2); ctx.fill();
      break;
    case 'tech': // goggles
      ctx.fillStyle = '#1c1c1c';
      ctx.fillRect(cx - s * 0.115, s * 0.235, s * 0.23, s * 0.045);
      ctx.fillStyle = '#9fe6ff';
      ctx.fillRect(cx - s * 0.095, s * 0.24, s * 0.075, s * 0.034);
      ctx.fillRect(cx + s * 0.02, s * 0.24, s * 0.075, s * 0.034);
      break;
  }
}

// A jagged, oversized, glowing-eyed mass -- deliberately NOT humanoid, so
// the boss reads as something else entirely the moment it comes into view.
function drawMonsterBoss(ctx, s, def) {
  const cx = s / 2;
  const accent = def.color;
  ctx.lineJoin = 'round';

  ctx.beginPath();
  const pts = [
    [0.30, 1.00], [0.22, 0.86], [0.10, 0.74], [0.16, 0.58], [0.06, 0.44],
    [0.20, 0.30], [0.16, 0.16], [0.34, 0.06], [0.50, 0.00], [0.66, 0.06],
    [0.84, 0.16], [0.80, 0.30], [0.94, 0.44], [0.84, 0.58], [0.90, 0.74],
    [0.78, 0.86], [0.70, 1.00],
  ];
  pts.forEach(([px, py], i) => {
    const x = px * s, y = py * s;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.closePath();
  ctx.fillStyle = '#211018';
  ctx.fill();
  ctx.strokeStyle = 'rgba(4,2,4,0.85)';
  ctx.lineWidth = 3;
  ctx.stroke();

  // Chest core glow -- breaks up the silhouette, reads as an energy core.
  ctx.globalAlpha = 0.4;
  ctx.fillStyle = accent;
  ctx.beginPath();
  ctx.ellipse(cx, s * 0.60, s * 0.15, s * 0.20, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.globalAlpha = 1;

  // Horn/spike flourishes.
  ctx.fillStyle = '#211018';
  ctx.strokeStyle = 'rgba(4,2,4,0.85)';
  [[0.30, 0.06, 0.24, -0.08], [0.70, 0.06, 0.76, -0.08]].forEach(([x1, y1, x2, y2]) => {
    ctx.beginPath();
    ctx.moveTo(x1 * s, y1 * s); ctx.lineTo(x2 * s, y2 * s); ctx.lineTo((x1 + 0.06) * s, (y1 + 0.02) * s);
    ctx.closePath(); ctx.fill(); ctx.stroke();
  });

  // Glowing eyes.
  ctx.shadowColor = accent;
  ctx.shadowBlur = 8;
  ctx.fillStyle = '#ffdf8a';
  [[0.40, 0.28], [0.60, 0.28]].forEach(([ex, ey]) => {
    ctx.beginPath(); ctx.ellipse(ex * s, ey * s, s * 0.045, s * 0.03, 0, 0, Math.PI * 2); ctx.fill();
  });
  ctx.shadowBlur = 0;
}

// Small hovering delivery-bot silhouette -- ellipse body, two angled side
// fins, a glowing warning light. Reads as a machine, not a person, which
// matters at a glance since this one rushes you.
function drawDroneSprite(ctx, s, def) {
  const cx = s / 2, cy = s * 0.5;
  ctx.strokeStyle = 'rgba(8,6,10,0.7)';
  ctx.lineWidth = 2;
  ctx.fillStyle = '#2c2c2c';
  ctx.beginPath(); ctx.ellipse(cx, cy, s * 0.22, s * 0.16, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  ctx.fillStyle = def.color;
  ctx.beginPath(); ctx.ellipse(cx - s * 0.30, cy, s * 0.12, s * 0.05, 0.3, 0, Math.PI * 2); ctx.fill();
  ctx.beginPath(); ctx.ellipse(cx + s * 0.30, cy, s * 0.12, s * 0.05, -0.3, 0, Math.PI * 2); ctx.fill();
  ctx.shadowColor = '#ff4f4f';
  ctx.shadowBlur = 10;
  ctx.fillStyle = '#ff4f4f';
  ctx.beginPath(); ctx.arc(cx, cy, s * 0.05, 0, Math.PI * 2); ctx.fill();
  ctx.shadowBlur = 0;
}

// Wall-mounted camera housing -- a bracket, a lens dome, a glowing "I see
// you" red eye. Deliberately not humanoid at all -- this one never moves.
function drawTurretSprite(ctx, s, def) {
  const cx = s / 2;
  ctx.fillStyle = '#1c1c1c';
  ctx.fillRect(cx - s * 0.05, s * 0.08, s * 0.1, s * 0.32);
  ctx.strokeStyle = 'rgba(8,6,10,0.7)';
  ctx.lineWidth = 2;
  ctx.fillStyle = def.color;
  ctx.beginPath(); ctx.ellipse(cx, s * 0.5, s * 0.2, s * 0.16, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  ctx.fillStyle = '#1c1c1c';
  ctx.fillRect(cx - s * 0.28, s * 0.46, s * 0.1, s * 0.08);
  ctx.fillRect(cx + s * 0.18, s * 0.46, s * 0.1, s * 0.08);
  ctx.shadowColor = '#ff4f4f';
  ctx.shadowBlur = 8;
  ctx.fillStyle = '#ff4f4f';
  ctx.beginPath(); ctx.arc(cx, s * 0.5, s * 0.08, 0, Math.PI * 2); ctx.fill();
  ctx.shadowBlur = 0;
}

// Humanoid base plus a big riot shield covering the front -- the
// frontal-damage-reduction mechanic needs a visual tell that flanking
// matters here.
function drawShieldSprite(ctx, s, def) {
  drawHumanoidSprite(ctx, s, def);
  const cx = s / 2;
  ctx.fillStyle = '#3a4450';
  ctx.strokeStyle = 'rgba(8,6,10,0.8)';
  ctx.lineWidth = 3;
  ctx.beginPath(); ctx.rect(cx - s * 0.2, s * 0.42, s * 0.4, s * 0.42); ctx.fill(); ctx.stroke();
  ctx.fillStyle = '#e0b23d';
  ctx.fillRect(cx - s * 0.03, s * 0.5, s * 0.06, s * 0.26);
}

function buildEnemySprite(enemyDef) {
  const c = makeTextureCanvas(128);
  const ctx = c.getContext('2d');
  if (enemyDef.isBoss) drawMonsterBoss(ctx, 128, enemyDef);
  else if (enemyDef.key === 'drone') drawDroneSprite(ctx, 128, enemyDef);
  else if (enemyDef.key === 'turret') drawTurretSprite(ctx, 128, enemyDef);
  else if (enemyDef.key === 'shieldtech') drawShieldSprite(ctx, 128, enemyDef);
  else drawHumanoidSprite(ctx, 128, enemyDef);
  return c;
}

// ── Projectile "energy bolt" textures: a soft radial glow instead of a
// flat filled circle, cached per color so each weapon/enemy type gets its
// own look without regenerating a canvas per shot. ──
const PROJECTILE_TEXTURES = {};
function getProjectileTexture(color) {
  if (PROJECTILE_TEXTURES[color]) return PROJECTILE_TEXTURES[color];
  const c = makeTextureCanvas(32);
  const ctx = c.getContext('2d');
  const cx = 16, cy = 16;
  const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, 16);
  grad.addColorStop(0, '#ffffff');
  grad.addColorStop(0.3, color);
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = grad;
  ctx.beginPath(); ctx.arc(cx, cy, 16, 0, Math.PI * 2); ctx.fill();
  PROJECTILE_TEXTURES[color] = c;
  return c;
}

// Explosive barrel prop -- a squat red drum with hazard bands and a warning
// glyph, distinct from any pickup or enemy silhouette.
function buildBarrelSprite() {
  const c = makeTextureCanvas(96);
  const ctx = c.getContext('2d');
  const cx = 48;
  ctx.fillStyle = '#8a2020';
  ctx.strokeStyle = 'rgba(8,6,10,0.7)';
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(cx - 26, 88); ctx.lineTo(cx - 28, 30); ctx.quadraticCurveTo(cx - 28, 12, cx, 12);
  ctx.quadraticCurveTo(cx + 28, 12, cx + 28, 30); ctx.lineTo(cx + 26, 88);
  ctx.closePath(); ctx.fill(); ctx.stroke();
  ctx.fillStyle = '#e0b23d';
  ctx.fillRect(cx - 28, 34, 56, 8);
  ctx.fillRect(cx - 28, 60, 56, 8);
  ctx.fillStyle = '#1c1a16';
  ctx.beginPath();
  ctx.moveTo(cx, 42); ctx.lineTo(cx + 9, 56); ctx.lineTo(cx - 9, 56);
  ctx.closePath(); ctx.fill();
  ctx.fillRect(cx - 2, 44, 4, 7);
  ctx.fillRect(cx - 2, 52, 4, 3);
  return c;
}

function buildPickupSprite(kind, color) {
  const c = makeTextureCanvas(64);
  const ctx = c.getContext('2d');
  const cx = 32, cy = 32;
  ctx.fillStyle = color;
  if (kind === 'health') {
    ctx.fillRect(cx - 14, cy - 4, 28, 8);
    ctx.fillRect(cx - 4, cy - 14, 8, 28);
  } else if (kind === 'armor') {
    ctx.beginPath();
    ctx.moveTo(cx, cy - 16); ctx.lineTo(cx + 14, cy - 6); ctx.lineTo(cx + 10, cy + 16);
    ctx.lineTo(cx - 10, cy + 16); ctx.lineTo(cx - 14, cy - 6);
    ctx.closePath(); ctx.fill();
  } else if (kind === 'ammo') {
    ctx.fillRect(cx - 10, cy - 14, 20, 28);
    ctx.fillStyle = 'rgba(0,0,0,0.3)';
    ctx.fillRect(cx - 10, cy - 14, 20, 6);
  } else if (kind === 'weapon') {
    ctx.fillRect(cx - 16, cy - 4, 32, 8);
    ctx.fillRect(cx + 8, cy - 8, 8, 16);
  } else if (kind === 'key') {
    ctx.beginPath(); ctx.arc(cx - 8, cy, 9, 0, Math.PI * 2); ctx.fill();
    ctx.fillRect(cx - 2, cy - 3, 20, 6);
    ctx.fillRect(cx + 10, cy + 3, 4, 7);
    ctx.fillRect(cx + 16, cy + 3, 4, 7);
  } else { // part
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.strokeRect(cx - 14, cy - 10, 28, 20);
    ctx.beginPath();
    ctx.moveTo(cx - 14, cy); ctx.lineTo(cx + 14, cy);
    ctx.stroke();
  }
  return c;
}

function buildAllSpriteTextures() {
  for (const key in ENEMY_TYPES) {
    SPRITE_TEXTURES['enemy_' + key] = buildEnemySprite(ENEMY_TYPES[key]);
  }
  for (const key in PICKUP_TYPES) {
    const def = PICKUP_TYPES[key];
    SPRITE_TEXTURES['pickup_' + key] = buildPickupSprite(def.kind, def.color);
  }
  SPRITE_TEXTURES['barrel'] = buildBarrelSprite();
}
