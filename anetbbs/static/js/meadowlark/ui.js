// ─── UI: build menu, top bar, info panel, toasts ───────────────────────────

let uiState = null; // reference to the shared game state, set by main.js
let selectedCategory = 'zone';
let selectedInfoTile = null; // {x,y} of the tile currently shown in the info panel
let selectedNpc = null; // an npc object (see npc.js), shown in the info panel instead of a tile

// Select a tile for the info panel — clears any NPC selection, since the
// panel can only show one or the other at a time.
function selectTile(state, x, y) {
  selectedInfoTile = { x, y };
  selectedNpc = null;
  renderInfoPanel(state, x, y);
}

// Select an NPC for the info panel (click-to-inspect on the canvas) —
// clears any tile selection the same way selectTile() clears NPC selection.
function selectNpc(npc) {
  selectedNpc = npc;
  selectedInfoTile = null;
  renderNpcInfoPanel(npc);
}

function renderNpcInfoPanel(npc) {
  const panel = document.getElementById('infoPanel');
  const moodLabel = npc.mood === 'happy' ? '🙂 Happy' : npc.mood === 'sad' ? '☹️ Unhappy' : '😐 Doing OK';
  const jobTile = npc.hasJob && uiState ? tileAt(uiState, npc.jobX, npc.jobY) : null;
  const employmentLabel = !npc.hasJob ? '🔍 Looking for work'
    : jobTile && jobTile.type === 'farm' ? '🌾 Farmer'
    : '💼 Employed';
  panel.innerHTML = `
    <h3>${escapeHtml(npc.name)}</h3>
    <p>${moodLabel}</p>
    <p>${employmentLabel}</p>
    <p>🏠 Lives at (${npc.homeX}, ${npc.homeY})</p>
  `;
}

function addLogEntry(state, msg, kind = 'info') {
  state.log.unshift({ msg, kind, day: state.day });
  if (state.log.length > 40) state.log.length = 40;
  showToast(msg, kind);
  renderLog(state);
}

function showToast(msg, kind) {
  const sfxByKind = {
    milestone: sfxMilestone, warning: sfxWarning,
    harvest: sfxHarvest, season: sfxSeason, info: sfxInfo
  };
  (sfxByKind[kind] || sfxInfo)();

  const el = document.getElementById('mlvToasts');
  const div = document.createElement('div');
  div.className = `mlv-toast mlv-toast-${kind}`;
  div.textContent = msg;
  el.appendChild(div);
  requestAnimationFrame(() => div.classList.add('show'));
  setTimeout(() => {
    div.classList.remove('show');
    setTimeout(() => div.remove(), 400);
  }, 4200);
}

function renderLog(state) {
  const el = document.getElementById('logList');
  el.innerHTML = state.log.slice(0, 12).map(e =>
    `<li class="log-${e.kind}"><span class="log-day">Day ${e.day}</span> ${escapeHtml(e.msg)}</li>`
  ).join('');
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function renderTopBar(state) {
  document.getElementById('statCash').textContent = `$${state.cash.toLocaleString()}`;
  document.getElementById('statPop').textContent = state.population.toLocaleString();
  document.getElementById('statDay').textContent = `Day ${state.day}`;
  document.getElementById('statSeason').textContent = SEASONS[state.season];
  const h = state.happiness;
  const face = h >= 75 ? '😄' : h >= 55 ? '🙂' : h >= 35 ? '😐' : h >= 15 ? '☹️' : '😠';
  document.getElementById('statHappiness').textContent = `${face} ${h}%`;
  document.getElementById('happinessBar').style.width = `${h}%`;
  document.getElementById('happinessBar').style.background =
    h >= 55 ? '#5fae6e' : h >= 35 ? '#e0b23d' : '#c0524f';
  document.getElementById('taxValue').textContent = `${state.taxRate}%`;
}

function buildMenuHtml(state) {
  const cats = { zone: 'Zones & Farms', infra: 'Roads', service: 'Services', deco: 'Decorations' };
  let html = '';
  for (const key in BUILDINGS) {
    const def = BUILDINGS[key];
    if (def.category !== selectedCategory || def.cost === 0) continue;
    const locked = state.population < def.unlock;
    html += `<button class="build-btn ${uiState.selectedTool === key ? 'selected' : ''} ${locked ? 'locked' : ''}"
                data-key="${key}" ${locked ? 'disabled' : ''} title="${escapeHtml(def.desc)}">
      <span class="swatch" style="background:${def.color}"></span>
      <span class="build-name">${def.name}</span>
      <span class="build-cost">${locked ? `Pop ${def.unlock}+` : '$' + def.cost}</span>
    </button>`;
  }
  return html || '<p class="empty-cat">Nothing here yet.</p>';
}

function renderBuildMenu(state) {
  document.getElementById('buildButtons').innerHTML = buildMenuHtml(state);
  document.querySelectorAll('.build-btn:not(.locked)').forEach(btn => {
    btn.addEventListener('click', () => {
      uiState.selectedTool = btn.dataset.key;
      renderBuildMenu(uiState);
    });
  });
  document.querySelectorAll('.cat-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.cat === selectedCategory);
  });
}

function renderInfoPanel(state, x, y) {
  const panel = document.getElementById('infoPanel');
  const t = tileAt(state, x, y);
  if (!t) { panel.innerHTML = ''; return; }
  const def = BUILDINGS[t.type];
  let html = `<h3>${def ? def.name : 'Grass'}</h3>`;
  if (t.type === 'house') {
    const cap = BUILDINGS.house.levelCap[t.level - 1];
    html += `<p>Level ${t.level} · ${t.population}/${cap} residents</p>
             <p>Growth: ${Math.floor(t.growth)}%</p>
             <p>Happiness here: ${Math.round(t.happinessLocal)}%</p>
             <p>Crime risk: ${Math.round(t.crimeScore ?? 0)}%</p>`;
  } else if (t.type === 'shop' || t.type === 'factory' || t.type === 'mall') {
    html += `<p>Level ${t.level} · ${t.jobs} jobs</p>`;
  } else if (t.type === 'farm') {
    const ripe = t.farmStage >= BUILDINGS.farm.growDays;
    const farmer = npcs.find(n => n.jobX === x && n.jobY === y);
    if (farmer) {
      html += `<p>👨‍🌾 Worked by ${escapeHtml(farmer.name)}</p>
               <p>${ripe ? 'Ripe — harvested automatically today.' : `Growing: ${t.farmStage}/${BUILDINGS.farm.growDays} days`}</p>`;
    } else {
      html += `<p>${ripe ? 'Ripe! Click to harvest (no farmer assigned yet).' : `Growing: ${t.farmStage}/${BUILDINGS.farm.growDays} days`}</p>`;
    }
  }
  if (def && def.category === 'zone') {
    html += `<p>${t.powered ? '⚡ Powered' : '⚡ No power'} · ${t.watered ? '💧 Watered' : '💧 No water'} · ${isRoadAdjacent(state, x, y) ? '🛣 Road access' : '🛣 No road'}</p>`;
  }
  const lv = t.landValue ?? 40;
  const lvLabel = lv >= 70 ? 'Excellent' : lv >= 50 ? 'Good' : lv >= 30 ? 'Fair' : 'Poor';
  html += `<p>💲 Land value: ${lvLabel} (${Math.round(lv)})</p>`;
  panel.innerHTML = html;
}

function initCategoryTabs() {
  document.querySelectorAll('.cat-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      selectedCategory = tab.dataset.cat;
      renderBuildMenu(uiState);
    });
  });
}

function initTopBarControls(onSave, onLoad, onNewGame) {
  document.getElementById('taxSlider').addEventListener('input', (e) => {
    uiState.taxRate = parseInt(e.target.value, 10);
    document.getElementById('taxValue').textContent = `${uiState.taxRate}%`;
  });
  document.querySelectorAll('.speed-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      uiState.speed = parseInt(btn.dataset.speed, 10);
      document.querySelectorAll('.speed-btn').forEach(b => b.classList.toggle('active', b === btn));
    });
  });
  document.getElementById('btnBulldoze').addEventListener('click', () => {
    uiState.selectedTool = 'bulldoze';
    document.querySelectorAll('.build-btn').forEach(b => b.classList.remove('selected'));
  });
  document.getElementById('btnSave').addEventListener('click', onSave);
  document.getElementById('btnLoad').addEventListener('click', onLoad);
  document.getElementById('btnNewGame').addEventListener('click', onNewGame);
  document.getElementById('btnMute').addEventListener('click', toggleMuted);
  setMuted(soundMuted); // sync icon with the persisted preference on load
  const volumeSlider = document.getElementById('volumeSlider');
  volumeSlider.value = Math.round(soundVolume * 100);
  volumeSlider.addEventListener('input', (e) => {
    setVolume(parseInt(e.target.value, 10) / 100);
  });

  document.querySelectorAll('.viewmode-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      uiState.viewMode = btn.dataset.mode;
      document.querySelectorAll('.viewmode-btn').forEach(b => b.classList.toggle('active', b === btn));
    });
  });
}

function renderAll(state) {
  renderTopBar(state);
  renderBuildMenu(state);
  renderLog(state);
  if (selectedNpc) renderNpcInfoPanel(selectedNpc);
  else if (selectedInfoTile) renderInfoPanel(state, selectedInfoTile.x, selectedInfoTile.y);
}
