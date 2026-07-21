// ─── ANetDarkForces: enemies, projectiles, pickups, props ────────────────

// Axis-separated collision against the grid: tries moving X then Y
// independently so sliding along a wall works (a diagonal move into a
// corner doesn't just fully stop, it slides along whichever axis is
// still clear) -- same idea as Meadowlark Valley's tile-based movement,
// adapted for continuous (non-grid-locked) FPS movement with a radius.
function moveWithCollision(grid, gridW, gridH, x, y, dx, dy, radius) {
  const isWall = (wx, wy) => {
    const gx = Math.floor(wx / TILE), gy = Math.floor(wy / TILE);
    if (gx < 0 || gx >= gridW || gy < 0 || gy >= gridH) return true;
    return grid[gy][gx] !== 0;
  };
  const blocked = (px, py) =>
    isWall(px - radius, py - radius) || isWall(px + radius, py - radius) ||
    isWall(px - radius, py + radius) || isWall(px + radius, py + radius);

  let nx = x, ny = y;
  if (!blocked(x + dx, y)) nx = x + dx;
  if (!blocked(nx, y + dy)) ny = y + dy;
  return { x: nx, y: ny };
}

function spawnEnemy(type, x, y) {
  const def = ENEMY_TYPES[type];
  return {
    type, def, x, y,
    hp: def.hp, maxHp: def.hp,
    dead: false,
    state: 'idle', // idle | chase | dead
    lastAttackTime: -999,
    hurtFlashTimer: 0,
    angle: 0,        // facing direction -- used for shieldtech's frontal damage reduction
    angryAt: null,    // another enemy object this one is currently hostile toward (infighting)
    angryUntil: 0,    // runtime.time after which the grudge above expires
    lastSeenTime: -999, // runtime.time this enemy last had direct line of sight to its target -- see ENEMY_MEMORY_DURATION
    armTimer: 0,      // kamikaze only: >0 while counting down to detonation (see KAMIKAZE_ARM_DURATION)
  };
}

function spawnPickup(type, x, y) {
  return { type, x, y, collected: false };
}

function spawnBarrel(x, y) {
  return { x, y, hp: 20, dead: false, hurtFlashTimer: 0 };
}

// `speed` is in TILES PER SECOND, the same convention used everywhere
// else in this file (enemy .speed, MOVE_SPEED) -- dx/dy store a
// per-second world-unit velocity, applied as `pos += velocity * dt` in
// updateProjectiles(), standard physics integration, no hidden scaling
// factors to keep in sync between here and there.
// `sourceEnemy` (optional) is the enemy object that fired this shot --
// null for player-fired shots. Used by updateProjectiles/applySplashDamage
// to know who NOT to hit (the shooter itself) and to trigger infighting
// when it hits a different enemy.
function spawnProjectile(x, y, angle, speed, damage, splashRadius, color, fromPlayer, sourceEnemy) {
  const unitsPerSecond = speed * TILE;
  return {
    x, y, damage, splashRadius, color, fromPlayer, sourceEnemy: sourceEnemy || null,
    dx: Math.cos(angle) * unitsPerSecond, dy: Math.sin(angle) * unitsPerSecond,
    dead: false,
  };
}

function distTo(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

// `sourceX/sourceY` (optional): where the damage came from, used for the
// Riot Tech's frontal-shield check (was this hit from roughly in front of
// it, or did the player flank it?). `sourceEnemy` (optional): if damage
// came from ANOTHER ENEMY (not the player/environment), this enemy turns
// hostile toward its attacker for a while -- the infighting mechanic.
function damageEnemy(runtime, enemy, amount, addLog, sourceX, sourceY, sourceEnemy) {
  if (enemy.dead) return;
  let finalAmount = amount;
  if (enemy.def.frontalDamageReduction && sourceX !== undefined && sourceY !== undefined) {
    const angleToSource = Math.atan2(sourceY - enemy.y, sourceX - enemy.x);
    let diff = angleToSource - enemy.angle;
    while (diff > Math.PI) diff -= Math.PI * 2;
    while (diff < -Math.PI) diff += Math.PI * 2;
    if (Math.abs(diff) < Math.PI / 2.5) { // roughly a 144-degree frontal arc -- flank outside this and the shield does nothing
      finalAmount = Math.round(amount * (1 - enemy.def.frontalDamageReduction));
    }
  }
  enemy.hp -= finalAmount;
  enemy.hurtFlashTimer = 0.15;
  if (sourceEnemy && sourceEnemy !== enemy && !sourceEnemy.dead) {
    enemy.angryAt = sourceEnemy;
    enemy.angryUntil = runtime.time + 8;
    enemy.state = 'chase';
    if (addLog) addLog(`${enemy.def.name} turns on ${sourceEnemy.def.name}!`, 'warning');
  }
  if (enemy.hp <= 0) {
    enemy.dead = true;
    enemy.state = 'dead';
    runtime.state.kills++;
    awardXp(runtime, enemy.def.xp, addLog);
    if (enemy.def.isBoss) {
      runtime.bossDefeated = true;
      if (addLog) addLog(`${enemy.def.name} is down! The exit is unlocked.`, 'milestone');
    }
  }
}

function awardXp(runtime, amount, addLog) {
  const p = runtime.state.player;
  p.xp += amount;
  while (p.level < XP_PER_LEVEL.length && p.xp >= XP_PER_LEVEL[p.level]) {
    p.level++;
    p.maxHp += LEVEL_UP_HEALTH_BONUS;
    p.hp = Math.min(p.maxHp, p.hp + LEVEL_UP_HEALTH_BONUS); // level-up also heals the bonus amount, matches genre convention (a level-up is a reward, not just a number going up while you stay hurt)
    if (addLog) addLog(`Level up! You're now level ${p.level}.`, 'milestone');
  }
}

// A drone that's reached melee range self-destructs instead of attacking
// normally -- splash damage to everything nearby, itself included.
function detonateKamikaze(runtime, enemy, addLog) {
  if (addLog) addLog(`${enemy.def.name} detonates!`, 'warning');
  sfxExplosion();
  enemy.dead = true;
  enemy.state = 'dead';
  runtime.state.kills++;
  awardXp(runtime, enemy.def.xp, addLog);
  applySplashDamage(runtime, { x: enemy.x, y: enemy.y, damage: enemy.def.damage, splashRadius: enemy.def.splashRadius, sourceEnemy: null }, addLog);
}

function updateEnemies(runtime, dt, addLog) {
  const { grid, w, h } = runtime.level;
  const player = runtime.state.player;
  for (const e of runtime.enemies) {
    if (e.dead) { e.hurtFlashTimer = Math.max(0, e.hurtFlashTimer - dt); continue; }
    e.hurtFlashTimer = Math.max(0, e.hurtFlashTimer - dt);

    // Infighting: an enemy hit by another enemy's attack stays angry at
    // its attacker (not the player) until the grudge times out.
    let target = player, targetIsPlayer = true;
    if (e.angryAt && !e.angryAt.dead && runtime.time < e.angryUntil) {
      target = e.angryAt;
      targetIsPlayer = false;
    }

    const d = distTo(e, target);
    const canSee = d < e.def.sightRange && hasLineOfSight(grid, w, h, e.x, e.y, target.x, target.y);
    if (canSee) {
      if (e.state !== 'chase') sfxAlert(); // "spotted you" bark, only on the idle->chase transition
      e.state = 'chase';
      e.lastSeenTime = runtime.time;
    }
    // Memory: an enemy that's lost direct sight of its target for too
    // long gives up and reverts to idle, rather than tracking the
    // target's live position forever through walls -- reacquiring sight
    // later re-triggers the alert bark above like a fresh spotting.
    if (e.state === 'chase' && runtime.time - e.lastSeenTime > ENEMY_MEMORY_DURATION) {
      e.state = 'idle';
      e.armTimer = 0; // cancel any in-progress kamikaze arm if it lost its target
    }
    if (e.state !== 'chase') continue;

    if (e.def.kind === 'kamikaze') {
      // Once armed, committed to the countdown regardless of subsequent
      // distance -- the player's counterplay window is the arm delay
      // itself (retreat far enough before it expires), not an infinite
      // ability to dodge by stepping back right at the trigger instant.
      if (e.armTimer > 0) {
        e.armTimer -= dt;
        if (e.armTimer <= 0) detonateKamikaze(runtime, e, addLog);
        continue;
      }
      if (d <= e.def.attackRange) {
        e.armTimer = KAMIKAZE_ARM_DURATION;
        sfxKamikazeArm();
        continue;
      }
    }

    const isStationary = e.def.speed === 0; // turrets: wall-mounted, never move
    if (!isStationary && d > e.def.attackRange * 0.8) {
      const angle = Math.atan2(target.y - e.y, target.x - e.x);
      e.angle = angle;
      const step = e.def.speed * TILE * dt;
      const moved = moveWithCollision(grid, w, h, e.x, e.y, Math.cos(angle) * step, Math.sin(angle) * step, TILE * 0.3);
      e.x = moved.x; e.y = moved.y;
    } else if (canSee && runtime.time - e.lastAttackTime >= e.def.attackRate) {
      e.lastAttackTime = runtime.time;
      e.angle = Math.atan2(target.y - e.y, target.x - e.x);
      if ((e.def.kind === 'melee' || e.def.kind === 'boss') && d <= e.def.attackRange * 1.4) {
        if (targetIsPlayer) applyDamageToPlayer(runtime, e.def.damage, addLog);
        else damageEnemy(runtime, target, e.def.damage, addLog, e.x, e.y, e);
      }
      if (e.def.kind === 'ranged' || e.def.kind === 'boss' || e.def.kind === 'turret') {
        runtime.projectiles.push(spawnProjectile(e.x, e.y, e.angle, e.def.projectileSpeed || 6, e.def.damage, 0, e.def.color, false, e));
      }
    }
  }
}

function applyDamageToPlayer(runtime, amount, addLog) {
  const p = runtime.state.player;
  let remaining = amount;
  if (p.armor > 0) {
    const absorbed = Math.min(p.armor, Math.ceil(remaining * 0.5));
    p.armor -= absorbed;
    remaining -= absorbed;
  }
  p.hp -= remaining;
  runtime.hurtFlashTimer = 0.2;
  if (p.hp <= 0 && !runtime.playerDead) {
    p.hp = 0;
    runtime.playerDead = true;
    runtime.state.deaths++;
    if (addLog) addLog('You went down. Respawning at the last checkpoint...', 'warning');
  }
}

function updateProjectiles(runtime, dt, addLog) {
  const { grid, w, h } = runtime.level;
  const player = runtime.state.player;
  for (const pr of runtime.projectiles) {
    if (pr.dead) continue;
    pr.x += pr.dx * dt;
    pr.y += pr.dy * dt;
    const gx = Math.floor(pr.x / TILE), gy = Math.floor(pr.y / TILE);
    if (gx < 0 || gx >= w || gy < 0 || gy >= h || grid[gy][gx] !== 0) {
      pr.dead = true;
      if (pr.splashRadius > 0) applySplashDamage(runtime, { x: pr.x, y: pr.y, damage: pr.damage, splashRadius: pr.splashRadius, sourceEnemy: pr.sourceEnemy }, addLog);
      continue;
    }
    if (pr.fromPlayer) {
      let hit = false;
      for (const e of runtime.enemies) {
        if (e.dead) continue;
        if (distTo(pr, e) < TILE * 0.4) {
          pr.dead = true; hit = true;
          if (pr.splashRadius > 0) applySplashDamage(runtime, { x: pr.x, y: pr.y, damage: pr.damage, splashRadius: pr.splashRadius, sourceEnemy: null }, addLog);
          else damageEnemy(runtime, e, pr.damage, addLog, pr.x, pr.y);
          break;
        }
      }
      if (!hit) {
        for (const b of runtime.props) {
          if (b.dead) continue;
          if (distTo(pr, b) < TILE * 0.4) {
            pr.dead = true;
            if (pr.splashRadius > 0) applySplashDamage(runtime, { x: pr.x, y: pr.y, damage: pr.damage, splashRadius: pr.splashRadius, sourceEnemy: null }, addLog);
            else damageBarrel(runtime, b, pr.damage, addLog);
            break;
          }
        }
      }
    } else {
      // Enemy-fired: can hit the player OR another enemy (friendly fire --
      // the trigger for infighting) OR a barrel.
      if (distTo(pr, player) < TILE * 0.4) {
        pr.dead = true;
        if (pr.splashRadius > 0) applySplashDamage(runtime, { x: pr.x, y: pr.y, damage: pr.damage, splashRadius: pr.splashRadius, sourceEnemy: pr.sourceEnemy }, addLog);
        else applyDamageToPlayer(runtime, pr.damage, addLog);
        continue;
      }
      let hit = false;
      for (const e of runtime.enemies) {
        if (e.dead || e === pr.sourceEnemy) continue;
        if (distTo(pr, e) < TILE * 0.4) {
          pr.dead = true; hit = true;
          if (pr.splashRadius > 0) applySplashDamage(runtime, { x: pr.x, y: pr.y, damage: pr.damage, splashRadius: pr.splashRadius, sourceEnemy: pr.sourceEnemy }, addLog);
          else damageEnemy(runtime, e, pr.damage, addLog, pr.x, pr.y, pr.sourceEnemy);
          break;
        }
      }
      if (!hit) {
        for (const b of runtime.props) {
          if (b.dead) continue;
          if (distTo(pr, b) < TILE * 0.4) {
            pr.dead = true;
            if (pr.splashRadius > 0) applySplashDamage(runtime, { x: pr.x, y: pr.y, damage: pr.damage, splashRadius: pr.splashRadius, sourceEnemy: pr.sourceEnemy }, addLog);
            else damageBarrel(runtime, b, pr.damage, addLog);
            break;
          }
        }
      }
    }
  }
  runtime.projectiles = runtime.projectiles.filter(pr => !pr.dead);
}

// `explosion` is a plain descriptor `{x, y, damage, splashRadius, sourceEnemy}`
// -- NOT necessarily a real projectile (kamikaze detonations and barrel
// blasts build one on the spot). `sourceEnemy` set means "an enemy caused
// this," which both excludes that enemy from its own blast and triggers
// infighting on whichever OTHER enemies it catches.
function applySplashDamage(runtime, explosion, addLog) {
  const player = runtime.state.player;
  const playerDist = distTo(explosion, player);
  if (playerDist <= explosion.splashRadius) {
    const falloff = 1 - playerDist / explosion.splashRadius;
    applyDamageToPlayer(runtime, Math.round(explosion.damage * (0.4 + 0.6 * falloff)), addLog);
  }
  for (const e of runtime.enemies) {
    if (e.dead || e === explosion.sourceEnemy) continue;
    const d = distTo(explosion, e);
    if (d <= explosion.splashRadius) {
      const falloff = 1 - d / explosion.splashRadius;
      damageEnemy(runtime, e, Math.round(explosion.damage * (0.4 + 0.6 * falloff)), addLog, explosion.x, explosion.y, explosion.sourceEnemy);
    }
  }
  for (const b of runtime.props) {
    if (b.dead) continue;
    if (distTo(explosion, b) <= explosion.splashRadius) damageBarrel(runtime, b, 999, addLog);
  }
}

// Barrels have no AI, just a hurt-flash timer that needs decaying each
// frame like enemies get inside updateEnemies -- there's no other tick
// path for props, so this has to be its own small function.
function updateProps(runtime, dt) {
  for (const b of runtime.props) {
    if (b.hurtFlashTimer > 0) b.hurtFlashTimer = Math.max(0, b.hurtFlashTimer - dt);
  }
}

function damageBarrel(runtime, barrel, amount, addLog) {
  if (barrel.dead) return;
  barrel.hp -= amount;
  barrel.hurtFlashTimer = 0.15;
  if (barrel.hp <= 0) explodeBarrel(runtime, barrel, addLog);
}

// Chain reactions: exploding sets every OTHER still-live barrel within
// range on a guaranteed-kill path too -- each one only ever explodes
// once, since damageBarrel() no-ops on an already-dead barrel, so this
// can't loop back on itself no matter how barrels are clustered.
function explodeBarrel(runtime, barrel, addLog) {
  barrel.dead = true;
  sfxExplosion();
  if (addLog) addLog('Barrel detonates!', 'warning');
  const radius = TILE * BARREL_EXPLOSION_RADIUS_MULT;
  applySplashDamage(runtime, { x: barrel.x, y: barrel.y, damage: BARREL_EXPLOSION_DAMAGE, splashRadius: radius, sourceEnemy: null }, addLog);
  for (const b of runtime.props) {
    if (b === barrel || b.dead) continue;
    if (distTo(barrel, b) <= radius) damageBarrel(runtime, b, 999, addLog);
  }
}

// Opens a security door / secret panel / vault door if the player is
// facing one within reach. Casts a single ray along the player's exact
// facing angle (reusing castRay, same as a hitscan trace). Regular doors
// and secrets always open; vault doors need a matching key first. Doors
// never lock again once opened -- every door is reachable by the player
// themselves, so there's no way to softlock a level behind one.
function interactWithDoor(runtime, addLog) {
  const p = runtime.state.player;
  const { grid, w, h } = runtime.level;
  const hit = castRay(grid, w, h, p.x, p.y, p.angle);
  if (!hit || hit.dist > TILE * 1.6) return false;
  if (hit.wallType !== DOOR_TYPE && hit.wallType !== SECRET_TYPE && hit.wallType !== LOCKED_DOOR_TYPE) return false;
  const px = p.x + Math.cos(p.angle) * (hit.dist + 1);
  const py = p.y + Math.sin(p.angle) * (hit.dist + 1);
  const cellX = Math.floor(px / TILE), cellY = Math.floor(py / TILE);
  if (!grid[cellY] || grid[cellY][cellX] !== hit.wallType) return false;

  if (hit.wallType === LOCKED_DOOR_TYPE) {
    const required = runtime.level.lockedDoors && runtime.level.lockedDoors[cellX + ',' + cellY];
    if (!required || !p.keys.includes(required)) {
      if (addLog) addLog(`Locked. Needs a ${required || 'matching'} access card.`, 'warning');
      return false;
    }
    grid[cellY][cellX] = 0;
    sfxDoorOpen();
    if (addLog) addLog('Vault door unlocks.', 'milestone');
    return true;
  }
  if (hit.wallType === SECRET_TYPE) {
    grid[cellY][cellX] = 0;
    sfxSecretFound();
    if (addLog) addLog('Found a secret!', 'milestone');
    return true;
  }
  grid[cellY][cellX] = 0;
  sfxDoorOpen();
  if (addLog) addLog('Security door opens.', 'info');
  return true;
}

// Ammo dispensers never open -- they top up ammo instead, on a per-station
// cooldown (tracked by grid coordinate, reset each level load).
function interactWithAmmoStation(runtime, addLog) {
  const p = runtime.state.player;
  const { grid, w, h } = runtime.level;
  const hit = castRay(grid, w, h, p.x, p.y, p.angle);
  if (!hit || hit.wallType !== AMMO_STATION_TYPE || hit.dist > TILE * 1.6) return false;
  const px = p.x + Math.cos(p.angle) * (hit.dist + 1);
  const py = p.y + Math.sin(p.angle) * (hit.dist + 1);
  const cellX = Math.floor(px / TILE), cellY = Math.floor(py / TILE);
  if (!grid[cellY] || grid[cellY][cellX] !== AMMO_STATION_TYPE) return false;

  const key = cellX + ',' + cellY;
  const lastUsed = runtime.ammoStationCooldowns[key] || -999;
  if (runtime.time - lastUsed < AMMO_STATION_COOLDOWN) {
    if (addLog) addLog('Dispenser recharging...', 'warning');
    return false;
  }
  let gained = false;
  for (const ammoType in AMMO_REFILL_THRESHOLDS) {
    if (p.ammo[ammoType] === undefined) continue;
    const threshold = AMMO_REFILL_THRESHOLDS[ammoType];
    if (p.ammo[ammoType] < threshold) { p.ammo[ammoType] = threshold; gained = true; }
  }
  runtime.ammoStationCooldowns[key] = runtime.time;
  sfxPickup();
  if (addLog) addLog(gained ? 'Ammo dispenser: topped up.' : 'Ammo dispenser: already full.', 'info');
  return true;
}

function checkPickupCollisions(runtime, addLog) {
  const player = runtime.state.player;
  for (const pk of runtime.pickups) {
    if (pk.collected) continue;
    if (distTo(pk, player) > TILE * 0.5) continue;
    const def = PICKUP_TYPES[pk.type];
    if (def.kind === 'health' && player.hp >= player.maxHp) continue;
    if (def.kind === 'armor' && player.armor >= player.maxArmor) continue;
    if (def.kind === 'weapon' && player.weapons.includes(def.weapon)) continue;
    if (def.kind === 'key' && player.keys.includes(def.keyId)) continue;
    pk.collected = true;
    applyPickupEffect(runtime, def, addLog);
  }
}

function applyPickupEffect(runtime, def, addLog) {
  const p = runtime.state.player;
  if (def.kind === 'health') { p.hp = Math.min(p.maxHp, p.hp + def.amount); if (addLog) addLog(`Picked up ${def.name} (+${def.amount} HP).`, 'info'); }
  else if (def.kind === 'armor') { p.armor = Math.min(p.maxArmor, p.armor + def.amount); if (addLog) addLog(`Picked up ${def.name} (+${def.amount} armor).`, 'info'); }
  else if (def.kind === 'ammo') {
    const cap = AMMO_MAX[def.ammoType] ?? Infinity;
    p.ammo[def.ammoType] = Math.min(cap, (p.ammo[def.ammoType] || 0) + def.amount);
    if (addLog) addLog(`Picked up ${def.name}.`, 'info');
  }
  else if (def.kind === 'weapon') { p.weapons.push(def.weapon); p.currentWeapon = def.weapon; if (addLog) addLog(`New weapon: ${WEAPONS[def.weapon].name}!`, 'milestone'); }
  else if (def.kind === 'part') {
    p.parts++; runtime.state.partsTotal++; runtime.partsThisLevel++;
    if (addLog) addLog(`Found a ${def.name}! (${p.parts} this run)`, 'harvest');
  }
  else if (def.kind === 'key') { p.keys.push(def.keyId); if (addLog) addLog(`Picked up ${def.name}.`, 'milestone'); }
}
