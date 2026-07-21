// ─── ANetDarkForces: weapon firing ────────────────────────────────────────

function canFire(runtime) {
  const p = runtime.state.player;
  const w = WEAPONS[p.currentWeapon];
  if (runtime.time - runtime.lastFireTime < w.fireRate) return false;
  if (w.ammoType && (p.ammo[w.ammoType] || 0) <= 0) return false;
  return true;
}

function fireWeapon(runtime, addLog) {
  const p = runtime.state.player;
  const w = WEAPONS[p.currentWeapon];
  if (!canFire(runtime)) {
    if (w.ammoType && (p.ammo[w.ammoType] || 0) <= 0 && runtime.time - runtime.lastDryFireTime > 0.3) {
      runtime.lastDryFireTime = runtime.time;
      sfxDryFire();
    }
    return false;
  }
  runtime.lastFireTime = runtime.time;
  if (w.ammoType) p.ammo[w.ammoType]--;

  const damageMult = 1 + (p.level - 1) * LEVEL_UP_DAMAGE_MULT;

  if (w.kind === 'hitscan') {
    fireHitscanShot(runtime, w, damageMult, addLog);
  } else if (w.kind === 'hitscan-spread') {
    for (let i = 0; i < w.pellets; i++) fireHitscanShot(runtime, w, damageMult, addLog);
  } else if (w.kind === 'projectile') {
    const boltColor = w.key === 'overclock' ? '#ff9f4f' : '#5fd6ff'; // EMP = cyan, Overclock = hot orange, distinct at a glance
    runtime.projectiles.push(spawnProjectile(p.x, p.y, p.angle, w.speed, Math.round(w.damage * damageMult), w.splashRadius || 0, boltColor, true));
  } else if (w.kind === 'melee') {
    fireMeleeSwing(runtime, w, damageMult, addLog);
  }
  sfxForWeapon(w.sfx);
  runtime.muzzleFlashTimer = 0.08;
  runtime.weaponKickTimer = WEAPON_KICK_DURATION;
  return true;
}

// A single hitscan trace: find the nearest enemy along a (slightly
// spread) ray within the weapon's range and the wall it would otherwise
// hit, then apply damage. Enemies are treated as a fixed-radius cylinder
// around their world position for hit-testing (project the enemy onto
// the ray direction, check perpendicular distance) -- simple and cheap,
// no need for real sprite-shape collision at this genre's fidelity.
function fireHitscanShot(runtime, w, damageMult, addLog) {
  const p = runtime.state.player;
  const spread = (Math.random() - 0.5) * w.spread * 2;
  const angle = p.angle + spread;
  const dirX = Math.cos(angle), dirY = Math.sin(angle);
  // wallHit.dist is already the raw distance along THIS ray (castRay's
  // return value before any screen-projection fisheye correction), which
  // is exactly what a hitscan trace needs to compare against -- no
  // player-facing-angle correction applies here, that's only relevant
  // when projecting a distance onto the 2D screen (see renderScene).
  const wallHit = castRay(runtime.level.grid, runtime.level.w, runtime.level.h, p.x, p.y, angle);
  const maxDist = wallHit ? Math.min(w.range, wallHit.dist) : w.range;

  let closestTarget = null, closestKind = null, closestAlong = maxDist;
  for (const e of runtime.enemies) {
    if (e.dead) continue;
    const ex = e.x - p.x, ey = e.y - p.y;
    const along = ex * dirX + ey * dirY;
    if (along < 0 || along > closestAlong) continue;
    const perpX = ex - along * dirX, perpY = ey - along * dirY;
    const perp = Math.hypot(perpX, perpY);
    const hitRadius = TILE * (e.def.isBoss ? 0.55 : 0.35);
    if (perp < hitRadius) { closestAlong = along; closestTarget = e; closestKind = 'enemy'; }
  }
  for (const b of runtime.props) {
    if (b.dead) continue;
    const bx = b.x - p.x, by = b.y - p.y;
    const along = bx * dirX + by * dirY;
    if (along < 0 || along > closestAlong) continue;
    const perpX = bx - along * dirX, perpY = by - along * dirY;
    if (Math.hypot(perpX, perpY) < TILE * 0.3) { closestAlong = along; closestTarget = b; closestKind = 'prop'; }
  }
  if (closestKind === 'enemy') damageEnemy(runtime, closestTarget, Math.round(w.damage * damageMult), addLog, p.x, p.y);
  else if (closestKind === 'prop') damageBarrel(runtime, closestTarget, Math.round(w.damage * damageMult), addLog);
}

// No ray trace needed -- just the nearest enemy/barrel within range and
// inside a narrow forward-facing swing cone (a real 60-degree arc, not
// just "close enough," so you can't melee something standing behind you).
function fireMeleeSwing(runtime, w, damageMult, addLog) {
  const p = runtime.state.player;
  const inSwingCone = (tx, ty) => {
    const angleTo = Math.atan2(ty - p.y, tx - p.x);
    let diff = angleTo - p.angle;
    while (diff > Math.PI) diff -= Math.PI * 2;
    while (diff < -Math.PI) diff += Math.PI * 2;
    return Math.abs(diff) <= Math.PI / 3;
  };
  let closest = null, closestKind = null, closestDist = w.range;
  for (const e of runtime.enemies) {
    if (e.dead) continue;
    const d = distTo(p, e);
    if (d > closestDist || !inSwingCone(e.x, e.y)) continue;
    closest = e; closestKind = 'enemy'; closestDist = d;
  }
  for (const b of runtime.props) {
    if (b.dead) continue;
    const d = distTo(p, b);
    if (d > closestDist || !inSwingCone(b.x, b.y)) continue;
    closest = b; closestKind = 'prop'; closestDist = d;
  }
  if (closestKind === 'enemy') damageEnemy(runtime, closest, Math.round(w.damage * damageMult), addLog, p.x, p.y);
  else if (closestKind === 'prop') damageBarrel(runtime, closest, Math.round(w.damage * damageMult), addLog);
}

function switchWeapon(runtime, weaponKey) {
  const p = runtime.state.player;
  if (p.weapons.includes(weaponKey)) p.currentWeapon = weaponKey;
}

function cycleWeapon(runtime, dir) {
  const p = runtime.state.player;
  const owned = WEAPON_ORDER.filter(k => p.weapons.includes(k));
  const idx = owned.indexOf(p.currentWeapon);
  const next = owned[(idx + dir + owned.length) % owned.length];
  p.currentWeapon = next;
}
