"""
ANetCRAFT - Minecraft-inspired 2D survival game for ANetBBS terminal.

Screen layout (80x24):
  Row 1      : header bar (title, day, coordinates, depth)
  Rows 2-21  : game viewport (20 rows x 78 cols = 20x78 blocks visible)
  Row 22     : divider + status messages
  Row 23     : hotbar (9 slots)
  Row 24     : crafting hint / mode indicator

Controls
--------
A / D        : move left / right          F        : mine block at cursor
W / Space    : jump                        P        : place hotbar item at cursor
S            : fast-fall (swim down)       1-9      : select hotbar slot
Arrow keys   : move mining cursor          I        : open/close inventory
Q            : quit & save                 C        : open/close craft menu
                                           Enter    : confirm craft / close menus
"""
import asyncio
import json
import math
import os
import random
import time
from pathlib import Path

# ─── ANSI helpers ─────────────────────────────────────────────────────────────

_E = '\x1b'
RST  = f'{_E}[0m'
HIDE = f'{_E}[?25l'
SHOW = f'{_E}[?25h'
CLS  = f'{_E}[2J{_E}[H'
BOLD = f'{_E}[1m'

def _fg(r, g, b):    return f'{_E}[38;2;{r};{g};{b}m'
def _bg(r, g, b):    return f'{_E}[48;2;{r};{g};{b}m'
def _at(row, col):   return f'{_E}[{row};{col}H'
def _clr_line():     return f'{_E}[2K'

# ─── Block table ──────────────────────────────────────────────────────────────
# id -> (name, char, fg_rgb or None, bg_rgb or None, hardness, drop_id, transparent)

BLK: dict[int, tuple] = {
    0:  ('air',       ' ',  None,            None,            0,   0,  True ),
    1:  ('grass',     '▀',  (55,170,55),     (110,72,35),     1,   2,  False),
    2:  ('dirt',      '▓',  (110,72,35),     (85, 54,22),     1,   2,  False),
    3:  ('stone',     '▒',  (135,135,135),   (105,105,105),   4,   4,  False),
    4:  ('cobble',    '░',  (145,145,145),   (115,115,115),   3,   4,  False),
    5:  ('log',       '║',  (160,100,40),    (120, 75,22),    2,   5,  False),
    6:  ('leaves',    '▓',  (30, 120,30),    (22,  90,22),    1,   0,  True ),
    7:  ('planks',    '░',  (200,140,60),    (165,110,40),    2,   7,  False),
    8:  ('sand',      '░',  (210,190,110),   (185,165,85),    1,   8,  False),
    9:  ('gravel',    '▓',  (155,145,135),   (125,115,105),   1,   9,  False),
    10: ('coal_ore',  '▒',  (55, 55, 55),    (105,105,105),   5,  30,  False),
    11: ('iron_ore',  '▒',  (200,155,110),   (105,105,105),   6,  31,  False),
    12: ('gold_ore',  '▒',  (255,215,40),    (105,105,105),   7,  32,  False),
    13: ('dia_ore',   '▒',  (80, 220,220),   (105,105,105),   9,  33,  False),
    14: ('water',     '≈',  (40, 100,200),   (20,  70,160),   0,   0,  True ),
    15: ('lava',      '≈',  (255,120,0),     (200, 60,  0),   0,   0,  False),
    16: ('torch',     '↑',  (255,220,60),    None,            0,  16,  True ),
    17: ('craft_tbl', '█',  (180,120,50),    (140, 90,30),    2,  17,  False),
    18: ('bedrock',   '▓',  (55, 55, 55),    (35,  35,35),  999,   0,  False),
    19: ('glass',     '░',  (200,230,255),   (170,200,220),   2,  19,  True ),
}

# Raw-material items (not placeable as blocks)
ITEMS: dict[int, str] = {
    30: 'Coal',       31: 'Iron Ingot',  32: 'Gold Ingot', 33: 'Diamond',
    50: 'Wood Pick',  51: 'Stone Pick',  52: 'Iron Pick',  53: 'Diamond Pick',
    54: 'Wood Axe',   55: 'Stone Axe',   56: 'Iron Axe',
    57: 'Wood Shovel',58: 'Stone Shovel',
    59: 'Stick',
}

def item_name(iid: int) -> str:
    if iid in ITEMS:   return ITEMS[iid]
    if iid in BLK:     return BLK[iid][0].replace('_', ' ').title()
    return '???'

def item_char(iid: int) -> str:
    if iid == 0:       return ' '
    if iid in BLK:     return BLK[iid][1]
    if iid in (50,51,52,53): return '≡'
    if iid in (54,55,56):   return '/'
    if iid in (57,58):      return '/'
    if iid == 59:           return '|'
    if iid == 30:           return '*'
    if iid == 33:           return '■'   # ■ = U+25A0 → CP437 0xFE, safe
    return '?'

# Tool speed bonus: tool_id -> {block_name: multiplier}
TOOL_SPD: dict[int, dict[str, float]] = {
    50: {'stone':2.5,'coal_ore':2.5,'cobble':2.5,'iron_ore':2},
    51: {'stone':5,  'coal_ore':5,  'cobble':5,  'iron_ore':4,'gold_ore':3},
    52: {'stone':10, 'coal_ore':10, 'cobble':10, 'iron_ore':8,'gold_ore':6,'dia_ore':3},
    53: {'stone':20, 'coal_ore':20, 'cobble':20, 'iron_ore':16,'gold_ore':12,'dia_ore':8},
    54: {'log':3,'leaves':2,'planks':3},
    55: {'log':6,'leaves':4,'planks':6},
    56: {'log':12,'leaves':8,'planks':12},
    57: {'dirt':3,'grass':3,'sand':3,'gravel':3},
    58: {'dirt':6,'grass':6,'sand':6,'gravel':6},
}

# Recipes: (result_id, result_count, [(ingredient_id, count), ...])
RECIPES: list[tuple] = [
    (7,  4, [(5,  1)]),                  # Log -> Planks
    (59, 4, [(7,  2)]),                  # Planks -> Sticks
    (50, 1, [(7,  3),(59, 2)]),          # Wood Pickaxe
    (51, 1, [(4,  3),(59, 2)]),          # Stone Pickaxe
    (52, 1, [(31, 3),(59, 2)]),          # Iron Pickaxe
    (53, 1, [(33, 3),(59, 2)]),          # Diamond Pickaxe
    (54, 1, [(7,  2),(59, 2)]),          # Wood Axe
    (55, 1, [(4,  2),(59, 2)]),          # Stone Axe
    (56, 1, [(31, 2),(59, 2)]),          # Iron Axe
    (57, 1, [(7,  1),(59, 2)]),          # Wood Shovel
    (58, 1, [(4,  1),(59, 2)]),          # Stone Shovel
    (17, 1, [(7,  4)]),                  # Crafting Table
    (16, 4, [(30, 1),(59, 1)]),          # Torch (coal + stick)
    (19, 1, [(8,  1)]),                  # Glass (sand -> glass)
]

# ─── World generation ─────────────────────────────────────────────────────────

WORLD_W  = 200
WORLD_H  = 80
SURFACE  = 26   # typical surface y
VP_W     = 78   # viewport width
VP_H     = 20   # viewport height


class World:
    def __init__(self, seed: int | None = None):
        self.seed  = seed or random.randint(0, 0xFFFFFF)
        self.data  = bytearray(WORLD_W * WORLD_H)
        self.dmg: dict[tuple, int] = {}   # (x,y) -> hit count
        self._gen()

    # ── accessors ──

    def _i(self, x, y): return y * WORLD_W + x

    def get(self, x, y) -> int:
        if y >= WORLD_H: return 18        # bedrock below
        if x < 0 or x >= WORLD_W or y < 0: return 0
        return self.data[self._i(x, y)]

    def set(self, x, y, bid):
        if 0 <= x < WORLD_W and 0 <= y < WORLD_H:
            self.data[self._i(x, y)] = bid
            self.dmg.pop((x, y), None)

    def solid(self, x, y) -> bool:
        b = self.get(x, y)
        return b != 0 and not BLK.get(b, ('',)*7)[6]

    # ── noise ──

    def _noise(self, x: float) -> float:
        ix = int(x)
        t  = x - ix
        t  = t * t * (3 - 2 * t)
        r0 = random.Random(self.seed + ix)
        r1 = random.Random(self.seed + ix + 1)
        return r0.random() * (1 - t) + r1.random() * t

    def _height(self, x: int) -> int:
        nx = x / WORLD_W
        h  = SURFACE
        h += int((self._noise(nx * 3)    - 0.5) * 10)
        h += int((self._noise(nx * 10)   - 0.5) * 4)
        h += int((self._noise(nx * 30)   - 0.5) * 2)
        return max(8, min(WORLD_H - 12, h))

    # ── generation ──

    def _gen(self):
        rng    = random.Random(self.seed)
        heights = [self._height(x) for x in range(WORLD_W)]

        for x in range(WORLD_W):
            s = heights[x]
            for y in range(WORLD_H - 3, WORLD_H):  # bedrock layer
                self.set(x, y, 18)
            for y in range(s + 4, WORLD_H - 3):     # stone
                self.set(x, y, 3)
            for y in range(s + 1, s + 4):            # dirt
                self.set(x, y, 2)
            self.set(x, s, 1)                         # grass top

        # sand patches
        for x in range(WORLD_W):
            if rng.random() < 0.04:
                s = heights[x]
                for y in range(s, s + 3):
                    if self.get(x, y) in (1, 2):
                        self.set(x, y, 8)

        # ores
        def ore_vein(ore_id, veins, min_y, max_y, sz):
            for _ in range(veins):
                ox = rng.randint(0, WORLD_W - 1)
                oy = rng.randint(min_y, max_y)
                for _ in range(sz):
                    nx = ox + rng.randint(-2, 2)
                    ny = oy + rng.randint(-2, 2)
                    if self.get(nx, ny) == 3:
                        self.set(nx, ny, ore_id)

        surf_avg = SURFACE
        ore_vein(10, 45, surf_avg + 4, WORLD_H - 10, 7)   # coal
        ore_vein(11, 28, surf_avg + 8, WORLD_H - 8,  5)   # iron
        ore_vein(12, 12, surf_avg + 14, WORLD_H - 6, 3)   # gold
        ore_vein(13,  6, surf_avg + 20, WORLD_H - 4, 2)   # diamond

        # caves
        for _ in range(35):
            cx  = rng.randint(10, WORLD_W - 10)
            cy  = rng.randint(surf_avg + 4, WORLD_H - 10)
            ang = rng.uniform(0, math.tau)
            r   = rng.randint(2, 4)
            for i in range(rng.randint(12, 50)):
                for dy in range(-r, r + 1):
                    for dx in range(-r, r + 1):
                        if dx * dx + dy * dy <= r * r:
                            bx = cx + dx + int(math.cos(ang) * i)
                            by = cy + dy + int(math.sin(ang) * i * 0.5)
                            if self.get(bx, by) not in (0, 18):
                                self.set(bx, by, 0)
                ang += rng.uniform(-0.4, 0.4)

        # trees
        x = 4
        while x < WORLD_W - 5:
            s = heights[x]
            if self.get(x, s) == 1 and rng.random() < 0.6:
                h = rng.randint(4, 7)
                for ty in range(s - h, s):
                    self.set(x, ty, 5)           # trunk
                for dy in range(-2, 2):           # leaf canopy
                    for dx in range(-2, 3):
                        lx, ly = x + dx, s - h + dy
                        if abs(dx) + abs(dy) <= 3 and self.get(lx, ly) == 0:
                            self.set(lx, ly, 6)
                self.set(x, s, 2)                 # replace grass under trunk
            x += rng.randint(6, 14)

        # water pools
        for x in range(15, WORLD_W - 15, rng.randint(20, 45)):
            s = heights[x]
            wd = rng.randint(5, 10)
            dp = rng.randint(3, 5)
            for wx in range(x - wd // 2, x + wd // 2):
                if 0 <= wx < WORLD_W:
                    for wy in range(s + 1, s + dp):
                        if self.get(wx, wy) in (1, 2):
                            self.set(wx, wy, 0)
                    if self.get(wx, s) == 1:
                        self.set(wx, s, 14)

    # ── serialisation ──

    def to_dict(self) -> dict:
        return {'seed': self.seed, 'data': list(self.data),
                'dmg': {f'{k[0]},{k[1]}': v for k, v in self.dmg.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> 'World':
        w = cls.__new__(cls)
        w.seed = d['seed']
        w.data = bytearray(d['data'])
        w.dmg  = {(int(k.split(',')[0]), int(k.split(',')[1])): v
                  for k, v in d.get('dmg', {}).items()}
        return w


# ─── Player ───────────────────────────────────────────────────────────────────

MAX_HP   = 20
HOTBAR_N = 9
INV_ROWS = 3
INV_COLS = 9


class Player:
    def __init__(self, x: float, y: float):
        self.x   = x
        self.y   = y          # feet block y
        self.vx  = 0.0
        self.vy  = 0.0
        self.on_ground = False
        self.in_water  = False
        self.hp  = MAX_HP
        self.facing    = 1    # +1 right, -1 left
        self.hot  = [[0, 0] for _ in range(HOTBAR_N)]    # [item_id, count]
        self.inv  = [[[0, 0] for _ in range(INV_COLS)] for _ in range(INV_ROWS)]
        self.hsel = 0         # hotbar selected slot
        self.reach = 5

    def bx(self): return int(self.x)
    def by(self): return int(self.y)
    def held(self): return self.hot[self.hsel][0]

    def _all_slots(self):
        yield from self.hot
        for row in self.inv:
            yield from row

    def count(self, iid: int) -> int:
        return sum(s[1] for s in self._all_slots() if s[0] == iid)

    def add(self, iid: int, n: int = 1) -> int:
        for s in self._all_slots():
            if s[0] == iid and s[1] < 64:
                give = min(n, 64 - s[1]); s[1] += give; n -= give
                if n == 0: return 0
        for s in self._all_slots():
            if s[0] == 0:
                s[0] = iid; s[1] = n; return 0
        return n

    def remove(self, iid: int, n: int = 1) -> bool:
        if self.count(iid) < n: return False
        for s in self._all_slots():
            if s[0] == iid:
                take = min(n, s[1]); s[1] -= take; n -= take
                if s[1] == 0: s[0] = 0
                if n == 0: return True
        return True

    def to_dict(self):
        return {'x': self.x, 'y': self.y, 'vx': self.vx, 'vy': self.vy,
                'hp': self.hp, 'facing': self.facing, 'hsel': self.hsel,
                'hot': self.hot, 'inv': self.inv}

    @classmethod
    def from_dict(cls, d):
        p = cls(d['x'], d['y'])
        for k in ('vx','vy','hp','facing','hsel'): setattr(p, k, d[k])
        p.hot = d['hot']; p.inv = d['inv']
        return p


# ─── Key parser ───────────────────────────────────────────────────────────────

class _Keys:
    """Incremental escape-sequence parser, adapted from anedit.py."""
    _CSI = {b'A':'UP', b'B':'DOWN', b'C':'RIGHT', b'D':'LEFT',
            b'H':'HOME', b'F':'END',
            b'1~':'HOME', b'4~':'END', b'7~':'HOME', b'8~':'END',
            b'3~':'DEL', b'5~':'PGUP', b'6~':'PGDN'}

    def __init__(self):
        self._buf: bytes = b''
        self._q:   list  = []

    def feed(self, data: bytes):
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
                while end < len(b) and b[end:end+1] in b'0123456789;':
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


# ─── Renderer ─────────────────────────────────────────────────────────────────

# Sky colours indexed by time-of-day (0.0 - 1.0)
def _sky(t: float) -> tuple:
    if t < 0.5:    # daytime
        v = min(1.0, t * 5)
        return (int(30 + v * 80), int(100 + v * 100), int(150 + v * 105))
    else:          # night
        v = min(1.0, (1.0 - t) * 5)
        return (int(5 + v * 25), int(10 + v * 40), int(30 + v * 80))


class Renderer:
    def __init__(self, vp_w: int, vp_h: int):
        self.vp_w = vp_w
        self.vp_h = vp_h
        # Double buffer: (char, fg, bg) per cell; fg/bg are rgb tuples or None
        empty = (' ', None, None)
        self._prev = [[empty] * vp_w for _ in range(vp_h)]
        self._curr = [[empty] * vp_w for _ in range(vp_h)]
        self._dirty_full = True   # force full redraw on first frame

    def full_redraw(self):
        self._dirty_full = True

    def set_cell(self, col: int, row: int, char: str, fg, bg):
        if 0 <= col < self.vp_w and 0 <= row < self.vp_h:
            self._curr[row][col] = (char, fg, bg)

    def flush(self) -> str:
        out = []
        last_fg = last_bg = None
        for row in range(self.vp_h):
            for col in range(self.vp_w):
                cell = self._curr[row][col]
                if not self._dirty_full and cell == self._prev[row][col]:
                    last_fg = last_bg = None   # reset run tracking on skip
                    continue
                out.append(_at(row + 2, col + 1))
                char, fg, bg = cell
                if bg != last_bg:
                    out.append(_bg(*bg) if bg else f'{_E}[49m')
                    last_bg = bg
                if fg != last_fg:
                    out.append(_fg(*fg) if fg else f'{_E}[39m')
                    last_fg = fg
                out.append(char)
        self._dirty_full = False
        # swap buffers
        self._prev = [row[:] for row in self._curr]
        return ''.join(out)


# ─── Game ─────────────────────────────────────────────────────────────────────

SAVE_DIR = Path(__file__).parent.parent.parent / 'data' / 'doors' / 'anetcraft'
TICK     = 0.08    # seconds per frame (~12 fps)
GRAV     = 0.35
JUMP_V   = -2.8
MOVE_SPD = 0.35
SWIM_SPD = 0.18
DAY_TICK = 250     # ticks per full day


class ANetCraft:
    def __init__(self, session, username: str):
        self.session  = session
        self.username = username
        self.world    = None
        self.player   = None
        self.cam_x    = 0
        self.cam_y    = 0
        self.cur_x    = 0      # mining cursor x
        self.cur_y    = 0
        self.running  = True
        self.tick     = 0
        self.msgs: list[list] = []           # [[text, expire_tick], ...]
        self.mode     = 'game'               # 'game' | 'inv' | 'craft'
        self.inv_cur  = (0, 0)              # (row, col) — row=-1 means hotbar
        self.cft_cur  = 0
        self._keys    = _Keys()
        self._rend    = Renderer(VP_W, VP_H)

    # ── save / load ──────────────────────────────────────────────────────────

    def _save_path(self) -> Path:
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        return SAVE_DIR / f'{self.username}.json'

    def save(self):
        data = {'world': self.world.to_dict(), 'player': self.player.to_dict(),
                'tick': self.tick}
        self._save_path().write_text(json.dumps(data))

    def load(self) -> bool:
        p = self._save_path()
        if not p.exists(): return False
        try:
            data  = json.loads(p.read_text())
            self.world  = World.from_dict(data['world'])
            self.player = Player.from_dict(data['player'])
            self.tick   = data.get('tick', 0)
            return True
        except Exception:
            return False

    def _new_world(self):
        self.world  = World()
        surf        = self.world._height(WORLD_W // 2)
        self.player = Player(float(WORLD_W // 2), float(surf - 2))
        self.tick   = 0
        self._msg('Welcome to ANetCRAFT!  (Q=quit, C=craft, I=inv)')

    # ── helpers ──────────────────────────────────────────────────────────────

    def _msg(self, text: str):
        self.msgs.append([text, self.tick + 60])

    def _msg_text(self) -> str:
        self.msgs = [m for m in self.msgs if m[1] > self.tick]
        return self.msgs[-1][0] if self.msgs else ''

    def _day_t(self) -> float:
        return (self.tick % DAY_TICK) / DAY_TICK

    def _update_cam(self):
        px, py     = self.player.bx(), self.player.by()
        self.cam_x = max(0, min(WORLD_W - VP_W, px - VP_W // 2))
        self.cam_y = max(0, min(WORLD_H - VP_H, py - VP_H // 2))

    def _in_reach(self) -> bool:
        return (abs(self.cur_x - self.player.bx()) +
                abs(self.cur_y - self.player.by())) <= self.player.reach

    def _sync_cursor(self):
        self.cur_x = self.player.bx() + self.player.facing
        self.cur_y = self.player.by()

    # ── physics ──────────────────────────────────────────────────────────────

    def _physics(self):
        p  = self.player
        w  = self.world
        bx, by = p.bx(), p.by()

        foot_blk  = w.get(bx, by + 1)
        head_blk  = w.get(bx, by - 1)
        body_blk  = w.get(bx, by)
        p.in_water = body_blk == 14 or foot_blk == 14

        # Gravity
        if p.in_water:
            p.vy = min(p.vy + GRAV * 0.08, 0.25)
        else:
            p.vy = min(p.vy + GRAV, 3.0)

        p.vx *= 0.65

        nx = p.x + p.vx
        ny = p.y + p.vy

        # X collision: check at head and feet; allow 1-block step-up
        inx = int(nx)
        if w.solid(inx, by) or w.solid(inx, by - 1):
            # Try stepping up 1 block if feet blocked but space above is clear
            if (p.on_ground and w.solid(inx, by)
                    and not w.solid(inx, by - 1)
                    and not w.solid(inx, by - 2)):
                ny = ny - 1.0
            else:
                nx  = p.x
                p.vx = 0

        # Y collision
        iny = int(ny)
        if p.vy >= 0:    # falling
            if w.solid(int(nx), iny + 1):
                ny     = float(iny)
                p.vy   = 0
                p.on_ground = True
            else:
                p.on_ground = False
        else:            # rising
            if w.solid(int(nx), iny - 1):
                ny   = p.y
                p.vy = 0

        p.x = max(0.0, min(float(WORLD_W - 1), nx))
        p.y = max(1.0, min(float(WORLD_H - 2), ny))

    # ── mining ───────────────────────────────────────────────────────────────

    def _mine_ticks(self, bx: int, by: int) -> int:
        bid  = self.world.get(bx, by)
        if bid == 0: return 0
        bdata = BLK.get(bid)
        if not bdata: return 9999
        hard = bdata[4]
        if hard == 0:   return 1
        if hard >= 999: return 9999
        tool = self.player.held()
        bname = bdata[0]
        mul = TOOL_SPD.get(tool, {}).get(bname, 1.0)
        return max(1, int(hard * 8 / max(0.5, mul)))

    def _mine_step(self, bx: int, by: int):
        if not (0 <= bx < WORLD_W and 0 <= by < WORLD_H): return
        bid = self.world.get(bx, by)
        if bid == 0: return
        needed = self._mine_ticks(bx, by)
        if needed >= 9999:
            self._msg("Can't mine that."); return
        self.world.dmg[(bx, by)] = self.world.dmg.get((bx, by), 0) + 1
        if self.world.dmg.get((bx, by), 0) >= needed:
            drop = BLK[bid][5]
            self.world.set(bx, by, 0)
            if drop:
                self.player.add(drop)
                self._msg(f'+1 {item_name(drop)}')

    # ── placing ──────────────────────────────────────────────────────────────

    def _place(self, bx: int, by: int):
        held = self.player.held()
        if held == 0 or held >= 30: return          # no item or non-block
        if self.world.get(bx, by) != 0: return      # occupied
        # Don't place inside player
        px, py = self.player.bx(), self.player.by()
        if bx == px and by in (py, py - 1): return
        if self.player.remove(held, 1):
            self.world.set(bx, by, held)

    # ── crafting ─────────────────────────────────────────────────────────────

    def _craft(self, idx: int):
        if idx >= len(RECIPES): return
        res, cnt, ingredients = RECIPES[idx]
        for iid, need in ingredients:
            if self.player.count(iid) < need:
                self._msg('Not enough materials!'); return
        for iid, need in ingredients:
            self.player.remove(iid, need)
        self.player.add(res, cnt)
        self._msg(f'Crafted {cnt}x {item_name(res)}!')

    # ── rendering ────────────────────────────────────────────────────────────

    def _block_cell(self, bx: int, by: int, is_cursor: bool, sky: tuple):
        bid   = self.world.get(bx, by)
        dmg   = self.world.dmg.get((bx, by), 0) if is_cursor else 0
        bdata = BLK.get(bid)

        if bid == 0:
            char = '+' if is_cursor else ' '
            fg   = (200, 200, 200) if is_cursor else None
            bg   = sky
        else:
            char = bdata[1]
            fg   = bdata[2]
            bg   = bdata[3] or sky
            if dmg > 0:
                needed = self._mine_ticks(bx, by)
                pct = dmg / max(1, needed)
                if   pct > 0.66: char = '░'
                elif pct > 0.33: char = '▒'
            if is_cursor:
                bg = (min(bg[0]+60, 255), min(bg[1]+60, 255), min(bg[2]+60, 255))

        return char, fg, bg

    def _draw_world(self):
        p   = self.player
        sky = _sky(self._day_t())

        for vy in range(VP_H):
            wy = self.cam_y + vy
            for vx in range(VP_W):
                wx = self.cam_x + vx

                # Player body — ╬ (CP437 0xCE) as stick-figure torso with arms
                if wx == p.bx() and wy == p.by():
                    bg = sky if self.world.get(wx, wy) == 0 else (80, 50, 20)
                    self._rend.set_cell(vx, vy, '╬', (100, 150, 220), bg)
                    continue
                # Player head — Ö (CP437 0x99) as face with eyes
                if wx == p.bx() and wy == p.by() - 1:
                    bg = sky if self.world.get(wx, wy) == 0 else (80, 50, 20)
                    self._rend.set_cell(vx, vy, 'Ö', (255, 215, 170), bg)
                    continue

                is_cur = (self.mode == 'game' and
                          wx == self.cur_x and wy == self.cur_y)
                char, fg, bg = self._block_cell(wx, wy, is_cur, sky)
                self._rend.set_cell(vx, vy, char, fg, bg)

    def _render_header(self) -> str:
        p      = self.player
        day    = self.tick // DAY_TICK + 1
        tod    = self._day_t()
        tod_s  = 'Day' if tod < 0.5 else 'Night'
        depth  = p.by() - self.world._height(p.bx())
        coords = f'X:{p.bx():<4} Y:{p.by():<3} Depth:{depth:+d}'
        title  = f' █ ANetCRAFT █  {tod_s} {day}  {coords} '
        pad    = VP_W + 2 - len(title)
        return (f'{_at(1,1)}{_fg(0,0,0)}{_bg(60,160,60)}'
                f'{BOLD}{title}{" " * pad}{RST}')

    def _render_ui(self) -> str:
        out = []
        p   = self.player

        # Row 22: divider + status message
        msg   = self._msg_text()
        div   = '═' * (VP_W + 2)
        out.append(f'{_at(22,1)}{_fg(80,80,80)}{div}{RST}')
        if msg:
            out.append(f'{_at(22, 3)}{_fg(255,255,100)}{msg[:74]}{RST}')

        # Row 23: health + hotbar
        hp_f = p.hp // 2
        hp_e = MAX_HP // 2 - hp_f
        # ■ = U+25A0 → CP437 byte 0xFE (safe, not a control char)
        hp_s = (_fg(220,40,40) + '■' * hp_f +
                _fg(60,20,20)  + '■' * hp_e + RST)

        hotbar = []
        for i, (iid, cnt) in enumerate(p.hot):
            sel = i == p.hsel
            bg  = _bg(180,180,50) if sel else _bg(55,55,55)
            fg  = _fg(0,0,0)      if sel else _fg(200,200,200)
            ch  = item_char(iid) if iid else ' '
            cn  = str(cnt) if cnt else ' '
            hotbar.append(f'{bg}{fg}{ch}{cn:<2}{RST}')
        hb_s = ' '.join(hotbar)

        mode_hint = ''
        if   self.mode == 'inv':   mode_hint = f' {_fg(255,200,50)}[INVENTORY]{RST}'
        elif self.mode == 'craft': mode_hint = f' {_fg(100,255,150)}[CRAFTING]{RST}'
        out.append(f'{_at(23,1)}{hp_s} {hb_s}{mode_hint}')

        # Row 24: controls hint
        ctrl = ' A/D=move W=jump S=fall Arrows=cur F=mine P=place 1-9=slot I=inv C=craft Q=quit'
        out.append(f'{_at(24,1)}{_fg(70,70,70)}{ctrl[:VP_W+2]}{RST}')

        return ''.join(out)

    def _render_inv_overlay(self) -> str:
        out = []
        ox, oy, w, h = 8, 4, 64, 13
        def box(r, c, s): out.append(f'{_at(oy+r, ox+c)}{s}')

        border_bg = _bg(30,30,50)
        box(0, 0, f'{border_bg}{_fg(150,150,255)}')
        out.append('╔' + '═'*(w-2) + '╗')
        for r in range(1, h-1):
            box(r, 0, f'{border_bg}║{" "*(w-2)}║')
        box(h-1, 0, f'{border_bg}╚' + '═'*(w-2) + '╝')

        box(1, 2, f'{_fg(200,200,255)}{BOLD}INVENTORY{RST}  '
                  f'{_fg(150,150,150)}I=close  Arrows=move  Enter=grab/drop')

        # Hotbar row
        box(3, 2, f'{_fg(200,200,150)}Hotbar:')
        rc, cc = self.inv_cur
        for i, (iid, cnt) in enumerate(self.player.hot):
            sel = rc == -1 and cc == i
            bg  = _bg(180,180,50) if sel else _bg(70,70,70)
            ch  = item_char(iid) if iid else ' '
            cn  = f'{cnt:<2}' if cnt else '  '
            box(4, 2 + i*6, f'{bg}{_fg(230,230,230)}{ch}{cn}  {RST}')

        # Inventory grid
        box(6, 2, f'{_fg(200,200,150)}Backpack:')
        for r in range(INV_ROWS):
            for c in range(INV_COLS):
                iid, cnt = self.player.inv[r][c]
                sel = rc == r and cc == c
                bg  = _bg(180,180,50) if sel else _bg(70,70,70)
                ch  = item_char(iid) if iid else ' '
                cn  = f'{cnt:<2}' if cnt else '  '
                box(7+r, 2+c*6, f'{bg}{_fg(230,230,230)}{ch}{cn}  {RST}')

        # Selected item name
        if rc == -1:
            iid, cnt = self.player.hot[cc]
        else:
            iid, cnt = self.player.inv[rc][cc]
        if iid:
            box(h-2, 2, f'{_fg(255,220,100)}{cnt}x {item_name(iid)}{" "*20}{RST}')

        return ''.join(out)

    def _render_craft_overlay(self) -> str:
        out = []
        ox, oy, w, h = 4, 3, 72, 18
        def box(r, c, s): out.append(f'{_at(oy+r, ox+c)}{s}')

        border_bg = _bg(20,40,30)
        box(0, 0, f'{border_bg}{_fg(100,255,150)}')
        out.append('╔' + '═'*(w-2) + '╗')
        for r in range(1, h-1):
            box(r, 0, f'{border_bg}║{" "*(w-2)}║')
        box(h-1, 0, f'{border_bg}╚' + '═'*(w-2) + '╝')
        box(1, 2, f'{_fg(150,255,180)}{BOLD}CRAFTING{RST}  '
                  f'{_fg(120,120,120)}C=close  Up/Down=select  Enter=craft')

        visible = h - 5
        start   = max(0, self.cft_cur - visible // 2)
        for di, i in enumerate(range(start, min(len(RECIPES), start + visible))):
            res, cnt, ingr = RECIPES[i]
            can   = all(self.player.count(iid) >= need for iid, need in ingr)
            sel   = i == self.cft_cur
            bg    = _bg(40,90,60) if sel else border_bg
            name_c = _fg(100,255,120) if can else _fg(130,130,130)
            have_c = _fg(200,200,200) if can else _fg(100,100,100)
            ingr_s = '  +  '.join(
                f'{need}x {item_name(iid)}' for iid, need in ingr)
            line   = f' {cnt}x {item_name(res):<20} <- {ingr_s}'
            line   = line[:w-4]
            box(3+di, 2, f'{bg}{name_c}{line:<{w-4}}{RST}')

        return ''.join(out)

    # ── input ────────────────────────────────────────────────────────────────

    def _handle_key(self, key: str):
        p = self.player
        # Mode-switching
        if key in ('q', 'Q'):
            self.running = False; return
        if key in ('i', 'I'):
            self.mode = 'game' if self.mode == 'inv'   else 'inv'
            self._rend.full_redraw(); return
        if key in ('c', 'C'):
            self.mode = 'game' if self.mode == 'craft' else 'craft'
            self._rend.full_redraw(); return
        if key in ('\r', '\n', 'ENTER'):
            if self.mode == 'craft':
                self._craft(self.cft_cur); return
            self.mode = 'game'; self._rend.full_redraw(); return

        if self.mode == 'inv':
            self._inv_key(key); return
        if self.mode == 'craft':
            if key == 'UP':   self.cft_cur = max(0, self.cft_cur - 1)
            if key == 'DOWN': self.cft_cur = min(len(RECIPES)-1, self.cft_cur+1)
            return

        # Game mode — A/D move player; all 4 arrows move mining cursor
        if key in ('a', 'A'):
            p.vx = -(SWIM_SPD if p.in_water else MOVE_SPD)
            p.facing = -1; self._sync_cursor()
        elif key in ('d', 'D'):
            p.vx = (SWIM_SPD if p.in_water else MOVE_SPD)
            p.facing =  1; self._sync_cursor()
        elif key in ('w', 'W', ' '):
            if p.on_ground:
                p.vy = JUMP_V; p.on_ground = False
            elif p.in_water:
                p.vy = -SWIM_SPD * 2.5
        elif key in ('s', 'S'):
            if p.in_water:
                p.vy = SWIM_SPD * 2
            else:
                p.vy = min(p.vy + 1.0, 3.0)
        elif key == 'UP':    self.cur_y -= 1
        elif key == 'DOWN':  self.cur_y += 1
        elif key == 'LEFT':  self.cur_x -= 1; p.facing = -1
        elif key == 'RIGHT': self.cur_x += 1; p.facing =  1
        elif key in ('f', 'F'):
            if self._in_reach(): self._mine_step(self.cur_x, self.cur_y)
            else: self._msg('Too far!')
        elif key in ('p', 'P'):
            if self._in_reach(): self._place(self.cur_x, self.cur_y)
            else: self._msg('Too far!')
        elif key.isdigit() and key != '0':
            p.hsel = int(key) - 1

        # Clamp cursor
        self.cur_x = max(0, min(WORLD_W - 1, self.cur_x))
        self.cur_y = max(0, min(WORLD_H - 1, self.cur_y))

    def _inv_key(self, key: str):
        rc, cc = self.inv_cur
        if key == 'UP':
            self.inv_cur = (max(-1, rc - 1), cc)
        elif key == 'DOWN':
            self.inv_cur = (min(INV_ROWS - 1, rc + 1), cc)
        elif key == 'LEFT':
            limit = HOTBAR_N - 1 if rc == -1 else INV_COLS - 1
            self.inv_cur = (rc, max(0, cc - 1))
        elif key == 'RIGHT':
            limit = HOTBAR_N - 1 if rc == -1 else INV_COLS - 1
            self.inv_cur = (rc, min(limit, cc + 1))
        elif key in ('\r', '\n', 'ENTER'):
            # Swap selected slot with hotbar selected slot
            if rc == -1:
                self.player.hsel = cc
            else:
                # Swap inv[r][c] with hotbar[hsel]
                h = self.player.hot[self.player.hsel]
                i = self.player.inv[rc][cc]
                h[:], i[:] = i[:], h[:]
                self._msg('Swapped with hotbar slot.')

    # ── main loop ────────────────────────────────────────────────────────────

    async def run(self):
        # Setup
        if not self.load():
            self._new_world()
        self._update_cam()
        self._sync_cursor()

        await self.session.write(CLS + HIDE)
        self._rend.full_redraw()

        try:
            while self.running:
                t0 = time.monotonic()

                # Read keys (non-blocking, poll window)
                try:
                    from ..core.session import CarrierLost
                    raw = await asyncio.wait_for(
                        self.session.read_raw(64), timeout=TICK * 0.7)
                    self._keys.feed(raw)
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    break

                while True:
                    k = self._keys.next()
                    if k is None: break
                    self._handle_key(k)

                # Physics (only in game mode)
                if self.mode == 'game':
                    self._physics()
                    self._update_cam()

                self.tick += 1

                # Build frame
                self._draw_world()
                world_cells = self._rend.flush()
                header  = self._render_header()
                ui      = self._render_ui()
                overlay = ''
                if self.mode == 'inv':   overlay = self._render_inv_overlay()
                elif self.mode == 'craft': overlay = self._render_craft_overlay()

                await self.session.write(header + world_cells + ui + overlay)

                # Pace to TICK interval
                elapsed = time.monotonic() - t0
                if elapsed < TICK:
                    await asyncio.sleep(TICK - elapsed)

        finally:
            self.save()
            await self.session.write(SHOW + CLS)


async def launch_anetcraft(session, username: str):
    """Entry point called from the BBS menu."""
    game = ANetCraft(session, username)
    await game.run()
