// ─── ANetDarkForces: bootstrap, input, game loop ─────────────────────────
// Ties together raycaster.js/entities.js/weapons.js/ui.js/sound.js into a
// running game. The live player position/angle live on
// `runtime.state.player` alongside the persisted RPG fields (hp/xp/etc.)
// -- one object, no second copy to keep in sync. A fresh level load always
// overwrites x/y/angle from that level's playerStart, so it's harmless
// that serializeState() ends up persisting a stale position too.

let runtime = null; // null until a game is actually started/loaded from the title screen
const keys = {};
let mouseDown = false;
let pointerLocked = false;

// ── Runtime lifecycle ──────────────────────────────────────────────────
function freshRuntimeFromState(state, slot) {
  return {
    state,
    currentSlot: slot,
    level: null,
    enemies: [], pickups: [], projectiles: [], props: [],
    ammoStationCooldowns: {}, // keyed by "x,y", value is runtime.time it was last used
    zBuffer: new Array(NUM_RAYS).fill(MAX_DEPTH),
    time: 0,
    lastFireTime: -999,
    lastDryFireTime: -999,
    lastLockedMsgTime: -999,
    hurtFlashTimer: 0,
    muzzleFlashTimer: 0,
    jumpTimer: 0, // cosmetic camera-hop only -- see JUMP_BOB_* in data.js
    walkCycle: 0,         // phase accumulator driving weapon-bob + footstep timing
    weaponKickTimer: 0,   // recoil-kick decay after firing (see WEAPON_KICK_*)
    footstepTimer: 0,     // counts down to the next footstep sound while moving
    stepIndex: 0,         // alternates footstep pitch left/right
    playerDead: false,
    bossDefeated: false,
    partsThisLevel: 0,
    log: [],
    status: 'intro', // intro | playing | paused | levelComplete | dead | victory
  };
}

function addLog(rt, msg, kind) { addLogEntry(rt, msg, kind); }

// Loads a level template fresh (enemies/pickups always rebuilt from
// LEVELS[index], never carried over from a previous attempt) and either
// shows the narrative intro screen or drops straight into play (used on
// respawn, where re-reading the intro text would just be annoying).
function loadLevel(rt, index, showIntro) {
  const level = LEVELS[index];
  rt.level = level;
  rt.state.levelIndex = index;
  rt.enemies = level.enemies.map(e => { const w = gridToWorld(e.gx, e.gy); return spawnEnemy(e.type, w.x, w.y); });
  rt.pickups = level.pickups.map(p => { const w = gridToWorld(p.gx, p.gy); return spawnPickup(p.type, w.x, w.y); });
  rt.props = (level.barrels || []).map(b => { const w = gridToWorld(b.gx, b.gy); return spawnBarrel(w.x, w.y); });
  rt.projectiles = [];
  rt.ammoStationCooldowns = {};
  rt.bossDefeated = !rt.enemies.some(e => e.def.isBoss);
  rt.playerDead = false;
  rt.partsThisLevel = 0;
  rt.hurtFlashTimer = 0;
  rt.muzzleFlashTimer = 0;
  rt.jumpTimer = 0;
  rt.walkCycle = 0;
  rt.weaponKickTimer = 0;
  rt.footstepTimer = 0;
  rt.lastLockedMsgTime = -999;

  const startPos = gridToWorld(level.playerStart.gx, level.playerStart.gy);
  rt.state.player.x = startPos.x;
  rt.state.player.y = startPos.y;
  rt.state.player.angle = level.playerStart.angle;

  if (showIntro) {
    rt.status = 'intro';
    renderLevelIntro(level, index + 1, LEVELS.length);
    showScreen('screenIntro');
  } else {
    rt.status = 'playing';
    hideScreens();
  }
}

function respawnPlayer(rt) {
  rt.state.player.hp = rt.state.player.maxHp; // checkpoint respawn: full heal, RPG progress otherwise untouched
  loadLevel(rt, rt.state.levelIndex, false);
}

// ── Input ───────────────────────────────────────────────────────────────
function bindInput() {
  window.addEventListener('keydown', e => {
    keys[e.code] = true;
    // Space and Alt both have default browser behavior (page scroll /
    // menu-bar focus) worth suppressing on every keydown, regardless of
    // game state, not just while actively playing.
    if (e.code === 'Space' || e.code === 'AltLeft' || e.code === 'AltRight') e.preventDefault();
    if (e.code === 'Escape') {
      e.preventDefault();
      if (runtime && runtime.status === 'laptop') toggleLaptop();
      else if (runtime) togglePause();
      return;
    }
    if (e.code === 'Tab' && runtime && (runtime.status === 'playing' || runtime.status === 'laptop') && !e.repeat) {
      e.preventDefault();
      toggleLaptop();
      return;
    }
    if (!runtime || runtime.status !== 'playing' || e.repeat) return;
    if (e.code === 'Space') {
      const cb = (m, k) => addLog(runtime, m, k);
      if (!interactWithDoor(runtime, cb)) interactWithAmmoStation(runtime, cb);
    }
    else if (e.code === 'AltLeft' || e.code === 'AltRight') {
      if (runtime.jumpTimer <= 0) runtime.jumpTimer = JUMP_BOB_DURATION;
    } else if (e.code.startsWith('Digit')) {
      const n = parseInt(e.code.slice(5), 10);
      if (n >= 1 && n <= WEAPON_ORDER.length) switchWeapon(runtime, WEAPON_ORDER[n - 1]);
    } else if (e.code === 'KeyQ') cycleWeapon(runtime, -1);
    else if (e.code === 'KeyE') cycleWeapon(runtime, 1);
  });
  window.addEventListener('keyup', e => { keys[e.code] = false; });

  document.getElementById('gameCanvas').addEventListener('wheel', e => {
    if (!runtime || runtime.status !== 'playing') return;
    e.preventDefault();
    cycleWeapon(runtime, e.deltaY > 0 ? 1 : -1);
  }, { passive: false });

  const canvas = document.getElementById('gameCanvas');
  canvas.addEventListener('click', () => {
    if (runtime && runtime.status === 'playing') canvas.requestPointerLock();
  });
  document.addEventListener('pointerlockchange', () => {
    pointerLocked = document.pointerLockElement === canvas;
  });
  document.addEventListener('mousemove', e => {
    if (!pointerLocked || !runtime || runtime.status !== 'playing') return;
    runtime.state.player.angle += e.movementX * 0.0025;
  });
  document.addEventListener('mousedown', e => {
    if (pointerLocked && e.button === 0) mouseDown = true;
  });
  document.addEventListener('mouseup', () => { mouseDown = false; });
}

function updatePlayerMovement(rt, dt) {
  const p = rt.state.player;
  let forward = 0, strafe = 0;
  if (keys['KeyW'] || keys['ArrowUp']) forward += 1;
  if (keys['KeyS'] || keys['ArrowDown']) forward -= 1;
  if (keys['KeyD']) strafe += 1;
  if (keys['KeyA']) strafe -= 1;
  if (keys['ArrowLeft']) p.angle -= TURN_SPEED * dt;
  if (keys['ArrowRight']) p.angle += TURN_SPEED * dt;

  let sprint = 1;
  if (forward !== 0 || strafe !== 0) {
    const len = Math.hypot(forward, strafe) || 1;
    forward /= len; strafe /= len;
    sprint = (keys['ShiftLeft'] || keys['ShiftRight']) ? SPRINT_MULT : 1;
    const fx = Math.cos(p.angle), fy = Math.sin(p.angle);
    const sx = Math.cos(p.angle + Math.PI / 2), sy = Math.sin(p.angle + Math.PI / 2);
    const dx = (fx * forward * MOVE_SPEED + sx * strafe * STRAFE_SPEED) * sprint * TILE * dt;
    const dy = (fy * forward * MOVE_SPEED + sy * strafe * STRAFE_SPEED) * sprint * TILE * dt;
    const moved = moveWithCollision(rt.level.grid, rt.level.w, rt.level.h, p.x, p.y, dx, dy, PLAYER_RADIUS);
    p.x = moved.x; p.y = moved.y;

    rt.walkCycle += WEAPON_BOB_SPEED * sprint * dt;
    rt.footstepTimer -= dt;
    if (rt.footstepTimer <= 0) {
      rt.footstepTimer = FOOTSTEP_INTERVAL / sprint;
      rt.stepIndex++;
      sfxFootstep(rt.stepIndex, sprint > 1);
    }
  } else {
    rt.footstepTimer = 0; // next step fires immediately on resuming movement, no stale carry-over silence
  }

  if (keys['ControlLeft'] || keys['ControlRight'] || mouseDown) fireWeapon(rt, (m, k) => addLog(rt, m, k));
}

// ── Update / render ────────────────────────────────────────────────────
function update(rt, dt) {
  rt.time += dt;
  rt.hurtFlashTimer = Math.max(0, rt.hurtFlashTimer - dt);
  rt.muzzleFlashTimer = Math.max(0, rt.muzzleFlashTimer - dt);
  rt.jumpTimer = Math.max(0, rt.jumpTimer - dt);
  rt.weaponKickTimer = Math.max(0, rt.weaponKickTimer - dt);
  if (rt.status !== 'playing') return;

  updatePlayerMovement(rt, dt);
  updateEnemies(rt, dt, (m, k) => addLog(rt, m, k));
  updateProjectiles(rt, dt, (m, k) => addLog(rt, m, k));
  updateProps(rt, dt);
  checkPickupCollisions(rt, (m, k) => addLog(rt, m, k));

  if (rt.playerDead) {
    rt.status = 'dead';
    sfxDeath();
    renderDeathScreen(rt);
    showScreen('screenDeath');
    return;
  }

  const level = rt.level;
  const p = rt.state.player;
  const exitWorld = gridToWorld(level.exit.gx, level.exit.gy);
  if (Math.hypot(p.x - exitWorld.x, p.y - exitWorld.y) < TILE * 0.6) {
    if (rt.bossDefeated) {
      rt.status = 'levelComplete';
      saveGame(rt.state, rt.currentSlot); // checkpoint save on every sector clear -- fire-and-forget, update() itself stays sync (called every animation frame, not worth awaiting a network round-trip here)
      sfxLevelComplete();
      renderLevelComplete(rt, rt.state.levelIndex + 1, LEVELS.length);
      showScreen('screenComplete');
    } else if (rt.time - rt.lastLockedMsgTime > 2) {
      rt.lastLockedMsgTime = rt.time;
      addLog(rt, 'The exit is sealed. Deal with whoever is really in charge here first.', 'warning');
    }
  }
}

function render(rt) {
  const canvas = document.getElementById('gameCanvas');
  const ctx = canvas.getContext('2d');
  if (!rt.level) return;
  renderScene(ctx, rt);
  renderMinimap(ctx, rt, RENDER_W - 66, 66, 6);
  renderHud(rt);
  renderWeaponBar(rt);
  renderDamageFlash(rt);
  const muzzle = document.getElementById('muzzleFlash');
  if (muzzle) muzzle.style.opacity = rt.muzzleFlashTimer > 0 ? '0.5' : '0';
}

let lastTs = 0;
function loop(ts) {
  const dt = Math.min(0.1, lastTs ? (ts - lastTs) / 1000 : 0);
  lastTs = ts;
  if (runtime) { update(runtime, dt); render(runtime); }
  requestAnimationFrame(loop);
}

// ── Screen flow / menu wiring ──────────────────────────────────────────
function togglePause() {
  if (!runtime || (runtime.status !== 'playing' && runtime.status !== 'paused')) return;
  if (runtime.status === 'playing') {
    runtime.status = 'paused';
    renderPauseMenu(runtime);
    showScreen('screenPause');
  } else {
    runtime.status = 'playing';
    hideScreens();
  }
}

function toggleLaptop() {
  if (!runtime) return;
  if (runtime.status === 'playing') {
    runtime.status = 'laptop';
    renderLaptopScreen(runtime);
    showScreen('screenLaptop');
  } else if (runtime.status === 'laptop') {
    runtime.status = 'playing';
    hideScreens();
  }
}

function showTitleScreen() {
  runtime = null;
  hideScreens();
  renderTitleScreen(1, slot => startOrLoadSlot(slot));
}

async function startOrLoadSlot(slot) {
  const loaded = await loadGame(slot);
  const state = loaded || newGameState();
  runtime = freshRuntimeFromState(state, slot);
  loadLevel(runtime, state.levelIndex, true);
}

function bindScreenButtons() {
  document.getElementById('btnBeginLevel').addEventListener('click', () => {
    if (!runtime) return;
    runtime.status = 'playing';
    hideScreens();
  });
  document.getElementById('btnLevelContinue').addEventListener('click', () => {
    if (!runtime) return;
    const next = runtime.state.levelIndex + 1;
    if (next < LEVELS.length) {
      loadLevel(runtime, next, true);
    } else {
      runtime.status = 'victory';
      sfxLevelComplete();
      renderGameComplete(runtime);
      showScreen('screenVictory');
    }
  });
  document.getElementById('btnRespawn').addEventListener('click', () => {
    if (!runtime) return;
    respawnPlayer(runtime);
  });
  document.getElementById('btnPlayAgain').addEventListener('click', showTitleScreen);
  document.getElementById('btnResume').addEventListener('click', togglePause);
  document.getElementById('btnSaveGame').addEventListener('click', async () => {
    if (!runtime) return;
    await saveGame(runtime.state, runtime.currentSlot);
    addLog(runtime, 'Game saved.', 'info');
  });
  document.getElementById('btnLoadGame').addEventListener('click', async () => {
    if (!runtime) return;
    const loaded = await loadGame(runtime.currentSlot);
    if (loaded) {
      runtime.state = loaded;
      loadLevel(runtime, loaded.levelIndex, true);
      addLog(runtime, 'Game loaded.', 'info');
    } else {
      addLog(runtime, 'No save in that slot.', 'warning');
    }
  });
  document.getElementById('btnQuitToTitle').addEventListener('click', showTitleScreen);
  document.getElementById('btnCloseLaptop').addEventListener('click', toggleLaptop);

  document.getElementById('btnMute').addEventListener('click', toggleMuted);
  const vol = document.getElementById('volumeSlider');
  if (vol) {
    vol.value = String(Math.round(soundVolume * 100));
    vol.addEventListener('input', () => setVolume(vol.value / 100));
  }
}

async function init() {
  buildAllWallTextures();
  buildAllSpriteTextures();
  unlockAudioOnFirstGesture();
  setMuted(soundMuted); // sync the mute button's initial icon to the stored preference
  bindInput();
  bindScreenButtons();
  await fetchSaveSummaries(); // so the title screen's slot picker shows real labels on its very first render, not "Empty" for a beat
  showTitleScreen();
  requestAnimationFrame(loop);
}

window.addEventListener('DOMContentLoaded', init);
