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
    11: ('iron_ore',  '▒',  (200,155,110),   (105,105,105),   6,  67,  False),
    12: ('gold_ore',  '▒',  (255,215,40),    (105,105,105),   7,  68,  False),
    13: ('dia_ore',   '▒',  (80, 220,220),   (105,105,105),   9,  33,  False),
    14: ('water',     '≈',  (40, 100,200),   (20,  70,160),   0,   0,  True ),
    15: ('lava',      '≈',  (255,120,0),     (200, 60,  0),   0,   0,  False),
    16: ('torch',     '↑',  (255,220,60),    None,            0,  16,  True ),
    17: ('craft_tbl', '█',  (180,120,50),    (140, 90,30),    2,  17,  False),
    18: ('bedrock',   '▓',  (55, 55, 55),    (35,  35,35),  999,   0,  False),
    19: ('glass',     '░',  (200,230,255),   (170,200,220),   2,  19,  True ),

    # ─── Furnace / Nether / End (fills the previously-unused 20-29 gap) ───
    20: ('furnace',       '▓', (90,90,90),    (60,60,60),    3,  20,  False),
    21: ('obsidian',      '▓', (30,10,50),    (15,5,25),    50,  21,  False),
    22: ('end_frame',     '▓', (50,180,120),  (30,120,80),  999,  0,  False),
    23: ('quartz_ore',    '▒', (230,225,215), (120,60,50),   5,  75,  False),
    24: ('netherrack',    '▓', (120,50,45),   (90,35,30),    2,  24,  False),
    25: ('soul_sand',     '▓', (90,65,50),    (65,45,35),    1,  25,  False),
    26: ('glowstone',     '▓', (255,220,120), (200,170,80),  1,  26,  False),
    27: ('end_stone',     '▓', (220,220,170), (180,180,135), 4,  27,  False),
    28: ('end_portal',    '◊', (20,10,30),    (10,5,20),     0,   0,  True ),
    29: ('nether_portal', '▓', (150,40,220),  (90,10,150),   0,   0,  True ),
}

# Light emitted by a placed block (0-15), for the lighting system.
# Lava (15) deliberately excluded: it's common enough in the Nether (whole
# lava seas visible at once) that treating every tile as an individual
# splat source is a real performance cliff in pure Python. Torch/glowstone/
# portal placements are sparse by comparison and don't have this problem.
LIGHT_SRC: dict[int, int] = {16: 13, 26: 15, 28: 8, 29: 11}

# Raw-material items (not placeable as blocks)
ITEMS: dict[int, str] = {
    30: 'Coal',        31: 'Iron Ingot',   32: 'Gold Ingot',  33: 'Diamond',
    50: 'Wood Pick',   51: 'Stone Pick',   52: 'Iron Pick',   53: 'Diamond Pick',
    54: 'Wood Axe',    55: 'Stone Axe',    56: 'Iron Axe',
    57: 'Wood Shovel', 58: 'Stone Shovel',
    59: 'Stick',
    60: 'Meat',        61: 'Bone',         62: 'Apple',
    63: 'Wood Sword',  64: 'Stone Sword',  65: 'Iron Sword',  66: 'Diamond Sword',

    # Smelting inputs / misc
    67: 'Raw Iron',     68: 'Raw Gold',     69: 'Flint',        70: 'Flint and Steel',
    71: 'Eye of Ender',  72: 'Blaze Powder', 73: 'Ender Pearl',  74: 'Blaze Rod',
    75: 'Nether Quartz', 76: 'Leather',      94: 'Gunpowder',

    # Armor (4 slots x 4 tiers)
    77: 'Leather Cap',    78: 'Leather Tunic',   79: 'Leather Pants',    80: 'Leather Boots',
    81: 'Iron Helmet',    82: 'Iron Chestplate', 83: 'Iron Leggings',    84: 'Iron Boots',
    85: 'Gold Helmet',    86: 'Gold Chestplate', 87: 'Gold Leggings',    88: 'Gold Boots',
    89: 'Diamond Helmet', 90: 'Diamond Chest',   91: 'Diamond Leggings', 92: 'Diamond Boots',

    # Endgame trophy
    93: 'Dragon Egg',
}

# item_id -> (slot 0=head/1=chest/2=legs/3=boots, defense points)
ARMOR: dict[int, tuple] = {
    77:(0,1), 78:(1,2), 79:(2,2), 80:(3,1),      # leather: 6 total
    81:(0,2), 82:(1,4), 83:(2,3), 84:(3,1),      # iron: 10 total
    85:(0,1), 86:(1,3), 87:(2,2), 88:(3,1),      # gold: 7 total
    89:(0,3), 90:(1,5), 91:(2,4), 92:(3,2),      # diamond: 14 total
}

# input item -> (output item, output count, smelt ticks needed)
SMELT: dict[int, tuple] = {67: (31, 1, 100), 68: (32, 1, 100), 8: (19, 1, 100)}

# fuel item -> burn ticks per unit consumed
FUEL: dict[int, int] = {30: 800, 5: 150, 7: 100, 59: 50}

def item_name(iid: int) -> str:
    if iid in ITEMS:   return ITEMS[iid]
    if iid in BLK:     return BLK[iid][0].replace('_', ' ').title()
    return '???'

def item_char(iid: int) -> str:
    if iid == 0:       return ' '
    if iid in BLK:     return BLK[iid][1]
    if iid in (50,51,52,53): return '≡'
    if iid in (54,55,56):   return '/'
    if iid in (57,58):          return '/'
    if iid == 59:               return '|'
    if iid in (30, 72, 75):     return '*'   # coal / blaze powder / quartz
    if iid == 33:               return '■'   # ■ = U+25A0 → CP437 0xFE, safe
    if iid == 60:               return '%'   # meat
    if iid == 61:               return '!'   # bone
    if iid == 62:               return '@'   # apple
    if iid in (63,64,65,66):    return '/'   # sword (slash)
    if iid in (67, 68):         return '*'   # raw ore
    if iid == 69:                return '^'   # flint
    if iid == 70:                return 'i'   # flint and steel
    if iid in (71, 73):          return 'o'   # eye of ender / ender pearl
    if iid == 74:                 return '|'   # blaze rod
    if iid == 76:                 return '~'   # leather
    if iid in (77, 81, 85, 89):   return '^'   # helmets
    if iid in (78, 82, 86, 90):   return '['   # chestplates
    if iid in (79, 83, 87, 91):   return 'U'   # leggings
    if iid in (80, 84, 88, 92):   return 'n'   # boots
    if iid == 93:                  return 'O'   # dragon egg
    if iid == 94:                  return '*'   # gunpowder
    return '?'

# Tool speed bonus: tool_id -> {block_name: multiplier}
TOOL_SPD: dict[int, dict[str, float]] = {
    50: {'stone':2.5,'coal_ore':2.5,'cobble':2.5,'iron_ore':2,
         'netherrack':3,'furnace':2.5,'end_stone':2},
    51: {'stone':5,  'coal_ore':5,  'cobble':5,  'iron_ore':4,'gold_ore':3,
         'netherrack':6,'furnace':5,'end_stone':4,'quartz_ore':3},
    52: {'stone':10, 'coal_ore':10, 'cobble':10, 'iron_ore':8,'gold_ore':6,'dia_ore':3,
         'netherrack':12,'furnace':10,'end_stone':8,'quartz_ore':7,'obsidian':3},
    53: {'stone':20, 'coal_ore':20, 'cobble':20, 'iron_ore':16,'gold_ore':12,'dia_ore':8,
         'netherrack':24,'furnace':20,'end_stone':16,'quartz_ore':14,'obsidian':15},
    54: {'log':3,'leaves':2,'planks':3},
    55: {'log':6,'leaves':4,'planks':6},
    56: {'log':12,'leaves':8,'planks':12},
    57: {'dirt':3,'grass':3,'sand':3,'gravel':3,'soul_sand':3},
    58: {'dirt':6,'grass':6,'sand':6,'gravel':6,'soul_sand':6},
}

# Sword bonus damage on top of base 4 HP hit
SWORD_DMG: dict[int, int] = {63: 3, 64: 5, 65: 7, 66: 11}

# Recipes: (result_id, result_count, [(ingredient_id, count), ...], needs_bench)
RECIPES: list[tuple] = [
    (7,  4, [(5,  1)],          False),  # Log -> Planks (anywhere)
    (59, 4, [(7,  2)],          False),  # Planks -> Sticks (anywhere)
    (17, 1, [(7,  4)],          False),  # Crafting Table (anywhere)
    (16, 4, [(30, 1),(59, 1)],  False),  # Torch (coal + stick, anywhere)
    (50, 1, [(7,  3),(59, 2)],  True ),  # Wood Pickaxe
    (51, 1, [(4,  3),(59, 2)],  True ),  # Stone Pickaxe
    (52, 1, [(31, 3),(59, 2)],  True ),  # Iron Pickaxe
    (53, 1, [(33, 3),(59, 2)],  True ),  # Diamond Pickaxe
    (54, 1, [(7,  2),(59, 2)],  True ),  # Wood Axe
    (55, 1, [(4,  2),(59, 2)],  True ),  # Stone Axe
    (56, 1, [(31, 2),(59, 2)],  True ),  # Iron Axe
    (57, 1, [(7,  1),(59, 2)],  True ),  # Wood Shovel
    (58, 1, [(4,  1),(59, 2)],  True ),  # Stone Shovel
    (63, 1, [(7,  2),(59, 1)],  True ),  # Wood Sword
    (64, 1, [(4,  2),(59, 1)],  True ),  # Stone Sword
    (65, 1, [(31, 2),(59, 1)],  True ),  # Iron Sword
    (66, 1, [(33, 2),(59, 1)],  True ),  # Diamond Sword

    # ─── Furnace + smelting support ────────────────────────────────────────
    (20, 1, [(4, 8)],           True ),  # Furnace (8 Cobble)

    # ─── Nether access ─────────────────────────────────────────────────────
    (70, 1, [(69, 1),(31, 1)],  True ),  # Flint and Steel

    # ─── End access ─────────────────────────────────────────────────────────
    (72, 1, [(74, 1)],          False),  # Blaze Rod -> Blaze Powder
    (71, 1, [(72, 1),(73, 1)],  True ),  # Eye of Ender
    (22, 1, [(21, 1),(71, 1)],  True ),  # End Portal Frame

    # ─── Armor: leather / iron / gold / diamond ────────────────────────────
    (77, 1, [(76, 5)],          True ),  # Leather Cap
    (78, 1, [(76, 8)],          True ),  # Leather Tunic
    (79, 1, [(76, 7)],          True ),  # Leather Pants
    (80, 1, [(76, 4)],          True ),  # Leather Boots
    (81, 1, [(31, 5)],          True ),  # Iron Helmet
    (82, 1, [(31, 8)],          True ),  # Iron Chestplate
    (83, 1, [(31, 7)],          True ),  # Iron Leggings
    (84, 1, [(31, 4)],          True ),  # Iron Boots
    (85, 1, [(32, 5)],          True ),  # Gold Helmet
    (86, 1, [(32, 8)],          True ),  # Gold Chestplate
    (87, 1, [(32, 7)],          True ),  # Gold Leggings
    (88, 1, [(32, 4)],          True ),  # Gold Boots
    (89, 1, [(33, 5)],          True ),  # Diamond Helmet
    (90, 1, [(33, 8)],          True ),  # Diamond Chestplate
    (91, 1, [(33, 7)],          True ),  # Diamond Leggings
    (92, 1, [(33, 4)],          True ),  # Diamond Boots
]

# ─── World generation ─────────────────────────────────────────────────────────

WORLD_W  = 200
WORLD_H  = 80
SURFACE  = 26   # typical surface y
VP_W     = 78   # viewport width
VP_H     = 20   # viewport height


class World:
    def __init__(self, seed: int | None = None, kind: str = 'overworld'):
        self.seed  = seed or random.randint(0, 0xFFFFFF)
        self.kind  = kind   # 'overworld' | 'nether' | 'end'
        self.data  = bytearray(WORLD_W * WORLD_H)
        self.dmg: dict[tuple, int] = {}   # (x,y) -> hit count
        self.furnaces: dict[tuple, dict] = {}   # (x,y) -> furnace state
        self._gen()

    # ── accessors ──

    def _i(self, x, y): return y * WORLD_W + x

    def get(self, x, y) -> int:
        if y >= WORLD_H: return 18        # bedrock below
        if x < 0 or x >= WORLD_W or y < 0: return 0
        return self.data[self._i(x, y)]

    def set(self, x, y, bid):
        if 0 <= x < WORLD_W and 0 <= y < WORLD_H:
            if self.data[self._i(x, y)] == 20 and bid != 20:
                self.furnaces.pop((x, y), None)
            self.data[self._i(x, y)] = bid
            self.dmg.pop((x, y), None)

    def solid(self, x, y) -> bool:
        b = self.get(x, y)
        return b != 0 and not BLK.get(b, ('',)*7)[6]

    def light_at(self, x: int, y: int, is_day: bool) -> int:
        """Combined sky + torch/glowstone light level, 0-15. Skylight only
        applies in the Overworld (Nether/End are roofed); point sources use
        linear falloff by Chebyshev distance within an 8-tile radius."""
        sky = 0
        if self.kind == 'overworld':
            open_above = True
            for yy in range(y - 1, -1, -1):
                if self.solid(x, yy):
                    open_above = False
                    break
            if open_above:
                sky = 15 if is_day else 4

        torch = 0
        R = 8
        for dy in range(-R, R + 1):
            for dx in range(-R, R + 1):
                bid = self.get(x + dx, y + dy)
                strength = LIGHT_SRC.get(bid)
                if strength is None:
                    continue
                dist = max(abs(dx), abs(dy))
                lvl = strength - dist
                if lvl > torch:
                    torch = lvl
        return max(sky, max(0, torch))

    def is_dark(self, x: int, y: int, is_day: bool) -> bool:
        return self.light_at(x, y, is_day) <= 3

    def compute_light_grid(self, cam_x: int, cam_y: int, vp_w: int, vp_h: int,
                            is_day: bool) -> list[list[int]]:
        """Precompute light levels for an entire viewport in one pass.

        `light_at()` independently re-scans an 8-tile neighbourhood for
        every query — fine in C# (compiled, called from a JIT'd render
        loop) but far too slow in pure Python at ~1560 cells/frame
        (measured ~155ms/frame, well over the 80ms tick budget, worse on
        weaker hardware like a Pi). This does the equivalent work once:
        one O(depth) scan per column for skylight, and one pass over the
        viewport+margin that *splats* each found light source's falloff
        onto nearby cells, instead of every cell re-scanning for sources.
        """
        R = 8
        grid = [[0] * vp_w for _ in range(vp_h)]

        if self.kind == 'overworld':
            sky_lvl = 15 if is_day else 4
            for col in range(vp_w):
                x = cam_x + col
                top = None
                for y in range(WORLD_H):
                    if self.solid(x, y):
                        top = y
                        break
                if top is None:
                    for row in range(vp_h):
                        grid[row][col] = sky_lvl
                else:
                    for row in range(vp_h):
                        if cam_y + row <= top:
                            grid[row][col] = sky_lvl

        x0, x1 = cam_x - R, cam_x + vp_w + R
        y0, y1 = max(0, cam_y - R), min(WORLD_H, cam_y + vp_h + R)
        for wy in range(y0, y1):
            for wx in range(x0, x1):
                strength = LIGHT_SRC.get(self.get(wx, wy))
                if strength is None:
                    continue
                gx_c, gy_c = wx - cam_x, wy - cam_y
                for gy in range(max(0, gy_c - R), min(vp_h, gy_c + R + 1)):
                    dy = abs(gy - gy_c)
                    grid_row = grid[gy]
                    for gx in range(max(0, gx_c - R), min(vp_w, gx_c + R + 1)):
                        lvl = strength - max(abs(gx - gx_c), dy)
                        if lvl > grid_row[gx]:
                            grid_row[gx] = lvl
        return grid

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
        if self.kind == 'nether':
            self._gen_nether()
        elif self.kind == 'end':
            self._gen_end()
        else:
            self._gen_overworld()

    def _gen_overworld(self):
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
        ore_vein(21,  8, WORLD_H - 8,   WORLD_H - 4, 3)   # obsidian, near bedrock

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

    def _gen_nether(self):
        """A mid-height netherrack 'ground' layer with lava seas below,
        carved caverns, scattered soul sand + glowstone, and a nether-quartz
        vein. No sky (roofed dimension) — bedrock above and below."""
        rng   = random.Random(self.seed + 7919)
        floor = WORLD_H - 30

        for x in range(WORLD_W):
            self.set(x, 0, 18)
            for y in range(1, floor):        self.set(x, y, 24)   # netherrack mass
            for y in range(floor, WORLD_H - 6): self.set(x, y, 15)   # lava sea
            for y in range(WORLD_H - 6, WORLD_H - 3): self.set(x, y, 24)
            for y in range(WORLD_H - 3, WORLD_H): self.set(x, y, 18)  # bedrock floor

        # Carve caverns through the netherrack mass so there's somewhere to walk
        for _ in range(60):
            cx  = rng.randint(10, WORLD_W - 10)
            cy  = rng.randint(6, floor - 4)
            ang = rng.uniform(0, math.tau)
            r   = rng.randint(3, 6)
            for i in range(rng.randint(15, 60)):
                for dy in range(-r, r + 1):
                    for dx in range(-r, r + 1):
                        if dx * dx + dy * dy <= r * r:
                            bx = cx + dx + int(math.cos(ang) * i)
                            by = cy + dy + int(math.sin(ang) * i * 0.5)
                            if 0 < by < floor and self.get(bx, by) != 18:
                                self.set(bx, by, 0)
                ang += rng.uniform(-0.4, 0.4)

        # Floor a walkable strip so the player has solid footing
        walk_y = floor - 8
        for x in range(5, WORLD_W - 5):
            if self.get(x, walk_y + 1) == 0:
                self.set(x, walk_y + 1, 24)
            for y in range(walk_y - 3, walk_y + 1):
                if self.get(x, y) == 24:
                    self.set(x, y, 0)

        # Soul sand patches + glowstone clusters + quartz veins
        for x in range(WORLD_W):
            if rng.random() < 0.05 and self.get(x, walk_y + 1) == 24:
                self.set(x, walk_y + 1, 25)

        def cluster(block, count, sz):
            for _ in range(count):
                ox = rng.randint(5, WORLD_W - 5)
                oy = rng.randint(4, floor - 4)
                for _ in range(sz):
                    nx = ox + rng.randint(-2, 2)
                    ny = oy + rng.randint(-2, 2)
                    if self.get(nx, ny) == 24:
                        self.set(nx, ny, block)

        cluster(26, 20, 4)   # glowstone (ceiling light source)
        cluster(23, 15, 4)   # nether quartz ore

    def _gen_end(self):
        """Floating end-stone islands over a bottomless void. Falling off
        an island is fatal — handled by the game loop's void-death check.
        A central obsidian-pillar arena hosts the dragon fight."""
        rng = random.Random(self.seed + 31337)
        for i in range(len(self.data)):
            self.data[i] = 0   # pure void by default

        cx, cy  = WORLD_W // 2, WORLD_H // 2
        main_r  = 26

        for x in range(WORLD_W):
            for y in range(WORLD_H):
                dx = x - cx
                dy = (y - cy) * 1.6
                d  = math.sqrt(dx * dx + dy * dy)
                edge = main_r + (self._noise(x / 11) - 0.5) * 8
                if d < edge:
                    self.set(x, y, 27)

        # Hollow out the underside so the island isn't a solid block
        for x in range(WORLD_W):
            top = -1
            for y in range(WORLD_H):
                if self.get(x, y) == 27:
                    top = y
                    break
            if top < 0:
                continue
            for y in range(top + 6, WORLD_H):
                if self.get(x, y) == 27:
                    self.set(x, y, 0)

        # A few smaller satellite islands
        for _ in range(5):
            ix = rng.randint(15, WORLD_W - 15)
            iy = rng.randint(15, WORLD_H - 15)
            ir = rng.randint(4, 8)
            for x in range(ix - ir, ix + ir + 1):
                for y in range(iy - ir // 2, iy + ir // 2 + 1):
                    dx = x - ix
                    dy = (y - iy) * 1.6
                    if dx * dx + dy * dy <= ir * ir:
                        self.set(x, y, 27)

        # Obsidian pillars ringing the arena (healing-crystal pillars)
        top2 = cy
        for y in range(WORLD_H):
            if self.get(cx, y) == 27:
                top2 = y
                break
        for p in range(4):
            ang = p * math.pi / 2 + math.pi / 4
            px  = cx + int(math.cos(ang) * 16)
            for y in range(top2, max(top2 - 6, -1), -1):
                self.set(px, y, 21)

    # ── serialisation ──

    def to_dict(self) -> dict:
        return {'seed': self.seed, 'kind': self.kind, 'data': list(self.data),
                'dmg': {f'{k[0]},{k[1]}': v for k, v in self.dmg.items()},
                'furnaces': {f'{k[0]},{k[1]}': v for k, v in self.furnaces.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> 'World':
        w = cls.__new__(cls)
        w.seed = d['seed']
        w.kind = d.get('kind', 'overworld')
        w.data = bytearray(d['data'])
        w.dmg  = {(int(k.split(',')[0]), int(k.split(',')[1])): v
                  for k, v in d.get('dmg', {}).items()}
        w.furnaces = {(int(k.split(',')[0]), int(k.split(',')[1])): v
                      for k, v in d.get('furnaces', {}).items()}
        return w


# ─── Player ───────────────────────────────────────────────────────────────────

MAX_HP     = 20
MAX_HUNGER = 20
FOOD_HEAL: dict[int, int] = {60: 6, 62: 3}  # item_id -> hunger restored
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
        self.hp     = MAX_HP
        self.hunger = MAX_HUNGER
        self.facing = 1    # +1 right, -1 left
        self.hot  = [[0, 0] for _ in range(HOTBAR_N)]    # [item_id, count]
        self.inv  = [[[0, 0] for _ in range(INV_COLS)] for _ in range(INV_ROWS)]
        self.hsel = 0         # hotbar selected slot
        self.reach = 5
        self.armor = [0, 0, 0, 0]   # head, chest, legs, boots — item id or 0
        self.fire_ticks = 0
        self.has_won = False

    def bx(self): return int(self.x)
    def by(self): return int(self.y)
    def held(self): return self.hot[self.hsel][0]

    def total_defense(self) -> int:
        return sum(ARMOR[i][1] for i in self.armor if i and i in ARMOR)

    def reduced_damage(self, raw: int) -> int:
        reduction = min(0.6, self.total_defense() * 0.04)
        return max(1, round(raw * (1 - reduction)))

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
                'hp': self.hp, 'hunger': self.hunger,
                'facing': self.facing, 'hsel': self.hsel,
                'hot': self.hot, 'inv': self.inv,
                'armor': self.armor, 'fire_ticks': self.fire_ticks,
                'has_won': self.has_won}

    @classmethod
    def from_dict(cls, d):
        p = cls(d['x'], d['y'])
        for k in ('vx','vy','hp','facing','hsel'): setattr(p, k, d[k])
        p.hunger = d.get('hunger', MAX_HUNGER)
        p.hot = d['hot']; p.inv = d['inv']
        p.armor = d.get('armor', [0, 0, 0, 0])
        p.fire_ticks = d.get('fire_ticks', 0)
        p.has_won = d.get('has_won', False)
        return p


# ─── Mob data ─────────────────────────────────────────────────────────────────
# type -> head_char, head_fg, body_char, body_fg, hp, dmg, speed, drops[(id,n),...], passive

MOB_DATA: dict[str, dict] = {
    'cow':     {'head': 'Ö', 'hfg': (200,140,70),  'body': '≡', 'bfg': (170,110,50),
                'hp': 10, 'dmg': 0,  'speed': 0.10, 'drops': [(60, 2), (76, 1)], 'passive': True},
    'zombie':  {'head': 'ü', 'hfg': (60, 200,60),  'body': '╬', 'bfg': (40, 140,40),
                'hp': 20, 'dmg': 2,  'speed': 0.14, 'drops': [], 'passive': False},
    'creeper': {'head': 'Ö', 'hfg': (60, 200,60),  'body': '▓', 'bfg': (30, 130,30),
                'hp': 20, 'dmg': 0,  'speed': 0.12, 'drops': [(94, 2)], 'passive': False},
    'skeleton':{'head': '°', 'hfg': (220,220,220), 'body': '╫', 'bfg': (170,170,170),
                'hp': 15, 'dmg': 2,  'speed': 0.13, 'drops': [(61, 2)], 'passive': False},

    # ─── Nether ─────────────────────────────────────────────────────────────
    'blaze':   {'head': '*', 'hfg': (255,200,40),  'body': '¥', 'bfg': (230,150,20),
                'hp': 20, 'dmg': 3,  'speed': 0.16, 'drops': [(74, 1)], 'passive': False, 'flies': True},
    'ghast':   {'head': '☺', 'hfg': (240,240,240), 'body': '☺', 'bfg': (220,220,220),
                'hp': 10, 'dmg': 4,  'speed': 0.08, 'drops': [(94, 2)], 'passive': False, 'flies': True},

    # ─── Enderman (overworld/end) ──────────────────────────────────────────
    'enderman':{'head': 'Ï', 'hfg': (20, 20, 20),   'body': '║', 'bfg': (180,60,220),
                'hp': 40, 'dmg': 5,  'speed': 0.20, 'drops': [(73, 1)], 'passive': False},

    # ─── Boss ────────────────────────────────────────────────────────────────
    'ender_dragon': {'head': 'W', 'hfg': (140,30,200), 'body': 'M', 'bfg': (90,15,140),
                'hp': 200, 'dmg': 6, 'speed': 0.22, 'drops': [(93, 1)], 'passive': False,
                'flies': True, 'boss': True},
}

MAX_MOBS = 20   # cap on total live mobs


class Mob:
    def __init__(self, mob_type: str, x: float, y: float):
        self.type      = mob_type
        self.x         = x
        self.y         = y
        self.vx        = 0.0
        self.vy        = 0.0
        self.hp        = MOB_DATA[mob_type]['hp']
        self.on_ground = False
        self.facing    = 1
        self.ai_tick   = 0    # frames until next AI decision
        self.dmg_cd    = 0    # damage cooldown (frames)
        self.fuse      = 0    # creeper countdown to explosion
        self.dead      = False
        self.tx        = x    # flying-mob wander/attack target
        self.ty        = y
        self.phase     = 0    # free-purpose state (enderman teleport cd, dragon phase)

    def bx(self): return int(self.x)
    def by(self): return int(self.y)

    def to_dict(self) -> dict:
        return {'type': self.type, 'x': self.x, 'y': self.y,
                'vx': self.vx, 'vy': self.vy, 'hp': self.hp,
                'on_ground': self.on_ground, 'facing': self.facing, 'fuse': self.fuse,
                'tx': self.tx, 'ty': self.ty, 'phase': self.phase}

    @classmethod
    def from_dict(cls, d: dict) -> 'Mob':
        m = cls(d['type'], d['x'], d['y'])
        for k in ('vx', 'vy', 'hp', 'on_ground', 'facing', 'fuse', 'tx', 'ty', 'phase'):
            if k in d: setattr(m, k, d[k])
        return m


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


def _dim(c: tuple | None, lvl: int) -> tuple | None:
    """Scale a colour by light level 0-15 (15=full brightness). Floored so
    dark cells aren't pure black — a faint silhouette stays visible."""
    if c is None:
        return None
    f = 0.06 + 0.94 * max(0, min(15, lvl)) / 15.0
    return (int(c[0] * f), int(c[1] * f), int(c[2] * f))


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

SAVE_DIR      = Path(__file__).parent.parent.parent / 'data' / 'doors' / 'anetcraft'
PLAYER_COLORS = [(220,60,60),(60,80,220),(220,190,50),(60,200,180),
                 (200,60,200),(220,140,50),(100,210,60),(180,100,220)]
_MP: dict     = {}   # module-level multiplayer shared state (all sessions share this)

def _player_color(username: str) -> tuple:
    return PLAYER_COLORS[sum(ord(c) for c in username) % len(PLAYER_COLORS)]


def _safe_username(username: str) -> str:
    """Sanitize a username for use as a save-file name component. Real
    path-traversal bug found in a full access-control audit: a username
    containing '/'/'..' spliced raw into a save filename could read/
    overwrite another player's save or write outside SAVE_DIR entirely.
    Shared by every save-path helper below -- a follow-up audit found
    the single-player _save_path() had been fixed but the two
    multiplayer-inventory paths (_mp_join/_mp_leave) still built their
    filename from the raw username directly, bypassing this."""
    return ''.join(c for c in username if c.isalnum() or c in '-_') or 'player'


def _shared_save_path() -> Path:
    return SAVE_DIR / 'multiplayer.json'

TICK     = 0.08    # seconds per frame (~12 fps)
GRAV     = 0.35
JUMP_V   = -2.8
MOVE_SPD = 0.35
SWIM_SPD = 0.18
DAY_TICK = 7500    # ~10 min/day at 80ms/tick (was 500 = 40s, way too fast)


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
        self.game_mode    = 'survival'   # 'survival' | 'creative'
        self._mp_mode     = False
        self._chat_pending = False
        self._mobs: list[Mob] = []
        self._keys    = _Keys()
        self._rend    = Renderer(VP_W, VP_H)

        # ── Dimension travel ──
        self.dimension    = 'overworld'   # 'overworld' | 'nether' | 'end'
        self._dim_cache: dict[str, tuple] = {}   # dim -> (world, mobs)
        self._return_dim  = 'overworld'
        self._return_x    = 0.0
        self._return_y    = 0.0
        self._was_on_portal = False
        self._portal_cd    = 0

        # ── Furnace UI ──
        self.furnace_at: tuple | None = None
        self.furnace_cur  = 0

        # ── Boss / victory ──
        self._victory_shown = False

    # ── save / load ──────────────────────────────────────────────────────────

    def _save_path(self) -> Path:
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        return SAVE_DIR / f'{_safe_username(self.username)}.json'

    def save(self):
        # Fold the active dimension into the cache so every dimension the
        # player has ever visited (Overworld/Nether/End) gets persisted,
        # not just whichever one they happen to be standing in.
        self._dim_cache[self.dimension] = (self.world, self._mobs)
        dims = [
            {'kind': kind, 'world': w.to_dict(), 'mobs': [m.to_dict() for m in mobs]}
            for kind, (w, mobs) in self._dim_cache.items()
        ]
        data = {'player': self.player.to_dict(), 'tick': self.tick,
                'game_mode': self.game_mode, 'dimension': self.dimension,
                'return_dim': self._return_dim, 'return_x': self._return_x,
                'return_y': self._return_y, 'dims': dims}
        self._save_path().write_text(json.dumps(data))

    def load(self) -> bool:
        p = self._save_path()
        if not p.exists(): return False
        try:
            data  = json.loads(p.read_text())
            self.player    = Player.from_dict(data['player'])
            self.tick      = data.get('tick', 0)
            self.game_mode = data.get('game_mode', 'survival')

            self._dim_cache = {}
            if 'dims' in data:
                for d in data['dims']:
                    w = World.from_dict(d['world'])
                    mobs = [Mob.from_dict(md) for md in d.get('mobs', [])]
                    self._dim_cache[d['kind']] = (w, mobs)
            elif 'world' in data:
                # Legacy single-dimension save format.
                w = World.from_dict(data['world'])
                mobs = [Mob.from_dict(md) for md in data.get('mobs', [])]
                self._dim_cache['overworld'] = (w, mobs)

            self.dimension   = data.get('dimension', 'overworld')
            self._return_dim = data.get('return_dim', 'overworld')
            self._return_x   = data.get('return_x', 0.0)
            self._return_y   = data.get('return_y', 0.0)

            if self.dimension not in self._dim_cache:
                return False
            self.world, self._mobs = self._dim_cache[self.dimension]
            return True
        except Exception:
            return False

    # ── multiplayer helpers ──────────────────────────────────────────────────

    def _is_mp_host(self) -> bool:
        return _MP.get('host') == self.username

    def _mp_join(self):
        shared_save = _shared_save_path()
        if not _MP:
            if shared_save.exists():
                try:
                    d = json.loads(shared_save.read_text())
                    world = World.from_dict(d['world'])
                    mobs  = [Mob.from_dict(m) for m in d.get('mobs', [])]
                    tick  = d.get('tick', 0)
                except Exception:
                    world, mobs, tick = World(), [], 0
            else:
                world, mobs, tick = World(), [], 0
            _MP['world']   = world
            _MP['mobs']    = mobs
            _MP['tick']    = tick
            _MP['players'] = {}
            _MP['chat']    = []
            _MP['host']    = self.username

        self.world      = _MP['world']
        self._mobs      = _MP['mobs']
        self.tick       = _MP['tick']
        self.game_mode  = 'survival'
        self._mp_mode   = True

        inv_path = SAVE_DIR / f'mp_{_safe_username(self.username)}.json'
        if inv_path.exists():
            try:
                self.player = Player.from_dict(json.loads(inv_path.read_text()))
            except Exception:
                surf = self.world._height(WORLD_W // 2)
                self.player = Player(float(WORLD_W // 2), float(surf - 2))
        else:
            surf = self.world._height(WORLD_W // 2)
            self.player = Player(float(WORLD_W // 2), float(surf - 2))

        _MP['players'][self.username] = self.player

    def _mp_leave(self):
        if self.username in _MP.get('players', {}):
            del _MP['players'][self.username]
        inv_path = SAVE_DIR / f'mp_{_safe_username(self.username)}.json'
        inv_path.write_text(json.dumps(self.player.to_dict()))
        if _MP.get('host') == self.username:
            remaining = list(_MP.get('players', {}).keys())
            if remaining:
                _MP['host'] = remaining[0]
            else:
                self._mp_save()
                _MP.clear()

    def _mp_save(self):
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        data = {'world': _MP['world'].to_dict(),
                'mobs':  [m.to_dict() for m in _MP['mobs']],
                'tick':  _MP['tick']}
        _shared_save_path().write_text(json.dumps(data))

    async def _mp_chat_prompt(self) -> str:
        buf = ''
        col_start = 6  # after 'Say: '
        W = _fg(255, 255, 200)
        await self.session.write(
            _at(23, 1) + _fg(100, 200, 255) + 'Say: ' + W + ' ' * 70 +
            _at(23, col_start))
        while True:
            try:
                raw = await asyncio.wait_for(self.session.read_raw(16), timeout=30.0)
            except (asyncio.TimeoutError, Exception):
                return ''
            self._keys.feed(raw)
            sent = False
            while True:
                k = self._keys.next()
                if k is None: break
                if k in ('\r', '\n'):
                    sent = True; break
                elif k in ('\x08', '\x7f'):
                    buf = buf[:-1]
                elif k == '\x1b':
                    return ''
                elif len(k) == 1 and ord(k) >= 32 and len(buf) < 64:
                    buf += k
            await self.session.write(
                _at(23, col_start) + W + buf + ' ' * (65 - len(buf)) +
                _at(23, col_start + len(buf)))
            if sent:
                break
        return buf.strip()

    def _new_world(self, mode: str = 'survival'):
        self.game_mode = mode
        self.world  = World()
        surf        = self.world._height(WORLD_W // 2)
        self.player = Player(float(WORLD_W // 2), float(surf - 2))
        self.tick   = 0
        self._mobs  = []
        if mode == 'creative':
            self._fill_creative_inv()
        else:
            self._spawn_initial_mobs()
        self._msg('Welcome to ANetCRAFT!  (Q=quit, C=craft, I=inv)')

    def _fill_creative_inv(self):
        # Fills all 36 slots: every placeable block (1-27, skipping the
        # portal trigger blocks 28/29 which are created by gameplay, not
        # hand-placed) plus a curated set of top-tier tools/armor/portal
        # items so a sysop can test or build without a full survival grind.
        p, slot = self.player, 0
        all_ids = list(range(1, 28)) + [53, 66, 70, 71, 89, 90, 91, 92, 93]
        for iid in all_ids:
            if slot < HOTBAR_N:
                p.hot[slot] = [iid, 64]
            else:
                r = (slot - HOTBAR_N) // INV_COLS
                c = (slot - HOTBAR_N) % INV_COLS
                if r < INV_ROWS:
                    p.inv[r][c] = [iid, 64]
            slot += 1

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

        if self.game_mode == 'creative':
            p.x = max(0.0, min(float(WORLD_W - 1), p.x + p.vx))
            p.y = max(1.0, min(float(WORLD_H - 2), p.y + p.vy))
            p.vx *= 0.5
            p.vy *= 0.5
            return

        bx, by = p.bx(), p.by()

        foot_blk  = w.get(bx, by + 1)
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
        if self.game_mode == 'creative':
            self.world.set(bx, by, 0)
            return
        self.world.dmg[(bx, by)] = self.world.dmg.get((bx, by), 0) + 1
        if self.world.dmg.get((bx, by), 0) >= needed:
            drop = BLK[bid][5]
            self.world.set(bx, by, 0)
            if drop:
                self.player.add(drop)
                self._msg(f'+1 {item_name(drop)}')
            # Leaves have a chance to drop an apple
            if bid == 6 and random.random() < 0.15:
                self.player.add(62, 1)
                self._msg('+1 Apple!')

    # ── placing ──────────────────────────────────────────────────────────────

    def _place(self, bx: int, by: int):
        held = self.player.held()
        if held == 0 or held >= 30: return          # no item or non-block
        if self.world.get(bx, by) != 0: return      # occupied
        # Don't place inside player
        px, py = self.player.bx(), self.player.by()
        if bx == px and by in (py, py - 1): return
        if self.game_mode == 'creative':
            self.world.set(bx, by, held)
        elif self.player.remove(held, 1):
            self.world.set(bx, by, held)

    # ── crafting ─────────────────────────────────────────────────────────────

    def _near_bench(self) -> bool:
        px, py = self.player.bx(), self.player.by()
        for dy in range(-3, 4):
            for dx in range(-4, 5):
                if self.world.get(px + dx, py + dy) == 17:
                    return True
        return False

    def _craft(self, idx: int):
        if idx >= len(RECIPES): return
        res, cnt, ingredients, needs_bench = RECIPES[idx]
        if needs_bench and not self._near_bench():
            self._msg('Need a Crafting Table nearby!'); return
        for iid, need in ingredients:
            if self.player.count(iid) < need:
                self._msg('Not enough materials!'); return
        for iid, need in ingredients:
            self.player.remove(iid, need)
        self.player.add(res, cnt)
        self._msg(f'Crafted {cnt}x {item_name(res)}!')

    def _eat(self):
        if self.game_mode == 'creative':
            return
        iid = self.player.held()
        heal = FOOD_HEAL.get(iid)
        if heal is None:
            self._msg("Can't eat that!"); return
        if self.player.hunger >= MAX_HUNGER:
            self._msg('Not hungry.'); return
        self.player.remove(iid, 1)
        self.player.hunger = min(MAX_HUNGER, self.player.hunger + heal)
        self._msg(f'Ate {item_name(iid)}. ({self.player.hunger}/{MAX_HUNGER} hunger)')

    # ── damage / death / respawn ────────────────────────────────────────────

    def _damage_player(self, raw: int, cause: str):
        if self.game_mode == 'creative':
            return
        dmg = self.player.reduced_damage(raw)
        self.player.hp = max(0, self.player.hp - dmg)
        self._msg(f'{cause} -{dmg} HP ({self.player.hp}/{MAX_HP})')
        if self.player.hp <= 0:
            self._respawn_player()

    def _respawn_player(self):
        self._msg('You died! Respawning...')
        # Always respawn in the Overworld — guarantees the player is never
        # stranded in a dimension they can't escape.
        if self.dimension != 'overworld':
            self._dim_cache[self.dimension] = (self.world, list(self._mobs))
            if 'overworld' in self._dim_cache:
                self.world, mobs = self._dim_cache['overworld']
                self._mobs = mobs
            self.dimension = 'overworld'

        surf = self.world._height(WORLD_W // 2)
        self.player.x, self.player.y = float(WORLD_W // 2), float(surf - 2)
        self.player.vx = self.player.vy = 0.0
        self.player.hp = MAX_HP // 2
        self.player.fire_ticks = 0
        self._rend.full_redraw()

    def _tick_hazards(self):
        if self.game_mode == 'creative':
            return
        p, w = self.player, self.world
        bx, by = p.bx(), p.by()
        in_lava = w.get(bx, by) == 15 or w.get(bx, by - 1) == 15
        if in_lava:
            p.fire_ticks = max(p.fire_ticks, 40)
        elif p.in_water:
            p.fire_ticks = 0

        if p.fire_ticks > 0:
            p.fire_ticks -= 1
            if p.fire_ticks % 20 == 0:
                self._damage_player(2, 'Burning!')

        # Void fall (End dimension only) — falling past the bottom with
        # nothing but air below is fatal.
        if self.dimension == 'end' and p.by() >= WORLD_H - 2:
            self._damage_player(MAX_HP, 'You fell into the void!')

    # ── dimension travel: Nether / End portals ──────────────────────────────

    def _try_ignite_portal(self, bx: int, by: int):
        """Flood-fill an obsidian-bounded air pocket and, if fully
        enclosed, fill it with Nether portal blocks."""
        w = self.world
        if w.get(bx, by) != 0:
            self._msg('Target a hollow space to ignite.'); return

        region: set = set()
        queue = [(bx, by)]
        valid = True
        while queue:
            x, y = queue.pop()
            if (x, y) in region:
                continue
            if len(region) > 40:
                valid = False; break
            bid = w.get(x, y)
            if bid == 0:
                region.add((x, y))
            elif bid == 21:
                continue   # obsidian border cell — don't enter, don't fail
            else:
                valid = False; break
            for nx, ny in ((x+1,y), (x-1,y), (x,y+1), (x,y-1)):
                if (nx, ny) not in region:
                    queue.append((nx, ny))

        if not valid or len(region) < 2:
            self._msg('Not a valid portal frame — surround a gap with Obsidian.')
            return

        for x, y in region:
            for nx, ny in ((x+1,y), (x-1,y), (x,y+1), (x,y-1)):
                if (nx, ny) in region:
                    continue
                if w.get(nx, ny) != 21:
                    self._msg("Portal frame isn't fully enclosed by Obsidian.")
                    return

        for x, y in region:
            w.set(x, y, 29)
        self.player.remove(70, 1)
        self._msg('The portal roars to life!')
        self._rend.full_redraw()

    def _try_activate_end_portal(self, cx: int, cy: int):
        w = self.world
        if w.get(cx, cy) != 0:
            self._msg('Center must be empty.'); return
        frame_count = sum(
            1 for dx, dy in ((-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1))
            if w.get(cx + dx, cy + dy) == 22)
        if frame_count < 8:
            self._msg(f'Portal frame incomplete ({frame_count}/8 placed).'); return
        w.set(cx, cy, 28)
        self._msg('The End Portal opens...')
        self._rend.full_redraw()

    def _check_portal_teleport(self):
        if self._mp_mode:
            return
        if self._portal_cd > 0:
            self._portal_cd -= 1; return
        under = self.world.get(self.player.bx(), self.player.by())
        on_portal = under in (28, 29)
        if on_portal and not self._was_on_portal:
            if under == 29:
                self._teleport_via_nether_portal()
            else:
                self._teleport_via_end_portal()
        self._was_on_portal = on_portal

    def _ensure_dim(self, kind: str) -> World:
        if kind in self._dim_cache:
            return self._dim_cache[kind][0]
        w = World(kind=kind)
        self._dim_cache[kind] = (w, [])
        return w

    def _find_safe_spawn(self, w: World) -> tuple:
        cx = WORLD_W // 2
        for y in range(1, WORLD_H - 2):
            if not w.solid(cx, y) and w.solid(cx, y + 1):
                return float(cx), float(y)
        return float(cx), float(WORLD_H // 2)

    def _place_portal_pad(self, w: World, px: int, py: int):
        for dx in (-1, 0, 1):
            w.set(px + dx, py + 2, 21)
            w.set(px + dx, py - 2, 21)
        for dy in (-1, 0, 1):
            w.set(px - 1, py + dy, 21)
            w.set(px + 1, py + dy, 21)
        for dy in (-1, 0, 1):
            w.set(px, py + dy, 29)

    def _spawn_dragon_if_absent(self):
        if any(m.type == 'ender_dragon' for m in self._mobs):
            return
        self._mobs.append(Mob('ender_dragon', float(WORLD_W // 2), float(WORLD_H // 2 - 15)))
        self._msg('The Ender Dragon awakens!')

    def _teleport_via_nether_portal(self):
        if self.dimension == 'overworld':
            self._return_dim, self._return_x, self._return_y = 'overworld', self.player.x, self.player.y
            self._dim_cache['overworld'] = (self.world, self._mobs)
            nether = self._ensure_dim('nether')
            self.world, self._mobs = nether, self._dim_cache['nether'][1]
            self.dimension = 'nether'
            self.player.x, self.player.y = self._find_safe_spawn(nether)
            self._place_portal_pad(nether, self.player.bx(), self.player.by())
            self._msg('You entered the Nether!')
        elif self.dimension == 'nether':
            self._dim_cache['nether'] = (self.world, self._mobs)
            ow, mobs = self._dim_cache.get('overworld', (self._ensure_dim('overworld'), []))
            self.world, self._mobs = ow, mobs
            self.dimension = 'overworld'
            self.player.x, self.player.y = self._return_x, self._return_y
            self._msg('Back in the Overworld.')
        self._portal_cd = 30
        self._rend.full_redraw()

    def _teleport_via_end_portal(self):
        if self.dimension != 'end':
            self._return_dim = self.dimension
            self._return_x, self._return_y = self.player.x, self.player.y
            self._dim_cache[self.dimension] = (self.world, self._mobs)
            end = self._ensure_dim('end')
            self.world, self._mobs = end, self._dim_cache['end'][1]
            if not self._mobs:
                self._spawn_dragon_if_absent()
            self.dimension = 'end'
            self.player.x, self.player.y = self._find_safe_spawn(end)
            self._msg('You have entered the End...')
        else:
            self._dim_cache['end'] = (self.world, self._mobs)
            back_w, back_m = self._dim_cache.get(self._return_dim, (self._ensure_dim(self._return_dim), []))
            self.world, self._mobs = back_w, back_m
            self.dimension = self._return_dim
            self.player.x, self.player.y = self._return_x, self._return_y
            self._msg('You return from the End.')
        self._portal_cd = 30
        self._rend.full_redraw()

    def _interact(self):
        """U = interact with whatever's under the cursor: open a furnace,
        ignite a Nether portal (holding Flint and Steel on a hollow cell),
        or activate a completed End portal frame ring."""
        if not self._in_reach():
            self._msg('Too far!'); return
        bid = self.world.get(self.cur_x, self.cur_y)
        if bid == 20:
            self._open_furnace(self.cur_x, self.cur_y); return
        if bid != 0:
            self._msg('Nothing to interact with.'); return
        if self._mp_mode:
            self._msg("Dimension travel isn't available in multiplayer yet.")
            return
        if self.player.held() == 70:
            self._try_ignite_portal(self.cur_x, self.cur_y)
        else:
            self._try_activate_end_portal(self.cur_x, self.cur_y)

    # ── furnace ──────────────────────────────────────────────────────────────

    def _current_furnace(self) -> dict | None:
        if self.furnace_at is None:
            return None
        fs = self.world.furnaces.get(self.furnace_at)
        if fs is None:
            fs = {'input_id': 0, 'input_n': 0, 'fuel_id': 0, 'fuel_n': 0,
                  'output_id': 0, 'output_n': 0, 'fuel_ticks_left': 0, 'smelt_ticks_done': 0}
            self.world.furnaces[self.furnace_at] = fs
        return fs

    def _open_furnace(self, x: int, y: int):
        self.furnace_at = (x, y)
        self.furnace_cur = 0
        self.mode = 'furnace'
        self._rend.full_redraw()

    def _tick_furnaces(self):
        for fs in self.world.furnaces.values():
            recipe = SMELT.get(fs['input_id'])
            if fs['input_id'] == 0 or recipe is None:
                continue
            if fs['fuel_ticks_left'] <= 0:
                burn = FUEL.get(fs['fuel_id'])
                if fs['fuel_id'] == 0 or fs['fuel_n'] <= 0 or burn is None:
                    continue
                fs['fuel_n'] -= 1
                fs['fuel_ticks_left'] = burn
                if fs['fuel_n'] == 0:
                    fs['fuel_id'] = 0
            fs['fuel_ticks_left'] -= 1
            fs['smelt_ticks_done'] += 1
            out_id, out_cnt, ticks_needed = recipe
            if fs['smelt_ticks_done'] >= ticks_needed:
                fs['smelt_ticks_done'] = 0
                fs['input_n'] -= 1
                if fs['output_id'] in (out_id, 0):
                    fs['output_id'] = out_id
                    fs['output_n'] += out_cnt
                if fs['input_n'] <= 0:
                    fs['input_id'] = 0

    def _furnace_key(self, key: str):
        fs = self._current_furnace()
        if fs is None:
            return
        if key == 'LEFT':
            self.furnace_cur = max(0, self.furnace_cur - 1)
        elif key == 'RIGHT':
            self.furnace_cur = min(2, self.furnace_cur + 1)
        elif key in ('\r', '\n', 'ENTER'):
            held = self.player.held()
            held_n = self.player.hot[self.player.hsel][1]
            if self.furnace_cur == 0:
                if held in SMELT and fs['input_id'] in (held, 0):
                    if self.player.remove(held, held_n):
                        fs['input_id'] = held; fs['input_n'] += held_n
                else:
                    self._msg("Held item can't be smelted.")
            elif self.furnace_cur == 1:
                if held in FUEL and fs['fuel_id'] in (held, 0):
                    if self.player.remove(held, held_n):
                        fs['fuel_id'] = held; fs['fuel_n'] += held_n
                else:
                    self._msg("Held item isn't valid fuel.")
            elif self.furnace_cur == 2 and fs['output_id'] > 0:
                leftover = self.player.add(fs['output_id'], fs['output_n'])
                taken = fs['output_n'] - leftover
                if taken > 0:
                    self._msg(f"Collected {taken}x {item_name(fs['output_id'])}")
                fs['output_n'] = leftover
                if fs['output_n'] == 0:
                    fs['output_id'] = 0

    def _render_furnace_overlay(self) -> str:
        fs = self._current_furnace()
        if fs is None:
            return ''
        out = []
        ox, oy, w, h = 14, 6, 50, 11
        def box(r, c, s): out.append(f'{_at(oy+r, ox+c)}{s}')

        border_bg = _bg(50, 30, 20)
        box(0, 0, f'{border_bg}{_fg(255,180,100)}')
        out.append('╔' + '═'*(w-2) + '╗')
        for r in range(1, h-1):
            box(r, 0, f'{border_bg}║{" "*(w-2)}║')
        box(h-1, 0, f'{border_bg}╚' + '═'*(w-2) + '╝')
        box(1, 2, f'{_fg(255,200,140)}{BOLD}FURNACE{RST}  '
                  f'{_fg(150,120,100)}U=close  Left/Right=select  Enter=load/collect')

        def slot(row, idx, label, iid, cnt):
            sel = idx == self.furnace_cur
            bg  = _bg(120,70,30) if sel else border_bg
            ch  = item_char(iid) if iid else ' '
            name = item_name(iid) if iid else '(empty)'
            box(row, 2, f'{bg}{_fg(230,230,230)}{label:<8}{ch} {cnt:<3} {name}{RST}')

        slot(3, 0, 'Input:',  fs['input_id'],  fs['input_n'])
        slot(4, 1, 'Fuel:',   fs['fuel_id'],   fs['fuel_n'])
        slot(5, 2, 'Output:', fs['output_id'], fs['output_n'])

        recipe = SMELT.get(fs['input_id'])
        if fs['input_id'] > 0 and recipe:
            _, _, ticks_needed = recipe
            pct = int(100 * fs['smelt_ticks_done'] / max(1, ticks_needed))
            barw = w - 8
            fill = barw * pct // 100
            bar  = '=' * fill + '-' * (barw - fill)
            box(7, 2, f'{_fg(255,140,60)}[{bar}] {pct:>3}%{RST}')
        else:
            box(7, 2, f'{_fg(140,140,140)}Not smelting.{RST}')

        fuel_status = (f"Burning ({fs['fuel_ticks_left']} ticks left)"
                       if fs['fuel_ticks_left'] > 0 else 'No fuel burning')
        box(8, 2, f'{_fg(200,120,60)}{fuel_status}{RST}')

        return ''.join(out)

    # ── mobs ─────────────────────────────────────────────────────────────────

    def _spawn_initial_mobs(self):
        rng = random.Random(self.world.seed + 99)
        for _ in range(6):
            x = rng.randint(10, WORLD_W - 10)
            y = float(self.world._height(x) - 1)
            self._mobs.append(Mob('cow', float(x), y))

    def _hostile_table(self) -> list[str]:
        """Which hostile types can spawn in the current dimension."""
        if self.dimension == 'nether':
            return ['blaze', 'ghast', 'enderman']
        if self.dimension == 'end':
            return ['enderman']
        return ['zombie', 'creeper', 'skeleton', 'enderman']

    def _spawn_hostile(self):
        if len(self._mobs) >= MAX_MOBS:
            return
        px = self.player.bx()
        mob_type = random.choice(self._hostile_table())
        is_day = self._day_t() < 0.5
        for _ in range(15):
            x = random.randint(10, WORLD_W - 10)
            if self.dimension == 'overworld':
                y = self.world._height(x)
            else:
                # No surface heightmap outside the Overworld — scan a random
                # column for open air over solid ground.
                y = None
                for sy in range(4, WORLD_H - 4):
                    if not self.world.solid(x, sy) and self.world.solid(x, sy + 1):
                        y = sy
                        break
                if y is None:
                    continue
            if abs(x - px) > 15 and self.world.is_dark(x, y - 1, is_day):
                self._mobs.append(Mob(mob_type, float(x), float(y - 1)))
                break

    def _mob_physics(self, mob: Mob):
        w  = self.world
        spec = MOB_DATA[mob.type]

        if spec.get('flies'):
            # Flying mobs steer toward tx/ty (set by their AI); no gravity.
            mob.x = max(0.0, min(float(WORLD_W - 1), mob.x + mob.vx))
            mob.y = max(1.0, min(float(WORLD_H - 2), mob.y + mob.vy))
            mob.on_ground = False
            return

        by = mob.by()
        mob.vy = min(mob.vy + GRAV, 3.0)
        nx = mob.x + mob.vx
        ny = mob.y + mob.vy
        inx = int(nx)
        if w.solid(inx, by) or w.solid(inx, by - 1):
            # try 1-block step-up
            if mob.on_ground and w.solid(inx, by) and not w.solid(inx, by-1) and not w.solid(inx, by-2):
                ny -= 1.0
            else:
                nx = mob.x; mob.vx = 0
        iny = int(ny)
        if mob.vy >= 0:
            if w.solid(int(nx), iny + 1):
                ny = float(iny); mob.vy = 0; mob.on_ground = True
            else:
                mob.on_ground = False
        else:
            if w.solid(int(nx), iny - 1):
                ny = mob.y; mob.vy = 0
        mob.x = max(0.0, min(float(WORLD_W - 1), nx))
        mob.y = max(1.0, min(float(WORLD_H - 2), ny))
        mob.vx *= 0.65

    def _mob_ai(self, mob: Mob):
        if mob.type == 'ender_dragon':
            self._dragon_ai(mob)
            return

        if mob.ai_tick > 0:
            mob.ai_tick -= 1
            if mob.dmg_cd > 0: mob.dmg_cd -= 1
            return
        p     = self.player
        dx    = p.x - mob.x
        dy    = p.y - mob.y
        dist  = abs(dx) + abs(dy)
        speed = MOB_DATA[mob.type]['speed']

        if mob.type == 'cow':
            mob.ai_tick = random.randint(20, 50)
            if random.random() < 0.6:
                mob.vx = random.choice([-1, 0, 1]) * speed
                mob.facing = 1 if mob.vx >= 0 else -1

        elif mob.type == 'zombie':
            mob.ai_tick = 5
            if dist < 25:
                mob.vx = speed * (1 if dx > 0 else -1)
                mob.facing = 1 if dx > 0 else -1
                if mob.on_ground and self.world.solid(mob.bx() + mob.facing, mob.by()):
                    mob.vy = JUMP_V * 0.8
                if mob.dmg_cd == 0 and abs(dx) <= 1.5 and abs(dy) <= 2:
                    self._damage_player(MOB_DATA['zombie']['dmg'], 'Zombie hit you!')
                    mob.dmg_cd = 25
            else:
                mob.vx *= 0.3
            if mob.dmg_cd > 0: mob.dmg_cd -= 1

        elif mob.type == 'skeleton':
            mob.ai_tick = 8
            if dist < 20:
                # Keep some distance, shoot player (damage on proximity for now)
                if dist > 6:
                    mob.vx = speed * (1 if dx > 0 else -1)
                    mob.facing = 1 if dx > 0 else -1
                    if mob.on_ground and self.world.solid(mob.bx() + mob.facing, mob.by()):
                        mob.vy = JUMP_V * 0.8
                else:
                    mob.vx *= 0.3
                if mob.dmg_cd == 0 and dist <= 12:
                    self._damage_player(MOB_DATA['skeleton']['dmg'], 'Skeleton arrow!')
                    mob.dmg_cd = 40
            else:
                mob.vx *= 0.3
            if mob.dmg_cd > 0: mob.dmg_cd -= 1

        elif mob.type == 'creeper':
            mob.ai_tick = 3
            if dist < 22:
                mob.vx = speed * (1 if dx > 0 else -1)
                mob.facing = 1 if dx > 0 else -1
                if mob.on_ground and self.world.solid(mob.bx() + mob.facing, mob.by()):
                    mob.vy = JUMP_V * 0.8
                if abs(dx) <= 2 and abs(dy) <= 2:
                    mob.fuse += 1
                    if mob.fuse >= 25:
                        self._explode(mob)
                        return
                else:
                    mob.fuse = max(0, mob.fuse - 2)
            else:
                mob.vx *= 0.3
                mob.fuse = max(0, mob.fuse - 1)

        elif mob.type == 'blaze':
            mob.ai_tick = 3
            if mob.dmg_cd > 0: mob.dmg_cd -= 1
            mob.tx = p.x + math.sin(self.tick * 0.05) * 6
            mob.ty = p.y - 4 + math.cos(self.tick * 0.05) * 3
            bx, by = mob.tx - mob.x, mob.ty - mob.y
            blen = max(0.01, math.hypot(bx, by))
            mob.vx = bx / blen * speed
            mob.vy = by / blen * speed
            if mob.dmg_cd == 0 and dist <= 14:
                self._damage_player(MOB_DATA['blaze']['dmg'], 'Blaze fireball!')
                mob.dmg_cd = 45

        elif mob.type == 'ghast':
            mob.ai_tick = 3
            if mob.dmg_cd > 0: mob.dmg_cd -= 1
            if dist < 10:
                mob.tx, mob.ty = mob.x - (1 if dx > 0 else -1) * 8, mob.y
            elif dist > 25:
                mob.tx, mob.ty = p.x, p.y - 6
            gx, gy = mob.tx - mob.x, mob.ty - mob.y
            glen = max(0.01, math.hypot(gx, gy))
            mob.vx = gx / glen * speed
            mob.vy = gy / glen * speed
            if mob.dmg_cd == 0 and 4 < dist <= 20:
                self._damage_player(MOB_DATA['ghast']['dmg'], 'Ghast fireball!')
                mob.dmg_cd = 60

        elif mob.type == 'enderman':
            # Neutral: wanders until the player gets close, then chases.
            # Randomly short-hop teleports, matching its signature ability.
            mob.ai_tick = 4
            if mob.dmg_cd > 0: mob.dmg_cd -= 1
            if mob.phase > 0: mob.phase -= 1
            if dist < 12:
                mob.vx = speed * (1 if dx > 0 else -1)
                mob.facing = 1 if dx > 0 else -1
                if mob.phase == 0 and random.random() < 0.05:
                    mob.x = max(0.0, min(float(WORLD_W - 1), mob.x + (random.random() - 0.5) * 10))
                    mob.phase = 60
            elif random.random() < 0.1:
                mob.vx = (random.random() - 0.5) * speed
            if mob.dmg_cd == 0 and abs(dx) <= 1.5 and abs(dy) <= 2:
                self._damage_player(MOB_DATA['enderman']['dmg'], 'Enderman strikes!')
                mob.dmg_cd = 30

    def _explode(self, mob: Mob):
        bx, by = mob.bx(), mob.by()
        r = 4
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx*dx + dy*dy <= r*r:
                    wx, wy = bx+dx, by+dy
                    if self.world.get(wx, wy) not in (0, 18):
                        self.world.set(wx, wy, 0)
        px, py = self.player.bx(), self.player.by()
        if abs(px - bx) + abs(py - by) <= r + 2:
            self._damage_player(10, 'CREEPER BOOM!')
        else:
            self._msg('Creeper exploded nearby!')
        mob.dead = True
        self._rend.full_redraw()

    def _mob_at(self, bx: int, by: int) -> 'Mob | None':
        for mob in self._mobs:
            if mob.bx() == bx and mob.by() in (by, by + 1):
                return mob
        return None

    def _attack_mob(self, mob: Mob):
        dmg = 4 + SWORD_DMG.get(self.player.held(), 0)
        mob.hp -= dmg
        if mob.hp <= 0:
            mob.dead = True
            drops = MOB_DATA[mob.type]['drops']
            if drops:
                names = []
                for drop_id, drop_cnt in drops:
                    self.player.add(drop_id, drop_cnt)
                    names.append(f'+{drop_cnt} {item_name(drop_id)}')
                self._msg(f'Killed {mob.type}! {" ".join(names)}')
            else:
                self._msg(f'Killed {mob.type}!')
            if mob.type == 'ender_dragon':
                self._on_dragon_defeated(mob)
        else:
            self._msg(f'Hit {mob.type}! ({mob.hp} HP)')

    def _tick_mobs(self):
        for mob in self._mobs:
            self._mob_physics(mob)
            self._mob_ai(mob)
        # Remove dead mobs in-place (preserves list reference for MP shared state)
        for m in [m for m in self._mobs if m.dead]:
            self._mobs.remove(m)
        if self.game_mode != 'creative':
            if len(self._mobs) < MAX_MOBS and self.tick % 150 == 0:
                self._spawn_hostile()

    # ── Ender Dragon boss + victory ─────────────────────────────────────────

    def _dragon_ai(self, mob: Mob):
        p = self.player
        spec = MOB_DATA['ender_dragon']
        dx, dy = p.x - mob.x, p.y - mob.y
        dist = math.hypot(dx, dy)

        # Heal slowly while any healing-crystal pillar (obsidian) still
        # stands — a nod to the vanilla end-crystal mechanic.
        crystals_standing = any(self.world.get(x, 0) == 21 for x in range(0, WORLD_W, 4))
        if crystals_standing and self.tick % 40 == 0 and mob.hp < spec['hp']:
            mob.hp = min(spec['hp'], mob.hp + 2)

        if mob.phase == 0 and dist < 30:
            mob.phase = 1
            self._msg('The dragon turns towards you!')

        if mob.phase == 0 or dist > 45:
            ang = self.tick * 0.02
            mob.tx = WORLD_W / 2 + math.cos(ang) * 22
            mob.ty = WORLD_H / 2 - 15 + math.sin(ang) * 8
        else:
            mob.tx, mob.ty = p.x, p.y - 3

        mx, my = mob.tx - mob.x, mob.ty - mob.y
        mlen = max(0.01, math.hypot(mx, my))
        mob.vx = mx / mlen * spec['speed'] * 2.2
        mob.vy = my / mlen * spec['speed'] * 2.2
        if mx != 0:
            mob.facing = 1 if mx > 0 else -1

        if mob.dmg_cd > 0:
            mob.dmg_cd -= 1
        if mob.dmg_cd == 0 and dist < 3:
            self._damage_player(spec['dmg'], 'The dragon strikes!')
            mob.dmg_cd = 30

    def _on_dragon_defeated(self, dragon: Mob):
        self.player.has_won = True
        self.world.set(dragon.bx(), dragon.by(), 28)   # exit portal appears where it fell
        self._msg('THE ENDER DRAGON HAS BEEN SLAIN!')
        if not self._victory_shown:
            self._victory_shown = True
            self.mode = 'victory'
            self._rend.full_redraw()

    def _render_victory_overlay(self) -> str:
        out = []
        ox, oy, w, h = 6, 3, 68, 18
        def box(r, c, s): out.append(f'{_at(oy+r, ox+c)}{s}')

        border_bg = _bg(20, 15, 40)
        box(0, 0, f'{border_bg}{_fg(255,220,100)}')
        out.append('╔' + '═'*(w-2) + '╗')
        for r in range(1, h-1):
            box(r, 0, f'{border_bg}║{" "*(w-2)}║')
        box(h-1, 0, f'{border_bg}╚' + '═'*(w-2) + '╝')

        lines = [
            '',
            f'{_fg(255,230,120)}{BOLD}          V   I   C   T   O   R   Y{RST}',
            '',
            f'{_fg(200,200,255)}      You have slain the Ender Dragon and{RST}',
            f'{_fg(200,200,255)}         completed your ANetCRAFT journey!{RST}',
            '',
            f'{_fg(150,255,150)}   A shimmering exit portal has opened where{RST}',
            f'{_fg(150,255,150)}   the dragon fell. Step onto it to return home.{RST}',
            '',
            f'{_fg(180,180,200)}   Your Dragon Egg trophy has been added to{RST}',
            f'{_fg(180,180,200)}   your inventory. The world is still yours —{RST}',
            f'{_fg(180,180,200)}   mine, build, and explore as long as you like.{RST}',
            '',
            f'{_fg(255,255,255)}   Thank you for playing ANetCRAFT!{RST}',
            '',
            f'{_fg(120,120,120)}                Press Enter to continue{RST}',
        ]
        for i, line in enumerate(lines):
            if i >= h - 3:
                break
            box(2 + i, 2, f'{border_bg}{line}{RST}')

        return ''.join(out)

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
        p      = self.player
        is_day = self._day_t() < 0.5
        sky    = _sky(self._day_t())
        light_grid = self.world.compute_light_grid(self.cam_x, self.cam_y, VP_W, VP_H, is_day)

        # Mob-position lookup
        mob_cells: dict[tuple, Mob] = {}
        for mob in self._mobs:
            mob_cells[(mob.bx(), mob.by())]     = mob
            mob_cells[(mob.bx(), mob.by() - 1)] = mob

        # Other-player lookup (MP)
        op_cells: dict[tuple, tuple] = {}
        if self._mp_mode:
            for uname, opl in _MP.get('players', {}).items():
                if uname == self.username: continue
                col = _player_color(uname)
                op_cells[(opl.bx(), opl.by())]     = ('╬', col)
                op_cells[(opl.bx(), opl.by() - 1)] = ('Ö', col)

        for vy in range(VP_H):
            wy = self.cam_y + vy
            for vx in range(VP_W):
                wx = self.cam_x + vx

                # Our player body
                if wx == p.bx() and wy == p.by():
                    bg = sky if self.world.get(wx, wy) == 0 else (80, 50, 20)
                    self._rend.set_cell(vx, vy, '╬', (100, 150, 220), bg)
                    continue
                # Our player head
                if wx == p.bx() and wy == p.by() - 1:
                    bg = sky if self.world.get(wx, wy) == 0 else (80, 50, 20)
                    self._rend.set_cell(vx, vy, 'Ö', (255, 215, 170), bg)
                    continue

                # Other players (MP) — each user gets a unique colour
                op = op_cells.get((wx, wy))
                if op is not None:
                    char, col = op
                    bg = sky if self.world.get(wx, wy) == 0 else (80, 50, 20)
                    self._rend.set_cell(vx, vy, char, col, bg)
                    continue

                # Mobs
                mob = mob_cells.get((wx, wy))
                if mob is not None:
                    mdata = MOB_DATA[mob.type]
                    bg    = sky if self.world.get(wx, wy) == 0 else (60, 40, 20)
                    if wy == mob.by() - 1:
                        self._rend.set_cell(vx, vy, mdata['head'], mdata['hfg'], bg)
                    else:
                        bfg = (255,255,100) if mob.fuse > 10 and self.tick % 4 < 2 else mdata['bfg']
                        self._rend.set_cell(vx, vy, mdata['body'], bfg, bg)
                    continue

                is_cur = (self.mode == 'game' and wx == self.cur_x and wy == self.cur_y)
                char, fg, bg = self._block_cell(wx, wy, is_cur, sky)
                lvl = light_grid[vy][vx]
                self._rend.set_cell(vx, vy, char, _dim(fg, lvl), _dim(bg, lvl))

    def _render_header(self) -> str:
        p      = self.player
        day    = self.tick // DAY_TICK + 1
        tod    = self._day_t()
        tod_s  = 'Day' if tod < 0.5 else 'Night'
        if self.dimension == 'overworld':
            depth  = p.by() - self.world._height(p.bx())
            coords = f'X:{p.bx():<4} Y:{p.by():<3} Depth:{depth:+d}'
        else:
            coords = f'X:{p.bx():<4} Y:{p.by():<3}'
        if self._mp_mode:
            online = len(_MP.get('players', {}))
            mode_s = f'[MULTI {online}P]'
        elif self.game_mode == 'creative':
            mode_s = '[CREATIVE]'
        else:
            mode_s = '[SURVIVAL]'
        dim_s = {'nether': '  [NETHER]', 'end': '  [THE END]'}.get(self.dimension, '')
        title  = f' █ ANetCRAFT █  {tod_s} {day}  {coords}  {mode_s}{dim_s} '
        pad    = max(0, VP_W + 2 - len(title))
        if self.dimension == 'nether':
            hdr_bg, hdr_fg = _bg(140,40,20), _fg(255,255,255)
        elif self.dimension == 'end':
            hdr_bg, hdr_fg = _bg(50,20,70), _fg(255,255,255)
        elif self.game_mode == 'creative':
            hdr_bg, hdr_fg = _bg(100,60,160), _fg(255,255,255)
        else:
            hdr_bg, hdr_fg = _bg(60,160,60), _fg(0,0,0)
        return (f'{_at(1,1)}{hdr_fg}{hdr_bg}'
                f'{BOLD}{title}{" " * pad}{RST}')

    def _render_ui(self) -> str:
        out = []
        p   = self.player

        # Row 22: divider + status message
        msg   = self._msg_text()
        div   = '═' * (VP_W + 2)
        out.append(f'{_at(22,1)}{_fg(80,80,80)}{div}{RST}')
        if self._mp_mode and 'chat' in _MP:
            _MP['chat'] = [m for m in _MP['chat'] if m[2] > self.tick]
            if _MP['chat']:
                u, cm, _ = _MP['chat'][-1]
                short_u = u[:10]
                out.append(f'{_at(22, 3)}{_fg(100,200,255)}{short_u}: {_fg(255,255,150)}{cm[:62]}{RST}')
            elif msg:
                out.append(f'{_at(22, 3)}{_fg(255,255,100)}{msg[:74]}{RST}')
        elif msg:
            out.append(f'{_at(22, 3)}{_fg(255,255,100)}{msg[:74]}{RST}')

        # Row 23: health + hunger + hotbar
        hp_f = p.hp // 2
        hp_e = MAX_HP // 2 - hp_f
        # ■ = U+25A0 → CP437 byte 0xFE (safe, not a control char)
        hp_s = (_fg(220,40,40) + '■' * hp_f +
                _fg(60,20,20)  + '■' * hp_e + RST)

        if self.game_mode != 'creative':
            hg_f = p.hunger // 2
            hg_e = MAX_HUNGER // 2 - hg_f
            hg_s = (' ' + _fg(210,130,30) + '■' * hg_f +
                    _fg(70,40,10) + '■' * hg_e + RST)
        else:
            hg_s = ''

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
        if   self.mode == 'inv':     mode_hint = f' {_fg(255,200,50)}[INVENTORY]{RST}'
        elif self.mode == 'craft':   mode_hint = f' {_fg(100,255,150)}[CRAFTING]{RST}'
        elif self.mode == 'furnace': mode_hint = f' {_fg(255,160,80)}[FURNACE]{RST}'
        out.append(f'{_at(23,1)}{hp_s}{hg_s} {hb_s}{mode_hint}')

        # Row 24: controls hint
        if self.game_mode == 'creative':
            ctrl = ' A/D=move W=fly-up S=fly-dn F=mine P=place U=use 1-9=slot I=inv Q=quit'
        elif self._mp_mode:
            ctrl = ' A/D=move W=jump F=mine/attack P=place E=eat U=use 1-9=slot I=inv T=chat Q=quit'
        else:
            ctrl = ' A/D=move W=jump F=mine/attack P=place E=eat U=use 1-9=slot I=inv C=craft Q=quit'
        out.append(f'{_at(24,1)}{_fg(70,70,70)}{ctrl[:VP_W+2]}{RST}')

        return ''.join(out)

    def _render_inv_overlay(self) -> str:
        out = []
        ox, oy, w, h = 8, 3, 64, 16
        def box(r, c, s): out.append(f'{_at(oy+r, ox+c)}{s}')

        border_bg = _bg(30,30,50)
        box(0, 0, f'{border_bg}{_fg(150,150,255)}')
        out.append('╔' + '═'*(w-2) + '╗')
        for r in range(1, h-1):
            box(r, 0, f'{border_bg}║{" "*(w-2)}║')
        box(h-1, 0, f'{border_bg}╚' + '═'*(w-2) + '╝')

        box(1, 2, f'{_fg(200,200,255)}{BOLD}INVENTORY{RST}  '
                  f'{_fg(150,150,150)}I=close  Arrows=move  Enter=grab/drop  R=wear armor')

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

        # Armor slots
        slot_lbl = ['Head', 'Chest', 'Legs', 'Boots']
        box(11, 2, f'{_fg(200,200,150)}Armor (Def {self.player.total_defense()}):')
        for i, iid in enumerate(self.player.armor):
            ch = item_char(iid) if iid else ' '
            name = item_name(iid) if iid else '-'
            box(12, 2 + i*14, f'{_bg(60,60,80)}{_fg(230,230,230)}{slot_lbl[i]:<6}{ch} {name:<6}{RST}')

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

        near_b = self._near_bench()
        visible = h - 5
        start   = max(0, self.cft_cur - visible // 2)
        for di, i in enumerate(range(start, min(len(RECIPES), start + visible))):
            res, cnt, ingr, needs_bench = RECIPES[i]
            can   = (all(self.player.count(iid) >= need for iid, need in ingr)
                     and (not needs_bench or near_b))
            sel   = i == self.cft_cur
            bg    = _bg(40,90,60) if sel else border_bg
            name_c = _fg(100,255,120) if can else _fg(130,130,130)
            ingr_s = '  +  '.join(
                f'{need}x {item_name(iid)}' for iid, need in ingr)
            bench_tag = _fg(200,140,50)+'[bench]'+name_c if needs_bench else '       '
            line   = f' {cnt}x {item_name(res):<17} {bench_tag} <- {ingr_s}'
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
        if key in ('u', 'U') and self.mode == 'furnace':
            self.mode = 'game'; self._rend.full_redraw(); return
        if key in ('\r', '\n', 'ENTER'):
            if self.mode == 'craft':
                self._craft(self.cft_cur); return
            if self.mode == 'furnace':
                self._furnace_key(key); return
            if self.mode == 'inv':
                self._inv_key(key); return
            self.mode = 'game'; self._rend.full_redraw(); return

        if self.mode == 'inv':
            self._inv_key(key); return
        if self.mode == 'craft':
            if key == 'UP':   self.cft_cur = max(0, self.cft_cur - 1)
            if key == 'DOWN': self.cft_cur = min(len(RECIPES)-1, self.cft_cur+1)
            return
        if self.mode == 'furnace':
            self._furnace_key(key); return
        if self.mode == 'victory':
            return   # dismissed only via Enter (handled above)

        # Game mode — A/D move player; all 4 arrows move mining cursor
        if key in ('a', 'A'):
            p.vx = -(SWIM_SPD if p.in_water else MOVE_SPD)
            p.facing = -1; self._sync_cursor()
        elif key in ('d', 'D'):
            p.vx = (SWIM_SPD if p.in_water else MOVE_SPD)
            p.facing =  1; self._sync_cursor()
        elif key in ('w', 'W', ' '):
            if self.game_mode == 'creative':
                p.vy = -MOVE_SPD * 1.5
            elif p.on_ground:
                p.vy = JUMP_V; p.on_ground = False
            elif p.in_water:
                p.vy = -SWIM_SPD * 2.5
        elif key in ('s', 'S'):
            if self.game_mode == 'creative':
                p.vy = MOVE_SPD * 1.5
            elif p.in_water:
                p.vy = SWIM_SPD * 2
            else:
                p.vy = min(p.vy + 1.0, 3.0)
        elif key == 'UP':    self.cur_y -= 1
        elif key == 'DOWN':  self.cur_y += 1
        elif key == 'LEFT':  self.cur_x -= 1; p.facing = -1
        elif key == 'RIGHT': self.cur_x += 1; p.facing =  1
        elif key in ('f', 'F'):
            if self._in_reach():
                mob = self._mob_at(self.cur_x, self.cur_y)
                if mob:
                    self._attack_mob(mob)
                else:
                    self._mine_step(self.cur_x, self.cur_y)
            else:
                self._msg('Too far!')
        elif key in ('p', 'P'):
            if self._in_reach(): self._place(self.cur_x, self.cur_y)
            else: self._msg('Too far!')
        elif key in ('e', 'E'):
            self._eat()
        elif key in ('u', 'U'):
            self._interact()
        elif key in ('t', 'T'):
            if self._mp_mode:
                self._chat_pending = True
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
        elif key in ('r', 'R'):
            self._equip_selected(rc, cc)

    def _equip_selected(self, rc: int, cc: int):
        """R in the inventory equips the selected slot's item if it's
        armor, swapping whatever was already worn back into that slot."""
        slot = self.player.hot[cc] if rc == -1 else self.player.inv[rc][cc]
        armor_def = ARMOR.get(slot[0])
        if slot[0] == 0 or armor_def is None:
            self._msg('Not wearable.'); return
        idx = armor_def[0]
        slot[0], self.player.armor[idx] = self.player.armor[idx], slot[0]
        slot[1] = 1 if slot[0] else 0
        self._msg(f'Equipped {item_name(self.player.armor[idx])}. Defense: {self.player.total_defense()}')

    # ── lobby ────────────────────────────────────────────────────────────────

    async def _lobby(self) -> tuple | None:
        """Pre-game menu. Returns ('load', None), ('new', mode), or None."""
        has_save  = self._save_path().exists()
        saved_info = ''
        if has_save:
            try:
                d = json.loads(self._save_path().read_text())
                sm = d.get('game_mode', 'survival').upper()
                sd = d.get('tick', 0) // DAY_TICK + 1
                saved_info = f'{sm}  Day {sd}'
            except Exception:
                saved_info = 'SAVED GAME'

        kp = _Keys()
        BW = 50          # box width
        lx = (80 - BW) // 2 + 1  # left col (1-indexed)
        G  = _fg(60,160,60)

        def bline(row, txt='', col=''):
            s = _at(row, lx) + G + '║'
            if txt:
                s += _at(row, lx+2) + col + txt + RST
            s += _at(row, lx+BW-1) + G + '║' + RST
            return s

        while True:
            out = [CLS, HIDE]
            out.append(_at(3, lx) + G + '╔' + '═'*(BW-2) + '╗' + RST)
            out.append(bline(4, '  █ ANetCRAFT █', _fg(255,255,100)+BOLD))
            out.append(bline(5, '  A BBS Minecraft Adventure', _fg(180,230,180)))
            out.append(bline(6))
            out.append(_at(7, lx) + G + '╚' + '═'*(BW-2) + '╝' + RST)

            row = 9
            W2  = _fg(200,200,200)
            if has_save:
                out.append(_at(row, lx+2) + _fg(100,200,255) + BOLD + 'L' + RST +
                           W2 + f'  Load Game  [{saved_info}]' + RST)
                row += 2
            out.append(_at(row, lx+2) + _fg(100,255,100) + BOLD + 'N' + RST +
                       W2 + '  New Game - Survival Mode' + RST)
            row += 2
            out.append(_at(row, lx+2) + _fg(255,200,100) + BOLD + 'C' + RST +
                       W2 + '  New Game - Creative Mode' + RST)
            row += 2
            online = len(_MP.get('players', {}))
            mp_tag = f'  Multiplayer  ({online} online)' if online else '  Multiplayer'
            out.append(_at(row, lx+2) + _fg(100,220,255) + BOLD + 'M' + RST +
                       W2 + mp_tag + RST)
            row += 2
            out.append(_at(row, lx+2) + _fg(255,100,100) + BOLD + 'Q' + RST +
                       W2 + '  Quit' + RST)
            row += 3
            out.append(_at(row, lx+2) + _fg(80,80,80) +
                       'Survival: health bar, resources, mobs' + RST)
            row += 1
            out.append(_at(row, lx+2) + _fg(80,80,80) +
                       'Creative: fly freely, instant break, all blocks free' + RST)
            row += 1
            out.append(_at(row, lx+2) + _fg(80,80,80) +
                       'Multi: shared world, see other players, T=chat' + RST)

            await self.session.write(''.join(out))

            try:
                raw = await asyncio.wait_for(self.session.read_raw(16), timeout=120.0)
            except (asyncio.TimeoutError, Exception):
                return None
            kp.feed(raw)
            while True:
                k = kp.next()
                if k is None: break
                if k in ('l', 'L') and has_save: return ('load', None)
                if k in ('n', 'N'):              return ('new', 'survival')
                if k in ('c', 'C'):              return ('new', 'creative')
                if k in ('m', 'M'):              return ('mp',  None)
                if k in ('q', 'Q'):              return None

    # ── main loop ────────────────────────────────────────────────────────────

    async def run(self):
        result = await self._lobby()
        if result is None:
            return
        action, mode = result

        if action == 'mp':
            self._mp_join()
        elif action == 'load':
            if not self.load():
                self._new_world('survival')
                self._msg('Save corrupted — new world started.')
        else:
            self._new_world(mode)

        self._update_cam()
        self._sync_cursor()
        await self.session.write(CLS + HIDE)
        self._rend.full_redraw()

        try:
            while self.running:
                t0 = time.monotonic()

                # In MP: sync tick from shared state before processing
                if self._mp_mode:
                    self.tick = _MP['tick']

                # Chat input (MP only) — suspend normal loop, collect text
                if self._chat_pending:
                    self._chat_pending = False
                    msg = await self._mp_chat_prompt()
                    if msg:
                        _MP['chat'].append((self.username, msg, self.tick + 750))
                    self._rend.full_redraw()

                # Read keys (non-blocking, poll window)
                try:
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

                # Physics + mob AI only in game mode
                if self.mode == 'game':
                    self._physics()
                    self._update_cam()
                    # Only the MP host runs mob AI; SP always runs it
                    if not self._mp_mode or self._is_mp_host():
                        self._tick_mobs()
                    self._tick_hazards()
                    self._check_portal_teleport()
                self._tick_furnaces()

                # Advance tick
                if self._mp_mode:
                    if self._is_mp_host():
                        _MP['tick'] += 1
                    self.tick = _MP['tick']
                else:
                    self.tick += 1

                # Hunger drain in survival mode (every ~32 sec of play)
                if self.game_mode == 'survival' and self.tick % 400 == 1:
                    p = self.player
                    if p.hunger > 0:
                        p.hunger -= 1
                    elif p.hp > 1:
                        p.hp -= 1
                        self._msg(f'Starving! ({p.hp}/{MAX_HP} HP)')
                    elif p.hp <= 1:
                        self._respawn_player()

                # Periodic auto-save (~1 min)
                if self._mp_mode:
                    if self._is_mp_host() and self.tick % 750 == 0:
                        self._mp_save()
                else:
                    if self.tick % 750 == 0:
                        self.save()

                # Skip world redraw when overlay open — prevents sky-colour flicker
                if self.mode == 'game':
                    self._draw_world()
                    world_cells = self._rend.flush()
                else:
                    world_cells = ''

                header  = self._render_header()
                ui      = self._render_ui()
                overlay = ''
                if self.mode == 'inv':       overlay = self._render_inv_overlay()
                elif self.mode == 'craft':   overlay = self._render_craft_overlay()
                elif self.mode == 'furnace': overlay = self._render_furnace_overlay()
                elif self.mode == 'victory': overlay = self._render_victory_overlay()

                await self.session.write(header + world_cells + ui + overlay)

                # Pace to TICK interval
                elapsed = time.monotonic() - t0
                if elapsed < TICK:
                    await asyncio.sleep(TICK - elapsed)

        finally:
            if self._mp_mode:
                self._mp_leave()
            else:
                self.save()
            await self.session.write(SHOW + CLS)


async def launch_anetcraft(session, username: str):
    """Entry point called from the BBS menu."""
    game = ANetCraft(session, username)
    await game.run()
