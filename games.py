import random

# ---------- Coinflip ----------
def play_coinflip(choice: str) -> tuple[bool, str]:
    """choice: 'orel' or 'reshka'"""
    result = random.choice(["orel", "reshka"])
    win = result == choice
    label = "Орёл" if result == "orel" else "Решка"
    return win, label


# ---------- Dice (кости 1-6, ставка больше/меньше 3.5) ----------
def play_dice(bet_type: str) -> tuple[bool, int]:
    """bet_type: 'more' (4-6) or 'less' (1-3)"""
    roll = random.randint(1, 6)
    if bet_type == "more":
        win = roll >= 4
    else:
        win = roll <= 3
    return win, roll


# ---------- Slots ----------
SLOT_SYMBOLS = ["🍒", "🍋", "🍇", "🔔", "⭐", "💎"]
SLOT_WEIGHTS = [30, 25, 20, 12, 8, 5]  # rarer symbols pay more

PAYOUTS = {
    "🍒": 2,
    "🍋": 2.5,
    "🍇": 3,
    "🔔": 5,
    "⭐": 10,
    "💎": 25,
}


def spin_slots() -> tuple[list[str], float]:
    reels = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)
    if reels[0] == reels[1] == reels[2]:
        multiplier = PAYOUTS[reels[0]]
    elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        multiplier = 1.2
    else:
        multiplier = 0
    return reels, multiplier


# ---------- Crash (имитация трейдинга/графика) ----------
def generate_crash_point() -> float:
    """
    Generates the multiplier at which the 'chart' crashes.
    House edge baked in via distribution (similar style to real crash games).
    """
    r = random.random()
    if r < 0.02:
        return 1.0  # instant crash, ~2% of the time
    # heavier tail is rarer, using inverse distribution
    crash = 0.99 / (1 - r)
    return max(1.0, round(crash, 2))


# ---------- Roulette ----------
RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def spin_roulette() -> tuple[int, str]:
    number = random.randint(0, 36)
    if number == 0:
        color = "green"
    elif number in RED_NUMBERS:
        color = "red"
    else:
        color = "black"
    return number, color


def check_roulette_win(bet: str, number: int, color: str) -> tuple[bool, float]:
    """bet: 'red'/'black'/'green', 'even'/'odd', 'low'(1-18)/'high'(19-36), or a number '0'-'36'"""
    if bet in ("red", "black", "green"):
        win = bet == color
        multiplier = 14.0 if bet == "green" else 2.0
        return win, multiplier if win else 0
    if bet in ("even", "odd"):
        if number == 0:
            return False, 0
        win = (number % 2 == 0) == (bet == "even")
        return win, 2.0 if win else 0
    if bet in ("low", "high"):
        if number == 0:
            return False, 0
        win = (number <= 18) == (bet == "low")
        return win, 2.0 if win else 0
    win = int(bet) == number
    return win, 36.0 if win else 0


# ---------- Rock-Paper-Scissors vs bot ----------
RPS_OPTIONS = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def play_rps(choice: str) -> tuple[str, str]:
    """returns (bot_choice, result) where result is 'win'/'lose'/'draw'"""
    bot_choice = random.choice(list(RPS_OPTIONS.keys()))
    if bot_choice == choice:
        result = "draw"
    elif RPS_BEATS[choice] == bot_choice:
        result = "win"
    else:
        result = "lose"
    return bot_choice, result


# ---------- Wheel of fortune ----------
WHEEL_SEGMENTS = [0, 0.5, 1, 1.5, 2, 3, 5, 10]
WHEEL_WEIGHTS = [15, 20, 20, 15, 12, 10, 6, 2]


def spin_wheel() -> float:
    return random.choices(WHEEL_SEGMENTS, weights=WHEEL_WEIGHTS, k=1)[0]


# ---------- Blackjack (21) ----------
CARD_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
CARD_SUITS = ["♠", "♥", "♦", "♣"]
CARD_VALUES = {"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
               "10": 10, "J": 10, "Q": 10, "K": 10, "A": 11}


def draw_card() -> tuple[str, str]:
    return random.choice(CARD_RANKS), random.choice(CARD_SUITS)


def hand_value(cards: list[tuple[str, str]]) -> int:
    value = sum(CARD_VALUES[rank] for rank, _ in cards)
    aces = sum(1 for rank, _ in cards if rank == "A")
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value


def is_natural_blackjack(cards: list[tuple[str, str]]) -> bool:
    return len(cards) == 2 and hand_value(cards) == 21


def format_hand(cards: list[tuple[str, str]]) -> str:
    return " ".join(f"{rank}{suit}" for rank, suit in cards)


def dealer_play(cards: list[tuple[str, str]]) -> list[tuple[str, str]]:
    while hand_value(cards) < 17:
        cards.append(draw_card())
    return cards


# ---------- Higher / Lower (карта больше/меньше) ----------
CARD_RANK_ORDER = {r: i for i, r in enumerate(
    ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"], start=2
)}


def play_higher_lower(guess: str) -> tuple[tuple[str, str], tuple[str, str], str]:
    """Returns (first_card, second_card, outcome) where outcome is 'win'/'lose'/'push'"""
    first = draw_card()
    second = draw_card()
    v1, v2 = CARD_RANK_ORDER[first[0]], CARD_RANK_ORDER[second[0]]
    if v1 == v2:
        outcome = "push"
    elif (guess == "higher" and v2 > v1) or (guess == "lower" and v2 < v1):
        outcome = "win"
    else:
        outcome = "lose"
    return first, second, outcome


# ---------- Mines ----------
GRID_SIZE = 5  # 5x5 = 25 cells
MINES_HOUSE_EDGE = 0.97


def generate_mine_positions(mines_count: int) -> set[tuple[int, int]]:
    all_cells = [(r, c) for r in range(GRID_SIZE) for c in range(GRID_SIZE)]
    return set(random.sample(all_cells, mines_count))


def next_mines_multiplier(current_multiplier: float, revealed_count: int, mines_count: int) -> float:
    total = GRID_SIZE * GRID_SIZE
    unopened = total - revealed_count
    safe_unopened = unopened - mines_count
    if safe_unopened <= 0:
        return current_multiplier
    return round(current_multiplier * (unopened / safe_unopened) * MINES_HOUSE_EDGE, 3)
