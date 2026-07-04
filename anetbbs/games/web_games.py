# anetbbs/games/web_games.py
"""
Built-in Web Game Registry for ANetBBS Game Center.

Each entry describes a self-contained browser game that runs entirely
client-side and does not need a PTY/terminal session.
"""

WEB_GAMES = [
    {
        'name': 'Hangman',
        'slug': 'hangman',
        'description': 'Classic word guessing game. Guess the hidden word before '
                       'the hangman is drawn!',
        'category': 'puzzle',
        'icon': 'bi-alphabet',
        'web_game_module': 'hangman',
        'sort_order': 10,
    },
    {
        'name': 'Trivia Challenge',
        'slug': 'trivia',
        'description': 'Test your knowledge with multiple-choice trivia questions '
                       'across various categories.',
        'category': 'puzzle',
        'icon': 'bi-question-circle',
        'web_game_module': 'trivia',
        'sort_order': 20,
    },
    {
        'name': 'Number Guesser',
        'slug': 'numguess',
        'description': 'I\'m thinking of a number between 1 and 100. How many '
                       'guesses will it take you?',
        'category': 'puzzle',
        'icon': 'bi-123',
        'web_game_module': 'numguess',
        'sort_order': 30,
    },
    {
        'name': 'Snake',
        'slug': 'snake',
        'description': 'Classic snake game — eat the food, grow longer, '
                       'don\'t hit the walls!',
        'category': 'action',
        'icon': 'bi-arrow-repeat',
        'web_game_module': 'snake',
        'sort_order': 40,
    },
    {
        'name': 'Tic Tac Toe',
        'slug': 'tictactoe',
        'description': 'Play Tic Tac Toe against the computer. Can you beat '
                       'the AI?',
        'category': 'strategy',
        'icon': 'bi-grid-3x3',
        'web_game_module': 'tictactoe',
        'sort_order': 50,
    },
    {
        'name': 'Memory Match',
        'slug': 'memory',
        'description': 'Flip cards and find matching pairs. Test your memory!',
        'category': 'puzzle',
        'icon': 'bi-layers',
        'web_game_module': 'memory',
        'sort_order': 60,
    },
    {
        'name': 'Typing Speed Test',
        'slug': 'typing',
        'description': 'How fast can you type? Take the challenge and measure '
                       'your WPM!',
        'category': 'other',
        'icon': 'bi-keyboard',
        'web_game_module': 'typing',
        'sort_order': 70,
    },
    {
        'name': 'Minesweeper',
        'slug': 'minesweeper',
        'description': 'Classic Minesweeper — clear the minefield without '
                       'triggering any mines!',
        'category': 'puzzle',
        'icon': 'bi-bomb',
        'web_game_module': 'minesweeper',
        'sort_order': 80,
    },
    {
        'name': '2048',
        'slug': '2048',
        'description': 'Slide and merge tiles to reach the legendary 2048 tile!',
        'category': 'puzzle',
        'icon': 'bi-grid-fill',
        'web_game_module': '2048',
        'sort_order': 90,
    },
    {
        'name': 'Text Adventure',
        'slug': 'adventure',
        'description': 'A classic parser-based text adventure. Explore a dark '
                       'dungeon and find the treasure!',
        'category': 'rpg',
        'icon': 'bi-map',
        'web_game_module': 'adventure',
        'sort_order': 100,
    },
    {
        'name': 'Klondike Solitaire',
        'slug': 'solitaire',
        'description': 'Classic Klondike solitaire with drag-and-drop cards. '
                       'Build all four suits from Ace to King!',
        'category': 'cards',
        'icon': 'bi-suit-heart',
        'web_game_module': 'solitaire',
        'sort_order': 110,
    },
    {
        'name': 'Video Poker',
        'slug': 'videopoker',
        'description': 'Jacks or Better video poker. Hold your cards, draw, '
                       'and aim for the royal flush!',
        'category': 'cards',
        'icon': 'bi-suit-spade',
        'web_game_module': 'videopoker',
        'sort_order': 120,
    },
    {
        'name': "Texas Hold'em",
        'slug': 'holdem',
        'description': "No-limit Texas Hold'em against up to 4 CPU opponents. "
                       "Easy, Medium, or Hard AI — can you take their chips?",
        'category': 'cards',
        'icon': 'bi-suit-club',
        'web_game_module': 'holdem',
        'sort_order': 130,
    },
    {
        'name': 'Blackjack',
        'slug': 'blackjack',
        'description': 'Classic casino blackjack vs. the dealer. Hit, stand, '
                       'double down, or split your way to 21!',
        'category': 'cards',
        'icon': 'bi-suit-diamond',
        'web_game_module': 'blackjack',
        'sort_order': 140,
    },
    {
        'name': 'Slot Machines',
        'slug': 'slots',
        'description': 'Three beautifully animated slot machines: Classic Bars, '
                       'Lucky Fruits, and Retro BBS. Spin to win!',
        'category': 'casino',
        'icon': 'bi-dice-5',
        'web_game_module': 'slots',
        'sort_order': 150,
    },
    {
        'name': 'Galaga',
        'slug': 'galaga',
        'description': 'Classic arcade shooter! Defend Earth from waves of '
                       'alien formations. Watch out for dive-bombing bosses!',
        'category': 'action',
        'icon': 'bi-rocket-takeoff',
        'web_game_module': 'galaga',
        'sort_order': 160,
    },
    {
        'name': 'Tetris',
        'slug': 'tetris',
        'description': 'The legendary falling-block puzzle game! Clear lines, '
                       'level up, and chase the high score. Ghost piece, hard '
                       'drop, and wall-kick rotation included.',
        'category': 'puzzle',
        'icon': 'bi-grid-1x2',
        'web_game_module': 'tetris',
        'sort_order': 170,
    },
    {
        'name': 'Breakout',
        'slug': 'breakout',
        'description': 'Arkanoid-style brick breaker with power-ups! Wide paddle, '
                       'multi-ball, laser cannon, and more. Clear all bricks to '
                       'advance to the next level.',
        'category': 'action',
        'icon': 'bi-circle',
        'web_game_module': 'breakout',
        'sort_order': 180,
    },
    {
        'name': 'Ebook Reader',
        'slug': 'ebooks',
        'description': 'Search and read free public-domain classics from Project '
                       'Gutenberg in a clean, book-styled reading view. '
                       'Bookmarks, reading history, and downloads included.',
        'category': 'other',
        'icon': 'bi-book',
        'web_game_module': 'ebooks',
        'sort_order': 190,
    },
]


def get_web_game_info(module_name):
    """Return the metadata dict for a built-in web game by module name."""
    for game in WEB_GAMES:
        if game['web_game_module'] == module_name:
            return game
    return None
