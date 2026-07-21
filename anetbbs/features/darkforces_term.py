"""
ANetDarkForces - Terminal Edition: a first-person raycasting shooter for the
ANetBBS terminal (telnet/SSH/rlogin/web terminal), ported from the standalone
browser game (canvas + WebGL-free raycaster) at anetdarkforces/.

Rendering strategy (the reason this file exists rather than just running the
JS game headless): walls are rendered as a real-time truecolor-ANSI cell grid
(one raycast per terminal COLUMN, same DDA math as the browser version, just
mapped to a colored block character instead of a textured pixel column) --
this is the same technique ANetCRAFT already proved works over a raw
telnet/SSH connection at ~12fps (see features/anetcraft.py's Renderer/TICK
loop, which this file's game loop and Renderer class are directly modeled
on). Enemies, pickups, and barrels are then composited on top as small
sixel-encoded raster sprites IF the session's terminal reports sixel support
(SyncTerm, etc.) -- sessions without sixel just see them as colored ANSI
glyphs instead, so the game is fully playable either way.

No img2sixel subprocess is used anywhere in the per-frame path (that's the
existing RSS-image pipeline's approach, and it's fine for one static image
but far too slow -- up to a 10s subprocess timeout -- for a live game loop).
Sixel encoding here is a small from-scratch encoder (see SixelEncoder below)
run entirely in-process against tiny (16-24px) Pillow-drawn sprite bitmaps,
cached once per enemy/pickup type rather than re-encoded every frame.

Screen layout (80x24, the same baseline ANetCRAFT targets):
  Row 1      : header bar (sector name, HP/armor/weapon/ammo)
  Rows 2-19  : raycast viewport (18 rows x 78 cols)
  Row 20     : minimap (ASCII top-down, small, corner-anchored) overlaps
               the right edge of the viewport rather than taking its own row
  Row 21     : divider + status/log message
  Row 22-24  : reserved for overlay screens (intro/pause/death/victory)

Controls
--------
W/A/S/D      : move forward/back/strafe        1-7      : switch weapon
Arrow keys   : turn left/right, move fwd/back  SPACE    : fire / open door
Q            : cycle weapon                    L        : laptop (stats)
P            : pause / save                    ESC      : quit to menu

Design note on input: a real terminal has no key-up event (unlike the
browser version's held-WASD model), so movement here is discrete-per-tick --
holding a key on a real terminal auto-repeats at the OS/terminal level,
which reads close enough to continuous movement at TICK=0.08s (~12fps),
matching ANetCRAFT's own movement feel exactly.
"""
import asyncio
import copy
import io
import json
import logging
import math
import os
import random
import textwrap
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── ANSI helpers (identical convention to anetcraft.py) ────────────────────

_E = '\x1b'
RST = f'{_E}[0m'
HIDE = f'{_E}[?25l'
SHOW = f'{_E}[?25h'
CLS = f'{_E}[2J{_E}[H'
BOLD = f'{_E}[1m'


def _fg(r, g, b): return f'{_E}[38;2;{r};{g};{b}m'
def _bg(r, g, b): return f'{_E}[48;2;{r};{g};{b}m'
def _at(row, col): return f'{_E}[{row};{col}H'


# ─── World/physics constants (ported 1:1 from the browser game's data.js) ──
# TILE is a purely internal unit here (no pixel meaning) -- world positions
# and the DDA math are identical to the browser version, just never
# rasterized to real pixels for the wall layer.
TILE = 64
FOV = math.pi / 3
MAX_DEPTH = TILE * 20
HALF_FOV = FOV / 2

MOVE_SPEED = 3.2       # tiles/sec, same convention as the browser version
STRAFE_SPEED = 2.8
TURN_SPEED = 2.4        # radians/sec
PLAYER_RADIUS = 18
SPRINT_MULT = 1.6

TICK = 0.08  # seconds/tick, ~12fps -- matches ANetCRAFT exactly

ENEMY_MEMORY_DURATION = 5
KAMIKAZE_ARM_DURATION = 0.5
BARREL_EXPLOSION_DAMAGE = 45
BARREL_EXPLOSION_RADIUS_MULT = 2

AMMO_REFILL_THRESHOLDS = {'shells': 12, 'cells': 6, 'cores': 3, 'rounds': 40, 'scopes': 6}
AMMO_STATION_COOLDOWN = 25
AMMO_MAX = {'shells': 40, 'cells': 20, 'cores': 10, 'rounds': 120, 'scopes': 15}

XP_PER_LEVEL = [0, 40, 100, 180, 280, 400, 540, 700, 880, 1080]
LEVEL_UP_HEALTH_BONUS = 15
LEVEL_UP_DAMAGE_MULT = 0.08

# ── Wall types: id -> (name, base_rgb, mortar_rgb, block_char) ──
# block_char is the glyph drawn for this wall type's columns when NOT using
# a shaded ramp (doors/dispensers/vaults get a distinct fixed glyph so
# they're readable at a glance in the ANSI layer, same as their distinct
# hazard-stripe/vertical-bar textures in the browser version).
WALL_TYPES = {
    1: ('Cinderblock', (122, 114, 102), (90, 84, 72), '#'),
    2: ('Steel Shutter', (95, 104, 112), (58, 65, 72), '='),
    3: ('Shelving Unit', (138, 90, 58), (90, 58, 32), 'H'),
    4: ('Server Rack', (42, 58, 74), (26, 36, 48), '$'),
    5: ('Neon Sign Wall', (58, 32, 80), (224, 95, 208), '%'),
    6: ('Loading Dock', (106, 96, 80), (58, 52, 40), '~'),
    7: ('Security Door', (74, 58, 26), (224, 178, 61), 'D'),
    8: ('Reinforced Panel', (122, 114, 102), (90, 84, 72), '#'),  # secret -- same glyph as Cinderblock, no visual tell
    9: ('Ammo Dispenser', (28, 58, 74), (95, 214, 255), 'A'),
    10: ('Vault Door', (58, 26, 26), (255, 79, 79), 'V'),
}
DOOR_TYPE = 7
SECRET_TYPE = 8
AMMO_STATION_TYPE = 9
LOCKED_DOOR_TYPE = 10

# Shaded ANSI ramp for distance -- far walls step down through these glyphs
# rather than a smooth per-pixel gradient (which the sixel layer already
# provides plenty of contrast against); index 0 = closest/brightest.
SHADE_RAMP = ['█', '▓', '▒', '░']


def _shade_glyph(dist_tiles):
    if dist_tiles < 2.5:
        return SHADE_RAMP[0]
    elif dist_tiles < 5:
        return SHADE_RAMP[1]
    elif dist_tiles < 9:
        return SHADE_RAMP[2]
    else:
        return SHADE_RAMP[3]


def wall_glyph(wall_type, dist_tiles):
    """Pick a block character for a wall hit based on distance in tiles --
    walls step through SHADE_RAMP so far walls read as visibly dimmer/
    flatter. See wall_glyph_for_cell for door/vault/dispenser cells, which
    need a per-row/col decision instead of one glyph for the whole column."""
    return _shade_glyph(dist_tiles)


def wall_glyph_for_cell(wall_type, dist_tiles, row, col):
    """Per-cell glyph for one column's vertical span. Door/vault/ammo-
    dispenser walls used to print their identifying letter (D/V/A) solid
    across every row -- fine at a distance, but that surface can span
    almost the whole viewport up close, turning into a solid grid of the
    same letter (reported live: looked like a rendering glitch, not a
    door). Now the letter is stenciled on a sparse diagonal over an
    otherwise normally-shaded fill, so it still reads as identifiable at
    a glance without spamming the screen with text."""
    fill = _shade_glyph(dist_tiles)
    if wall_type in (DOOR_TYPE, AMMO_STATION_TYPE, LOCKED_DOOR_TYPE):
        if (row + col) % 4 == 0:
            _, _, _, fixed_glyph = WALL_TYPES[wall_type]
            return fixed_glyph
        return fill
    return fill


# ── Weapons (identical stats to the browser version's data.js) ──
WEAPONS = {
    'solder': {'key': 'solder', 'name': 'Solder Gun', 'kind': 'hitscan',
               'damage': 8, 'fire_rate': 0.14, 'spread': 0.02, 'ammo_type': None,
               'range': TILE * 10, 'sfx': 'shoot_light'},
    'static': {'key': 'static', 'name': 'Static Shotgun', 'kind': 'hitscan-spread',
               'damage': 7, 'pellets': 6, 'fire_rate': 0.7, 'spread': 0.14,
               'ammo_type': 'shells', 'range': TILE * 4.5, 'sfx': 'shoot_heavy'},
    'emp': {'key': 'emp', 'name': 'EMP Launcher', 'kind': 'projectile',
            'damage': 26, 'splash_radius': TILE * 1.6, 'speed': 9, 'fire_rate': 0.9,
            'ammo_type': 'cells', 'range': TILE * 12, 'sfx': 'shoot_emp'},
    'overclock': {'key': 'overclock', 'name': 'Overclock Cannon', 'kind': 'projectile',
                  'damage': 55, 'splash_radius': TILE * 2.2, 'speed': 7, 'fire_rate': 1.3,
                  'ammo_type': 'cores', 'range': TILE * 14, 'sfx': 'shoot_ultimate'},
    'multitool': {'key': 'multitool', 'name': 'Multitool', 'kind': 'melee',
                  'damage': 16, 'fire_rate': 0.45, 'range': TILE * 1.15, 'ammo_type': None,
                  'sfx': 'melee_swing'},
    'packetspray': {'key': 'packetspray', 'name': 'Packet Spray', 'kind': 'hitscan',
                     'damage': 5, 'fire_rate': 0.08, 'spread': 0.05, 'ammo_type': 'rounds',
                     'range': TILE * 7, 'sfx': 'shoot_smg'},
    'debugger': {'key': 'debugger', 'name': 'Long-Range Debugger', 'kind': 'hitscan',
                 'damage': 42, 'fire_rate': 1.1, 'spread': 0.006, 'ammo_type': 'scopes',
                 'range': TILE * 18, 'sfx': 'shoot_sniper'},
}
WEAPON_ORDER = ['solder', 'multitool', 'packetspray', 'static', 'debugger', 'emp', 'overclock']
WEAPON_ABBR = {'solder': 'SLD', 'multitool': 'MTL', 'packetspray': 'PKT',
               'static': 'SHG', 'debugger': 'DBG', 'emp': 'EMP', 'overclock': 'OVC'}

# ── Enemies (identical stats to the browser version) ──
ENEMY_TYPES = {
    'scalper': {'key': 'scalper', 'name': 'Scalper', 'hp': 18, 'speed': 1.6, 'xp': 8,
                'kind': 'melee', 'damage': 6, 'attack_range': TILE * 0.7, 'attack_rate': 0.9,
                'color': (201, 160, 79), 'sight_range': TILE * 8},
    'goon': {'key': 'goon', 'name': 'Store Goon', 'hp': 34, 'speed': 1.1, 'xp': 14,
             'kind': 'ranged', 'damage': 9, 'attack_range': TILE * 7, 'attack_rate': 1.4,
             'projectile_speed': 7, 'color': (138, 90, 90), 'sight_range': TILE * 9},
    'guard': {'key': 'guard', 'name': 'Security Guard', 'hp': 60, 'speed': 0.85, 'xp': 22,
              'kind': 'melee', 'damage': 14, 'attack_range': TILE * 0.8, 'attack_rate': 1.1,
              'color': (79, 111, 160), 'sight_range': TILE * 7},
    'tech': {'key': 'tech', 'name': 'Rogue Tech', 'hp': 40, 'speed': 1.0, 'xp': 18,
             'kind': 'ranged', 'damage': 12, 'attack_range': TILE * 8, 'attack_rate': 1.7,
             'projectile_speed': 8, 'color': (90, 160, 106), 'sight_range': TILE * 10},
    'drone': {'key': 'drone', 'name': 'Overclocked Drone', 'hp': 12, 'speed': 2.6, 'xp': 10,
              'kind': 'kamikaze', 'damage': 22, 'attack_range': TILE * 0.7, 'attack_rate': 999,
              'splash_radius': TILE * 1.3, 'color': (224, 96, 61), 'sight_range': TILE * 9},
    'turret': {'key': 'turret', 'name': 'Security Camera Turret', 'hp': 30, 'speed': 0, 'xp': 16,
               'kind': 'turret', 'damage': 10, 'attack_range': TILE * 10, 'attack_rate': 1.2,
               'projectile_speed': 9, 'color': (201, 192, 176), 'sight_range': TILE * 11},
    'shieldtech': {'key': 'shieldtech', 'name': 'Riot Tech', 'hp': 90, 'speed': 0.6, 'xp': 30,
                   'kind': 'melee', 'damage': 16, 'attack_range': TILE * 0.85, 'attack_rate': 1.3,
                   'color': (90, 106, 122), 'sight_range': TILE * 7, 'frontal_damage_reduction': 0.7},
    'boss_middleman': {'key': 'boss_middleman', 'name': 'The Middleman', 'hp': 420, 'speed': 0.7, 'xp': 300,
                       'kind': 'boss', 'damage': 20, 'attack_range': TILE * 6, 'attack_rate': 1.0,
                       'projectile_speed': 8, 'color': (192, 79, 79), 'sight_range': TILE * 20,
                       'is_boss': True},
}

# ── Pickups ──
PICKUP_TYPES = {
    'health_small': {'name': 'Spare Battery', 'kind': 'health', 'amount': 15, 'color': (95, 174, 110)},
    'health_large': {'name': 'UPS Battery Pack', 'kind': 'health', 'amount': 35, 'color': (95, 174, 110)},
    'armor': {'name': 'Static Vest', 'kind': 'armor', 'amount': 25, 'color': (123, 154, 201)},
    'ammo_shells': {'name': 'Discharge Cells', 'kind': 'ammo', 'ammo_type': 'shells', 'amount': 8, 'color': (224, 178, 61)},
    'ammo_cells': {'name': 'EMP Cells', 'kind': 'ammo', 'ammo_type': 'cells', 'amount': 4, 'color': (224, 178, 61)},
    'ammo_cores': {'name': 'Overclock Cores', 'kind': 'ammo', 'ammo_type': 'cores', 'amount': 2, 'color': (224, 178, 61)},
    'ammo_rounds': {'name': 'Data Rounds', 'kind': 'ammo', 'ammo_type': 'rounds', 'amount': 30, 'color': (224, 178, 61)},
    'ammo_scopes': {'name': 'Scope Charges', 'kind': 'ammo', 'ammo_type': 'scopes', 'amount': 5, 'color': (224, 178, 61)},
    'part': {'name': 'BBS Part', 'kind': 'part', 'color': (224, 95, 208)},
    'weapon_static': {'name': 'Static Shotgun', 'kind': 'weapon', 'weapon': 'static', 'color': (201, 192, 176)},
    'weapon_emp': {'name': 'EMP Launcher', 'kind': 'weapon', 'weapon': 'emp', 'color': (201, 192, 176)},
    'weapon_overclock': {'name': 'Overclock Cannon', 'kind': 'weapon', 'weapon': 'overclock', 'color': (201, 192, 176)},
    'weapon_packetspray': {'name': 'Packet Spray', 'kind': 'weapon', 'weapon': 'packetspray', 'color': (201, 192, 176)},
    'weapon_debugger': {'name': 'Long-Range Debugger', 'kind': 'weapon', 'weapon': 'debugger', 'color': (201, 192, 176)},
    'key_red': {'name': 'Red Access Card', 'kind': 'key', 'key_id': 'red', 'color': (255, 79, 79)},
    'key_blue': {'name': 'Blue Access Card', 'kind': 'key', 'key_id': 'blue', 'color': (95, 214, 255)},
    'key_gold': {'name': 'Gold Access Card', 'kind': 'key', 'key_id': 'gold', 'color': (224, 178, 61)},
}

PLAYER_START_STATE = {
    'hp': 100, 'max_hp': 100, 'armor': 0, 'max_armor': 100,
    'level': 1, 'xp': 0,
    'weapons': ['solder', 'multitool'],
    'current_weapon': 'solder',
    'ammo': {'shells': 0, 'cells': 0, 'cores': 0, 'rounds': 0, 'scopes': 0},
    'parts': 0,
    'keys': [],
}


# ═══════════════════════════════════════════════════════════════════════════
# Level grid builder + all 10 levels -- direct, faithful port of the browser
# game's levels.js. Grids are built programmatically (make_grid/wall_rect/
# wall_ring/opening helpers) for the exact same reason the JS version does:
# a real 2D array of fixed width from the start can't drift out of sync
# row-to-row the way hand-typed ASCII-art maps did during that game's early
# development. Every coordinate, enemy, pickup, and wall placement below is
# copied 1:1 from the validated, Playwright-tested browser layouts.
# ═══════════════════════════════════════════════════════════════════════════

def make_grid(w, h, border_type=1):
    grid = [[0] * w for _ in range(h)]
    for x in range(w):
        grid[0][x] = border_type
        grid[h - 1][x] = border_type
    for y in range(h):
        grid[y][0] = border_type
        grid[y][w - 1] = border_type
    return grid


def wall_rect(level, x1, y1, x2, y2, wtype):
    for y in range(y1, y2 + 1):
        for x in range(x1, x2 + 1):
            level['grid'][y][x] = wtype


def wall_ring(level, x1, y1, x2, y2, wtype):
    for x in range(x1, x2 + 1):
        level['grid'][y1][x] = wtype
        level['grid'][y2][x] = wtype
    for y in range(y1, y2 + 1):
        level['grid'][y][x1] = wtype
        level['grid'][y][x2] = wtype


def opening(level, x1, y1, x2, y2):
    for y in range(y1, y2 + 1):
        for x in range(x1, x2 + 1):
            level['grid'][y][x] = 0


def count_cells_of_type(grid, wtype):
    return sum(1 for row in grid for cell in row if cell == wtype)


def get_secrets_progress(level):
    remaining = count_cells_of_type(level['grid'], SECRET_TYPE)
    return {'found': level['_secrets_total'] - remaining, 'total': level['_secrets_total']}


class LevelValidationError(Exception):
    pass


def validate_level(level):
    """Mirrors levels.js's validateLevel() exactly: row-length/border
    integrity, clearance-checked spawns for anything with a collision
    radius (player/enemies), proximity-only checks for pickups/barrels/
    exit. Raises LevelValidationError with the same diagnostic detail the
    JS version's thrown Errors carried, since this is what the automated
    level tests (see tests/test_darkforces_term_levels.py) assert against."""
    grid, w, h, name = level['grid'], level['w'], level['h'], level['name']
    for row in grid:
        if len(row) != w:
            raise LevelValidationError(f'Level "{name}": row length {len(row)} != declared width {w}')
    for x in range(w):
        if grid[0][x] == 0 or grid[h - 1][x] == 0:
            raise LevelValidationError(f'Level "{name}": gap in top/bottom border at column {x}')
    for y in range(h):
        if grid[y][0] == 0 or grid[y][w - 1] == 0:
            raise LevelValidationError(f'Level "{name}": gap in left/right border at row {y}')

    def check_pt(label, gx, gy):
        ix, iy = math.floor(gx), math.floor(gy)
        if iy < 0 or iy >= h or ix < 0 or ix >= w:
            raise LevelValidationError(f'Level "{name}": {label} out of bounds ({gx},{gy})')
        if grid[iy][ix] != 0:
            raise LevelValidationError(f'Level "{name}": {label} lands on a wall ({gx},{gy}) type={grid[iy][ix]}')

    clearance = 0.35
    def check_clearance(label, gx, gy):
        check_pt(label, gx, gy)
        for dx, dy in ((-clearance, -clearance), (clearance, -clearance), (-clearance, clearance), (clearance, clearance)):
            ix, iy = math.floor(gx + dx), math.floor(gy + dy)
            if iy < 0 or iy >= h or ix < 0 or ix >= w or grid[iy][ix] != 0:
                raise LevelValidationError(
                    f'Level "{name}": {label} at ({gx},{gy}) has no collision clearance -- '
                    f'a corner of its bounding box lands on a wall/border')

    check_clearance('playerStart', level['player_start']['gx'], level['player_start']['gy'])
    check_pt('exit', level['exit']['gx'], level['exit']['gy'])
    for e in level['enemies']:
        check_clearance(f"enemy:{e['type']}@{e['gx']},{e['gy']}", e['gx'], e['gy'])
    for p in level['pickups']:
        check_pt(f"pickup:{p['type']}@{p['gx']},{p['gy']}", p['gx'], p['gy'])
    for b in level.get('barrels', []):
        check_pt(f"barrel@{b['gx']},{b['gy']}", b['gx'], b['gy'])

    level['_secrets_total'] = count_cells_of_type(grid, SECRET_TYPE)


def _new_level(name, intro, w, h, player_start, exit_pos, enemies=None,
               pickups=None, barrels=None, is_final_level=False):
    grid = make_grid(w, h)
    return {
        'name': name, 'intro': intro, 'grid': grid, 'w': w, 'h': h,
        'player_start': player_start, 'exit': exit_pos,
        'enemies': enemies or [], 'pickups': pickups or [], 'barrels': barrels or [],
        'is_final_level': is_final_level, 'locked_doors': {},
    }


def _e(etype, gx, gy):
    return {'type': etype, 'gx': gx, 'gy': gy}


def _p(ptype, gx, gy):
    return {'type': ptype, 'gx': gx, 'gy': gy}


def _b(gx, gy):
    return {'gx': gx, 'gy': gy}


def build_levels():
    levels = []

    # ── Level 1: ByteMart Warehouse (32x15, height extended for a secret alcove) ──
    level = _new_level(
        'ByteMart Warehouse',
        "First stop: ByteMart's back warehouse. Word is the Dark Forces have been "
        "stockpiling parts here since they hit the place last month. Get in, take "
        "back what's ours, get out.",
        32, 15, {'gx': 1.5, 'gy': 1.5, 'angle': 0}, {'gx': 29, 'gy': 7},
        enemies=[
            _e('scalper', 4, 3.5), _e('scalper', 5, 7), _e('goon', 14, 8),
            _e('scalper', 9, 10), _e('guard', 14, 4), _e('guard', 21, 4),
            _e('scalper', 28, 5), _e('goon', 28, 8), _e('turret', 22, 4),
        ],
        pickups=[
            _p('health_small', 2, 10), _p('ammo_shells', 9, 1), _p('part', 13, 10),
            _p('part', 5, 10), _p('weapon_static', 16, 5), _p('weapon_packetspray', 9, 6),
            _p('ammo_rounds', 12, 7), _p('health_small', 19, 6), _p('part', 23, 4),
            _p('armor', 29, 5), _p('ammo_shells', 27, 8), _p('ammo_cores', 9, 13),
        ],
        barrels=[_b(19, 4), _b(29, 7)],
    )
    wall_ring(level, 2, 2, 6, 5, 3)
    opening(level, 4, 2, 4, 2)
    level['grid'][2][4] = DOOR_TYPE
    wall_ring(level, 8, 2, 12, 5, 3)
    opening(level, 10, 2, 10, 2)
    level['grid'][2][10] = DOOR_TYPE
    wall_rect(level, 4, 8, 9, 8, 4)
    opening(level, 6, 8, 7, 8)
    level['grid'][8][6] = DOOR_TYPE
    level['grid'][8][7] = DOOR_TYPE
    wall_ring(level, 11, 9, 15, 11, 6)
    opening(level, 13, 9, 13, 9)
    level['grid'][9][13] = DOOR_TYPE
    wall_ring(level, 18, 2, 24, 8, 2)
    opening(level, 21, 2, 21, 2)
    level['grid'][2][21] = DOOR_TYPE
    wall_rect(level, 20, 5, 22, 5, 2)
    wall_ring(level, 26, 3, 30, 9, 6)
    opening(level, 28, 3, 28, 3)
    level['grid'][3][28] = DOOR_TYPE
    level['grid'][6][27] = 3
    level['grid'][6][29] = 3
    wall_rect(level, 1, 12, 30, 12, 1)
    level['grid'][12][9] = SECRET_TYPE
    validate_level(level)
    levels.append(level)

    # ── Level 2: Overclock Alley Pawn Shop (24x14) ──
    level = _new_level(
        'Overclock Alley Pawn Shop',
        "The pawn shop on Overclock Alley. Cramped aisles, more guards. "
        "They know we're coming after ByteMart.",
        24, 14, {'gx': 1.5, 'gy': 1.5, 'angle': 0}, {'gx': 21.5, 'gy': 1.5},
        enemies=[
            _e('guard', 11.5, 7.5), _e('scalper', 6, 3), _e('scalper', 18, 3),
            _e('goon', 11, 10.5), _e('guard', 20, 10), _e('scalper', 3, 10),
            _e('drone', 5.5, 3), _e('drone', 17.5, 3),
        ],
        pickups=[
            _p('health_large', 11, 8), _p('armor', 2, 6), _p('ammo_shells', 21, 6),
            _p('part', 11, 12), _p('part', 2, 12), _p('weapon_emp', 20, 12),
            _p('ammo_rounds', 16, 6),
        ],
        barrels=[_b(15, 10)],
    )
    for i in range(5):
        x = 3 + i * 4
        wall_rect(level, x, 2, x + 1, 4, 2)
    wall_ring(level, 9, 6, 14, 9, 2)
    opening(level, 11, 6, 12, 6)
    level['grid'][6][11] = DOOR_TYPE
    level['grid'][6][12] = DOOR_TYPE
    wall_rect(level, 3, 11, 20, 11, 3)
    opening(level, 11, 11, 12, 11)
    level['grid'][11][11] = DOOR_TYPE
    level['grid'][11][12] = DOOR_TYPE
    validate_level(level)
    levels.append(level)

    # ── NetCafe Hideout (22x12) ──
    level = _new_level(
        'NetCafe Hideout',
        "A gutted internet cafe two blocks from ByteMart -- the terminals are long "
        "dead, but the Dark Forces have been using the back office as a stakeout "
        "post. Smaller job. Doesn't mean smaller trouble.",
        22, 12, {'gx': 2, 'gy': 2, 'angle': 0}, {'gx': 18, 'gy': 8},
        enemies=[
            _e('scalper', 4, 6), _e('scalper', 9, 7), _e('goon', 13, 6), _e('guard', 17, 7),
        ],
        pickups=[
            _p('health_small', 3, 8), _p('ammo_shells', 5, 8), _p('health_large', 10, 3),
            _p('part', 4, 8), _p('part', 17, 6), _p('ammo_cells', 17, 8),
        ],
    )
    wall_rect(level, 5, 2, 5, 3, 2)
    wall_rect(level, 8, 2, 8, 3, 2)
    wall_rect(level, 11, 2, 11, 3, 2)
    wall_rect(level, 14, 2, 14, 3, 2)
    wall_ring(level, 15, 5, 19, 9, 1)
    opening(level, 15, 7, 15, 7)
    level['grid'][7][15] = DOOR_TYPE
    level['grid'][9][3] = AMMO_STATION_TYPE
    validate_level(level)
    levels.append(level)

    # ── Level 3: CircuitSide Outlet (28x16, height extended for a locked vault) ──
    level = _new_level(
        'CircuitSide Outlet',
        "Bigger store, bigger crew. CircuitSide's showroom floor is wide open — "
        "no cover, no mercy. Techs are running EMP crowd control in here.",
        28, 16, {'gx': 1.5, 'gy': 1.5, 'angle': 0}, {'gx': 26.5, 'gy': 11.5},
        enemies=[
            _e('tech', 5, 6), _e('tech', 21, 6), _e('goon', 13, 1.5), _e('goon', 14, 11),
            _e('guard', 8, 11), _e('guard', 19, 1.5), _e('scalper', 12, 6), _e('scalper', 16, 6),
            _e('turret', 14, 6), _e('shieldtech', 13, 14),
        ],
        pickups=[
            _p('health_large', 13, 6), _p('armor', 2, 11), _p('ammo_cells', 25, 1.5),
            _p('ammo_shells', 2, 1.5), _p('part', 13, 1.5), _p('part', 13, 11),
            _p('weapon_overclock', 25, 11), _p('weapon_debugger', 25, 5), _p('ammo_scopes', 25, 6),
            _p('key_red', 2, 9), _p('health_large', 11, 14), _p('ammo_cores', 15, 14),
        ],
        barrels=[_b(9, 6), _b(19, 9)],
    )
    for i in range(4):
        x = 3 + i * 6
        wall_ring(level, x, 2, x + 2, 4, 1)
    for i in range(4):
        x = 3 + i * 6
        wall_ring(level, x, 7, x + 2, 9, 4)
    wall_ring(level, 24, 4, 26, 7, 1)
    opening(level, 24, 5, 24, 5)
    level['grid'][5][24] = DOOR_TYPE
    wall_rect(level, 1, 12, 26, 12, 1)
    level['grid'][12][13] = LOCKED_DOOR_TYPE
    validate_level(level)
    level['locked_doors'] = {'13,12': 'red'}
    levels.append(level)

    # ── TechBarn Superstore (36x16) ──
    level = _new_level(
        'TechBarn Superstore',
        "TechBarn's flagship store -- the Dark Forces practically run their whole "
        "retail-front operation out of here. Long aisles, lots of angles for an "
        "ambush. Watch the endcaps.",
        36, 16, {'gx': 2, 'gy': 2, 'angle': 0}, {'gx': 32, 'gy': 7},
        enemies=[
            _e('scalper', 3, 8), _e('scalper', 8.5, 3), _e('goon', 8, 13), _e('guard', 13, 8),
            _e('tech', 13, 13), _e('goon', 18, 8), _e('guard', 23, 8), _e('scalper', 27, 3),
            _e('guard', 31, 7), _e('drone', 12.5, 8), _e('drone', 22.5, 8), _e('shieldtech', 32, 5),
        ],
        pickups=[
            _p('health_small', 3, 12), _p('ammo_shells', 5, 3), _p('armor', 14.5, 3),
            _p('ammo_rounds', 18, 3), _p('health_large', 23, 3), _p('part', 8, 8),
            _p('part', 18, 13), _p('ammo_scopes', 23, 13), _p('health_large', 30, 4),
        ],
        barrels=[_b(3, 8), _b(27, 8)],
    )
    for i in range(4):
        x = 3 + i * 3
        wall_rect(level, x, 2, x + 1, 4, 2)
    for i in range(5):
        x = 5 + i * 5
        wall_rect(level, x, 6, x, 11, 3)
    wall_ring(level, 29, 3, 33, 9, 2)
    opening(level, 29, 6, 29, 6)
    level['grid'][6][29] = DOOR_TYPE
    level['grid'][4][4] = AMMO_STATION_TYPE
    validate_level(level)
    levels.append(level)

    # ── Level 4: The Mainframe Megastore (34x13) ──
    level = _new_level(
        'The Mainframe Megastore',
        "The Mainframe Megastore — the Dark Forces' regional distribution hub. "
        "Every part they've stolen this year is stacked in here somewhere. Bigger "
        "store, bigger crew, and this is only the halfway point.",
        34, 13, {'gx': 1.5, 'gy': 1.5, 'angle': 0}, {'gx': 32.5, 'gy': 11.5},
        enemies=[
            _e('guard', 3, 5.5), _e('guard', 27.5, 5.5), _e('tech', 16.5, 5.5),
            _e('goon', 3, 9.5), _e('goon', 29, 9.5), _e('scalper', 10, 9.5),
            _e('scalper', 21, 9.5), _e('scalper', 16, 9.5), _e('guard', 16.5, 1.5),
            _e('turret', 16.5, 3.5),
        ],
        pickups=[
            _p('health_large', 1.5, 10.5), _p('health_large', 32.5, 1.5), _p('armor', 16.5, 10.5),
            _p('ammo_cores', 8, 1.5), _p('ammo_cells', 25, 1.5), _p('part', 4, 3),
            _p('part', 29, 5.5), _p('part', 16.5, 5.5), _p('ammo_rounds', 8, 9),
            _p('ammo_scopes', 25, 9), _p('key_blue', 1.5, 9),
        ],
        barrels=[_b(11, 9.5), _b(22, 5.5)],
    )
    rooms = [(2, 2, 10, 8), (12, 2, 21, 8), (23, 2, 31, 8)]
    locked_cell_key = None
    for i, (x1, y1, x2, y2) in enumerate(rooms):
        wall_ring(level, x1, y1, x2, y2, 1)
        cx1, cy1, cx2, cy2 = x1 + 3, y1 + 2, x2 - 3, y2 - 2
        wall_ring(level, cx1, cy1, cx2, cy2, 5)
        mid_y = (y1 + y2) // 2
        opening(level, x1, mid_y, x1, mid_y)
        opening(level, x2, mid_y, x2, mid_y)
        inner_door_x, inner_door_y = (cx1 + cx2) // 2, cy1
        opening(level, inner_door_x, inner_door_y, inner_door_x, inner_door_y)
        if i == 1:
            level['grid'][inner_door_y][inner_door_x] = LOCKED_DOOR_TYPE
            locked_cell_key = f'{inner_door_x},{inner_door_y}'
        else:
            level['grid'][inner_door_y][inner_door_x] = DOOR_TYPE
    wall_rect(level, 2, 10, 31, 10, 3)
    opening(level, 16, 10, 17, 10)
    validate_level(level)
    level['locked_doors'] = {locked_cell_key: 'blue'}
    levels.append(level)

    # ── Level 5: Datastream Distribution Depot (32x14) ──
    level = _new_level(
        'Datastream Distribution Depot',
        "Past the Megastore, into the depot that actually moves the Dark Forces' "
        "stolen hardware around the city. Sealed bay doors between every warehouse "
        "block -- expect a fight every time one opens.",
        32, 14, {'gx': 2.5, 'gy': 2.5, 'angle': 0}, {'gx': 28, 'gy': 10},
        enemies=[
            _e('scalper', 4, 8), _e('goon', 12, 4), _e('tech', 12, 10), _e('guard', 19, 3),
            _e('guard', 21, 10), _e('scalper', 19, 9), _e('tech', 27, 3), _e('guard', 27, 10),
            _e('drone', 20, 10),
        ],
        pickups=[
            _p('health_small', 3, 5), _p('armor', 6, 9), _p('ammo_shells', 11, 2),
            _p('ammo_cells', 13, 11), _p('part', 20, 3), _p('ammo_rounds', 22, 3),
            _p('health_large', 26, 6), _p('part', 29, 10), _p('ammo_cores', 29, 2),
        ],
        barrels=[_b(4, 4)],
    )
    wall_rect(level, 8, 1, 8, 12, 2)
    opening(level, 8, 3, 8, 3)
    level['grid'][3][8] = DOOR_TYPE
    wall_rect(level, 16, 1, 16, 12, 6)
    opening(level, 16, 9, 16, 9)
    level['grid'][9][16] = DOOR_TYPE
    wall_rect(level, 24, 1, 24, 12, 4)
    opening(level, 24, 5, 24, 5)
    level['grid'][5][24] = DOOR_TYPE
    level['grid'][2][17] = AMMO_STATION_TYPE
    wall_ring(level, 11, 6, 13, 8, 3)
    wall_ring(level, 19, 5, 21, 7, 4)
    wall_ring(level, 27, 6, 29, 8, 1)
    validate_level(level)
    levels.append(level)

    # ── The Chip Foundry (30x16) ──
    level = _new_level(
        'The Chip Foundry',
        "The foundry that stamps out half the counterfeit parts flooding the "
        "district. Heavier crew than anything so far -- this is where the Dark "
        "Forces actually make their money.",
        30, 16, {'gx': 2, 'gy': 2, 'angle': 0}, {'gx': 26, 'gy': 12},
        enemies=[
            _e('guard', 3, 4), _e('tech', 10, 4), _e('scalper', 10, 9), _e('guard', 18, 4),
            _e('tech', 18, 9), _e('goon', 25, 4), _e('goon', 25, 9), _e('guard', 17, 13),
            _e('tech', 4, 12.5), _e('guard', 24.5, 12.5), _e('shieldtech', 12, 9),
            _e('turret', 14, 2.5),
        ],
        pickups=[
            _p('health_large', 3, 7), _p('armor', 10, 6), _p('ammo_cells', 18, 6),
            _p('ammo_scopes', 25, 6), _p('health_small', 14, 4), _p('ammo_cores', 12, 10),
            _p('part', 5, 12), _p('ammo_rounds', 10, 13), _p('part', 23, 12),
        ],
        barrels=[_b(24, 3), _b(26, 3), _b(25, 5)],
    )
    wall_rect(level, 6, 3, 7, 10, 4)
    wall_rect(level, 14, 5, 15, 12, 4)
    wall_rect(level, 21, 2, 22, 9, 4)
    wall_ring(level, 2, 11, 6, 13, 1)
    opening(level, 4, 11, 4, 11)
    level['grid'][11][4] = DOOR_TYPE
    wall_ring(level, 22, 11, 27, 13, 1)
    opening(level, 24, 11, 24, 11)
    level['grid'][11][24] = DOOR_TYPE
    level['grid'][2][4] = AMMO_STATION_TYPE
    validate_level(level)
    levels.append(level)

    # ── Level 6: The Server Farm (28x18, width extended for a secret alcove) ──
    level = _new_level(
        'The Server Farm',
        "The Dark Forces' server farm — rows of racked hardware humming in the "
        "dark, and the last thing standing between you and the Middleman himself. "
        "No more open floors. Every room here was built to slow you down.",
        28, 18, {'gx': 2, 'gy': 2, 'angle': 0}, {'gx': 22, 'gy': 15},
        enemies=[
            _e('scalper', 5, 3), _e('scalper', 10, 3), _e('goon', 16, 3), _e('tech', 20, 3),
            _e('guard', 4, 9), _e('guard', 12, 9), _e('tech', 18, 9), _e('scalper', 22, 9),
            _e('guard', 6, 15), _e('guard', 14, 15), _e('tech', 20, 14), _e('turret', 8, 3),
            _e('shieldtech', 10, 15),
        ],
        pickups=[
            _p('health_small', 3, 4), _p('ammo_shells', 15, 3), _p('health_large', 22, 4),
            _p('armor', 7, 9), _p('ammo_rounds', 16, 9), _p('part', 4, 10),
            _p('ammo_scopes', 2, 15), _p('health_large', 10, 14), _p('ammo_cores', 19, 15),
            _p('part', 22, 13), _p('armor', 26, 9),
        ],
        barrels=[_b(13, 10), _b(15, 10)],
    )
    wall_rect(level, 1, 6, 24, 6, 4)
    opening(level, 6, 6, 6, 6)
    level['grid'][6][6] = DOOR_TYPE
    wall_rect(level, 1, 12, 24, 12, 5)
    opening(level, 18, 12, 18, 12)
    level['grid'][12][18] = DOOR_TYPE
    wall_ring(level, 12, 2, 14, 4, 4)
    wall_ring(level, 8, 8, 10, 10, 3)
    level['grid'][11][19] = AMMO_STATION_TYPE
    wall_rect(level, 25, 1, 25, 16, 1)
    level['grid'][9][25] = SECRET_TYPE
    validate_level(level)
    levels.append(level)

    # ── Level 7: HQ Showroom, boss (23x12, width extended for a locked gold vault) ──
    level = _new_level(
        "The Middleman's Showroom",
        "The penthouse showroom. Marble floors, gold-plated cable trays, and one "
        "very smug crime boss who thinks he owns every computer store in the "
        "city. Time to change his mind.",
        23, 12, {'gx': 1.5, 'gy': 1.5, 'angle': 0}, {'gx': 10, 'gy': 1.5},
        enemies=[
            _e('boss_middleman', 10, 6.5), _e('guard', 4, 8), _e('guard', 16, 8),
        ],
        pickups=[
            _p('health_large', 3, 3), _p('health_large', 16, 3), _p('ammo_cores', 10, 3),
            _p('armor', 4, 8), _p('armor', 15, 8), _p('key_gold', 1.5, 6),
            _p('health_large', 20.5, 1.5), _p('ammo_cores', 20.5, 2.5), _p('ammo_scopes', 20.5, 3.5),
        ],
        barrels=[_b(5, 8), _b(15, 8)],
        is_final_level=True,
    )
    wall_ring(level, 2, 2, 17, 9, 2)
    opening(level, 9, 2, 10, 2)
    opening(level, 9, 9, 10, 9)
    wall_ring(level, 8, 5, 11, 7, 5)
    opening(level, 9, 7, 10, 7)
    level['grid'][7][9] = DOOR_TYPE
    level['grid'][7][10] = DOOR_TYPE
    wall_rect(level, 19, 1, 19, 10, 1)
    level['grid'][1][19] = LOCKED_DOOR_TYPE
    wall_rect(level, 20, 4, 21, 4, 1)
    validate_level(level)
    level['locked_doors'] = {'19,1': 'gold'}
    levels.append(level)

    return levels


LEVELS = build_levels()


def grid_to_world(gx, gy):
    return gx * TILE, gy * TILE


# ═══════════════════════════════════════════════════════════════════════════
# Raycaster -- direct port of raycaster.js's castRay()/hasLineOfSight().
# Same grid-DDA algorithm, same "raw ray distance, caller fisheye-corrects"
# contract. Verified against the same known-distance test cases the browser
# version used (see tests/test_darkforces_term_raycaster.py).
# ═══════════════════════════════════════════════════════════════════════════

class RayHit:
    __slots__ = ('dist', 'wall_type', 'side', 'texture_x')
    def __init__(self, dist, wall_type, side, texture_x):
        self.dist = dist
        self.wall_type = wall_type
        self.side = side
        self.texture_x = texture_x


def cast_ray(grid, grid_w, grid_h, world_x, world_y, angle):
    pos_x, pos_y = world_x / TILE, world_y / TILE
    ray_dir_x, ray_dir_y = math.cos(angle), math.sin(angle)

    map_x, map_y = math.floor(pos_x), math.floor(pos_y)

    delta_dist_x = math.inf if ray_dir_x == 0 else abs(1 / ray_dir_x)
    delta_dist_y = math.inf if ray_dir_y == 0 else abs(1 / ray_dir_y)

    if ray_dir_x < 0:
        step_x = -1
        side_dist_x = (pos_x - map_x) * delta_dist_x
    else:
        step_x = 1
        side_dist_x = (map_x + 1 - pos_x) * delta_dist_x

    if ray_dir_y < 0:
        step_y = -1
        side_dist_y = (pos_y - map_y) * delta_dist_y
    else:
        step_y = 1
        side_dist_y = (map_y + 1 - pos_y) * delta_dist_y

    side = 0
    hit_type = 0
    max_steps = grid_w + grid_h + 4
    for _ in range(max_steps):
        if side_dist_x < side_dist_y:
            side_dist_x += delta_dist_x
            map_x += step_x
            side = 0
        else:
            side_dist_y += delta_dist_y
            map_y += step_y
            side = 1
        if map_x < 0 or map_x >= grid_w or map_y < 0 or map_y >= grid_h:
            return None
        cell = grid[map_y][map_x]
        if cell != 0:
            hit_type = cell
            break
    if not hit_type:
        return None

    if side == 0:
        raw_grid_dist = (map_x - pos_x + (1 - step_x) / 2) / ray_dir_x
    else:
        raw_grid_dist = (map_y - pos_y + (1 - step_y) / 2) / ray_dir_y

    wall_x = pos_y + raw_grid_dist * ray_dir_y if side == 0 else pos_x + raw_grid_dist * ray_dir_x
    wall_x -= math.floor(wall_x)

    return RayHit(raw_grid_dist * TILE, hit_type, side, wall_x)


def has_line_of_sight(grid, grid_w, grid_h, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 1:
        return True
    angle = math.atan2(dy, dx)
    hit = cast_ray(grid, grid_w, grid_h, x0, y0, angle)
    return (not hit) or hit.dist >= dist


# ═══════════════════════════════════════════════════════════════════════════
# Entities: enemies, projectiles, pickups, barrels, doors/secrets/vaults/
# ammo-stations -- direct port of entities.js + weapons.js. `runtime` is a
# plain dict mirroring the browser version's runtime object exactly (same
# key names, snake_cased): level, enemies, pickups, props, projectiles,
# state (player/kills/deaths/parts_total/level_index), time,
# ammo_station_cooldowns, boss_defeated, player_dead, hurt_flash_timer,
# parts_this_level. `log_fn(msg, kind)` is the addLog callback (kind one of
# info/warning/milestone/harvest, matching the browser version's toast
# categories -- the terminal UI maps these to different ANSI colors).
# ═══════════════════════════════════════════════════════════════════════════

def move_with_collision(grid, grid_w, grid_h, x, y, dx, dy, radius):
    def is_wall(wx, wy):
        gx, gy = math.floor(wx / TILE), math.floor(wy / TILE)
        if gx < 0 or gx >= grid_w or gy < 0 or gy >= grid_h:
            return True
        return grid[gy][gx] != 0

    def blocked(px, py):
        return (is_wall(px - radius, py - radius) or is_wall(px + radius, py - radius) or
                is_wall(px - radius, py + radius) or is_wall(px + radius, py + radius))

    nx, ny = x, y
    if not blocked(x + dx, y):
        nx = x + dx
    if not blocked(nx, y + dy):
        ny = y + dy
    return nx, ny


def spawn_enemy(etype, x, y):
    edef = ENEMY_TYPES[etype]
    return {
        'type': etype, 'def': edef, 'x': x, 'y': y,
        'hp': edef['hp'], 'max_hp': edef['hp'], 'dead': False, 'state': 'idle',
        'last_attack_time': -999, 'hurt_flash_timer': 0, 'angle': 0,
        'angry_at': None, 'angry_until': 0, 'last_seen_time': -999, 'arm_timer': 0,
    }


def spawn_pickup(ptype, x, y):
    return {'type': ptype, 'x': x, 'y': y, 'collected': False}


def spawn_barrel(x, y):
    return {'x': x, 'y': y, 'hp': 20, 'dead': False, 'hurt_flash_timer': 0}


def spawn_projectile(x, y, angle, speed, damage, splash_radius, color, from_player, source_enemy=None):
    units_per_second = speed * TILE
    return {
        'x': x, 'y': y, 'damage': damage, 'splash_radius': splash_radius, 'color': color,
        'from_player': from_player, 'source_enemy': source_enemy,
        'dx': math.cos(angle) * units_per_second, 'dy': math.sin(angle) * units_per_second,
        'dead': False,
    }


def dist_to(a, b):
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


def damage_enemy(runtime, enemy, amount, log_fn, source_x=None, source_y=None, source_enemy=None):
    if enemy['dead']:
        return
    final_amount = amount
    edef = enemy['def']
    if edef.get('frontal_damage_reduction') and source_x is not None and source_y is not None:
        angle_to_source = math.atan2(source_y - enemy['y'], source_x - enemy['x'])
        diff = angle_to_source - enemy['angle']
        while diff > math.pi:
            diff -= math.pi * 2
        while diff < -math.pi:
            diff += math.pi * 2
        if abs(diff) < math.pi / 2.5:
            final_amount = round(amount * (1 - edef['frontal_damage_reduction']))
    enemy['hp'] -= final_amount
    enemy['hurt_flash_timer'] = 0.15
    if source_enemy and source_enemy is not enemy and not source_enemy['dead']:
        enemy['angry_at'] = source_enemy
        enemy['angry_until'] = runtime['time'] + 8
        enemy['state'] = 'chase'
        if log_fn:
            log_fn(f"{edef['name']} turns on {source_enemy['def']['name']}!", 'warning')
    if enemy['hp'] <= 0:
        enemy['dead'] = True
        enemy['state'] = 'dead'
        runtime['state']['kills'] += 1
        award_xp(runtime, edef['xp'], log_fn)
        if edef.get('is_boss'):
            runtime['boss_defeated'] = True
            if log_fn:
                log_fn(f"{edef['name']} is down! The exit is unlocked.", 'milestone')


def award_xp(runtime, amount, log_fn):
    p = runtime['state']['player']
    p['xp'] += amount
    while p['level'] < len(XP_PER_LEVEL) and p['xp'] >= XP_PER_LEVEL[p['level']]:
        p['level'] += 1
        p['max_hp'] += LEVEL_UP_HEALTH_BONUS
        p['hp'] = min(p['max_hp'], p['hp'] + LEVEL_UP_HEALTH_BONUS)
        if log_fn:
            log_fn(f"Level up! You're now level {p['level']}.", 'milestone')


def detonate_kamikaze(runtime, enemy, log_fn):
    edef = enemy['def']
    if log_fn:
        log_fn(f"{edef['name']} detonates!", 'warning')
    enemy['dead'] = True
    enemy['state'] = 'dead'
    runtime['state']['kills'] += 1
    award_xp(runtime, edef['xp'], log_fn)
    apply_splash_damage(runtime, {'x': enemy['x'], 'y': enemy['y'], 'damage': edef['damage'],
                                   'splash_radius': edef['splash_radius'], 'source_enemy': None}, log_fn)


def update_enemies(runtime, dt, log_fn):
    level = runtime['level']
    grid, w, h = level['grid'], level['w'], level['h']
    player = runtime['state']['player']
    for e in runtime['enemies']:
        if e['dead']:
            e['hurt_flash_timer'] = max(0, e['hurt_flash_timer'] - dt)
            continue
        e['hurt_flash_timer'] = max(0, e['hurt_flash_timer'] - dt)
        edef = e['def']

        target, target_is_player = player, True
        if e['angry_at'] and not e['angry_at']['dead'] and runtime['time'] < e['angry_until']:
            target, target_is_player = e['angry_at'], False

        d = dist_to(e, target)
        can_see = d < edef['sight_range'] and has_line_of_sight(grid, w, h, e['x'], e['y'], target['x'], target['y'])
        if can_see:
            if e['state'] != 'chase' and runtime.get('sfx'):
                runtime['sfx']('alert')
            e['state'] = 'chase'
            e['last_seen_time'] = runtime['time']
        if e['state'] == 'chase' and runtime['time'] - e['last_seen_time'] > ENEMY_MEMORY_DURATION:
            e['state'] = 'idle'
            e['arm_timer'] = 0
        if e['state'] != 'chase':
            continue

        if edef['kind'] == 'kamikaze':
            if e['arm_timer'] > 0:
                e['arm_timer'] -= dt
                if e['arm_timer'] <= 0:
                    detonate_kamikaze(runtime, e, log_fn)
                continue
            if d <= edef['attack_range']:
                e['arm_timer'] = KAMIKAZE_ARM_DURATION
                if runtime.get('sfx'):
                    runtime['sfx']('kamikaze_arm')
                continue

        is_stationary = edef['speed'] == 0
        if not is_stationary and d > edef['attack_range'] * 0.8:
            angle = math.atan2(target['y'] - e['y'], target['x'] - e['x'])
            e['angle'] = angle
            step = edef['speed'] * TILE * dt
            nx, ny = move_with_collision(grid, w, h, e['x'], e['y'],
                                          math.cos(angle) * step, math.sin(angle) * step, TILE * 0.3)
            e['x'], e['y'] = nx, ny
        elif can_see and runtime['time'] - e['last_attack_time'] >= edef['attack_rate']:
            e['last_attack_time'] = runtime['time']
            e['angle'] = math.atan2(target['y'] - e['y'], target['x'] - e['x'])
            if edef['kind'] in ('melee', 'boss') and d <= edef['attack_range'] * 1.4:
                if target_is_player:
                    apply_damage_to_player(runtime, edef['damage'], log_fn)
                else:
                    damage_enemy(runtime, target, edef['damage'], log_fn, e['x'], e['y'], e)
            if edef['kind'] in ('ranged', 'boss', 'turret'):
                runtime['projectiles'].append(spawn_projectile(
                    e['x'], e['y'], e['angle'], edef.get('projectile_speed', 6),
                    edef['damage'], 0, edef['color'], False, e))


def apply_damage_to_player(runtime, amount, log_fn):
    p = runtime['state']['player']
    remaining = amount
    if p['armor'] > 0:
        absorbed = min(p['armor'], math.ceil(remaining * 0.5))
        p['armor'] -= absorbed
        remaining -= absorbed
    p['hp'] -= remaining
    runtime['hurt_flash_timer'] = 0.2
    if p['hp'] <= 0 and not runtime['player_dead']:
        p['hp'] = 0
        runtime['player_dead'] = True
        runtime['state']['deaths'] += 1
        if log_fn:
            log_fn('You went down. Respawning at the last checkpoint...', 'warning')


def update_projectiles(runtime, dt, log_fn):
    level = runtime['level']
    grid, w, h = level['grid'], level['w'], level['h']
    player = runtime['state']['player']
    for pr in runtime['projectiles']:
        if pr['dead']:
            continue
        pr['x'] += pr['dx'] * dt
        pr['y'] += pr['dy'] * dt
        gx, gy = math.floor(pr['x'] / TILE), math.floor(pr['y'] / TILE)
        if gx < 0 or gx >= w or gy < 0 or gy >= h or grid[gy][gx] != 0:
            pr['dead'] = True
            if pr['splash_radius'] > 0:
                apply_splash_damage(runtime, {'x': pr['x'], 'y': pr['y'], 'damage': pr['damage'],
                                               'splash_radius': pr['splash_radius'],
                                               'source_enemy': pr['source_enemy']}, log_fn)
            continue
        if pr['from_player']:
            hit = False
            for e in runtime['enemies']:
                if e['dead']:
                    continue
                if dist_to(pr, e) < TILE * 0.4:
                    pr['dead'] = True
                    hit = True
                    if pr['splash_radius'] > 0:
                        apply_splash_damage(runtime, {'x': pr['x'], 'y': pr['y'], 'damage': pr['damage'],
                                                       'splash_radius': pr['splash_radius'], 'source_enemy': None}, log_fn)
                    else:
                        damage_enemy(runtime, e, pr['damage'], log_fn, pr['x'], pr['y'])
                    break
            if not hit:
                for b in runtime['props']:
                    if b['dead']:
                        continue
                    if dist_to(pr, b) < TILE * 0.4:
                        pr['dead'] = True
                        if pr['splash_radius'] > 0:
                            apply_splash_damage(runtime, {'x': pr['x'], 'y': pr['y'], 'damage': pr['damage'],
                                                           'splash_radius': pr['splash_radius'], 'source_enemy': None}, log_fn)
                        else:
                            damage_barrel(runtime, b, pr['damage'], log_fn)
                        break
        else:
            if dist_to(pr, player) < TILE * 0.4:
                pr['dead'] = True
                if pr['splash_radius'] > 0:
                    apply_splash_damage(runtime, {'x': pr['x'], 'y': pr['y'], 'damage': pr['damage'],
                                                   'splash_radius': pr['splash_radius'],
                                                   'source_enemy': pr['source_enemy']}, log_fn)
                else:
                    apply_damage_to_player(runtime, pr['damage'], log_fn)
                continue
            hit = False
            for e in runtime['enemies']:
                if e['dead'] or e is pr['source_enemy']:
                    continue
                if dist_to(pr, e) < TILE * 0.4:
                    pr['dead'] = True
                    hit = True
                    if pr['splash_radius'] > 0:
                        apply_splash_damage(runtime, {'x': pr['x'], 'y': pr['y'], 'damage': pr['damage'],
                                                       'splash_radius': pr['splash_radius'],
                                                       'source_enemy': pr['source_enemy']}, log_fn)
                    else:
                        damage_enemy(runtime, e, pr['damage'], log_fn, pr['x'], pr['y'], pr['source_enemy'])
                    break
            if not hit:
                for b in runtime['props']:
                    if b['dead']:
                        continue
                    if dist_to(pr, b) < TILE * 0.4:
                        pr['dead'] = True
                        if pr['splash_radius'] > 0:
                            apply_splash_damage(runtime, {'x': pr['x'], 'y': pr['y'], 'damage': pr['damage'],
                                                           'splash_radius': pr['splash_radius'],
                                                           'source_enemy': pr['source_enemy']}, log_fn)
                        else:
                            damage_barrel(runtime, b, pr['damage'], log_fn)
                        break
    runtime['projectiles'] = [pr for pr in runtime['projectiles'] if not pr['dead']]


def apply_splash_damage(runtime, explosion, log_fn):
    player = runtime['state']['player']
    player_dist = dist_to(explosion, player)
    if player_dist <= explosion['splash_radius']:
        falloff = 1 - player_dist / explosion['splash_radius']
        apply_damage_to_player(runtime, round(explosion['damage'] * (0.4 + 0.6 * falloff)), log_fn)
    for e in runtime['enemies']:
        if e['dead'] or e is explosion.get('source_enemy'):
            continue
        d = dist_to(explosion, e)
        if d <= explosion['splash_radius']:
            falloff = 1 - d / explosion['splash_radius']
            damage_enemy(runtime, e, round(explosion['damage'] * (0.4 + 0.6 * falloff)), log_fn,
                         explosion['x'], explosion['y'], explosion.get('source_enemy'))
    for b in runtime['props']:
        if b['dead']:
            continue
        if dist_to(explosion, b) <= explosion['splash_radius']:
            damage_barrel(runtime, b, 999, log_fn)


def update_props(runtime, dt):
    for b in runtime['props']:
        if b['hurt_flash_timer'] > 0:
            b['hurt_flash_timer'] = max(0, b['hurt_flash_timer'] - dt)


def damage_barrel(runtime, barrel, amount, log_fn):
    if barrel['dead']:
        return
    barrel['hp'] -= amount
    barrel['hurt_flash_timer'] = 0.15
    if barrel['hp'] <= 0:
        explode_barrel(runtime, barrel, log_fn)


def explode_barrel(runtime, barrel, log_fn):
    barrel['dead'] = True
    if log_fn:
        log_fn('Barrel detonates!', 'warning')
    radius = TILE * BARREL_EXPLOSION_RADIUS_MULT
    apply_splash_damage(runtime, {'x': barrel['x'], 'y': barrel['y'], 'damage': BARREL_EXPLOSION_DAMAGE,
                                   'splash_radius': radius, 'source_enemy': None}, log_fn)
    for b in runtime['props']:
        if b is barrel or b['dead']:
            continue
        if dist_to(barrel, b) <= radius:
            damage_barrel(runtime, b, 999, log_fn)


def interact_with_door(runtime, log_fn):
    p = runtime['state']['player']
    level = runtime['level']
    grid, w, h = level['grid'], level['w'], level['h']
    hit = cast_ray(grid, w, h, p['x'], p['y'], p['angle'])
    if not hit or hit.dist > TILE * 1.6:
        return False
    if hit.wall_type not in (DOOR_TYPE, SECRET_TYPE, LOCKED_DOOR_TYPE):
        return False
    px = p['x'] + math.cos(p['angle']) * (hit.dist + 1)
    py = p['y'] + math.sin(p['angle']) * (hit.dist + 1)
    cell_x, cell_y = math.floor(px / TILE), math.floor(py / TILE)
    if not (0 <= cell_y < h and 0 <= cell_x < w) or grid[cell_y][cell_x] != hit.wall_type:
        return False

    if hit.wall_type == LOCKED_DOOR_TYPE:
        required = level.get('locked_doors', {}).get(f'{cell_x},{cell_y}')
        if not required or required not in p['keys']:
            if log_fn:
                log_fn(f"Locked. Needs a {required or 'matching'} access card.", 'warning')
            return False
        grid[cell_y][cell_x] = 0
        if log_fn:
            log_fn('Vault door unlocks.', 'milestone')
        return True
    if hit.wall_type == SECRET_TYPE:
        grid[cell_y][cell_x] = 0
        if log_fn:
            log_fn('Found a secret!', 'milestone')
        return True
    grid[cell_y][cell_x] = 0
    if log_fn:
        log_fn('Security door opens.', 'info')
    return True


def interact_with_ammo_station(runtime, log_fn):
    p = runtime['state']['player']
    level = runtime['level']
    grid, w, h = level['grid'], level['w'], level['h']
    hit = cast_ray(grid, w, h, p['x'], p['y'], p['angle'])
    if not hit or hit.wall_type != AMMO_STATION_TYPE or hit.dist > TILE * 1.6:
        return False
    px = p['x'] + math.cos(p['angle']) * (hit.dist + 1)
    py = p['y'] + math.sin(p['angle']) * (hit.dist + 1)
    cell_x, cell_y = math.floor(px / TILE), math.floor(py / TILE)
    if not (0 <= cell_y < h and 0 <= cell_x < w) or grid[cell_y][cell_x] != AMMO_STATION_TYPE:
        return False

    key = f'{cell_x},{cell_y}'
    last_used = runtime['ammo_station_cooldowns'].get(key, -999)
    if runtime['time'] - last_used < AMMO_STATION_COOLDOWN:
        if log_fn:
            log_fn('Dispenser recharging...', 'warning')
        return False
    gained = False
    for ammo_type, threshold in AMMO_REFILL_THRESHOLDS.items():
        if ammo_type not in p['ammo']:
            continue
        if p['ammo'][ammo_type] < threshold:
            p['ammo'][ammo_type] = threshold
            gained = True
    runtime['ammo_station_cooldowns'][key] = runtime['time']
    if log_fn:
        log_fn('Ammo dispenser: topped up.' if gained else 'Ammo dispenser: already full.', 'info')
    return True


def check_pickup_collisions(runtime, log_fn):
    player = runtime['state']['player']
    for pk in runtime['pickups']:
        if pk['collected']:
            continue
        if dist_to(pk, player) > TILE * 0.5:
            continue
        pdef = PICKUP_TYPES[pk['type']]
        if pdef['kind'] == 'health' and player['hp'] >= player['max_hp']:
            continue
        if pdef['kind'] == 'armor' and player['armor'] >= player['max_armor']:
            continue
        if pdef['kind'] == 'weapon' and pdef['weapon'] in player['weapons']:
            continue
        if pdef['kind'] == 'key' and pdef['key_id'] in player['keys']:
            continue
        pk['collected'] = True
        apply_pickup_effect(runtime, pdef, log_fn)


def apply_pickup_effect(runtime, pdef, log_fn):
    p = runtime['state']['player']
    kind = pdef['kind']
    if kind == 'health':
        p['hp'] = min(p['max_hp'], p['hp'] + pdef['amount'])
        if log_fn:
            log_fn(f"Picked up {pdef['name']} (+{pdef['amount']} HP).", 'info')
    elif kind == 'armor':
        p['armor'] = min(p['max_armor'], p['armor'] + pdef['amount'])
        if log_fn:
            log_fn(f"Picked up {pdef['name']} (+{pdef['amount']} armor).", 'info')
    elif kind == 'ammo':
        cap = AMMO_MAX.get(pdef['ammo_type'], math.inf)
        p['ammo'][pdef['ammo_type']] = min(cap, p['ammo'].get(pdef['ammo_type'], 0) + pdef['amount'])
        if log_fn:
            log_fn(f"Picked up {pdef['name']}.", 'info')
    elif kind == 'weapon':
        p['weapons'].append(pdef['weapon'])
        p['current_weapon'] = pdef['weapon']
        if log_fn:
            log_fn(f"New weapon: {WEAPONS[pdef['weapon']]['name']}!", 'milestone')
    elif kind == 'part':
        p['parts'] += 1
        runtime['state']['parts_total'] += 1
        runtime['parts_this_level'] += 1
        if log_fn:
            log_fn(f"Found a {pdef['name']}! ({p['parts']} this run)", 'harvest')
    elif kind == 'key':
        p['keys'].append(pdef['key_id'])
        if log_fn:
            log_fn(f"Picked up {pdef['name']}.", 'milestone')


# ─── Weapon firing (direct port of weapons.js) ──────────────────────────────

def can_fire(runtime):
    p = runtime['state']['player']
    w = WEAPONS[p['current_weapon']]
    if runtime['time'] - runtime['last_fire_time'] < w['fire_rate']:
        return False
    if w['ammo_type'] and p['ammo'].get(w['ammo_type'], 0) <= 0:
        return False
    return True


def fire_weapon(runtime, log_fn):
    p = runtime['state']['player']
    w = WEAPONS[p['current_weapon']]
    if not can_fire(runtime):
        if w['ammo_type'] and p['ammo'].get(w['ammo_type'], 0) <= 0 and \
           runtime['time'] - runtime['last_dry_fire_time'] > 0.3:
            runtime['last_dry_fire_time'] = runtime['time']
            if runtime.get('sfx'):
                runtime['sfx']('dry_fire')
        return False
    runtime['last_fire_time'] = runtime['time']
    if w['ammo_type']:
        p['ammo'][w['ammo_type']] -= 1

    damage_mult = 1 + (p['level'] - 1) * LEVEL_UP_DAMAGE_MULT

    if w['kind'] == 'hitscan':
        fire_hitscan_shot(runtime, w, damage_mult, log_fn)
    elif w['kind'] == 'hitscan-spread':
        for _ in range(w['pellets']):
            fire_hitscan_shot(runtime, w, damage_mult, log_fn)
    elif w['kind'] == 'projectile':
        bolt_color = (255, 159, 79) if w['key'] == 'overclock' else (95, 214, 255)
        runtime['projectiles'].append(spawn_projectile(
            p['x'], p['y'], p['angle'], w['speed'], round(w['damage'] * damage_mult),
            w.get('splash_radius', 0), bolt_color, True))
    elif w['kind'] == 'melee':
        fire_melee_swing(runtime, w, damage_mult, log_fn)

    if runtime.get('sfx'):
        runtime['sfx'](w['sfx'])
    runtime['muzzle_flash_timer'] = 0.15
    return True


def fire_hitscan_shot(runtime, w, damage_mult, log_fn):
    p = runtime['state']['player']
    spread = (random.random() - 0.5) * w['spread'] * 2
    angle = p['angle'] + spread
    dir_x, dir_y = math.cos(angle), math.sin(angle)
    level = runtime['level']
    wall_hit = cast_ray(level['grid'], level['w'], level['h'], p['x'], p['y'], angle)
    max_dist = min(w['range'], wall_hit.dist) if wall_hit else w['range']

    closest_target, closest_kind, closest_along = None, None, max_dist
    for e in runtime['enemies']:
        if e['dead']:
            continue
        ex, ey = e['x'] - p['x'], e['y'] - p['y']
        along = ex * dir_x + ey * dir_y
        if along < 0 or along > closest_along:
            continue
        perp_x, perp_y = ex - along * dir_x, ey - along * dir_y
        perp = math.hypot(perp_x, perp_y)
        hit_radius = TILE * (0.55 if e['def'].get('is_boss') else 0.35)
        if perp < hit_radius:
            closest_along, closest_target, closest_kind = along, e, 'enemy'
    for b in runtime['props']:
        if b['dead']:
            continue
        bx, by = b['x'] - p['x'], b['y'] - p['y']
        along = bx * dir_x + by * dir_y
        if along < 0 or along > closest_along:
            continue
        perp_x, perp_y = bx - along * dir_x, by - along * dir_y
        if math.hypot(perp_x, perp_y) < TILE * 0.3:
            closest_along, closest_target, closest_kind = along, b, 'prop'
    if closest_kind == 'enemy':
        damage_enemy(runtime, closest_target, round(w['damage'] * damage_mult), log_fn, p['x'], p['y'])
    elif closest_kind == 'prop':
        damage_barrel(runtime, closest_target, round(w['damage'] * damage_mult), log_fn)


def fire_melee_swing(runtime, w, damage_mult, log_fn):
    p = runtime['state']['player']

    def in_swing_cone(tx, ty):
        angle_to = math.atan2(ty - p['y'], tx - p['x'])
        diff = angle_to - p['angle']
        while diff > math.pi:
            diff -= math.pi * 2
        while diff < -math.pi:
            diff += math.pi * 2
        return abs(diff) <= math.pi / 3

    closest, closest_kind, closest_dist = None, None, w['range']
    for e in runtime['enemies']:
        if e['dead']:
            continue
        d = dist_to(p, e)
        if d > closest_dist or not in_swing_cone(e['x'], e['y']):
            continue
        closest, closest_kind, closest_dist = e, 'enemy', d
    for b in runtime['props']:
        if b['dead']:
            continue
        d = dist_to(p, b)
        if d > closest_dist or not in_swing_cone(b['x'], b['y']):
            continue
        closest, closest_kind, closest_dist = b, 'prop', d
    if closest_kind == 'enemy':
        damage_enemy(runtime, closest, round(w['damage'] * damage_mult), log_fn, p['x'], p['y'])
    elif closest_kind == 'prop':
        damage_barrel(runtime, closest, round(w['damage'] * damage_mult), log_fn)


def switch_weapon(runtime, weapon_key):
    p = runtime['state']['player']
    if weapon_key in p['weapons']:
        p['current_weapon'] = weapon_key


def cycle_weapon(runtime, direction):
    p = runtime['state']['player']
    owned = [k for k in WEAPON_ORDER if k in p['weapons']]
    idx = owned.index(p['current_weapon'])
    p['current_weapon'] = owned[(idx + direction) % len(owned)]


# ═══════════════════════════════════════════════════════════════════════════
# Native sixel encoder -- no subprocess (the existing _rss_render_sixel()
# pipeline in bbs_ui.py shells out to img2sixel, which has up to a 10s
# timeout and is far too slow to call once per game tick). This is a
# from-scratch encoder against small in-memory Pillow images, run entirely
# in-process, cached per sprite key so the actual per-frame cost is just a
# dict lookup, not a re-encode.
#
# Sprites are drawn with a SOLID dark background fill (not attempted
# transparency) rather than relying on sixel's inconsistently-supported
# transparent-background register across terminals -- this can't be
# visually verified against a real SyncTerm session in this environment,
# so the safer choice is an approach with no terminal-specific behavior to
# get wrong: every sixel-capable terminal renders a solid-background image
# the same way, "transparent register 0" support varies.
# ═══════════════════════════════════════════════════════════════════════════

SIXEL_BG = (18, 16, 14)  # dark background fill every sprite is drawn on


class SixelEncoder:
    """Encodes a small Pillow RGB Image to a sixel (DCS q ... ST) escape
    sequence. Palette-quantized (max 32 colors -- plenty for these flat-
    shaded procedural sprites, keeps the color-definition preamble short)."""

    def __init__(self, max_colors=32):
        self.max_colors = max_colors

    def encode(self, image):
        image = image.convert('RGB')
        w, h = image.size
        quant = image.quantize(colors=self.max_colors, method=2)  # method=2 = MEDIANCUT, deterministic
        palette_raw = quant.getpalette()[:self.max_colors * 3]
        palette = [tuple(palette_raw[i:i + 3]) for i in range(0, len(palette_raw), 3)]
        pixels = list(quant.getdata())  # one palette index per pixel, row-major

        out = [f'{_E}Pq', f'"1;1;{w};{h}']
        for idx, (r, g, b) in enumerate(palette):
            out.append(f'#{idx};2;{round(r / 255 * 100)};{round(g / 255 * 100)};{round(b / 255 * 100)}')

        used_colors = sorted(set(pixels))
        for band_y in range(0, h, 6):
            band_h = min(6, h - band_y)
            for ci, color_idx in enumerate(used_colors):
                chars = []
                for x in range(w):
                    mask = 0
                    for row in range(band_h):
                        y = band_y + row
                        if pixels[y * w + x] == color_idx:
                            mask |= (1 << row)
                    chars.append(chr(mask + 63))
                rle = self._rle(chars)
                if rle.strip('?') == '' and rle.count('?') == w:
                    continue  # this color contributes nothing in this band -- skip the pass entirely
                out.append(f'#{color_idx}{rle}$')
            out.append('-' if band_y + 6 < h else '')
        out.append(f'{_E}\\')
        return ''.join(out)

    @staticmethod
    def _rle(chars):
        """Run-length-compress a list of sixel data characters: 4+ repeats
        of the same char become `!<count><char>`, shorter runs are left
        literal (the `!count` overhead isn't worth it below ~4 repeats)."""
        out = []
        i = 0
        n = len(chars)
        while i < n:
            j = i
            while j < n and chars[j] == chars[i]:
                j += 1
            run_len = j - i
            if run_len >= 4:
                out.append(f'!{run_len}{chars[i]}')
            else:
                out.append(chars[i] * run_len)
            i = j
        return ''.join(out)


_sixel_encoder = SixelEncoder()
_SPRITE_CACHE = {}  # sprite key -> encoded sixel string (built once, lazily, on first use)


def _sprite_image(draw_fn, size=32):
    """Pillow is already an ANetBBS dependency (requirements.txt) -- used
    here purely for its ImageDraw primitives (circles/polygons), the same
    role canvas 2D context shapes play in the browser version's
    textures.js. Imported lazily so this module has no hard Pillow
    dependency at import time for callers that never render (e.g. the
    level-validation tests)."""
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (size, size), SIXEL_BG)
    draw = ImageDraw.Draw(img)
    draw_fn(draw, size)
    return img


def _draw_humanoid(draw, s, color, is_boss=False):
    cx = s / 2
    outline = (10, 8, 8)
    draw.rectangle([cx - s * 0.12, s * 0.7, cx - s * 0.02, s * 0.95], fill=(35, 32, 37), outline=outline)
    draw.rectangle([cx + s * 0.02, s * 0.7, cx + s * 0.12, s * 0.95], fill=(35, 32, 37), outline=outline)
    draw.polygon([(cx - s * 0.22, s * 0.38), (cx + s * 0.22, s * 0.38),
                  (cx + s * 0.17, s * 0.72), (cx - s * 0.17, s * 0.72)], fill=color, outline=outline)
    head_r = s * 0.16 * (1.3 if is_boss else 1.0)
    draw.ellipse([cx - head_r, s * 0.24 - head_r, cx + head_r, s * 0.24 + head_r],
                 fill=(232, 185, 138) if not is_boss else (255, 223, 138), outline=outline)
    if is_boss:
        eye_color = (255, 90, 90)
        draw.ellipse([cx - s * 0.08, s * 0.2, cx - s * 0.03, s * 0.25], fill=eye_color)
        draw.ellipse([cx + s * 0.03, s * 0.2, cx + s * 0.08, s * 0.25], fill=eye_color)


def _draw_drone(draw, s, color):
    cx, cy = s / 2, s / 2
    draw.ellipse([cx - s * 0.22, cy - s * 0.16, cx + s * 0.22, cy + s * 0.16], fill=(44, 44, 44), outline=(10, 8, 8))
    draw.ellipse([cx - s * 0.42, cy - s * 0.06, cx - s * 0.18, cy + s * 0.06], fill=color)
    draw.ellipse([cx + s * 0.18, cy - s * 0.06, cx + s * 0.42, cy + s * 0.06], fill=color)
    draw.ellipse([cx - s * 0.05, cy - s * 0.05, cx + s * 0.05, cy + s * 0.05], fill=(255, 79, 79))


def _draw_turret(draw, s, color):
    cx = s / 2
    draw.rectangle([cx - s * 0.05, s * 0.08, cx + s * 0.05, s * 0.4], fill=(28, 28, 28))
    draw.ellipse([cx - s * 0.2, s * 0.34, cx + s * 0.2, s * 0.66], fill=color, outline=(10, 8, 8))
    draw.ellipse([cx - s * 0.08, s * 0.42, cx + s * 0.08, s * 0.58], fill=(255, 79, 79))


def _draw_barrel(draw, s):
    cx = s / 2
    draw.rectangle([cx - s * 0.28, s * 0.15, cx + s * 0.28, s * 0.92], fill=(138, 32, 32), outline=(10, 8, 8))
    draw.rectangle([cx - s * 0.28, s * 0.36, cx + s * 0.28, s * 0.44], fill=(224, 178, 61))
    draw.rectangle([cx - s * 0.28, s * 0.62, cx + s * 0.28, s * 0.70], fill=(224, 178, 61))


def _draw_pickup(draw, s, kind, color):
    cx, cy = s / 2, s / 2
    if kind == 'health':
        draw.rectangle([cx - s * 0.22, cy - s * 0.07, cx + s * 0.22, cy + s * 0.07], fill=color)
        draw.rectangle([cx - s * 0.07, cy - s * 0.22, cx + s * 0.07, cy + s * 0.22], fill=color)
    elif kind == 'armor':
        draw.polygon([(cx, cy - s * 0.26), (cx + s * 0.22, cy - s * 0.1), (cx + s * 0.16, cy + s * 0.26),
                      (cx - s * 0.16, cy + s * 0.26), (cx - s * 0.22, cy - s * 0.1)], fill=color, outline=(10, 8, 8))
    elif kind == 'ammo':
        draw.rectangle([cx - s * 0.16, cy - s * 0.22, cx + s * 0.16, cy + s * 0.22], fill=color, outline=(10, 8, 8))
    elif kind == 'weapon':
        draw.rectangle([cx - s * 0.26, cy - s * 0.06, cx + s * 0.26, cy + s * 0.06], fill=color)
        draw.rectangle([cx + s * 0.12, cy - s * 0.14, cx + s * 0.26, cy + s * 0.02], fill=color)
    elif kind == 'key':
        draw.ellipse([cx - s * 0.26, cy - s * 0.14, cx - s * 0.04, cy + s * 0.14], outline=color, width=3)
        draw.rectangle([cx - s * 0.06, cy - s * 0.04, cx + s * 0.26, cy + s * 0.04], fill=color)
    else:  # part
        draw.rectangle([cx - s * 0.22, cy - s * 0.16, cx + s * 0.22, cy + s * 0.16], outline=color, width=3)
        draw.line([(cx - s * 0.22, cy), (cx + s * 0.22, cy)], fill=color, width=2)


def get_sprite_sixel(sprite_key):
    """Lazily builds + caches the sixel-encoded sprite for `sprite_key`
    (e.g. 'enemy_scalper', 'pickup_health_small', 'barrel'). Returns None
    for an unknown key so the ANSI-glyph fallback renderer can handle it
    instead of the caller needing its own key-existence check."""
    if sprite_key in _SPRITE_CACHE:
        return _SPRITE_CACHE[sprite_key]

    img = None
    if sprite_key.startswith('enemy_'):
        etype = sprite_key[len('enemy_'):]
        edef = ENEMY_TYPES.get(etype)
        if edef:
            if etype == 'drone':
                img = _sprite_image(lambda d, s: _draw_drone(d, s, edef['color']))
            elif etype == 'turret':
                img = _sprite_image(lambda d, s: _draw_turret(d, s, edef['color']))
            else:
                img = _sprite_image(lambda d, s: _draw_humanoid(d, s, edef['color'], edef.get('is_boss', False)))
    elif sprite_key.startswith('pickup_'):
        ptype = sprite_key[len('pickup_'):]
        pdef = PICKUP_TYPES.get(ptype)
        if pdef:
            img = _sprite_image(lambda d, s: _draw_pickup(d, s, pdef['kind'], pdef['color']))
    elif sprite_key == 'barrel':
        img = _sprite_image(lambda d, s: _draw_barrel(d, s))

    if img is None:
        _SPRITE_CACHE[sprite_key] = None
        return None
    encoded = _sixel_encoder.encode(img)
    _SPRITE_CACHE[sprite_key] = encoded
    return encoded


# ═══════════════════════════════════════════════════════════════════════════
# ANSI truecolor renderer + sixel compositing. The wall/floor/ceiling layer
# is a dirty-diffed double-buffer character grid -- directly the same
# pattern anetcraft.py's Renderer class uses (only send escape sequences
# for cells that actually changed since last frame, which is what makes
# real-time terminal rendering bandwidth-viable at all). One raycast per
# VIEWPORT column, same DDA math as cast_ray() above, mapped to a colored
# block character + row-span instead of a textured pixel column.
#
# Enemies/pickups/barrels are composited on top: as small sixel raster
# images (positioned via cursor movement, drawn after the character grid)
# on sessions that report sixel support, or as a single colored ANSI glyph
# at the same screen position otherwise -- z-buffer occluded against the
# wall columns either way, so a sprite behind a wall doesn't show through.
# ═══════════════════════════════════════════════════════════════════════════

VIEWPORT_W = 78
VIEWPORT_H = 18
HEADER_ROW = 1
VIEWPORT_TOP_ROW = 2  # 1-indexed terminal row where the viewport's row 0 lands
STATUS_ROW = VIEWPORT_TOP_ROW + VIEWPORT_H  # divider/log line just below the viewport


class Renderer:
    def __init__(self, vp_w=VIEWPORT_W, vp_h=VIEWPORT_H):
        self.vp_w = vp_w
        self.vp_h = vp_h
        empty = (' ', None, None)
        self._prev = [[empty] * vp_w for _ in range(vp_h)]
        self._curr = [[empty] * vp_w for _ in range(vp_h)]
        self._dirty_full = True

    def full_redraw(self):
        self._dirty_full = True

    def invalidate_rect(self, col_start, col_end, row_start, row_end):
        """Forces cells in this rect to be re-sent next flush() even if
        their logical (char,fg,bg) content hasn't changed -- used to clear
        stale sixel pixels a sprite left behind at its previous position,
        without paying for a full-viewport redraw every frame just
        because *some* sprite is on screen (see render_sixel_sprites)."""
        sentinel = (None, None, None)
        for row in range(max(0, row_start), min(self.vp_h, row_end)):
            for col in range(max(0, col_start), min(self.vp_w, col_end)):
                self._prev[row][col] = sentinel

    def set_cell(self, col, row, char, fg, bg):
        if 0 <= col < self.vp_w and 0 <= row < self.vp_h:
            self._curr[row][col] = (char, fg, bg)

    def flush(self):
        out = []
        last_fg = last_bg = None
        for row in range(self.vp_h):
            for col in range(self.vp_w):
                cell = self._curr[row][col]
                if not self._dirty_full and cell == self._prev[row][col]:
                    last_fg = last_bg = None
                    continue
                out.append(_at(row + VIEWPORT_TOP_ROW, col + 1))
                char, fg, bg = cell
                if bg != last_bg:
                    out.append(_bg(*bg) if bg else f'{_E}[49m')
                    last_bg = bg
                if fg != last_fg:
                    out.append(_fg(*fg) if fg else f'{_E}[39m')
                    last_fg = fg
                out.append(char)
        self._dirty_full = False
        self._prev = [row[:] for row in self._curr]
        return ''.join(out)


def _dim(color, factor):
    return (int(color[0] * factor), int(color[1] * factor), int(color[2] * factor))


FLOOR_COLOR = (35, 30, 26)
CEILING_COLOR = (20, 20, 28)
FOG_COLOR = (10, 8, 10)


def _wall_color(wall_type, dist_tiles, side):
    _name, base, mortar, _glyph = WALL_TYPES[wall_type]
    color = mortar if wall_type in (DOOR_TYPE, AMMO_STATION_TYPE, LOCKED_DOOR_TYPE) else base
    # Distance fog + Y-side darkening, same idea as renderScene()'s
    # per-column shading in the browser version, just coarser (character
    # cells, not per-pixel gradients).
    fog = min(0.82, dist_tiles / 9)
    lit = _dim(color, 1 - fog)
    if side == 1:
        lit = _dim(lit, 0.8)
    return lit


def render_walls(runtime):
    """Casts one ray per viewport column, fills the Renderer's grid with
    wall/floor/ceiling cells, and returns the per-column corrected-distance
    z-buffer (in TILES, not world units) for sprite occlusion."""
    level = runtime['level']
    grid, w, h = level['grid'], level['w'], level['h']
    player = runtime['state']['player']
    rend = runtime['renderer']
    z_buffer = [MAX_DEPTH / TILE] * VIEWPORT_W

    for col in range(VIEWPORT_W):
        ray_angle = player['angle'] - HALF_FOV + (col / VIEWPORT_W) * FOV
        hit = cast_ray(grid, w, h, player['x'], player['y'], ray_angle)
        if not hit:
            for row in range(VIEWPORT_H):
                bg = CEILING_COLOR if row < VIEWPORT_H // 2 else FLOOR_COLOR
                rend.set_cell(col, row, ' ', None, bg)
            continue

        corrected_dist = max(0.1, hit.dist * math.cos(ray_angle - player['angle'])) / TILE
        z_buffer[col] = corrected_dist

        col_height = min(VIEWPORT_H, VIEWPORT_H / corrected_dist)
        draw_start = int((VIEWPORT_H - col_height) / 2)
        draw_end = int(draw_start + col_height)
        color = _wall_color(hit.wall_type, corrected_dist, hit.side)

        for row in range(VIEWPORT_H):
            if draw_start <= row < draw_end:
                glyph = wall_glyph_for_cell(hit.wall_type, corrected_dist, row, col)
                rend.set_cell(col, row, glyph, color, None)
            else:
                bg = CEILING_COLOR if row < VIEWPORT_H // 2 else FLOOR_COLOR
                rend.set_cell(col, row, ' ', None, bg)
    return z_buffer


def _screen_col_for_angle(angle_to_sprite):
    """Maps an angle offset from the player's facing direction to a
    viewport column, same projection the browser version uses for
    sprites (0.5 + angleToSprite/FOV) * width -- just VIEWPORT_W instead
    of RENDER_W pixels."""
    return (0.5 + angle_to_sprite / FOV) * VIEWPORT_W


def gather_sprites(runtime):
    """Collects enemies/pickups/barrels into a single far-to-near sorted
    list with screen column + corrected distance (tiles) precomputed,
    ready for either sixel or ANSI-glyph compositing."""
    player = runtime['state']['player']
    sprites = []
    for e in runtime['enemies']:
        if e['dead']:
            continue
        sprites.append({'x': e['x'], 'y': e['y'], 'kind': 'enemy', 'ref': e,
                        'sprite_key': f"enemy_{e['type']}", 'glyph_color': e['def']['color']})
    for pk in runtime['pickups']:
        if pk['collected']:
            continue
        pdef = PICKUP_TYPES[pk['type']]
        sprites.append({'x': pk['x'], 'y': pk['y'], 'kind': 'pickup', 'ref': pk,
                        'sprite_key': f"pickup_{pk['type']}", 'glyph_color': pdef['color']})
    for b in runtime['props']:
        if b['dead']:
            continue
        sprites.append({'x': b['x'], 'y': b['y'], 'kind': 'barrel', 'ref': b,
                        'sprite_key': 'barrel', 'glyph_color': (200, 80, 40)})

    for s in sprites:
        s['dist'] = math.hypot(s['x'] - player['x'], s['y'] - player['y'])
    sprites.sort(key=lambda s: -s['dist'])

    visible = []
    for s in sprites:
        dx, dy = s['x'] - player['x'], s['y'] - player['y']
        angle_to = math.atan2(dy, dx) - player['angle']
        while angle_to > math.pi:
            angle_to -= math.pi * 2
        while angle_to < -math.pi:
            angle_to += math.pi * 2
        if abs(angle_to) > HALF_FOV + 0.3:
            continue
        col = _screen_col_for_angle(angle_to)
        corrected_dist = max(0.1, s['dist'] * math.cos(angle_to)) / TILE
        s['col'] = col
        s['corrected_dist'] = corrected_dist
        visible.append(s)
    return visible


def render_glyph_sprites(runtime, sprites, z_buffer):
    """ANSI-glyph fallback for non-sixel sessions. Enemies get a real
    2-row humanoid silhouette (a head glyph over a body glyph, colored
    per enemy type) instead of one floating dot -- a single character
    doesn't read as "a person" at any distance, and that was a real
    reported gap (a player killed by something they never visually
    registered as an enemy at all). Pickups/barrels stay single-glyph
    since they're not the thing players need to instantly recognize as
    a threat."""
    rend = runtime['renderer']
    glyph_by_kind = {'pickup': '*', 'barrel': 'O'}
    for s in sprites:
        col = int(s['col'])
        if col < 0 or col >= VIEWPORT_W:
            continue
        if s['corrected_dist'] >= z_buffer[col]:
            continue  # behind a wall
        center_row = VIEWPORT_H // 2
        if s['kind'] == 'enemy':
            edef = s['ref']['def']
            head_color = (255, 224, 138) if edef.get('is_boss') else (232, 185, 138)
            head_glyph = '@' if edef.get('is_boss') else 'o'
            # A close enemy (large on-screen presence) gets a taller
            # silhouette (head + body + legs) than a distant one (just
            # head + body), matching how the sixel path scales with
            # distance instead of every enemy being an identical dot.
            tall = s['corrected_dist'] < 3
            if tall and 0 <= center_row + 1 < VIEWPORT_H:
                rend.set_cell(col, center_row - 1, head_glyph, head_color, None)
                rend.set_cell(col, center_row, '¥', s['glyph_color'], None)
                rend.set_cell(col, center_row + 1, '║', s['glyph_color'], None)
            else:
                rend.set_cell(col, center_row - 1 if center_row > 0 else center_row, head_glyph, head_color, None)
                rend.set_cell(col, center_row, '¥', s['glyph_color'], None)
        else:
            glyph = glyph_by_kind.get(s['kind'], '?')
            rend.set_cell(col, center_row, glyph, s['glyph_color'], None)


def render_sixel_sprites(runtime, sprites, z_buffer):
    """Sixel overlay for sixel-capable sessions. Returns the raw escape
    sequence STRING to append after the character grid's flush() output
    (sixel positioning uses direct cursor moves, independent of the
    Renderer's own cell-diffing, since these aren't character cells).
    Records the screen rects painted into runtime['_last_sixel_rects'] --
    the CALLER is responsible for invalidating those rects before NEXT
    frame's render_walls()/flush() (see render_frame() below), so a
    sprite's previous position doesn't leave a stale pixel "ghost" behind
    once it's moved (a sixel image bypasses the Renderer's own
    dirty-diffing entirely, so nothing else would ever force that cell to
    resend on its own)."""
    out = []
    new_rects = []
    for s in sprites:
        col = int(s['col'])
        if col < 0 or col >= VIEWPORT_W:
            continue
        if s['corrected_dist'] >= z_buffer[col]:
            continue
        encoded = get_sprite_sixel(s['sprite_key'])
        if not encoded:
            continue
        # Larger + a higher floor than the original formula -- reported
        # live that enemies were unreadable/invisible-feeling at combat
        # range. Bumped both the divisor (bigger at any given distance)
        # and the minimum (a far-off enemy is still at least 2 cells
        # tall, not a single pixel-sized speck).
        scale = max(2, min(7, int(9 / max(1.0, s['corrected_dist']))))
        row = max(0, VIEWPORT_H // 2 - scale // 2)
        term_col = max(1, col - scale // 2 + 1)
        out.append(_at(row + VIEWPORT_TOP_ROW, term_col))
        out.append(encoded)
        new_rects.append((term_col - 1, term_col - 1 + scale, row, row + scale))

    runtime['_last_sixel_rects'] = new_rects
    return ''.join(out)


MINIMAP_RADIUS = 4  # cells in each direction from the player -- a 9x9 block
MINIMAP_WALL_COLOR = (110, 120, 128)
MINIMAP_FLOOR_BG = (8, 8, 12)
MINIMAP_ENEMY_COLOR = (224, 96, 61)
MINIMAP_BOSS_COLOR = (255, 220, 60)
MINIMAP_PLAYER_COLOR = (95, 174, 110)


def render_minimap(runtime):
    """Small top-down overlay in the viewport's top-right corner, drawn
    LAST (on top of walls and, for the glyph path, sprites too) so it's
    never obscured. This is the terminal port's single biggest missing
    piece of situational awareness compared to the browser version: the
    raycast view only shows a narrow forward-facing FOV slice with no
    peripheral vision at all, and enemies attack based on line-of-sight/
    distance, NOT on whether they're inside that FOV -- without this,
    something approaching from the side or behind is completely invisible
    right up until it lands a hit, with zero warning. Reported live: a
    player was killed without ever seeing what killed them."""
    rend = runtime['renderer']
    level = runtime['level']
    grid, w, h = level['grid'], level['w'], level['h']
    player = runtime['state']['player']
    origin_gx, origin_gy = int(player['x'] / TILE), int(player['y'] / TILE)

    size = MINIMAP_RADIUS * 2 + 1
    anchor_col = VIEWPORT_W - size
    anchor_row = 0
    if anchor_col < 0:
        return  # viewport too narrow to fit a minimap, skip rather than crash

    for dy in range(-MINIMAP_RADIUS, MINIMAP_RADIUS + 1):
        for dx in range(-MINIMAP_RADIUS, MINIMAP_RADIUS + 1):
            gx, gy = origin_gx + dx, origin_gy + dy
            col, row = anchor_col + dx + MINIMAP_RADIUS, anchor_row + dy + MINIMAP_RADIUS
            if 0 <= gx < w and 0 <= gy < h and grid[gy][gx] != 0:
                rend.set_cell(col, row, '#', MINIMAP_WALL_COLOR, MINIMAP_FLOOR_BG)
            else:
                rend.set_cell(col, row, ' ', None, MINIMAP_FLOOR_BG)

    # Player marker + a facing tick so "which way am I looking" is legible
    # at a glance, matching the browser minimap's rotated arrow. Drawn
    # BEFORE enemies so a nearby enemy that happens to land on the same
    # cell as the facing tick (a real one-cell-radius coincidence caught
    # in testing -- an enemy directly in the player's own facing
    # direction, one tile away) always wins and stays visible; a lost
    # facing tick is a minor cosmetic loss, a lost enemy marker defeats
    # the entire point of this overlay.
    rend.set_cell(anchor_col + MINIMAP_RADIUS, anchor_row + MINIMAP_RADIUS, '@', MINIMAP_PLAYER_COLOR, MINIMAP_FLOOR_BG)
    face_dx = round(math.cos(player['angle']))
    face_dy = round(math.sin(player['angle']))
    fc, fr = anchor_col + MINIMAP_RADIUS + face_dx, anchor_row + MINIMAP_RADIUS + face_dy
    if 0 <= fc < VIEWPORT_W and 0 <= fr < VIEWPORT_H and (face_dx, face_dy) != (0, 0):
        rend.set_cell(fc, fr, '^', MINIMAP_PLAYER_COLOR, MINIMAP_FLOOR_BG)

    for e in runtime['enemies']:
        if e['dead']:
            continue
        egx, egy = int(e['x'] / TILE), int(e['y'] / TILE)
        dx, dy = egx - origin_gx, egy - origin_gy
        if abs(dx) > MINIMAP_RADIUS or abs(dy) > MINIMAP_RADIUS:
            continue
        col, row = anchor_col + dx + MINIMAP_RADIUS, anchor_row + dy + MINIMAP_RADIUS
        color = MINIMAP_BOSS_COLOR if e['def'].get('is_boss') else MINIMAP_ENEMY_COLOR
        rend.set_cell(col, row, '*', color, MINIMAP_FLOOR_BG)


def render_frame(runtime, sixel_capable):
    """Top-level per-tick render: wall/floor/ceiling grid (dirty-diffed)
    plus sprite compositing, returning the complete ANSI/sixel string for
    this frame's viewport. Ordering matters here:
      1. Invalidate whatever screen rects LAST frame's sixel sprites
         painted into, BEFORE this frame's wall pass/flush -- so those
         cells are guaranteed to resend even if the wall content there
         happens to be identical to last frame (see render_sixel_sprites).
      2. Cast all wall columns into the grid.
      3. Glyph-sprite path (non-sixel sessions) sets its cells into the
         grid BEFORE flush(), so they're included in this same frame's
         diffed output. Sixel-sprite path instead flushes the walls
         first, then appends its own cursor-positioned escape sequences
         afterward, since sixel images aren't part of the character grid
         at all.
    """
    rend = runtime['renderer']
    for rect in runtime.get('_last_sixel_rects', []):
        rend.invalidate_rect(*rect)
    if not sixel_capable:
        runtime['_last_sixel_rects'] = []

    z_buffer = render_walls(runtime)
    sprites = gather_sprites(runtime)

    if sixel_capable:
        render_minimap(runtime)  # character-grid overlay either way -- sixel sprites are a separate cursor-write pass, not part of this grid at all
        wall_ansi = rend.flush()
        sprite_ansi = render_sixel_sprites(runtime, sprites, z_buffer)
        return wall_ansi + sprite_ansi
    else:
        render_glyph_sprites(runtime, sprites, z_buffer)
        render_minimap(runtime)  # drawn last so it's never covered by a glyph sprite
        return rend.flush()


# ═══════════════════════════════════════════════════════════════════════════
# Save/load -- one save slot per player (simpler than the browser version's
# 3-slot system, matching ANetCRAFT's own single-save-per-player
# convention for terminal door games). Migration-safe merge for ammo/
# weapons, same reasoning as the browser version's state.js: a save from
# before a since-added ammo type or starter weapon existed must not
# silently lose it when loaded.
# ═══════════════════════════════════════════════════════════════════════════

SAVE_DIR = Path(__file__).parent.parent.parent / 'data' / 'doors' / 'darkforces'


def _save_path(username):
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = ''.join(c for c in username if c.isalnum() or c in '-_') or 'player'
    return SAVE_DIR / f'{safe_name}.json'


def new_game_state():
    return {
        'level_index': 0,
        'player': copy.deepcopy(PLAYER_START_STATE),
        'parts_total': 0, 'kills': 0, 'deaths': 0,
    }


def serialize_state(state):
    return json.dumps({
        'level_index': state['level_index'], 'player': state['player'],
        'parts_total': state['parts_total'], 'kills': state['kills'], 'deaths': state['deaths'],
    })


def deserialize_state(raw_json):
    data = json.loads(raw_json)
    state = new_game_state()
    state['level_index'] = max(0, min(len(LEVELS) - 1, data.get('level_index', 0)))
    fresh_player = copy.deepcopy(PLAYER_START_STATE)
    incoming_player = data.get('player') or {}
    state['player'] = {**fresh_player, **incoming_player}
    state['player']['ammo'] = {**fresh_player['ammo'], **(incoming_player.get('ammo') or {})}
    incoming_weapons = incoming_player.get('weapons')
    if isinstance(incoming_weapons, list):
        state['player']['weapons'] = list(dict.fromkeys(fresh_player['weapons'] + incoming_weapons))
    else:
        state['player']['weapons'] = fresh_player['weapons']
    state['parts_total'] = data.get('parts_total', 0)
    state['kills'] = data.get('kills', 0)
    state['deaths'] = data.get('deaths', 0)
    return state


def save_game(state, username):
    try:
        _save_path(username).write_text(serialize_state(state))
        return True
    except Exception:
        logger.exception('darkforces_term: save failed for %s', username)
        return False


def load_game(username):
    path = _save_path(username)
    if not path.exists():
        return None
    try:
        return deserialize_state(path.read_text())
    except Exception:
        logger.exception('darkforces_term: load failed for %s', username)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Sixel capability detection -- adapted from BBSMenuUI._detect_sixel_support
# (features/bbs_ui.py) for a raw BBSSession (builtin_python games receive
# the plain session, not the full menu-UI wrapper). Same DA1 + SyncTerm/
# CTerm CTDA-quirk handling, same session.user sixel_mode preference
# honored first -- but WITHOUT that version's img2sixel-installed gate,
# since this game's sixel path never shells out to img2sixel at all (see
# SixelEncoder above).
# ═══════════════════════════════════════════════════════════════════════════

async def _read_escape_response(session, terminator=b'c', timeout_total=1.5, timeout_per_read=0.3):
    buf = b''
    try:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_total
        while loop.time() < deadline:
            remaining = deadline - loop.time()
            ch = await asyncio.wait_for(session.reader.read(1), timeout=min(timeout_per_read, remaining))
            if not ch:
                break
            buf += ch
            if ch == terminator:
                break
    except Exception:
        pass
    return buf.decode('latin-1', errors='replace')


async def detect_sixel_support(session):
    if hasattr(session, '_sixel_ok'):
        return session._sixel_ok

    mode = 'auto'
    uid = (session.user or {}).get('id')
    if uid is not None:
        try:
            from ..web_app import create_app
            from ..config import get_config
            from ..models import User
            app = create_app(os.environ.get('FLASK_ENV', 'production'))
            with app.app_context():
                u = User.query.get(uid)
                if u is not None:
                    mode = u.sixel_mode or 'auto'
        except Exception:
            pass

    if mode == 'forced_off':
        session._sixel_ok = False
        return False
    if mode == 'forced_on':
        session._sixel_ok = True
        return True

    import re
    await session.write('\x1b[0c')
    resp = await _read_escape_response(session, timeout_total=1.5)
    if '=67;84;101;114;109' in resp:  # SyncTerm/CTerm signature ("CTerm" in decimal ASCII)
        await session.write('\x1b[<0c')
        resp = await _read_escape_response(session, timeout_total=1.0)
    session._sixel_ok = bool(re.search(r'[;?]4[;c]', resp))
    return session._sixel_ok


# ═══════════════════════════════════════════════════════════════════════════
# Key input -- direct port of anetcraft.py's _Keys incremental
# escape-sequence parser (same CSI table, same feed/next contract).
# ═══════════════════════════════════════════════════════════════════════════

class Keys:
    _CSI = {b'A': 'UP', b'B': 'DOWN', b'C': 'RIGHT', b'D': 'LEFT',
            b'H': 'HOME', b'F': 'END',
            b'1~': 'HOME', b'4~': 'END', b'7~': 'HOME', b'8~': 'END',
            b'3~': 'DEL', b'5~': 'PGUP', b'6~': 'PGDN'}

    def __init__(self):
        self._buf = b''
        self._q = []

    def feed(self, data):
        self._buf += data
        self._parse()

    def _parse(self):
        while self._buf:
            b = self._buf
            if b[0:1] != b'\x1b':
                self._q.append(b[0:1].decode('latin-1', errors='replace'))
                self._buf = b[1:]
                continue
            if len(b) < 2:
                break
            if b[1:2] == b'[' or b[1:2] == b'O':
                end = 2
                while end < len(b) and b[end:end + 1] in b'0123456789;':
                    end += 1
                if end >= len(b):
                    break
                end += 1
                seq = b[2:end]
                key = self._CSI.get(seq) or self._CSI.get(seq[:-1] + b'~') or f'ESC{seq.decode("latin-1")}'
                self._q.append(key)
                self._buf = b[end:]
            else:
                self._q.append('ESC')
                self._buf = b[1:]

    def next(self):
        return self._q.pop(0) if self._q else None


# ═══════════════════════════════════════════════════════════════════════════
# Main game loop -- ANetCraft's exact real-time pattern: TICK-paced,
# non-blocking read_raw()-with-timeout for input, discrete-per-tick
# movement (a real terminal has no key-up event, unlike the browser
# version's held-WASD model -- terminal auto-repeat while a key is
# physically held arrives as a burst of repeated bytes within a tick's
# read window, which "was this key seen at all this tick" treats as
# equivalent to "currently held," matching the movement feel closely
# enough at TICK=0.08s that the distinction isn't perceptible in play).
# ═══════════════════════════════════════════════════════════════════════════

MOVE_FORWARD_KEYS = {'w', 'W', 'UP'}
MOVE_BACK_KEYS = {'s', 'S', 'DOWN'}
STRAFE_LEFT_KEYS = {'a', 'A'}
STRAFE_RIGHT_KEYS = {'d', 'D'}
TURN_LEFT_KEYS = {'LEFT'}
TURN_RIGHT_KEYS = {'RIGHT'}


def load_level(runtime, index, username):
    level = LEVELS[index]
    runtime['level'] = level
    runtime['state']['level_index'] = index
    runtime['enemies'] = [spawn_enemy(e['type'], *grid_to_world(e['gx'], e['gy'])) for e in level['enemies']]
    runtime['pickups'] = [spawn_pickup(p['type'], *grid_to_world(p['gx'], p['gy'])) for p in level['pickups']]
    runtime['props'] = [spawn_barrel(*grid_to_world(b['gx'], b['gy'])) for b in level.get('barrels', [])]
    runtime['projectiles'] = []
    runtime['ammo_station_cooldowns'] = {}
    runtime['boss_defeated'] = not any(e['def'].get('is_boss') for e in runtime['enemies'])
    runtime['player_dead'] = False
    runtime['parts_this_level'] = 0
    runtime['hurt_flash_timer'] = 0
    runtime['muzzle_flash_timer'] = 0
    runtime['last_locked_msg_time'] = -999
    runtime['renderer'] = Renderer()
    runtime['_last_sixel_rects'] = []

    start = level['player_start']
    p = runtime['state']['player']
    p['x'], p['y'] = grid_to_world(start['gx'], start['gy'])
    p['angle'] = start['angle']


def respawn_player(runtime, username):
    runtime['state']['player']['hp'] = runtime['state']['player']['max_hp']
    load_level(runtime, runtime['state']['level_index'], username)


def apply_movement(runtime, dt, seen_keys):
    p = runtime['state']['player']
    forward = (1 if seen_keys & MOVE_FORWARD_KEYS else 0) - (1 if seen_keys & MOVE_BACK_KEYS else 0)
    strafe = (1 if seen_keys & STRAFE_RIGHT_KEYS else 0) - (1 if seen_keys & STRAFE_LEFT_KEYS else 0)
    if seen_keys & TURN_LEFT_KEYS:
        p['angle'] -= TURN_SPEED * dt
    if seen_keys & TURN_RIGHT_KEYS:
        p['angle'] += TURN_SPEED * dt

    if forward or strafe:
        length = math.hypot(forward, strafe) or 1
        forward, strafe = forward / length, strafe / length
        fx, fy = math.cos(p['angle']), math.sin(p['angle'])
        sx, sy = math.cos(p['angle'] + math.pi / 2), math.sin(p['angle'] + math.pi / 2)
        dx = (fx * forward * MOVE_SPEED + sx * strafe * STRAFE_SPEED) * TILE * dt
        dy = (fy * forward * MOVE_SPEED + sy * strafe * STRAFE_SPEED) * TILE * dt
        level = runtime['level']
        nx, ny = move_with_collision(level['grid'], level['w'], level['h'], p['x'], p['y'], dx, dy, PLAYER_RADIUS)
        p['x'], p['y'] = nx, ny


class DarkForcesSession:
    """Bundles the mutable per-player state a running game needs beyond
    `runtime` itself (which sound.js/main.js's closures covered via plain
    module-level `runtime`/`keys` globals in the browser version -- kept
    as an explicit small object here instead, since a terminal door can
    have multiple concurrent players each needing their own isolated
    state, unlike a single browser tab)."""
    def __init__(self, session, username):
        self.session = session
        self.username = username
        self.keys = Keys()
        self.sixel_capable = False
        self.log_lines = []
        self.status_message = ''
        self.running = True

    def log(self, msg, kind='info'):
        self.status_message = msg
        self.log_lines.append((msg, kind))
        if len(self.log_lines) > 5:
            self.log_lines.pop(0)


LOG_COLOR = {
    'info': (200, 200, 210), 'warning': (255, 120, 120),
    'milestone': (230, 190, 80), 'harvest': (230, 95, 208),
}


def render_hud(runtime, dsession):
    p = runtime['state']['player']
    w = WEAPONS[p['current_weapon']]
    ammo_str = '∞' if not w['ammo_type'] else str(p['ammo'].get(w['ammo_type'], 0))
    level = runtime['level']
    header = (f"{BOLD}{level['name'][:28]:<28}{RST}  "
              f"HP {_fg(120,220,140)}{max(0,round(p['hp'])):>3}{RST}  "
              f"ARM {_fg(140,170,230)}{round(p['armor']):>3}{RST}  "
              f"{WEAPON_ABBR.get(w['key'], '???')} {ammo_str:>4}  "
              f"LVL{p['level']:>2}  PARTS {p['parts']:>3}")
    status_color = LOG_COLOR.get(dsession.log_lines[-1][1] if dsession.log_lines else 'info', (200, 200, 210))
    status = dsession.status_message[:VIEWPORT_W]
    return (_at(HEADER_ROW, 1) + f'{_E}[K' + header +
            _at(STATUS_ROW, 1) + f'{_E}[K' + _fg(*status_color) + status + RST)


async def run_game(session, username):
    dsession = DarkForcesSession(session, username)
    dsession.sixel_capable = await detect_sixel_support(session)

    loaded = load_game(username)
    state = loaded if loaded else new_game_state()
    runtime = {
        'state': state, 'time': 0, 'last_fire_time': -999, 'last_dry_fire_time': -999,
        'muzzle_flash_timer': 0, 'hurt_flash_timer': 0, 'ammo_station_cooldowns': {},
        'player_dead': False, 'boss_defeated': False, 'parts_this_level': 0,
        'last_locked_msg_time': -999,
        'sfx': None,  # terminal sessions have no audio channel -- silently ignored, matching a muted browser session
    }
    load_level(runtime, state['level_index'], username)

    def log_fn(msg, kind='info'):
        dsession.log(msg, kind)

    await session.write(HIDE + CLS)
    await session.write(f"{BOLD}ANetDarkForces{RST} -- Terminal Edition\r\n\r\n")
    await session.write(f"{level_intro_text(runtime['level'], state['level_index'])}\r\n\r\n")
    await session.write('Press any key to begin...')
    try:
        await session.read_raw(1)
    except Exception:
        dsession.running = False

    await session.write(CLS)
    dsession.log(f"Sector: {runtime['level']['name']}", 'milestone')

    try:
        while dsession.running:
            t0 = time.monotonic()

            try:
                raw = await asyncio.wait_for(session.read_raw(64), timeout=TICK * 0.7)
                dsession.keys.feed(raw)
            except asyncio.TimeoutError:
                pass
            except Exception:
                break

            seen_movement_keys = set()
            quit_requested = False
            while True:
                k = dsession.keys.next()
                if k is None:
                    break
                if k in MOVE_FORWARD_KEYS or k in MOVE_BACK_KEYS or k in STRAFE_LEFT_KEYS or \
                   k in STRAFE_RIGHT_KEYS or k in TURN_LEFT_KEYS or k in TURN_RIGHT_KEYS:
                    seen_movement_keys.add(k)
                elif k == ' ':
                    if not interact_with_door(runtime, log_fn) and not interact_with_ammo_station(runtime, log_fn):
                        fire_weapon(runtime, log_fn)
                elif k in ('q', 'Q'):
                    cycle_weapon(runtime, -1)
                elif k == 'e':
                    cycle_weapon(runtime, 1)
                elif k.isdigit() and k != '0':
                    n = int(k)
                    if n <= len(WEAPON_ORDER):
                        switch_weapon(runtime, WEAPON_ORDER[n - 1])
                elif k in ('l', 'L'):
                    await show_laptop_screen(session, runtime)
                    runtime['renderer'].full_redraw()
                elif k in ('p', 'P'):
                    save_game(runtime['state'], username)
                    dsession.log('Game saved.', 'milestone')
                elif k == 'ESC':
                    quit_requested = True

            if quit_requested:
                break

            apply_movement(runtime, TICK, seen_movement_keys)
            update_enemies(runtime, TICK, log_fn)
            update_projectiles(runtime, TICK, log_fn)
            update_props(runtime, TICK)
            check_pickup_collisions(runtime, log_fn)
            runtime['time'] += TICK

            if runtime['player_dead']:
                await session.write(_at(STATUS_ROW, 1) + f'{_E}[K' +
                                     _fg(255, 100, 100) + 'You went down! Respawning...' + RST)
                await asyncio.sleep(1.2)
                respawn_player(runtime, username)
                continue

            level = runtime['level']
            p = runtime['state']['player']
            exit_x, exit_y = grid_to_world(level['exit']['gx'], level['exit']['gy'])
            if math.hypot(p['x'] - exit_x, p['y'] - exit_y) < TILE * 0.6:
                if runtime['boss_defeated']:
                    save_game(runtime['state'], username)
                    next_index = state['level_index'] + 1
                    if next_index >= len(LEVELS):
                        await show_victory_screen(session, runtime)
                        break
                    else:
                        await show_level_complete_screen(session, runtime, next_index)
                        load_level(runtime, next_index, username)
                        dsession.log(f"Sector: {runtime['level']['name']}", 'milestone')
                elif runtime['time'] - runtime['last_locked_msg_time'] > 2:
                    runtime['last_locked_msg_time'] = runtime['time']
                    dsession.log('The exit is sealed. Deal with whoever is in charge first.', 'warning')

            frame = render_frame(runtime, dsession.sixel_capable)
            hud = render_hud(runtime, dsession)
            await session.write(frame + hud)

            elapsed = time.monotonic() - t0
            if elapsed < TICK:
                await asyncio.sleep(TICK - elapsed)
    finally:
        save_game(runtime['state'], username)
        await session.write(SHOW + CLS)


INTRO_TEXT_WIDTH = 76


def level_intro_text(level, index):
    # Written raw (outside the Renderer grid) to an 80-col terminal, which
    # auto-wraps overlong lines itself -- but at the raw column boundary,
    # not a word boundary, so a long single-line intro string used to come
    # out with words split mid-letter ("stockpi" / "ling"). Wrap it
    # ourselves at word boundaries first so every line the terminal
    # receives already fits.
    wrapped = '\r\n'.join(textwrap.wrap(level['intro'], INTRO_TEXT_WIDTH))
    return f"Sector {index + 1}/{len(LEVELS)}: {level['name']}\r\n\r\n{wrapped}"


async def show_level_complete_screen(session, runtime, next_index):
    p = runtime['state']['player']
    secrets = get_secrets_progress(runtime['level'])
    await session.write(CLS)
    await session.write(f"{BOLD}{runtime['level']['name']} -- Cleared{RST}\r\n\r\n")
    await session.write(f"Parts recovered this sector: {runtime['parts_this_level']}\r\n")
    await session.write(f"Sysop level: {p['level']}  Total BBS parts: {p['parts']}\r\n")
    await session.write(f"Secrets found: {secrets['found']}/{secrets['total']}\r\n\r\n")
    await session.write(f"Next: {LEVELS[next_index]['name']}\r\n\r\n")
    await session.write('Press any key to continue...')
    try:
        await session.read_raw(1)
    except Exception:
        pass
    await session.write(CLS)
    await session.write(f"{level_intro_text(LEVELS[next_index], next_index)}\r\n\r\n")
    await session.write('Press any key to begin...')
    try:
        await session.read_raw(1)
    except Exception:
        pass
    await session.write(CLS)


async def show_victory_screen(session, runtime):
    p = runtime['state']['player']
    await session.write(CLS)
    await session.write(f"{BOLD}Board Saved{RST}\r\n\r\n")
    await session.write("The Middleman is out of business. Every store on his list is\r\n"
                        "back in honest hands -- and your BBS has never had a better\r\n"
                        "parts supply.\r\n\r\n")
    await session.write(f"Final sysop level: {p['level']}\r\n")
    await session.write(f"Total BBS parts recovered: {p['parts']}\r\n")
    await session.write(f"Total kills: {runtime['state']['kills']}\r\n\r\n")
    await session.write('Press any key to exit...')
    try:
        await session.read_raw(1)
    except Exception:
        pass


async def show_laptop_screen(session, runtime):
    p = runtime['state']['player']
    secrets = get_secrets_progress(runtime['level'])
    next_xp = XP_PER_LEVEL[p['level']] if p['level'] < len(XP_PER_LEVEL) else None
    weapon_rows = []
    for k in WEAPON_ORDER:
        if k not in p['weapons']:
            continue
        w = WEAPONS[k]
        ammo = 'INF' if not w['ammo_type'] else str(p['ammo'].get(w['ammo_type'], 0))
        marker = '>' if k == p['current_weapon'] else ' '
        weapon_rows.append(f"{marker} {w['name']:.<24} {ammo}")
    keys_row = ', '.join(k.upper() for k in p['keys']) or 'none'

    await session.write(CLS)
    await session.write(f"{_fg(79,255,138)}ANetDarkForces Field Terminal v1.0{RST}\r\n")
    await session.write('-' * 44 + '\r\n')
    await session.write(f"SECTOR:    {runtime['level']['name']}\r\n")
    xp_str = f"{p['xp']}/{next_xp}" if next_xp is not None else f"{p['xp']} (MAX)"
    await session.write(f"SYSOP LVL: {p['level']}   XP: {xp_str}\r\n")
    await session.write(f"HP:        {max(0, round(p['hp']))}/{p['max_hp']}\r\n")
    await session.write(f"ARMOR:     {round(p['armor'])}/{p['max_armor']}\r\n\r\n")
    await session.write(f"BBS PARTS RECOVERED: {p['parts']}  (run total: {runtime['state']['parts_total']})\r\n")
    await session.write(f"KILLS: {runtime['state']['kills']}   DEATHS: {runtime['state']['deaths']}\r\n")
    await session.write(f"SECRETS THIS SECTOR: {secrets['found']}/{secrets['total']}\r\n")
    await session.write(f"ACCESS CARDS: {keys_row}\r\n\r\n")
    await session.write('LOADOUT:\r\n')
    for row in weapon_rows:
        await session.write(row + '\r\n')
    await session.write('\r\n[connection secure -- press any key to stow]')
    try:
        await session.read_raw(1)
    except Exception:
        pass
    await session.write(CLS)


async def launch(session, username):
    """Entry point called from the BBS menu (builtin_python game)."""
    try:
        await run_game(session, username)
    except Exception:
        logger.exception('darkforces_term: game crashed')
        try:
            await session.write(SHOW + f"\r\n{_fg(255,100,100)}The game hit an unexpected error and had to stop -- "
                                       f"your progress up to the last sector clear was saved.{RST}\r\n")
        except Exception:
            pass
