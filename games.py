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
