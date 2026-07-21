// ─── ANetDarkForces: sound effects — synthesized via WebAudio ───────────
// Short procedural tones/noise bursts for combat/pickup/level events. No
// audio files, no composed music — same honest, zero-dependency approach
// Meadowlark Valley uses for its own sound.js.

let audioCtx = null;
let soundMuted = localStorage.getItem('anetDarkForces.muted') === 'true';
let soundVolume = parseFloat(localStorage.getItem('anetDarkForces.volume'));
if (isNaN(soundVolume)) soundVolume = 0.7;
soundVolume = Math.max(0, Math.min(1, soundVolume));

function ensureAudioCtx() {
  if (!audioCtx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    audioCtx = new AC();
  }
  if (audioCtx.state === 'suspended') audioCtx.resume();
  return audioCtx;
}

function playTone(freq, startOffset, duration, type = 'sine', volume = 0.15, freqEnd = null) {
  if (soundMuted || soundVolume <= 0) return;
  const ctx = ensureAudioCtx();
  if (!ctx) return;
  const t0 = ctx.currentTime + startOffset;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);
  if (freqEnd !== null) osc.frequency.exponentialRampToValueAtTime(Math.max(1, freqEnd), t0 + duration);
  gain.gain.setValueAtTime(0, t0);
  gain.gain.linearRampToValueAtTime(volume * soundVolume, t0 + 0.008);
  gain.gain.exponentialRampToValueAtTime(0.001, t0 + duration);
  osc.connect(gain).connect(ctx.destination);
  osc.start(t0);
  osc.stop(t0 + duration + 0.02);
}

// White-noise burst (for gunfire/impact texture) via a short buffer source.
function playNoise(startOffset, duration, volume = 0.15, filterFreq = 2000) {
  if (soundMuted || soundVolume <= 0) return;
  const ctx = ensureAudioCtx();
  if (!ctx) return;
  const t0 = ctx.currentTime + startOffset;
  const bufferSize = Math.floor(ctx.sampleRate * duration);
  const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < bufferSize; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / bufferSize);
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  const filter = ctx.createBiquadFilter();
  filter.type = 'lowpass';
  filter.frequency.value = filterFreq;
  const gain = ctx.createGain();
  gain.gain.setValueAtTime(volume * soundVolume, t0);
  gain.gain.exponentialRampToValueAtTime(0.001, t0 + duration);
  src.connect(filter).connect(gain).connect(ctx.destination);
  src.start(t0);
}

function sfxForWeapon(kind) {
  if (kind === 'shootLight') { playNoise(0, 0.06, 0.12, 3000); playTone(180, 0, 0.05, 'square', 0.05); }
  else if (kind === 'shootHeavy') { playNoise(0, 0.14, 0.22, 1400); playTone(90, 0, 0.1, 'square', 0.1); }
  else if (kind === 'shootEmp') { playTone(220, 0, 0.22, 'sawtooth', 0.12, 60); playNoise(0.02, 0.08, 0.08, 800); }
  else if (kind === 'shootUltimate') { playTone(140, 0, 0.35, 'sawtooth', 0.16, 40); playNoise(0, 0.2, 0.18, 1000); }
  else if (kind === 'shootSmg') { playNoise(0, 0.04, 0.1, 3400); playTone(240, 0, 0.03, 'square', 0.04); }
  else if (kind === 'shootSniper') { playNoise(0, 0.09, 0.22, 1100); playTone(55, 0, 0.16, 'sawtooth', 0.14, 25); }
  else if (kind === 'meleeSwing') { playTone(320, 0, 0.05, 'square', 0.05, 180); }
}
function sfxDryFire() { playTone(150, 0, 0.06, 'square', 0.05); }
function sfxHit() { playTone(300, 0, 0.06, 'square', 0.08); }
function sfxEnemyDeath() { playTone(200, 0, 0.14, 'sawtooth', 0.1, 50); playNoise(0.02, 0.1, 0.08, 900); }
function sfxPickup() { playTone(660, 0, 0.08, 'sine', 0.12); playTone(880, 0.06, 0.1, 'sine', 0.1); }
function sfxLevelUp() { [523, 659, 784, 1047].forEach((f, i) => playTone(f, i * 0.09, 0.18, 'triangle', 0.14)); }
function sfxHurt() { playTone(140, 0, 0.12, 'square', 0.12); }
function sfxDeath() { playTone(220, 0, 0.5, 'sawtooth', 0.16, 40); }
function sfxLevelComplete() { [392, 523, 659, 784, 1047].forEach((f, i) => playTone(f, i * 0.1, 0.22, 'triangle', 0.15)); }
function sfxExplosion() { playNoise(0, 0.3, 0.22, 600); playTone(80, 0, 0.3, 'sawtooth', 0.14, 30); }
function sfxDoorOpen() { playTone(180, 0, 0.16, 'square', 0.09, 320); playNoise(0.04, 0.1, 0.06, 2200); }
function sfxSecretFound() { [660, 880, 1108].forEach((f, i) => playTone(f, i * 0.07, 0.16, 'triangle', 0.13)); }
function sfxAlert() { playTone(260, 0, 0.09, 'square', 0.07, 340); }
function sfxKamikazeArm() { playTone(700, 0, 0.05, 'square', 0.09); playTone(700, 0.12, 0.05, 'square', 0.09); }

// Alternates a slightly higher/lower thud per step for a left/right feel;
// `stepIndex` is owned by the caller (main.js tracks it alongside the
// walk cycle) so this stays a pure "make one footstep sound" function.
function sfxFootstep(stepIndex, sprinting) {
  const base = stepIndex % 2 === 0 ? 90 : 78;
  playNoise(0, 0.05, sprinting ? 0.07 : 0.05, 500);
  playTone(base, 0, 0.06, 'sine', 0.03);
}

function setMuted(muted) {
  soundMuted = muted;
  localStorage.setItem('anetDarkForces.muted', muted ? 'true' : 'false');
  const btn = document.getElementById('btnMute');
  if (btn) { btn.textContent = muted ? '🔇' : '🔊'; btn.classList.toggle('muted', muted); }
}
function toggleMuted() { setMuted(!soundMuted); }
function setVolume(v) {
  soundVolume = Math.max(0, Math.min(1, v));
  localStorage.setItem('anetDarkForces.volume', String(soundVolume));
}
function unlockAudioOnFirstGesture() {
  const unlock = () => {
    ensureAudioCtx();
    window.removeEventListener('pointerdown', unlock);
    window.removeEventListener('keydown', unlock);
  };
  window.addEventListener('pointerdown', unlock);
  window.addEventListener('keydown', unlock);
}
