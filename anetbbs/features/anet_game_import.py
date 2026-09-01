# anetbbs/features/anet_game_import.py
"""
Bulk-import tool: pull the live game list from A-Net Online's own
rlogin game server (https://a-net-online.lol/gameserver/) and turn it
into `door_rlogin` Game rows, so a sysop doesn't have to hand-enter
450+ games one at a time via Admin -> Door Games -> Add Game.

Scraping logic (scrape_games below) intentionally mirrors Jerry's own
reference scraper script byte-for-byte in structure -- same CSS
selectors, same name/code split logic, same "Added" tag detection --
so this reads the exact same page the same way his script already
does, rather than reinventing HTML-parsing from scratch and risking a
subtly different (and wrong) result.

Every imported game reuses the SAME host/password/BBS-tag already
configured on the bundled "A-Net Game Server" Game row (slug
a-net-game-server, seeded in web_app.py's _create_default_data()) --
it's the same remote server, so there's nothing new to coordinate with
Jerry, the sysop just picks which categories to bring in.
"""
import logging
import re

import requests

logger = logging.getLogger(__name__)

ANET_GAMESERVER_URL = 'https://a-net-online.lol/gameserver/index.xjs'
_FETCH_TIMEOUT = 15


class AnetGameImportError(Exception):
    """Raised when the game list can't be fetched or parsed at all."""


def scrape_games(url=ANET_GAMESERVER_URL, timeout=_FETCH_TIMEOUT):
    """Fetch and parse the A-Net Game Server listing.

    Returns a list of dicts: {'name', 'code', 'category', 'is_new'}.
    Raises AnetGameImportError on a network/parse failure -- callers
    show this to the sysop rather than crashing the admin page.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise AnetGameImportError(
            'The beautifulsoup4 package is not installed -- run '
            '`pip install -e .` again to pick up the new dependency.') from exc

    try:
        resp = requests.get(url, timeout=timeout,
                            headers={'User-Agent': 'ANetBBS/game-import'})
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise AnetGameImportError(
            f'Could not reach {url}: {exc}') from exc

    soup = BeautifulSoup(resp.text, 'html.parser')
    games = []

    # Ported directly from Jerry's own scraper (anet_door_games.py) --
    # same selectors, same name/code split, same "Added" tag check.
    for category_div in soup.select('.code-category'):
        cat_header = category_div.find('h3')
        if not cat_header:
            continue
        category = cat_header.get_text(strip=True)

        for li in category_div.select('ul.flex-list > li'):
            code_span = li.find('span', class_='door-code')
            if not code_span:
                continue
            code = code_span.text.strip()

            game_text = li.get_text(separator='', strip=True)
            if code in game_text:
                before, *_ = game_text.split(code, 1)
                name = before.rstrip('- ').strip()
            else:
                name = game_text

            new_tag = li.find('span', class_='-tag')
            is_new = bool(new_tag and 'Added' in new_tag.text)

            if name and code:
                games.append({
                    'name': name,
                    'code': code,
                    'category': category,
                    'is_new': is_new,
                })

    if not games:
        raise AnetGameImportError(
            f'Fetched {url} but found no games on the page -- the site '
            'layout may have changed.')

    return games


def group_by_category(games):
    """games -> {category_name: [game, ...]}, preserving first-seen
    category order (matches on-page order, not alphabetical)."""
    grouped = {}
    for g in games:
        grouped.setdefault(g['category'], []).append(g)
    return grouped


def category_form_key(category_name):
    """Deterministic, pure function of the category name -- used as an
    HTML form field suffix so the GET-rendered review page and the
    POST handler agree on field names without needing any server-side
    session/temp-table state between the two requests."""
    key = re.sub(r'[^a-z0-9]+', '-', category_name.lower()).strip('-')
    return key or 'category'


def slug_for_code(code):
    """A door code like 'LORD408' -> Game.slug 'anet-lord408'. The
    'anet-' prefix avoids colliding with a sysop's own locally-added
    door sharing a similar short name (e.g. a local DOS LORD install
    already using slug 'lord')."""
    cleaned = re.sub(r'[^a-z0-9]+', '-', code.lower()).strip('-')
    return f'anet-{cleaned}' if cleaned else None


def build_game_kwargs(game, category_slug, host_port, password, bbs_tag,
                      max_nodes=4, sort_order=0):
    """The exact Game(**kwargs) this scraped entry becomes -- a
    door_rlogin pointed at the same server/credentials as the bundled
    'A-Net Game Server' row, direct-launching via xtrn=<code>."""
    return {
        'name': game['name'],
        'slug': slug_for_code(game['code']),
        'description': f"A-Net Game Server: {game['category']} (auto-imported)",
        'category': category_slug,
        'game_type': 'door_rlogin',
        'executable_path': host_port,
        'command_line_args': f'@USER@ {password} xtrn={game["code"]}',
        'rlogin_bbs_tag': bbs_tag,
        'max_nodes': max_nodes,
        'sort_order': sort_order,
        'is_active': True,
    }


def base_server_credentials():
    """Read host/password/tag off the bundled 'A-Net Game Server' Game
    row (slug a-net-game-server) so every imported game reuses the
    exact same already-configured remote-server identity -- no new
    credentials for the sysop to enter. Returns
    (host_port, password, bbs_tag) or raises AnetGameImportError if
    that row is missing/misconfigured (should not happen on a normal
    install -- it's bundled and active by default)."""
    from ..models import Game
    base = Game.query.filter_by(slug='a-net-game-server').first()
    if base is None:
        raise AnetGameImportError(
            "The bundled \"A-Net Game Server\" game (slug "
            "a-net-game-server) wasn't found -- configure that first "
            "at Admin → Door Games, then try the import again.")
    host_port = (base.executable_path or '').strip()
    args = (base.command_line_args or '').strip()
    # command_line_args is '<user template> <password> [terminal]' --
    # the password is always the second whitespace-separated token.
    parts = args.split()
    password = parts[1] if len(parts) >= 2 else ''
    if not host_port or not password:
        raise AnetGameImportError(
            'The bundled "A-Net Game Server" game is missing its '
            'server address or password -- check its configuration at '
            'Admin → Door Games before importing.')
    return host_port, password, (base.rlogin_bbs_tag or '').strip()
