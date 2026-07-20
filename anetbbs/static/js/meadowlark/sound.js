// ─── Sound effects — synthesized via WebAudio, no audio files ─────────────
// Short procedural tones for build/bulldoze/harvest/milestone/warning
// events. No composed music — just SFX, kept honest about scope.

let audioCtx = null;
let soundMuted = localStorage.getItem('meadowlarkValley.muted') === 'true';
// Master volume multiplier (0-1), independent of the mute toggle -- muting
// and unmuting restores whatever volume was last set, rather than the two
// controls fighting over one piece of state. Every playTone() call already
// passes its own per-tone volume (build/harvest/warning/etc all sound
// different relative to each other); this just scales all of them
// together, same idea as an OS master volume slider sitting on top of
// each app's own per-sound mix.
let soundVolume = parseFloat(localStorage.getItem('meadowlarkValley.volume'));
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

// Plays a single tone with a quick attack/decay envelope so it doesn't
// click at the edges. type: 'sine' | 'square' | 'triangle' | 'sawtooth'.
function playTone(freq, startOffset, duration, type = 'sine', volume = 0.15) {
  if (soundMuted || soundVolume <= 0) return;
  const ctx = ensureAudioCtx();
  if (!ctx) return;
  const t0 = ctx.currentTime + startOffset;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);
  gain.gain.setValueAtTime(0, t0);
  gain.gain.linearRampToValueAtTime(volume * soundVolume, t0 + 0.015);
  gain.gain.exponentialRampToValueAtTime(0.001, t0 + duration);
  osc.connect(gain).connect(ctx.destination);
  osc.start(t0);
  osc.stop(t0 + duration + 0.02);
}

function sfxBuild()     { playTone(520, 0, 0.10, 'triangle', 0.12); playTone(720, 0.04, 0.10, 'triangle', 0.08); }
function sfxBulldoze()  { playTone(180, 0, 0.16, 'square', 0.10); }
function sfxHarvest()   { playTone(660, 0, 0.09, 'sine', 0.14); playTone(880, 0.07, 0.12, 'sine', 0.12); }
function sfxWarning()   { playTone(260, 0, 0.1, 'square', 0.09); playTone(200, 0.11, 0.14, 'square', 0.09); }
function sfxSeason()    { playTone(440, 0, 0.16, 'sine', 0.08); playTone(550, 0.12, 0.2, 'sine', 0.07); }
function sfxInfo()      { playTone(500, 0, 0.08, 'sine', 0.07); }
function sfxMilestone() {
  // Little ascending fanfare.
  [523, 659, 784, 1047].forEach((f, i) => playTone(f, i * 0.09, 0.18, 'triangle', 0.14));
}

function setMuted(muted) {
  soundMuted = muted;
  localStorage.setItem('meadowlarkValley.muted', muted ? 'true' : 'false');
  const btn = document.getElementById('btnMute');
  if (btn) {
    btn.textContent = muted ? '🔇' : '🔊';
    btn.classList.toggle('muted', muted);
  }
  if (muted) stopAmbientMusic(); else startAmbientMusic();
}

function setVolume(v) {
  soundVolume = Math.max(0, Math.min(1, v));
  localStorage.setItem('meadowlarkValley.volume', String(soundVolume));
}

function toggleMuted() { setMuted(!soundMuted); }

// ─── Ambient background loop — no composed music/audio files, just a slow
// procedural chord pad cycling through a 4-chord progression (a genuinely
// honest middle ground between "no music at all" and claiming composed
// music that doesn't exist). Reuses playTone()'s own oscillator/envelope
// machinery and its soundMuted/soundVolume checks, so it never needs its
// own separate mute/volume logic — starting it while muted (or at volume
// 0) is a harmless no-op every cycle.
const AMBIENT_CHORDS = [
  [261.63, 329.63, 392.00], // C4 E4 G4  — C major
  [220.00, 261.63, 329.63], // A3 C4 E4  — A minor
  [174.61, 220.00, 261.63], // F3 A3 C4  — F major
  [196.00, 246.94, 293.66], // G3 B3 D4  — G major
];
const AMBIENT_CHORD_SECONDS = 7.5;

let ambientInterval = null;
let ambientChordIndex = 0;

function playAmbientChord() {
  if (soundMuted) return;
  const chord = AMBIENT_CHORDS[ambientChordIndex % AMBIENT_CHORDS.length];
  ambientChordIndex++;
  for (const freq of chord) {
    playTone(freq, 0, AMBIENT_CHORD_SECONDS, 'sine', 0.035);
  }
  // A single soft higher-octave note on top, for a little shimmer/movement
  // rather than a completely static pad.
  const shimmer = chord[Math.floor(Math.random() * chord.length)] * 2;
  playTone(shimmer, 0.4, AMBIENT_CHORD_SECONDS - 0.8, 'triangle', 0.018);
}

function startAmbientMusic() {
  if (ambientInterval || soundMuted) return; // already running, or muted
  playAmbientChord();
  ambientInterval = setInterval(playAmbientChord, AMBIENT_CHORD_SECONDS * 1000);
}

function stopAmbientMusic() {
  if (ambientInterval) { clearInterval(ambientInterval); ambientInterval = null; }
}

// Browsers only actually let an AudioContext.resume() call take effect
// when it runs synchronously inside a real user-gesture event handler —
// calling it from a setInterval callback (which is how the ambient loop's
// own notes get scheduled) does NOT count, so the chords would otherwise
// stay silently suspended until the player happens to trigger a real SFX
// (a build click, etc). Unlock explicitly on the player's first pointer/
// key interaction instead of waiting on that. Safe to call any time —
// ensureAudioCtx() is a no-op once the context already exists and isn't
// suspended.
function unlockAudioOnFirstGesture() {
  const unlock = () => {
    ensureAudioCtx();
    window.removeEventListener('pointerdown', unlock);
    window.removeEventListener('keydown', unlock);
  };
  window.addEventListener('pointerdown', unlock);
  window.addEventListener('keydown', unlock);
}
