// ─── Bootstrap: wire state, canvas input, simulation & render loops ────────

// Async IIFE, not a plain one -- the ANetBBS migration moved save/load
// from synchronous localStorage to a server round-trip (see js/state.js),
// so figuring out the INITIAL state now needs one await before anything
// else in here can run. Everything after that first await is unchanged
// synchronous logic.
(async function () {
  const canvas = document.getElementById('gameCanvas');
  canvas.width = VIEW_TILES_W * TILE_SIZE;
  canvas.height = VIEW_TILES_H * TILE_SIZE;
  const ctx = canvas.getContext('2d');

  const minimapCanvas = document.getElementById('minimapCanvas');
  minimapCanvas.width = Math.round(GRID_W * MINIMAP_SCALE);
  minimapCanvas.height = Math.round(GRID_H * MINIMAP_SCALE);
  const minimapCtx = minimapCanvas.getContext('2d');

  await fetchSaveSummaries();
  let state = hasSave(1) ? await loadGame(1) : newGameState();
  if (!state) state = newGameState(); // server load failed despite hasSave(1) -- don't hard-fail
  uiState = state;
  npcs = [];
  recomputeUnlocks(state);
  resyncNpcs(state);

  function log(msg, kind) { addLogEntry(state, msg, kind); }

  // ── Camera: world-pixel top-left of the viewport, plus zoom ──
  const camera = { x: 0, y: 0, zoom: 1 };
  window.__debugCamera = camera; // read-only introspection for automated tests
  function clampCamera() {
    const viewWorldW = canvas.width / camera.zoom;
    const viewWorldH = canvas.height / camera.zoom;
    const maxX = Math.max(0, GRID_W * TILE_SIZE - viewWorldW);
    const maxY = Math.max(0, GRID_H * TILE_SIZE - viewWorldH);
    camera.x = Math.max(0, Math.min(maxX, camera.x));
    camera.y = Math.max(0, Math.min(maxY, camera.y));
  }
  function centerCameraOn(px, py) {
    camera.x = px - (canvas.width / camera.zoom) / 2;
    camera.y = py - (canvas.height / camera.zoom) / 2;
    clampCamera();
  }
  // Start centered on Town Hall (or the loaded save's town center).
  centerCameraOn((GRID_W / 2) * TILE_SIZE, (GRID_H / 2) * TILE_SIZE);

  function screenToWorldPx(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    const sx = clientX - rect.left, sy = clientY - rect.top;
    return { x: camera.x + sx / camera.zoom, y: camera.y + sy / camera.zoom };
  }
  function screenToTile(clientX, clientY) {
    const { x: worldX, y: worldY } = screenToWorldPx(clientX, clientY);
    return { x: Math.floor(worldX / TILE_SIZE), y: Math.floor(worldY / TILE_SIZE) };
  }

  // ── Building placement ──
  function canAfford(cost) { return state.cash >= cost; }

  // Co-op: broadcasts a successful placement to the room (no-op if not in
  // a co-op room, or if this call is itself the local application of a
  // remote player's action -- see coopBroadcastAction/coopApplyingRemote
  // further down). `tool` is passed explicitly rather than read back off
  // state.selectedTool, since a remote action's replay temporarily swaps
  // selectedTool without that being "this client's real tool selection."
  function placeAt(x, y) {
    const t = tileAt(state, x, y);
    if (!t) return;
    const tool = state.selectedTool;

    if (tool === 'bulldoze') {
      if (t.type === 'grass' || t.type === 'townhall') return;
      const def = BUILDINGS[t.type];
      const refund = def ? Math.round(def.cost * 0.4) : 0;
      state.cash += refund;
      state.grid[y][x] = makeEmptyTile();
      recomputeUtilities(state);
      selectTile(state, x, y);
      renderTopBar(state);
      sfxBulldoze();
      coopBroadcastAction('place', { x, y, tool: 'bulldoze' });
      return;
    }

    const def = BUILDINGS[tool];
    if (!def) return;
    if (t.type !== 'grass') { log(`That tile is already occupied.`, 'warning'); return; }
    if (state.population < def.unlock) { log(`${def.name} unlocks at population ${def.unlock}.`, 'warning'); return; }
    if (!canAfford(def.cost)) { log(`Not enough cash for a ${def.name} ($${def.cost}).`, 'warning'); return; }

    state.cash -= def.cost;
    const newTile = makeEmptyTile();
    newTile.type = tool;
    if (tool === 'shop') newTile.jobs = BUILDINGS.shop.baseJobs;
    if (tool === 'factory') newTile.jobs = BUILDINGS.factory.baseJobs;
    if (tool === 'mall') newTile.jobs = BUILDINGS.mall.baseJobs;
    if (tool === 'farm') newTile.jobs = BUILDINGS.farm.baseJobs;
    state.grid[y][x] = newTile;
    recomputeUtilities(state);
    selectTile(state, x, y);
    renderTopBar(state);
    sfxBuild();
    coopBroadcastAction('place', { x, y, tool });
  }

  function handleTileClick(x, y) {
    const t = tileAt(state, x, y);
    if (!t) return;
    // Harvesting a ripe farm takes priority over re-building on it.
    if (t.type === 'farm' && t.farmStage >= BUILDINGS.farm.growDays) {
      harvestFarm(state, x, y, log);
      selectTile(state, x, y);
      renderTopBar(state);
      return;
    }
    if (state.selectedTool && (t.type === 'grass' || state.selectedTool === 'bulldoze')) {
      placeAt(x, y);
    } else {
      selectTile(state, x, y);
    }
  }

  // Left click = build/select. Right-click-drag = pan. Browsers only ever
  // dispatch a `click` event for the primary (left) button — a right-drag
  // never generates one — so no drag-vs-click disambiguation is needed
  // here at all. (An earlier version tried to suppress the click right
  // after a pan via a `didPan` flag; since that flag was only ever
  // consumed by this same left-click handler, and pan-dragging uses the
  // right button, it just stayed stuck `true` until whatever the next
  // unrelated left-click happened to be — silently eating one real build
  // click any time a player panned before their next placement.)
  let dragStart = null;

  canvas.addEventListener('mousedown', (e) => {
    if (e.button === 2) {
      dragStart = { x: e.clientX, y: e.clientY, camX: camera.x, camY: camera.y };
    }
  });
  window.addEventListener('mousemove', (e) => {
    if (dragStart) {
      const dx = (e.clientX - dragStart.x) / camera.zoom;
      const dy = (e.clientY - dragStart.y) / camera.zoom;
      camera.x = dragStart.camX - dx;
      camera.y = dragStart.camY - dy;
      clampCamera();
    }
  });
  window.addEventListener('mouseup', (e) => {
    if (e.button === 2) dragStart = null;
  });
  canvas.addEventListener('contextmenu', (e) => e.preventDefault());

  // Shared by the mouse click handler and the touch tap handler below —
  // NPC hit-test takes priority over tile placement/inspection either way.
  function tapAt(clientX, clientY) {
    const worldPx = screenToWorldPx(clientX, clientY);
    const npc = findNpcNear(worldPx.x, worldPx.y);
    if (npc) { selectNpc(npc); return; }
    const { x, y } = screenToTile(clientX, clientY);
    handleTileClick(x, y);
  }

  canvas.addEventListener('click', (e) => tapAt(e.clientX, e.clientY));

  canvas.addEventListener('mousemove', (e) => {
    hoverTile = screenToTile(e.clientX, e.clientY);
  });
  canvas.addEventListener('mouseleave', () => { hoverTile = null; });

  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
    const worldXBefore = camera.x + sx / camera.zoom;
    const worldYBefore = camera.y + sy / camera.zoom;
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    camera.zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, camera.zoom * factor));
    camera.x = worldXBefore - sx / camera.zoom;
    camera.y = worldYBefore - sy / camera.zoom;
    clampCamera();
  }, { passive: false });

  // ── Touch input: one-finger tap = click, one-finger drag = pan,
  // two-finger pinch = zoom. `{ passive: false }` + preventDefault on all
  // three touch events, not just touchmove, so the browser never gets a
  // chance to treat this as a page-scroll/pinch-zoom gesture itself. ──
  let touchState = null; // { mode: 'pan'|'pinch', ... } or null between gestures
  const TAP_MOVE_THRESHOLD = 8; // px — beyond this, a 1-finger touch is a pan, not a tap

  canvas.addEventListener('touchstart', (e) => {
    e.preventDefault();
    if (e.touches.length === 1) {
      const t = e.touches[0];
      touchState = {
        mode: 'pan', startX: t.clientX, startY: t.clientY,
        camX: camera.x, camY: camera.y, moved: false
      };
    } else if (e.touches.length === 2) {
      const [t1, t2] = e.touches;
      touchState = {
        mode: 'pinch',
        lastDist: Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY)
      };
    }
  }, { passive: false });

  canvas.addEventListener('touchmove', (e) => {
    e.preventDefault();
    if (!touchState) return;
    if (touchState.mode === 'pan' && e.touches.length === 1) {
      const t = e.touches[0];
      const dx = (t.clientX - touchState.startX) / camera.zoom;
      const dy = (t.clientY - touchState.startY) / camera.zoom;
      if (Math.hypot(t.clientX - touchState.startX, t.clientY - touchState.startY) > TAP_MOVE_THRESHOLD) {
        touchState.moved = true;
      }
      camera.x = touchState.camX - dx;
      camera.y = touchState.camY - dy;
      clampCamera();
    } else if (touchState.mode === 'pinch' && e.touches.length === 2) {
      const [t1, t2] = e.touches;
      const dist = Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
      const factor = dist / touchState.lastDist;
      const rect = canvas.getBoundingClientRect();
      const midX = (t1.clientX + t2.clientX) / 2, midY = (t1.clientY + t2.clientY) / 2;
      const sx = midX - rect.left, sy = midY - rect.top;
      const worldXBefore = camera.x + sx / camera.zoom;
      const worldYBefore = camera.y + sy / camera.zoom;
      camera.zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, camera.zoom * factor));
      camera.x = worldXBefore - sx / camera.zoom;
      camera.y = worldYBefore - sy / camera.zoom;
      clampCamera();
      touchState.lastDist = dist;
    }
  }, { passive: false });

  canvas.addEventListener('touchend', (e) => {
    e.preventDefault();
    if (touchState && touchState.mode === 'pan' && !touchState.moved) {
      const t = e.changedTouches[0];
      tapAt(t.clientX, t.clientY);
    }
    touchState = null;
  }, { passive: false });
  canvas.addEventListener('touchcancel', () => { touchState = null; });

  window.addEventListener('keydown', (e) => {
    const panSpeed = 24 / camera.zoom;
    if (e.key === 'ArrowLeft')  { camera.x -= panSpeed; clampCamera(); }
    if (e.key === 'ArrowRight') { camera.x += panSpeed; clampCamera(); }
    if (e.key === 'ArrowUp')    { camera.y -= panSpeed; clampCamera(); }
    if (e.key === 'ArrowDown')  { camera.y += panSpeed; clampCamera(); }

    // Number-key tool shortcuts (see KEY_TOOL_MAP, data.js) — skipped
    // while a form control has focus (e.g. the tax slider) so this
    // doesn't hijack normal input interaction.
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    const key = KEY_TOOL_MAP[e.key];
    if (!key) return;
    if (key === 'bulldoze') {
      state.selectedTool = 'bulldoze';
      document.querySelectorAll('.build-btn').forEach(b => b.classList.remove('selected'));
      return;
    }
    const def = BUILDINGS[key];
    if (!def || state.population < def.unlock) return; // matches disabled-button behavior for locked tools
    selectedCategory = def.category;
    state.selectedTool = key;
    renderBuildMenu(state);
  });

  function zoomBy(factor) {
    const cx = camera.x + (canvas.width / camera.zoom) / 2;
    const cy = camera.y + (canvas.height / camera.zoom) / 2;
    camera.zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, camera.zoom * factor));
    centerCameraOn(cx, cy);
  }
  document.getElementById('btnZoomIn')?.addEventListener('click', () => zoomBy(1.25));
  document.getElementById('btnZoomOut')?.addEventListener('click', () => zoomBy(1 / 1.25));
  document.getElementById('btnRecenter')?.addEventListener('click', () => {
    // Find the Town Hall and recenter on it.
    for (let y = 0; y < GRID_H; y++) {
      for (let x = 0; x < GRID_W; x++) {
        if (state.grid[y][x].type === 'townhall') {
          centerCameraOn(x * TILE_SIZE + TILE_SIZE / 2, y * TILE_SIZE + TILE_SIZE / 2);
          return;
        }
      }
    }
  });

  // ── Save slots ──
  let currentSlot = 1;
  function renderSlotPicker() {
    const el = document.getElementById('slotPicker');
    let html = '';
    for (let slot = 1; slot <= SAVE_SLOTS; slot++) {
      const summary = getSaveSummary(slot);
      const sub = summary ? `Day ${summary.day} · Pop ${summary.population}` : 'Empty';
      html += `<button class="slot-btn ${slot === currentSlot ? 'active' : ''}" data-slot="${slot}">
                 <span>Slot ${slot}</span><span class="slot-sub">${sub}</span>
               </button>`;
    }
    el.innerHTML = html;
    el.querySelectorAll('.slot-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        currentSlot = parseInt(btn.dataset.slot, 10);
        renderSlotPicker();
      });
    });
  }

  // ── Top bar / save / load / new game ──
  // No export/import here -- removed as part of the ANetBBS migration.
  // Those were file-based conveniences for the localStorage era; a save
  // tied to your account doesn't need a "download it, re-upload it" path.
  async function doSave() {
    const ok = await saveGame(state, currentSlot);
    renderSlotPicker();
    log(ok ? `Game saved to Slot ${currentSlot}.` : 'Save failed — try again.', ok ? 'info' : 'warning');
  }
  async function doLoad() {
    const loaded = await loadGame(currentSlot);
    if (loaded) {
      state = loaded; uiState = state;
      selectedInfoTile = null; selectedNpc = null;
      recomputeUnlocks(state); resyncNpcs(state); renderAll(state);
      log(`Game loaded from Slot ${currentSlot}.`, 'info');
    }
    else log(`Slot ${currentSlot} is empty.`, 'warning');
  }
  function doNewGame() {
    if (!confirm('Start a new town? This discards the current one unless saved.')) return;
    state = newGameState();
    uiState = state;
    npcs = [];
    selectedInfoTile = null;
    selectedNpc = null;
    camera.zoom = 1;
    centerCameraOn((GRID_W / 2) * TILE_SIZE, (GRID_H / 2) * TILE_SIZE);
    recomputeUnlocks(state);
    renderAll(state);
    log('A new town begins in Meadowlark Valley!', 'info');
  }

  // ── Co-op: build the same town together with friends ──────────────────
  // One player (whoever clicks "Host") is the single source of simulation
  // truth -- simulateDay() below only ever runs for the host (see the
  // `if (!coopRoomCode || coopIsHost)` guard in the sim loop further
  // down); everyone else just renders whatever full-state snapshot the
  // host last broadcast, and relays their own build/bulldoze/tax/speed
  // input as `action` events for the host to actually apply. See
  // anetbbs/web/meadowlark.py's /mlv-coop namespace for the server side
  // (a pure relay -- it doesn't understand game rules, just room
  // membership), and js/state.js's `io` global (loaded by base.html
  // site-wide for the MRC chat bridge's own use, reused here rather than
  // bundling a second copy of socket.io-client).
  let coopSocket = null;
  let coopRoomCode = null;
  let coopIsHost = false;
  // Guards against re-broadcasting an action that's itself the LOCAL
  // replay of a remote player's action (placeAt() always fires
  // coopBroadcastAction on success -- without this guard, replaying a
  // remote action back through placeAt() would immediately echo it back
  // out to the room, and every other member would do the same, forever).
  let coopApplyingRemote = false;

  function coopEnsureSocket() {
    if (coopSocket) return coopSocket;
    coopSocket = io('/mlv-coop');
    coopSocket.on('room_hosted', ({ code }) => {
      coopRoomCode = code; coopIsHost = true;
      renderCoopPanel();
      log(`Co-op room ${code} created — share this code with friends.`, 'info');
    });
    coopSocket.on('room_joined', ({ code, host_username }) => {
      coopRoomCode = code; coopIsHost = false;
      renderCoopPanel();
      log(`Joined ${host_username}'s co-op room (${code}).`, 'info');
    });
    coopSocket.on('room_error', ({ message }) => log(message, 'warning'));
    coopSocket.on('member_joined', ({ username }) => {
      log(`${username} joined the co-op room.`, 'info');
      // A new joiner otherwise waits until the host's next simulated day
      // (up to TICK_MS away, or forever if paused) to see anything --
      // send them the current town immediately instead.
      if (coopIsHost) {
        coopSocket.emit('state_sync', { code: coopRoomCode, state_json: serializeState(state) });
      }
    });
    coopSocket.on('member_left', ({ username }) => {
      log(`${username} left the co-op room.`, 'info');
    });
    coopSocket.on('host_left', () => {
      log('The host left — co-op session ended.', 'warning');
      coopRoomCode = null; coopIsHost = false;
      renderCoopPanel();
    });
    coopSocket.on('state_sync', ({ state_json }) => {
      if (coopIsHost || !state_json) return; // only guests consume full syncs
      try {
        state = deserializeState(state_json);
        uiState = state;
        selectedInfoTile = null; selectedNpc = null;
        recomputeUnlocks(state);
        resyncNpcs(state);
        renderAll(state);
      } catch (e) { console.error('coop state_sync apply failed', e); }
    });
    coopSocket.on('action', ({ type, payload }) => {
      coopApplyingRemote = true;
      try {
        if (type === 'place') {
          const prevTool = state.selectedTool;
          state.selectedTool = payload.tool;
          placeAt(payload.x, payload.y);
          state.selectedTool = prevTool;
        } else if (type === 'tax') {
          state.taxRate = payload.taxRate;
          document.getElementById('taxSlider').value = payload.taxRate;
          document.getElementById('taxValue').textContent = `${payload.taxRate}%`;
        } else if (type === 'speed' && coopIsHost) {
          // Only the host's own speed actually drives the shared
          // simulation (see the sim-loop guard below) -- a guest's
          // speed button still visually updates for THEM locally (ui.js
          // handles that independent of co-op), but replaying a guest's
          // speed choice into the host's state would let any one guest
          // pause/fast-forward the whole room's town.
          state.speed = payload.speed;
        }
      } finally {
        coopApplyingRemote = false;
      }
      // Host: push a corrective full sync right after applying a remote
      // action, not just once per simulated day. A guest's OWN 'place'
      // click applies optimistically to their local state immediately
      // (same placeAt() everyone uses) before this event even round-trips
      // -- if the host's real, authoritative state rejects it (e.g. the
      // host's real cash was lower than the guest's last-synced view, so
      // canAfford() failed on the host's copy even though it passed on
      // the guest's stale one), the guest would otherwise see a phantom
      // building for however long is left until the next day-tick sync
      // (up to ~2.6s at normal speed, longer if paused). This closes that
      // gap to one immediate round-trip instead.
      if (coopIsHost && coopRoomCode) {
        coopSocket.emit('state_sync', { code: coopRoomCode, state_json: serializeState(state) });
      }
    });
    return coopSocket;
  }

  function coopBroadcastAction(type, payload) {
    if (!coopRoomCode || coopApplyingRemote) return;
    coopEnsureSocket().emit('action', { code: coopRoomCode, type, payload });
  }

  function coopHost() { coopEnsureSocket().emit('host_room', {}); }
  function coopJoinRoom(code) { coopEnsureSocket().emit('join_room_req', { code }); }
  function coopLeaveRoom() {
    if (coopSocket && coopRoomCode) coopSocket.emit('leave_room_req', {});
    coopRoomCode = null; coopIsHost = false;
    renderCoopPanel();
  }

  function renderCoopPanel() {
    const panel = document.getElementById('coopPanel');
    if (!panel) return;
    if (!coopRoomCode) {
      panel.innerHTML = `
        <p class="mlv-modal-hint">Host a room and share the code, or join a friend's room, to build the same town together.</p>
        <button id="coopHostBtn" class="bulldoze-btn" style="background:#3a6a4a;">Host a Room</button>
        <div style="display:flex; gap:6px; margin-top:8px;">
          <input id="coopJoinCode" maxlength="5" placeholder="CODE" style="text-transform:uppercase; width:90px;">
          <button id="coopJoinBtn">Join</button>
        </div>`;
      document.getElementById('coopHostBtn').addEventListener('click', coopHost);
      document.getElementById('coopJoinBtn').addEventListener('click', () => {
        const code = document.getElementById('coopJoinCode').value.trim();
        if (code) coopJoinRoom(code);
      });
    } else {
      panel.innerHTML = `
        <p class="mlv-modal-hint">${coopIsHost ? 'Hosting' : 'Joined'} room <strong>${escapeHtml(coopRoomCode)}</strong>.
          ${coopIsHost ? 'Share this code — your town is the shared one everyone builds on.' : "You're building on the host's town."}</p>
        <button id="coopLeaveBtn" class="bulldoze-btn">Leave Room</button>`;
      document.getElementById('coopLeaveBtn').addEventListener('click', coopLeaveRoom);
    }
  }

  initCategoryTabs();
  initTopBarControls(doSave, doLoad, doNewGame);
  // Co-op doesn't get its own params threaded through initTopBarControls
  // (ui.js) -- these are ADDITIONAL listeners layered on top of ui.js's
  // own tax-slider/speed-button wiring, purely for the broadcast
  // side-effect, so ui.js didn't need to grow co-op-specific parameters.
  document.getElementById('taxSlider').addEventListener('change', (e) => {
    coopBroadcastAction('tax', { taxRate: parseInt(e.target.value, 10) });
  });
  document.querySelectorAll('.speed-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      coopBroadcastAction('speed', { speed: parseInt(btn.dataset.speed, 10) });
    });
  });
  document.getElementById('btnCoop').addEventListener('click', () => {
    const panel = document.getElementById('coopModal');
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) renderCoopPanel();
  });
  document.getElementById('btnCloseCoop').addEventListener('click', () => {
    document.getElementById('coopModal').classList.add('hidden');
  });
  document.getElementById('coopModal').addEventListener('click', (e) => {
    if (e.target.id === 'coopModal') document.getElementById('coopModal').classList.add('hidden');
  });

  // ── Help modal ──
  const helpModal = document.getElementById('helpModal');
  document.getElementById('btnHelp').addEventListener('click', () => {
    helpModal.classList.remove('hidden');
  });
  document.getElementById('btnCloseHelp').addEventListener('click', () => {
    helpModal.classList.add('hidden');
  });
  helpModal.addEventListener('click', (e) => {
    if (e.target === helpModal) helpModal.classList.add('hidden');
  });

  document.querySelector(`.speed-btn[data-speed="${state.speed}"]`)?.classList.add('active');
  document.getElementById('taxSlider').value = state.taxRate;
  unlockAudioOnFirstGesture();

  // ── Stats modal ──
  const statsModal = document.getElementById('statsModal');
  const statsCanvas = document.getElementById('statsCanvas');
  const statsCtx = statsCanvas.getContext('2d');
  document.getElementById('btnStats').addEventListener('click', () => {
    statsModal.classList.remove('hidden');
    renderStatsChart(statsCtx, state.history);
  });
  document.getElementById('btnCloseStats').addEventListener('click', () => {
    statsModal.classList.add('hidden');
  });
  statsModal.addEventListener('click', (e) => {
    if (e.target === statsModal) statsModal.classList.add('hidden');
  });

  // ── Simulation loop (fixed real-time interval, scaled by speed) ──
  // Co-op: a guest (in a room, not the host) never runs simulateDay()
  // itself -- only the host's simulation is authoritative. A guest's
  // `state` only ever changes via an incoming state_sync (full replace)
  // or an incoming/local `action` (see coopSocket.on('action', ...) and
  // placeAt() above), so this loop just skips the tick entirely for them.
  let simAccumulator = 0;
  let lastSimTime = performance.now();
  function simTick(now) {
    const elapsed = now - lastSimTime;
    lastSimTime = now;
    const isGuest = coopRoomCode && !coopIsHost;
    if (state.speed > 0 && !isGuest) {
      simAccumulator += elapsed * state.speed;
      while (simAccumulator >= TICK_MS) {
        simAccumulator -= TICK_MS;
        simulateDay(state, log);
        renderAll(state);
        if (!statsModal.classList.contains('hidden')) {
          renderStatsChart(statsCtx, state.history);
        }
        // Co-op host: push the freshly-simulated day out to the room.
        // Full-state (not a diff) -- Meadowlark's grid is small enough
        // (60x45 tiles) that this is cheap, and a full snapshot can't
        // drift out of sync the way an accumulating diff stream could.
        if (coopRoomCode && coopIsHost) {
          coopEnsureSocket().emit('state_sync', { code: coopRoomCode, state_json: serializeState(state) });
        }
      }
    }
    requestAnimationFrame(simTick);
  }
  requestAnimationFrame(simTick);

  // ── Render/animation loop (NPCs + hover, independent of sim speed) ──
  let lastFrameTime = performance.now();
  function frameLoop(now) {
    const dt = Math.min(3, (now - lastFrameTime) / 16.67); // in ~frames
    lastFrameTime = now;
    const dayFraction = (simAccumulator / TICK_MS) % 1;
    updateNpcs(state, dt, dayFraction);
    renderFrame(ctx, state, dayFraction, camera, dt);
    renderMinimap(minimapCtx, state, camera, canvas.width, canvas.height);
    requestAnimationFrame(frameLoop);
  }
  requestAnimationFrame(frameLoop);

  // ── Minimap click-to-jump ──
  minimapCanvas.addEventListener('click', (e) => {
    const rect = minimapCanvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const worldPx = (mx / MINIMAP_SCALE) * TILE_SIZE;
    const worldPy = (my / MINIMAP_SCALE) * TILE_SIZE;
    centerCameraOn(worldPx, worldPy);
  });

  // ── Autosave every ~30s (to whichever slot is currently selected) ──
  // Skipped for a co-op GUEST: their `state` is wholesale-replaced by
  // whatever the host last broadcast (see coopSocket.on('state_sync', ...)
  // above), not their own town -- autosaving it would silently overwrite
  // the guest's own save slot with the host's town every 30 seconds just
  // for having joined a room, with no explicit save action on their part.
  // The host is unaffected (their `state` really is their own town, co-op
  // or not) and a guest can still explicitly click Save if they actually
  // want a personal snapshot of the shared town.
  setInterval(async () => {
    if (coopRoomCode && !coopIsHost) return;
    await saveGame(state, currentSlot);
    renderSlotPicker();
  }, 30000);

  // ── Initial paint ──
  renderSlotPicker();
  renderAll(state);
  renderFrame(ctx, state, 0.3, camera);
  if (state.day === 1 && state.population === 0) {
    log('Welcome to Meadowlark Valley! Build a road out from Town Hall, add a Power Plant and Water Tower, then houses will grow.', 'info');
  }
})();
