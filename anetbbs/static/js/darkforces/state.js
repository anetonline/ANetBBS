// ─── ANetDarkForces: game state, save/load ───────────────────────────────
// Saves are LEVEL-CHECKPOINT based, not exact-frame resume: what's
// persisted is the player's RPG progress (level/xp/hp/armor/weapons/ammo/
// parts) and which level they're on, not the live position of every
// enemy/projectile mid-firefight. Reloading a save restarts the current
// level fresh (full enemy roster, player kept wherever they leveled up
// to) -- this is the same convention classic checkpoint-based FPS games
// use, and dramatically simpler/safer than trying to serialize a live
// combat scene (in-flight projectiles, partial enemy HP, exact AI state).

const SAVE_SLOTS = 3;

function clonePlayerStart() {
  return JSON.parse(JSON.stringify(PLAYER_START_STATE));
}

function newGameState() {
  return {
    levelIndex: 0,
    player: clonePlayerStart(),
    partsTotal: 0, // running count of BBS parts collected across the whole run (narrative flavor)
    kills: 0,
    deaths: 0,
  };
}

function serializeState(state) {
  return JSON.stringify({
    levelIndex: state.levelIndex,
    player: state.player,
    partsTotal: state.partsTotal,
    kills: state.kills,
    deaths: state.deaths,
  });
}

function deserializeState(json) {
  const data = JSON.parse(json);
  const state = newGameState();
  state.levelIndex = Math.max(0, Math.min(LEVELS.length - 1, data.levelIndex || 0));
  // `freshPlayer` must stay untouched as the TRUE-defaults reference --
  // Object.assign's first argument is its mutation target, so if
  // freshPlayer itself were passed there, it would already be overwritten
  // with the old incomplete data by the time the ammo/weapons merges
  // below try to read "the real defaults" back out of it.
  const freshPlayer = clonePlayerStart();
  state.player = Object.assign({}, freshPlayer, data.player || {});
  // `ammo` and `weapons` are sub-structures that need a field-by-field
  // merge, not the wholesale replacement the assign above just did -- a
  // save from before a since-added ammo type or starter weapon existed
  // would otherwise silently and permanently lose it (the outer assign
  // only protects keys ABSENT from old data; `ammo`/`weapons` ARE present
  // in old saves, just incomplete).
  state.player.ammo = Object.assign({}, freshPlayer.ammo, (data.player && data.player.ammo) || {});
  state.player.weapons = (data.player && Array.isArray(data.player.weapons))
    ? Array.from(new Set([...freshPlayer.weapons, ...data.player.weapons]))
    : freshPlayer.weapons;
  state.partsTotal = data.partsTotal || 0;
  state.kills = data.kills || 0;
  state.deaths = data.deaths || 0;
  return state;
}

// Save/load goes through anetbbs/web/darkforces.py's per-user API (tied
// to the logged-in ANetBBS account) instead of localStorage --
// serializeState()/deserializeState() above are UNCHANGED, only the
// storage backend moved. hasSave()/getSaveSummary() stay SYNCHRONOUS
// (reading a small client-side cache populated by fetchSaveSummaries())
// so the rest of the game's code -- renderSlotPicker() in particular --
// didn't need to become async-aware itself; only the actual save/load
// calls (and the one-time startup fetch) are async. Mirrors Meadowlark
// Valley's own migration (anetbbs/static/js/meadowlark/state.js) exactly.
let _saveSummaries = {}; // slot -> {empty, level_name, level_index, player_level, updated_at}

function _csrfToken() {
  const m = document.querySelector('meta[name="csrf-token"]');
  return m ? m.getAttribute('content') : '';
}

async function fetchSaveSummaries() {
  try {
    const res = await fetch('/games/darkforces/saves', { credentials: 'same-origin' });
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

function getSaveSummary(slot) {
  const s = _saveSummaries[slot];
  if (!s || s.empty) return null;
  return { levelName: s.level_name, levelIndex: s.level_index, playerLevel: s.player_level };
}

async function saveGame(state, slot = 1) {
  try {
    const lvl = LEVELS[state.levelIndex] || LEVELS[0];
    const res = await fetch(`/games/darkforces/state/${slot}`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _csrfToken() },
      body: JSON.stringify({
        state_json: serializeState(state),
        level_name: lvl.name,
        level_index: state.levelIndex + 1,
        player_level: state.player.level,
      }),
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
    const res = await fetch(`/games/darkforces/state/${slot}`, { credentials: 'same-origin' });
    if (!res.ok) return null;
    const data = await res.json();
    return deserializeState(data.state_json);
  } catch (e) {
    console.error('Load failed', e);
    return null;
  }
}
