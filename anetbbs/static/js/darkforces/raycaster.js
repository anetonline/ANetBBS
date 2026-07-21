// ─── ANetDarkForces: raycasting renderer ─────────────────────────────────
// Classic grid-DDA raycasting (Wolfenstein/early-Doom style: textured
// walls of uniform height, billboarded sprite enemies/pickups, flat-
// shaded floor/ceiling — not full Doom-style room-height variation,
// which needs a very different renderer). Angle-based casting (not the
// camera-plane-vector formulation some tutorials use), so distances are
// explicitly fisheye-corrected by the caller (see renderScene below) —
// verified against known-distance test cases in
// tests/adf_raycaster_check.js before trusting it visually.
//
// All positions/distances in this file are WORLD units (TILE = one grid
// cell) unless a variable name says "grid" — castRay() internally works
// in grid-cell units (position / TILE) for the DDA loop, matching the
// standard formulation, then converts back to world units before
// returning.

// Casts a single ray from (worldX, worldY) at `angle` against `grid`
// (2D array, 0 = floor, >0 = wall type). Returns null if nothing was hit
// within MAX_DEPTH (shouldn't happen against an enclosed level, but a
// malformed map or an angle that never hits within the depth cap
// shouldn't crash the renderer either). Returned `dist` is the RAW
// (uncorrected) distance along this specific ray, in world units — the
// caller applies fisheye correction using the ray's angular offset from
// the player's facing direction.
function castRay(grid, gridW, gridH, worldX, worldY, angle) {
  const posX = worldX / TILE, posY = worldY / TILE;
  const rayDirX = Math.cos(angle), rayDirY = Math.sin(angle);

  let mapX = Math.floor(posX), mapY = Math.floor(posY);

  const deltaDistX = rayDirX === 0 ? Infinity : Math.abs(1 / rayDirX);
  const deltaDistY = rayDirY === 0 ? Infinity : Math.abs(1 / rayDirY);

  let stepX, sideDistX;
  if (rayDirX < 0) { stepX = -1; sideDistX = (posX - mapX) * deltaDistX; }
  else { stepX = 1; sideDistX = (mapX + 1 - posX) * deltaDistX; }

  let stepY, sideDistY;
  if (rayDirY < 0) { stepY = -1; sideDistY = (posY - mapY) * deltaDistY; }
  else { stepY = 1; sideDistY = (mapY + 1 - posY) * deltaDistY; }

  let side = 0;
  let hitType = 0;
  const maxSteps = gridW + gridH + 4; // enough to cross the whole enclosed map in any direction
  for (let i = 0; i < maxSteps; i++) {
    if (sideDistX < sideDistY) {
      sideDistX += deltaDistX; mapX += stepX; side = 0;
    } else {
      sideDistY += deltaDistY; mapY += stepY; side = 1;
    }
    if (mapX < 0 || mapX >= gridW || mapY < 0 || mapY >= gridH) return null; // shouldn't happen inside an enclosed level
    const cell = grid[mapY][mapX];
    if (cell !== 0) { hitType = cell; break; }
  }
  if (!hitType) return null;

  // Perpendicular-to-THIS-RAY distance (i.e. plain distance along the ray,
  // since this ray's own direction vector is what's used) in grid units.
  const rawGridDist = side === 0
    ? (mapX - posX + (1 - stepX) / 2) / rayDirX
    : (mapY - posY + (1 - stepY) / 2) / rayDirY;

  // Texture X coordinate: fractional position along the hit wall face (0-1).
  let wallX = side === 0 ? posY + rawGridDist * rayDirY : posX + rawGridDist * rayDirX;
  wallX -= Math.floor(wallX);

  return { dist: rawGridDist * TILE, wallType: hitType, side, textureX: wallX };
}

// Casts a ray purely for line-of-sight checks (enemy AI) -- same DDA loop
// but only cares whether a wall is hit before `maxDist`, not texture
// details. Returns true if the straight line from (x0,y0) to (x1,y1) is
// unobstructed by any wall.
function hasLineOfSight(grid, gridW, gridH, x0, y0, x1, y1) {
  const dx = x1 - x0, dy = y1 - y0;
  const dist = Math.hypot(dx, dy);
  if (dist < 1) return true;
  const angle = Math.atan2(dy, dx);
  const hit = castRay(grid, gridW, gridH, x0, y0, angle);
  return !hit || hit.dist >= dist;
}

// ── Scene rendering ─────────────────────────────────────────────────────
function renderScene(ctx, runtime) {
  const { grid, w: gridW, h: gridH } = runtime.level;
  const player = runtime.state.player;
  const zBuffer = runtime.zBuffer; // reused array, one perpendicular distance per column

  // Cosmetic jump-bob: this engine has no real z-axis (flat-height
  // raycaster, same limitation classic Wolfenstein/Doom-style engines
  // have), so "jump" doesn't clear obstacles -- it's purely a screen-space
  // camera hop for input feedback, applied as a translate around the
  // entire draw so every layer (floor/ceiling/walls/sprites) moves
  // together instead of threading an offset through each draw call.
  ctx.save();
  if (runtime.jumpTimer > 0) {
    const progress = 1 - runtime.jumpTimer / JUMP_BOB_DURATION;
    ctx.translate(0, -Math.sin(progress * Math.PI) * JUMP_BOB_HEIGHT);
  }

  // Floor/ceiling (flat-shaded halves, Wolfenstein-style -- no floor
  // texture-casting, which is a deliberate, disclosed scope limit).
  ctx.fillStyle = '#1a1a22';
  ctx.fillRect(0, 0, RENDER_W, RENDER_H / 2);
  const floorGrad = ctx.createLinearGradient(0, RENDER_H / 2, 0, RENDER_H);
  floorGrad.addColorStop(0, '#2a2420');
  floorGrad.addColorStop(1, '#151210');
  ctx.fillStyle = floorGrad;
  ctx.fillRect(0, RENDER_H / 2, RENDER_W, RENDER_H / 2);

  for (let col = 0; col < NUM_RAYS; col++) {
    const rayAngle = player.angle - HALF_FOV + (col / NUM_RAYS) * FOV;
    const hit = castRay(grid, gridW, gridH, player.x, player.y, rayAngle);
    if (!hit) { zBuffer[col] = MAX_DEPTH; continue; }

    // Fisheye correction: project the raw ray distance onto the player's
    // facing direction.
    const correctedDist = Math.max(1, hit.dist * Math.cos(rayAngle - player.angle));
    zBuffer[col] = correctedDist;

    const lineHeight = Math.min(RENDER_H * 4, (TILE * RENDER_H) / correctedDist);
    const drawStart = (RENDER_H - lineHeight) / 2;

    const tex = WALL_TEXTURES[hit.wallType];
    const texX = Math.floor(hit.textureX * (tex ? tex.width : TILE));
    // Darken the Y-side (side===1, N/S-facing walls) slightly for a cheap
    // fake-lighting effect that makes corners read clearly.
    ctx.globalAlpha = hit.side === 1 ? 0.78 : 1;
    if (tex) {
      ctx.drawImage(tex, texX, 0, 1, tex.height, col, drawStart, 1, lineHeight);
    } else {
      ctx.fillStyle = '#888';
      ctx.fillRect(col, drawStart, 1, lineHeight);
    }
    // Distance fog -- darken far walls toward the floor/ceiling color.
    const fog = Math.min(0.75, correctedDist / (TILE * 9));
    if (fog > 0.02) {
      ctx.fillStyle = `rgba(10,8,10,${fog})`;
      ctx.fillRect(col, drawStart, 1, lineHeight);
    }
    ctx.globalAlpha = 1;
  }

  renderSprites(ctx, runtime);
  renderWeaponViewmodel(ctx, runtime);
  ctx.restore();
}

// ── Weapon viewmodel (on-screen gun) ────────────────────────────────────
// Drawn fresh every frame in SCREEN space (not raycast/world space) --
// simple silhouettes at this game's established fidelity level, matching
// the enemy sprites' primitive-canvas-shapes approach rather than trying
// to look photoreal. Shared shape recipe per weapon, parameterized by
// barrel length / body width / a couple of boolean flourishes, so 7
// weapons don't need 7 fully bespoke draw routines.
const WEAPON_VIEW_SHAPES = {
  solder: { barrelLen: 60, bodyW: 34, bodyH: 70, color: '#3a3a3a' },
  packetspray: { barrelLen: 90, bodyW: 30, bodyH: 90, color: '#2c2c2c', mag: true },
  static: { barrelLen: 70, bodyW: 56, bodyH: 60, color: '#4a3a2a', doubleBarrel: true },
  debugger: { barrelLen: 130, bodyW: 24, bodyH: 60, color: '#2a3a2a', scope: true },
  emp: { barrelLen: 60, bodyW: 70, bodyH: 80, color: '#1c3a4a' },
  overclock: { barrelLen: 70, bodyW: 80, bodyH: 90, color: '#4a1c1c' },
  multitool: { melee: true, color: '#8a5a3a' },
};

function renderWeaponViewmodel(ctx, runtime) {
  const p = runtime.state.player;
  const w = WEAPONS[p.currentWeapon];
  const shape = WEAPON_VIEW_SHAPES[w.key];
  if (!shape) return;

  // Bob: a walking sway driven by the same phase accumulator main.js
  // advances while the player is actually moving (frozen otherwise).
  // Kick: a quick upward recoil that eases back down after firing --
  // repurposed as a swing-progress value for melee instead (see below).
  const bobY = Math.abs(Math.sin(runtime.walkCycle)) * WEAPON_BOB_AMOUNT_Y;
  const bobX = Math.sin(runtime.walkCycle * 0.5) * WEAPON_BOB_AMOUNT_X;
  const kick = runtime.weaponKickTimer > 0 ? (runtime.weaponKickTimer / WEAPON_KICK_DURATION) * WEAPON_KICK_HEIGHT : 0;

  const anchorX = RENDER_W * 0.66 + bobX;
  const anchorY = RENDER_H + 40 - bobY - kick; // +40 so the grip's base sits just below the visible frame

  ctx.save();
  if (shape.melee) {
    const swingProgress = runtime.weaponKickTimer > 0 ? 1 - runtime.weaponKickTimer / WEAPON_KICK_DURATION : 0;
    const swingAngle = Math.sin(swingProgress * Math.PI) * 0.9;
    ctx.translate(anchorX, anchorY);
    ctx.rotate(-0.3 - swingAngle);
    ctx.fillStyle = shape.color;
    ctx.strokeStyle = 'rgba(0,0,0,0.6)';
    ctx.lineWidth = 3;
    ctx.beginPath(); ctx.rect(-10, -110, 20, 100); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#8a8a8a';
    ctx.beginPath(); ctx.rect(-16, -130, 32, 26); ctx.fill(); ctx.stroke();
    ctx.restore();
    return;
  }

  ctx.translate(anchorX, anchorY);
  ctx.fillStyle = '#c99a6a'; // hand/grip
  ctx.fillRect(-14, -20, 28, 50);
  ctx.fillStyle = shape.color;
  ctx.strokeStyle = 'rgba(0,0,0,0.6)';
  ctx.lineWidth = 3;
  ctx.beginPath(); ctx.rect(-shape.bodyW / 2, -shape.bodyH - 20, shape.bodyW, shape.bodyH); ctx.fill(); ctx.stroke();
  ctx.beginPath(); ctx.rect(-6, -shape.bodyH - 20 - shape.barrelLen, 12, shape.barrelLen); ctx.fill(); ctx.stroke();
  if (shape.doubleBarrel) {
    ctx.beginPath(); ctx.rect(8, -shape.bodyH - 20 - shape.barrelLen, 12, shape.barrelLen); ctx.fill(); ctx.stroke();
  }
  if (shape.mag) {
    ctx.fillStyle = '#1c1c1c';
    ctx.fillRect(-8, -shape.bodyH - 20 + shape.bodyH * 0.3, 16, 30);
  }
  if (shape.scope) {
    ctx.fillStyle = '#1c1c1c';
    ctx.fillRect(-8, -shape.bodyH - 20 - shape.barrelLen * 0.6, 16, 14);
  }
  if (runtime.muzzleFlashTimer > 0) {
    const flashY = -shape.bodyH - 20 - shape.barrelLen;
    const grad = ctx.createRadialGradient(0, flashY, 0, 0, flashY, 40);
    grad.addColorStop(0, 'rgba(255,230,150,0.9)');
    grad.addColorStop(1, 'rgba(255,230,150,0)');
    ctx.fillStyle = grad;
    ctx.beginPath(); ctx.arc(0, flashY, 40, 0, Math.PI * 2); ctx.fill();
  }
  ctx.restore();
}

// Billboarded sprites (enemies, pickups, projectiles) -- each is a flat
// image always facing the camera, positioned/scaled by its distance and
// angle from the player, occluded per-column against the wall z-buffer
// computed above so a sprite behind a wall doesn't draw on top of it.
function renderSprites(ctx, runtime) {
  const player = runtime.state.player;
  const sprites = [];
  for (const e of runtime.enemies) {
    if (e.dead) continue;
    sprites.push({ x: e.x, y: e.y, tex: SPRITE_TEXTURES['enemy_' + e.type], scale: e.def.isBoss ? 1.6 : 1, hurtFlash: e.hurtFlashTimer > 0 });
  }
  for (const p of runtime.pickups) {
    if (p.collected) continue;
    sprites.push({ x: p.x, y: p.y, tex: SPRITE_TEXTURES['pickup_' + p.type], scale: 0.55, bob: true });
  }
  for (const b of runtime.props) {
    if (b.dead) continue;
    sprites.push({ x: b.x, y: b.y, tex: SPRITE_TEXTURES['barrel'], scale: 0.85, hurtFlash: b.hurtFlashTimer > 0 });
  }
  for (const pr of runtime.projectiles) {
    sprites.push({ x: pr.x, y: pr.y, tex: null, color: pr.color, scale: 0.25, isProjectile: true });
  }

  // Sort far-to-near (painter's algorithm).
  sprites.forEach(s => { s.dist = Math.hypot(s.x - player.x, s.y - player.y); });
  sprites.sort((a, b) => b.dist - a.dist);

  for (const s of sprites) {
    const dx = s.x - player.x, dy = s.y - player.y;
    let angleToSprite = Math.atan2(dy, dx) - player.angle;
    // Normalize to [-PI, PI] so sprites just outside the FOV on either
    // side don't wrap around and appear to project from the wrong edge.
    while (angleToSprite > Math.PI) angleToSprite -= Math.PI * 2;
    while (angleToSprite < -Math.PI) angleToSprite += Math.PI * 2;
    if (Math.abs(angleToSprite) > HALF_FOV + 0.3) continue; // well outside view, skip

    const correctedDist = Math.max(1, s.dist * Math.cos(angleToSprite));
    const screenX = (0.5 + angleToSprite / FOV) * RENDER_W;
    const spriteSize = Math.min(RENDER_H * 3, (TILE * RENDER_H) / correctedDist) * s.scale;
    const drawStart = (RENDER_H - spriteSize) / 2;
    const left = screenX - spriteSize / 2;

    if (s.isProjectile) {
      const colStart = Math.max(0, Math.floor(left));
      const colEnd = Math.min(NUM_RAYS - 1, Math.floor(left + spriteSize));
      let visible = false;
      for (let c = colStart; c <= colEnd; c++) { if (correctedDist < runtime.zBuffer[c]) { visible = true; break; } }
      if (visible) {
        // A soft glow-bolt texture, capped independently from full-size
        // enemy sprites -- without this cap a projectile at point-blank
        // range would balloon to the same size as a nearby enemy (the
        // "big flat circle" look), which reads as a rendering bug rather
        // than a projectile.
        const tex = getProjectileTexture(s.color);
        const boltSize = Math.max(4, Math.min(RENDER_H * 0.35, spriteSize));
        ctx.drawImage(tex, screenX - boltSize / 2, RENDER_H / 2 - boltSize / 2, boltSize, boltSize);
      }
      continue;
    }

    if (!s.tex) continue;
    const bobOffset = s.bob ? Math.sin(runtime.time * 2 + s.x * 0.01) * 4 : 0;
    // Draw per-column, occluding against the z-buffer so sprites behind
    // walls don't show through -- the classic technique, unavoidable
    // without a full depth buffer on 2D canvas.
    const texW = s.tex.width;
    const colStart = Math.max(0, Math.floor(left));
    const colEnd = Math.min(NUM_RAYS - 1, Math.floor(left + spriteSize));
    if (s.hurtFlash) ctx.filter = 'brightness(2) saturate(0)';
    for (let c = colStart; c <= colEnd; c++) {
      if (correctedDist >= runtime.zBuffer[c]) continue;
      const texX = Math.floor(((c - left) / spriteSize) * texW);
      if (texX < 0 || texX >= texW) continue;
      ctx.drawImage(s.tex, texX, 0, 1, s.tex.height, c, drawStart + bobOffset, 1, spriteSize);
    }
    if (s.hurtFlash) ctx.filter = 'none';
  }
}

// ── Minimap (top-down, screen-space overlay) ──
function renderMinimap(ctx, runtime, cx, cy, scale) {
  const { grid, w, h } = runtime.level;
  const player = runtime.state.player;
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, scale * 6, 0, Math.PI * 2);
  ctx.clip();
  ctx.fillStyle = 'rgba(10,10,14,0.85)';
  ctx.fillRect(cx - scale * 6, cy - scale * 6, scale * 12, scale * 12);

  const originGX = player.x / TILE, originGY = player.y / TILE;
  for (let gy = Math.max(0, Math.floor(originGY - 6)); gy < Math.min(h, originGY + 6); gy++) {
    for (let gx = Math.max(0, Math.floor(originGX - 6)); gx < Math.min(w, originGX + 6); gx++) {
      if (grid[gy][gx] === 0) continue;
      const sx = cx + (gx - originGX) * scale, sy = cy + (gy - originGY) * scale;
      ctx.fillStyle = '#6a7480';
      ctx.fillRect(sx, sy, scale, scale);
    }
  }
  for (const e of runtime.enemies) {
    if (e.dead) continue;
    const sx = cx + (e.x / TILE - originGX) * scale, sy = cy + (e.y / TILE - originGY) * scale;
    ctx.fillStyle = e.def.isBoss ? '#f2e060' : '#e0603d';
    ctx.beginPath(); ctx.arc(sx, sy, 2.5, 0, Math.PI * 2); ctx.fill();
  }
  // Exit waypoint -- the exit is otherwise a pure invisible coordinate with
  // no texture/marker anywhere in the world, so a big/open level cleared of
  // enemies and pickups gave a player no clue where to actually go. Clamped
  // to the minimap's edge when off-screen so there's always a directional
  // cue, same idea as an off-screen objective marker.
  if (runtime.level.exit) {
    const exitDX = (runtime.level.exit.gx - originGX) * scale;
    const exitDY = (runtime.level.exit.gy - originGY) * scale;
    const exitDist = Math.hypot(exitDX, exitDY);
    const edgeRadius = scale * 6 - 4;
    let mx = exitDX, my = exitDY;
    if (exitDist > edgeRadius && exitDist > 0) {
      mx = (exitDX / exitDist) * edgeRadius;
      my = (exitDY / exitDist) * edgeRadius;
    }
    ctx.fillStyle = runtime.bossDefeated ? '#f2c14e' : '#6a7480';
    ctx.beginPath();
    ctx.moveTo(cx + mx, cy + my - 4);
    ctx.lineTo(cx + mx - 4, cy + my + 3);
    ctx.lineTo(cx + mx + 4, cy + my + 3);
    ctx.closePath();
    ctx.fill();
  }
  // Player arrow.
  ctx.translate(cx, cy);
  ctx.rotate(player.angle);
  ctx.fillStyle = '#5fae6e';
  ctx.beginPath();
  ctx.moveTo(6, 0); ctx.lineTo(-4, -4); ctx.lineTo(-4, 4);
  ctx.closePath(); ctx.fill();
  ctx.restore();
}
