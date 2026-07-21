"""Regression tests for anetbbs/features/darkforces_term.py -- the terminal
port of the standalone ANetDarkForces browser game.

Ported logic (levels, raycaster, entity/combat rules, weapon firing) is
checked for parity against the same invariants the browser game's own
Playwright/Node test suite already established. The genuinely new pieces
for this port -- the native sixel encoder, the ANSI dirty-diffed renderer,
save/load migration safety, and the real-time async game loop -- get their
own dedicated coverage here, since nothing else in the codebase exercises
that combination (ANetCRAFT is the closest architectural precedent for the
real-time-loop-over-a-raw-session pattern, but has no test suite of its
own to extend).

No live terminal/SyncTerm session is available in CI or in the sandbox
this was developed in, so sixel/ANSI output is verified structurally
(valid escape-sequence framing, correct diffing behavior) rather than by
visual inspection -- a sysop should still confirm the actual on-screen
look over a real SyncTerm connection before considering this "done," the
same way every other sixel feature in this project has been verified.
"""
import asyncio
import copy
import json
import math
import os

import pytest

from anetbbs.features import darkforces_term as df


# ─── Level integrity ─────────────────────────────────────────────────────

def test_all_levels_build_without_validation_errors():
    # LEVELS is built once at import time via build_levels() -> if any
    # level failed validate_level(), the import itself would already have
    # raised -- this just asserts the expected count made it through.
    assert len(df.LEVELS) == 10


def test_level_names_and_order_match_browser_campaign():
    names = [lv['name'] for lv in df.LEVELS]
    assert names == [
        'ByteMart Warehouse', 'Overclock Alley Pawn Shop', 'NetCafe Hideout',
        'CircuitSide Outlet', 'TechBarn Superstore', 'The Mainframe Megastore',
        'Datastream Distribution Depot', 'The Chip Foundry', 'The Server Farm',
        "The Middleman's Showroom",
    ]
    assert df.LEVELS[-1]['is_final_level'] is True
    assert all(not lv.get('is_final_level') for lv in df.LEVELS[:-1])


def test_secrets_and_locked_doors_match_browser_campaign():
    totals = {lv['name']: lv['_secrets_total'] for lv in df.LEVELS}
    assert totals['ByteMart Warehouse'] == 1
    assert totals['The Server Farm'] == 1
    assert sum(totals.values()) == 2

    locked = {lv['name']: lv['locked_doors'] for lv in df.LEVELS if lv['locked_doors']}
    assert locked['CircuitSide Outlet'] == {'13,12': 'red'}
    assert locked["The Middleman's Showroom"] == {'19,1': 'gold'}
    assert list(locked['The Mainframe Megastore'].values()) == ['blue']


def test_every_locked_door_key_reachable_without_the_vault():
    """The key for each locked door must be reachable via normal doors
    alone (never requiring the vault itself) -- otherwise the vault could
    never legitimately be opened. Mirrors the browser game's own
    adf_key_ordering_check.mjs verification."""
    for level in df.LEVELS:
        if not level['locked_doors']:
            continue
        grid, w, h = level['grid'], level['w'], level['h']
        solid = {1, 2, 3, 4, 5, 6, 9, 10}  # locked doors NOT passable in this BFS
        start = (math.floor(level['player_start']['gx']), math.floor(level['player_start']['gy']))
        visited = {start}
        queue = [start]
        while queue:
            x, y = queue.pop(0)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if (nx, ny) in visited or not (0 <= nx < w and 0 <= ny < h):
                    continue
                if grid[ny][nx] in solid:
                    continue
                visited.add((nx, ny))
                queue.append((nx, ny))
        for key_id in level['locked_doors'].values():
            key_pickup = next(p for p in level['pickups'] if p['type'] == f'key_{key_id}')
            pos = (math.floor(key_pickup['gx']), math.floor(key_pickup['gy']))
            assert pos in visited, f"{level['name']}: key_{key_id} not reachable without the vault"


# ─── Raycaster parity ────────────────────────────────────────────────────

def _test_room():
    grid = [[1] * 10 for _ in range(10)]
    for y in range(1, 9):
        for x in range(1, 9):
            grid[y][x] = 0
    return grid


def test_cast_ray_known_distances():
    grid = _test_room()
    px, py = 5.5 * df.TILE, 5.5 * df.TILE
    expected = 3.5 * df.TILE

    east = df.cast_ray(grid, 10, 10, px, py, 0)
    assert abs(east.dist - expected) < 0.01

    south = df.cast_ray(grid, 10, 10, px, py, math.pi / 2)
    assert abs(south.dist - expected) < 0.01

    west = df.cast_ray(grid, 10, 10, px, py, math.pi)
    assert abs(west.dist - (5.5 - 1.0) * df.TILE) < 0.01


def test_line_of_sight_clear_and_blocked():
    grid = _test_room()
    assert df.has_line_of_sight(grid, 10, 10, 2 * df.TILE + 32, 2 * df.TILE + 32,
                                 7 * df.TILE + 32, 7 * df.TILE + 32)
    grid2 = [row[:] for row in grid]
    grid2[5][5] = 1
    assert not df.has_line_of_sight(grid2, 10, 10, 2 * df.TILE + 32, 5 * df.TILE + 32,
                                     8 * df.TILE + 32, 5 * df.TILE + 32)


# ─── Entity/combat logic ─────────────────────────────────────────────────

def _make_runtime(level):
    state = {'player': copy.deepcopy(df.PLAYER_START_STATE), 'kills': 0, 'deaths': 0,
             'parts_total': 0, 'level_index': 0}
    state['player']['x'] = 9999
    state['player']['y'] = 9999
    state['player']['angle'] = 0
    return {
        'level': level, 'enemies': [], 'pickups': [], 'props': [], 'projectiles': [],
        'state': state, 'time': 0, 'ammo_station_cooldowns': {}, 'boss_defeated': False,
        'player_dead': False, 'hurt_flash_timer': 0, 'parts_this_level': 0,
        'last_fire_time': -999, 'last_dry_fire_time': -999, 'muzzle_flash_timer': 0,
    }


def test_barrel_chain_reaction():
    rt = _make_runtime(df.LEVELS[0])
    rt['props'] = [df.spawn_barrel(500, 500), df.spawn_barrel(540, 500), df.spawn_barrel(580, 500)]
    df.damage_barrel(rt, rt['props'][0], 999, None)
    assert all(b['dead'] for b in rt['props'])


def test_shieldtech_frontal_damage_reduction():
    rt = _make_runtime(df.LEVELS[0])
    front = df.spawn_enemy('shieldtech', 0, 0)
    front['angle'] = 0
    df.damage_enemy(rt, front, 100, None, 100, 0)  # source directly ahead
    back = df.spawn_enemy('shieldtech', 0, 0)
    back['angle'] = 0
    df.damage_enemy(rt, back, 100, None, -100, 0)  # source directly behind
    assert (front['max_hp'] - front['hp']) < (back['max_hp'] - back['hp'])


def test_ammo_pickup_respects_cap():
    rt = _make_runtime(df.LEVELS[0])
    rt['state']['player']['ammo']['shells'] = 0
    for _ in range(20):
        df.apply_pickup_effect(rt, df.PICKUP_TYPES['ammo_shells'], None)
    assert rt['state']['player']['ammo']['shells'] == df.AMMO_MAX['shells']


def test_kamikaze_telegraphs_before_detonating():
    grid = [[0] * 20 for _ in range(20)]
    fake_level = {'grid': grid, 'w': 20, 'h': 20, 'name': 'fake'}
    rt = _make_runtime(fake_level)
    rt['state']['player']['x'], rt['state']['player']['y'] = 300, 300
    drone = df.spawn_enemy('drone', 300, 300)
    rt['enemies'] = [drone]
    df.update_enemies(rt, 1 / 30, None)
    assert drone['arm_timer'] > 0 and not drone['dead']  # armed, not instant
    for _ in range(30):
        rt['time'] += 1 / 30
        df.update_enemies(rt, 1 / 30, None)
    assert drone['dead']  # detonates once the arm timer runs out


def test_enemy_forgets_target_after_memory_duration():
    grid = [[0] * 20 for _ in range(20)]
    fake_level = {'grid': grid, 'w': 20, 'h': 20, 'name': 'fake'}
    rt = _make_runtime(fake_level)
    rt['state']['player']['x'], rt['state']['player']['y'] = 100, 100
    scalper = df.spawn_enemy('scalper', 100, 100)
    rt['enemies'] = [scalper]
    df.update_enemies(rt, 1 / 30, None)
    assert scalper['state'] == 'chase'
    rt['state']['player']['x'], rt['state']['player']['y'] = -9999, -9999
    rt['time'] += df.ENEMY_MEMORY_DURATION + 1
    df.update_enemies(rt, 1 / 30, None)
    assert scalper['state'] == 'idle'


def test_fire_weapon_consumes_ammo():
    rt = _make_runtime(df.LEVELS[0])
    rt['level'] = df.LEVELS[0]
    rt['state']['player']['ammo']['rounds'] = 10
    rt['state']['player']['current_weapon'] = 'packetspray'
    assert df.fire_weapon(rt, None) is True
    assert rt['state']['player']['ammo']['rounds'] == 9


# ─── Native sixel encoder ────────────────────────────────────────────────

def test_sixel_sprite_valid_framing():
    encoded = df.get_sprite_sixel('enemy_scalper')
    assert encoded is not None
    assert encoded.startswith(df._E + 'Pq')
    assert encoded.endswith(df._E + '\\')
    assert '"1;1;' in encoded  # raster attributes
    assert '#0;2;' in encoded  # at least one palette color definition


def test_sixel_sprite_data_chars_in_valid_range():
    import re
    encoded = df.get_sprite_sixel('barrel')
    body = encoded[encoded.index('q') + 1:]
    stripped = re.sub(r'\x1b\\|"1;1;\d+;\d+|#\d+(;2;\d+;\d+;\d+)?|!\d+|\$|-', '', body)
    assert all(63 <= ord(c) <= 126 for c in stripped)


def test_sixel_unknown_sprite_key_returns_none():
    assert df.get_sprite_sixel('nonexistent_sprite_xyz') is None


def test_sixel_cache_returns_same_object():
    a = df.get_sprite_sixel('pickup_health_small')
    b = df.get_sprite_sixel('pickup_health_small')
    assert a is b


# ─── ANSI renderer + sixel compositing ───────────────────────────────────

def _open_room_runtime():
    grid = [[1] * 20 for _ in range(20)]
    for y in range(1, 19):
        for x in range(1, 19):
            grid[y][x] = 0
    fake_level = {'grid': grid, 'w': 20, 'h': 20, 'name': 'test'}
    rt = _make_runtime(fake_level)
    rt['renderer'] = df.Renderer()
    rt['_last_sixel_rects'] = []
    rt['state']['player']['x'] = 5 * df.TILE
    rt['state']['player']['y'] = 5 * df.TILE
    rt['state']['player']['angle'] = 0
    return rt


def test_render_frame_ansi_contains_truecolor_escapes():
    rt = _open_room_runtime()
    frame = df.render_frame(rt, sixel_capable=False)
    assert (df._E + '[38;2;') in frame


def test_render_frame_diffing_shrinks_unchanged_repeat():
    rt = _open_room_runtime()
    frame1 = df.render_frame(rt, sixel_capable=False)
    frame2 = df.render_frame(rt, sixel_capable=False)  # nothing moved
    assert len(frame2) < len(frame1)


def test_sixel_sprite_compositing_and_ghost_prevention():
    rt = _open_room_runtime()
    enemy = df.spawn_enemy('scalper', 10 * df.TILE, 5 * df.TILE)
    rt['enemies'] = [enemy]

    frame1 = df.render_frame(rt, sixel_capable=True)
    assert (df._E + 'Pq') in frame1
    rects_after_1 = list(rt['_last_sixel_rects'])
    assert len(rects_after_1) == 1

    enemy['y'] += df.TILE * 2  # move the sprite
    frame2 = df.render_frame(rt, sixel_capable=True)
    assert rt['_last_sixel_rects'] != rects_after_1
    # the OLD rect's screen position must be force-resent this frame so
    # no stale sixel pixels linger at the sprite's previous location
    old_col_start, _, old_row_start, _ = rects_after_1[0]
    assert df._at(old_row_start + df.VIEWPORT_TOP_ROW, old_col_start + 1) in frame2


def test_glyph_fallback_draws_distinct_sprite_chars():
    # Enemies get a 2-row humanoid silhouette (head glyph 'o'/'@' over a
    # body glyph '¥') rather than a single floating dot -- a real reported
    # gap where a lone character didn't read as "a person" at all.
    rt = _open_room_runtime()
    rt['enemies'] = [df.spawn_enemy('scalper', 10 * df.TILE, 5 * df.TILE)]
    rt['pickups'] = [df.spawn_pickup('health_small', 10 * df.TILE, 6 * df.TILE)]
    rt['props'] = [df.spawn_barrel(10 * df.TILE, 4 * df.TILE)]
    frame = df.render_frame(rt, sixel_capable=False)
    assert 'o' in frame  # enemy head
    assert '¥' in frame  # enemy body
    assert '*' in frame
    assert 'O' in frame


def test_minimap_shows_player_and_nearby_enemy():
    rt = _open_room_runtime()
    rt['enemies'] = [df.spawn_enemy('scalper', 6 * df.TILE, 5 * df.TILE)]  # close to the player, within minimap radius
    frame = df.render_frame(rt, sixel_capable=False)
    assert '@' in frame  # player marker
    assert '*' in frame  # enemy dot


def test_minimap_omits_enemy_outside_radius():
    rt = _open_room_runtime()
    far = df.spawn_enemy('scalper', (5 + df.MINIMAP_RADIUS + 3) * df.TILE, 5 * df.TILE)
    rt['enemies'] = [far]
    # No enemy sprite should be visible in the raycast view either (well
    # outside FOV range at this distance in a bounded test room), so any
    # '*' present would have to come from the minimap -- confirms the
    # radius cutoff actually excludes it.
    frame = df.render_frame(rt, sixel_capable=False)
    assert '*' not in frame


def test_door_wall_glyph_is_sparse_not_solid_fill():
    # Real bug: every cell of a door's on-screen span used to be the fixed
    # 'D' letter with no distance shading, so standing close to a Security
    # Door (a wide, tall span up close) filled almost the entire viewport
    # with solid 'D' characters -- reported live as looking like a
    # rendering glitch rather than a door.
    cells = [df.wall_glyph_for_cell(df.DOOR_TYPE, 1.0, row, col)
             for row in range(df.VIEWPORT_H) for col in range(df.VIEWPORT_W)]
    door_letters = sum(1 for c in cells if c == 'D')
    assert door_letters > 0  # still identifiable somewhere
    assert door_letters < len(cells) / 3  # but not solid-filled
    # Exact diagonal stencil: (row + col) % 4 == 0.
    assert df.wall_glyph_for_cell(df.DOOR_TYPE, 1.0, 0, 0) == 'D'
    assert df.wall_glyph_for_cell(df.DOOR_TYPE, 1.0, 0, 1) != 'D'


def test_plain_wall_glyph_unaffected_by_per_cell_stencil():
    # Cinderblock etc. never used the letter-spam path -- confirm the new
    # per-cell function still just returns the normal distance-shaded glyph.
    assert df.wall_glyph_for_cell(1, 1.0, 3, 5) == df.SHADE_RAMP[0]
    assert df.wall_glyph_for_cell(1, 6.0, 3, 5) == df.SHADE_RAMP[2]


def test_level_intro_text_wraps_at_word_boundaries():
    # Real bug: the intro was written raw to an 80-col terminal with no
    # wrapping of our own, so the terminal's own auto-wrap split words
    # mid-letter ("stockpi" / "ling"). level_intro_text() must now wrap
    # at word boundaries itself before any line reaches the terminal.
    text = df.level_intro_text(df.LEVELS[0], 0)
    lines = text.split('\r\n')
    for line in lines:
        assert len(line) <= df.INTRO_TEXT_WIDTH
    # The whole word must land intact on one line -- not split across a
    # line break the way "stockpiling" -> "stockpi" / "ling" was reported.
    assert any('stockpiling' in line for line in lines)


# ─── Save/load ────────────────────────────────────────────────────────────

@pytest.fixture
def temp_username():
    name = 'pytest_darkforces_user'
    path = df._save_path(name)
    yield name
    if path.exists():
        path.unlink()


def test_save_load_round_trip(temp_username):
    state = df.new_game_state()
    state['player']['hp'] = 77
    state['level_index'] = 3
    df.save_game(state, temp_username)
    loaded = df.load_game(temp_username)
    assert loaded['player']['hp'] == 77
    assert loaded['level_index'] == 3


def test_load_missing_save_returns_none(temp_username):
    assert df.load_game(temp_username) is None


def test_deserialize_migrates_old_save_without_losing_new_fields():
    old_json = json.dumps({
        'level_index': 0,
        'player': {'hp': 80, 'max_hp': 100, 'armor': 20, 'max_armor': 100, 'level': 2, 'xp': 50,
                   'weapons': ['solder'], 'current_weapon': 'solder',
                   'ammo': {'shells': 5, 'cells': 2, 'cores': 1}, 'parts': 3},
        'parts_total': 3, 'kills': 5, 'deaths': 1,
    })
    restored = df.deserialize_state(old_json)
    assert 'multitool' in restored['player']['weapons']  # auto-granted starter weapon
    assert 'solder' in restored['player']['weapons']
    assert restored['player']['ammo']['rounds'] == 0  # new ammo type defaults cleanly
    assert restored['player']['ammo']['scopes'] == 0
    assert restored['player']['ammo']['shells'] == 5  # old ammo preserved
    assert restored['player']['ammo']['cells'] == 2
    assert restored['player']['keys'] == []
    assert restored['player']['hp'] == 80


# ─── Key parser ───────────────────────────────────────────────────────────

def test_keys_parser_handles_plain_and_csi_sequences():
    k = df.Keys()
    k.feed(b'w')
    k.feed(b'\x1b[A')
    k.feed(b' ')
    k.feed(b'\x1b[D')
    seen = []
    while True:
        v = k.next()
        if v is None:
            break
        seen.append(v)
    assert seen == ['w', 'UP', ' ', 'LEFT']


# ─── Full async game-loop smoke test ─────────────────────────────────────

class _FakeSession:
    def __init__(self):
        self.user = {'id': None}
        self._sixel_ok = False  # skip DA1 detection -- covered separately
        self._queue = asyncio.Queue()

    async def write(self, text):
        pass

    async def read_raw(self, n=64):
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=999)
        except asyncio.TimeoutError:
            return b''

    def queue_bytes(self, b):
        self._queue.put_nowait(b)


def test_run_game_moves_player_and_saves_on_quit():
    # Plain sync test wrapping asyncio.run(), matching this project's own
    # convention for async coverage elsewhere (e.g. test_mrc_integration.py)
    # rather than depending on the pytest-asyncio plugin, which isn't one
    # of this project's dependencies.
    async def _run():
        session = _FakeSession()
        username = 'pytest_darkforces_gameloop'
        save_path = df._save_path(username)
        if save_path.exists():
            save_path.unlink()

        start_x, start_y = df.grid_to_world(df.LEVELS[0]['player_start']['gx'],
                                             df.LEVELS[0]['player_start']['gy'])

        session.queue_bytes(b'X')  # dismiss the opening "press any key" prompt
        task = asyncio.create_task(df.run_game(session, username))
        await asyncio.sleep(0.3)

        for _ in range(15):
            session.queue_bytes(b'w')
            await asyncio.sleep(df.TICK)

        session.queue_bytes(b'\x1bX')  # ESC, plus a follow-up byte to disambiguate it from a CSI prefix
        await asyncio.sleep(df.TICK * 2)

        await asyncio.wait_for(task, timeout=5)

        saved = df.load_game(username)
        assert saved is not None
        dist_moved = math.hypot(saved['player']['x'] - start_x, saved['player']['y'] - start_y)
        assert dist_moved > 50  # genuinely walked forward, not just a no-op tick loop

        save_path.unlink()

    asyncio.run(_run())
