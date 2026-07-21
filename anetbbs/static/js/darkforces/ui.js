// ─── ANetDarkForces: HUD, menus, screens ─────────────────────────────────

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function renderHud(runtime) {
  const p = runtime.state.player;
  document.getElementById('hudHp').textContent = Math.max(0, Math.round(p.hp));
  document.getElementById('hudArmor').textContent = Math.round(p.armor);
  document.getElementById('hudHpBar').style.width = `${Math.max(0, p.hp / p.maxHp) * 100}%`;
  document.getElementById('hudArmorBar').style.width = `${Math.max(0, p.armor / p.maxArmor) * 100}%`;

  const w = WEAPONS[p.currentWeapon];
  document.getElementById('hudWeaponName').textContent = w.name;
  document.getElementById('hudAmmo').textContent = w.ammoType ? (p.ammo[w.ammoType] || 0) : '∞';

  document.getElementById('hudLevel').textContent = p.level;
  const nextXp = XP_PER_LEVEL[p.level] ?? null;
  const prevXp = XP_PER_LEVEL[p.level - 1] ?? 0;
  const xpPct = nextXp ? Math.min(100, ((p.xp - prevXp) / (nextXp - prevXp)) * 100) : 100;
  document.getElementById('hudXpBar').style.width = `${xpPct}%`;
  document.getElementById('hudParts').textContent = p.parts;
  document.getElementById('hudLevelName').textContent = runtime.level.name;
}

const WEAPON_ABBR = { solder: 'SLD', multitool: 'MTL', packetspray: 'PKT', static: 'SHG', debugger: 'DBG', emp: 'EMP', overclock: 'OVC' };

// Quick-select bar -- one slot per WEAPON_ORDER entry, greyed out until
// found, highlighted on whichever is currently equipped. Rebuilt from
// scratch each call (7 small elements, cheap enough to not bother
// diffing) so scroll-wheel/number-key/Q-E switching all stay in sync
// without three separate update paths.
function renderWeaponBar(runtime) {
  const el = document.getElementById('weaponBar');
  if (!el) return;
  const p = runtime.state.player;
  el.innerHTML = WEAPON_ORDER.map((key, i) => {
    const owned = p.weapons.includes(key);
    const active = key === p.currentWeapon;
    const cls = ['weapon-slot'];
    if (!owned) cls.push('locked');
    if (active) cls.push('active');
    const abbr = WEAPON_ABBR[key] || key.slice(0, 3).toUpperCase();
    return `<div class="${cls.join(' ')}"><span class="weapon-slot-num">${i + 1}</span><span class="weapon-slot-abbr">${abbr}</span></div>`;
  }).join('');
}

function renderDamageFlash(runtime) {
  const el = document.getElementById('damageFlash');
  if (runtime.hurtFlashTimer > 0) {
    el.style.opacity = Math.min(0.55, runtime.hurtFlashTimer * 3);
  } else {
    el.style.opacity = 0;
  }
}

function addLogEntry(runtime, msg, kind = 'info') {
  runtime.log.unshift({ msg, kind });
  if (runtime.log.length > 20) runtime.log.length = 20;
  showToast(msg, kind);
  renderKillFeed(runtime);
}

function renderKillFeed(runtime) {
  const el = document.getElementById('killFeed');
  el.innerHTML = runtime.log.slice(0, 5).map(e =>
    `<div class="feed-${e.kind}">${escapeHtml(e.msg)}</div>`
  ).join('');
}

function showToast(msg, kind) {
  const sfxByKind = { milestone: sfxLevelUp, warning: sfxHurt, harvest: sfxPickup, info: null };
  const fn = sfxByKind[kind];
  if (fn) fn();
  const el = document.getElementById('toasts');
  if (!el) return;
  const div = document.createElement('div');
  div.className = `adf-toast adf-toast-${kind}`;
  div.textContent = msg;
  el.appendChild(div);
  requestAnimationFrame(() => div.classList.add('show'));
  setTimeout(() => { div.classList.remove('show'); setTimeout(() => div.remove(), 400); }, 3200);
}

// ── Screens ──
function showScreen(id) {
  document.querySelectorAll('.adf-screen').forEach(s => s.classList.add('hidden'));
  const el = document.getElementById(id);
  if (el) el.classList.remove('hidden');
}
function hideScreens() {
  document.querySelectorAll('.adf-screen').forEach(s => s.classList.add('hidden'));
}

function renderLevelIntro(level, levelNum, totalLevels) {
  document.getElementById('introTitle').textContent = `Sector ${levelNum}/${totalLevels}: ${level.name}`;
  document.getElementById('introText').textContent = level.intro;
  showScreen('screenIntro');
}

function renderLevelComplete(runtime, levelNum, totalLevels) {
  const p = runtime.state.player;
  const secrets = getSecretsProgress(runtime.level);
  document.getElementById('completeTitle').textContent = `${runtime.level.name} — Cleared`;
  document.getElementById('completeStats').innerHTML = `
    <p>Parts recovered this sector: <strong>${runtime.partsThisLevel}</strong></p>
    <p>Sysop level: <strong>${p.level}</strong> · Total BBS parts: <strong>${p.parts}</strong></p>
    <p>Secrets found: <strong>${secrets.found}/${secrets.total}</strong></p>
  `;
  document.getElementById('completeNextLabel').textContent = levelNum >= totalLevels ? 'Finish' : 'Next Sector →';
  showScreen('screenComplete');
}

function renderGameComplete(runtime) {
  const p = runtime.state.player;
  document.getElementById('victoryStats').innerHTML = `
    <p>The Middleman is out of business. Every store on his list is back in honest hands — and your BBS has never had a better parts supply.</p>
    <p>Final sysop level: <strong>${p.level}</strong></p>
    <p>Total BBS parts recovered: <strong>${p.parts}</strong></p>
    <p>Total kills: <strong>${runtime.state.kills}</strong></p>
  `;
  showScreen('screenVictory');
}

function renderDeathScreen(runtime) {
  document.getElementById('deathStats').innerHTML = `
    <p>You're not out of the game — respawning at the start of <strong>${runtime.level.name}</strong> with your sysop level and gear intact.</p>
  `;
  showScreen('screenDeath');
}

// Shared by the pause menu (pick-a-slot-for-save/load) and the title
// screen (pick-a-slot-to-start/continue) -- same 3-slot layout, different
// click behavior supplied by the caller.
function renderSlotPicker(containerId, currentSlot, onSelect) {
  const el = document.getElementById(containerId);
  if (!el) return;
  let html = '';
  for (let slot = 1; slot <= SAVE_SLOTS; slot++) {
    const summary = getSaveSummary(slot);
    const sub = summary ? `${summary.levelName} · Lvl ${summary.playerLevel}` : 'Empty';
    html += `<button class="adf-slot-btn ${slot === currentSlot ? 'active' : ''}" data-slot="${slot}">
               <span>Slot ${slot}</span><span class="adf-slot-sub">${escapeHtml(sub)}</span>
             </button>`;
  }
  el.innerHTML = html;
  el.querySelectorAll('.adf-slot-btn').forEach(btn => {
    btn.addEventListener('click', () => onSelect(parseInt(btn.dataset.slot, 10)));
  });
}

// Backpack laptop (Tab) -- a plain-text field-terminal report rather than
// styled HTML rows, both because it's cheap/robust and because a raw
// monospace readout is exactly the right "Hackers"-movie/BBS-terminal
// register for this project.
function renderLaptopScreen(runtime) {
  const p = runtime.state.player;
  const nextXp = XP_PER_LEVEL[p.level] ?? null;
  const el = document.getElementById('laptopBody');
  if (!el) return;
  const weaponRows = WEAPON_ORDER.filter(k => p.weapons.includes(k)).map(k => {
    const w = WEAPONS[k];
    const ammo = w.ammoType ? String(p.ammo[w.ammoType] || 0) : 'INF';
    const marker = k === w.key && k === p.currentWeapon ? '>' : ' ';
    return `${marker} ${w.name.padEnd(24, '.')} ${ammo}`;
  }).join('\n');
  const secrets = getSecretsProgress(runtime.level);
  const keysRow = p.keys.length ? p.keys.map(k => k.toUpperCase()).join(', ') : 'none';
  el.textContent =
`ANetDarkForces Field Terminal v1.0
------------------------------------------
SECTOR:    ${runtime.level.name}
SYSOP LVL: ${p.level}   XP: ${p.xp}${nextXp !== null ? '/' + nextXp : ' (MAX)'}
HP:        ${Math.max(0, Math.round(p.hp))}/${p.maxHp}
ARMOR:     ${Math.round(p.armor)}/${p.maxArmor}

BBS PARTS RECOVERED: ${p.parts}  (run total: ${runtime.state.partsTotal})
KILLS: ${runtime.state.kills}   DEATHS: ${runtime.state.deaths}
SECRETS THIS SECTOR: ${secrets.found}/${secrets.total}
ACCESS CARDS: ${keysRow}

LOADOUT:
${weaponRows}

[connection secure -- TAB or ESC to stow]`;
}

function renderPauseMenu(runtime) {
  renderSlotPicker('pauseSlotPicker', runtime.currentSlot, slot => {
    runtime.currentSlot = slot;
    renderPauseMenu(runtime);
  });
}

function renderTitleScreen(activeSlot, onPickSlot) {
  renderSlotPicker('titleSlotPicker', activeSlot, onPickSlot);
  showScreen('screenTitle');
}
