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


# ---------- Трейд верх/вниз ----------
def play_trade(guess: str) -> tuple[float, float, str]:
    """Simulates a short price move. Returns (price_before, price_after, outcome) where
    outcome is 'win'/'lose'/'push'."""
    price_before = round(random.uniform(50, 500), 2)
    change_percent = random.uniform(-8, 8)
    price_after = round(price_before * (1 + change_percent / 100), 2)
    if price_after == price_before:
        outcome = "push"
    elif (guess == "up" and price_after > price_before) or (guess == "down" and price_after < price_before):
        outcome = "win"
    else:
        outcome = "lose"
    return price_before, price_after, outcome


# ---------- Work (/work) ----------
# Each job: (label, min_pay, max_pay) — pay tiers roughly mirror real-world pay differences
# (a doctor or banker earns more per shift than a cleaner or factory hand).
WORK_JOBS = [
    ("🚛 Дальнобойщик — съездил в рейс", 400, 800),
    ("⚡ Электрик — чинил проводку", 350, 700),
    ("🩺 Врач — принял смену в больнице", 800, 1500),
    ("📚 Учитель — провёл уроки в школе", 200, 450),
    ("🏭 Завод — отстоял смену у станка", 150, 350),
    ("⛏ Вахта — отработал вахтовую смену", 600, 1200),
    ("🎭 Агентство — рандомная подработка на съёмке", 100, 900),
    ("🧱 Строитель — клал кирпичи на объекте", 300, 600),
    ("🧹 Уборщик — убрал офисное здание", 100, 250),
    ("📦 Доставщик — развозил заказы", 150, 400),
    ("🚕 Такси — покатал пассажиров по городу", 250, 550),
    ("💻 Программист — закрыл тикеты в проекте", 700, 1400),
    ("🎨 Дизайнер — сделал макет для клиента", 400, 900),
    ("💇 Парикмахер — постриг клиентов", 250, 500),
    ("📷 Фотограф — отснял фотосессию", 300, 700),
    ("🛍 Продавец — отстоял смену в магазине", 150, 350),
    ("🍔 Доставщик еды — развозил заказы из ресторанов", 150, 350),
    ("🏦 Банкир — закрыл сделки в офисе", 900, 1800),
    ("🧮 Бухгалтер — свёл отчётность", 400, 800),
    ("💪 Грузчик — разгрузил фуру", 150, 350),
]

DELIVERY_SERVICES = ["Ozon", "Wildberries", "Яндекс Маркет", "СДЭК", "Авито Доставка", "Почта России"]


def do_work() -> tuple[str, int]:
    label, min_pay, max_pay = random.choice(WORK_JOBS)
    if label.startswith("📦 Доставщик —"):
        label = f"📦 Доставщик {random.choice(DELIVERY_SERVICES)} — развозил заказы"
    pay = random.randint(min_pay, max_pay)
    return label, pay


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


# ---------- Businesses (passive income shop) ----------
# id: (name, price, income_per_hour, emoji, image_filename)
BUSINESSES = {
    1: ("Ларёк с шаурмой", 5_000, 50, "🌯", "biz1.png"),
    2: ("Автомойка", 15_000, 150, "🚿", "biz2.png"),
    3: ("Барбершоп", 30_000, 300, "💈", "biz3.png"),
    4: ("Кофейня", 50_000, 500, "☕", "biz4.png"),
    5: ("Автосервис", 100_000, 1_000, "🔧", "biz5.png"),
    6: ("Ресторан", 250_000, 2_500, "🍽", "biz6.png"),
    7: ("Строительная фирма", 500_000, 5_000, "🏗", "biz7.png"),
    8: ("Логистическая компания", 1_000_000, 10_000, "🚛", "biz8.png"),
    9: ("IT-компания", 2_500_000, 25_000, "💻", "biz9.png"),
    10: ("Сеть отелей", 5_000_000, 50_000, "🏨", "biz10.png"),
}

# Passive income stops accumulating past this many hours since last collection,
# so leaving the bot untouched for weeks doesn't create huge free payouts.
BUSINESS_MAX_ACCRUAL_HOURS = 24

# Utility bills / vehicle tax stop accruing past this many hours since last payment,
# so ignoring the bot for weeks doesn't create an absurd debt.
BILLS_MAX_ACCRUAL_HOURS = 72


# ---------- Property (real estate, aviation & watercraft — cosmetic status items) ----------
# category_key: (label, emoji)
PROPERTY_CATEGORIES = {
    "apartment": ("Квартиры", "🏢"),
    "house": ("Дома", "🏡"),
    "plane": ("Самолёты", "✈️"),
    "helicopter": ("Вертолёты", "🚁"),
    "boat": ("Водный транспорт", "🛥"),
}

# id: (category_key, name, price, image_filename)
PROPERTY = {
    1: ("apartment", "Однушка в спальном районе", 2_000_000, "apt1.png"),
    2: ("apartment", "Двушка в новостройке", 4_500_000, "apt2.png"),
    3: ("apartment", "Трёшка в центре города", 9_000_000, "apt3.png"),
    4: ("apartment", "Квартира с панорамным видом", 18_000_000, "apt4.png"),
    5: ("apartment", "Элитный пентхаус", 35_000_000, "apt5.png"),

    6: ("house", "Дачный домик", 3_000_000, "house1.png"),
    7: ("house", "Коттедж в посёлке", 12_000_000, "house2.png"),
    8: ("house", "Таунхаус", 22_000_000, "house3.png"),
    9: ("house", "Загородный дом премиум-класса", 45_000_000, "house4.png"),
    10: ("house", "Особняк с бассейном", 90_000_000, "house5.png"),

    11: ("plane", "Cessna 172", 60_000_000, "plane1.png"),
    12: ("plane", "Beechcraft King Air", 250_000_000, "plane2.png"),
    13: ("plane", "Cessna Citation CJ4", 700_000_000, "plane3.png"),
    14: ("plane", "Bombardier Challenger 350", 1_800_000_000, "plane4.png"),
    15: ("plane", "Gulfstream G650", 4_500_000_000, "plane5.png"),

    16: ("helicopter", "Robinson R44", 40_000_000, "heli1.png"),
    17: ("helicopter", "Bell 407", 120_000_000, "heli2.png"),
    18: ("helicopter", "Airbus H145", 350_000_000, "heli3.png"),
    19: ("helicopter", "AgustaWestland AW139", 800_000_000, "heli4.png"),
    20: ("helicopter", "Sikorsky S-92", 2_000_000_000, "heli5.png"),

    21: ("boat", "Катер", 20_000_000, "07_boat.png"),
    22: ("boat", "Яхта", 80_000_000, "09_yacht.png"),
}


# ---------- Cars (tiered garage — cosmetic status items) ----------
# tier_key: (label, emoji)
CAR_TIERS = {
    "budget": ("Бюджет", "🚗"),
    "mid": ("Средние", "🚙"),
    "comfort": ("Комфорт", "🚘"),
    "business": ("Бизнес", "🖤"),
    "sport": ("Спорт", "🏎"),
    "hyper": ("Гиперкар", "🏁"),
}

# id: (tier_key, model_name, price, image_filename)
CARS = {
    1:  ("budget", "Lada Granta", 700_000, "01_budget.png"),
    2:  ("budget", "Renault Logan", 900_000, "02_budget.png"),
    3:  ("budget", "Hyundai Solaris", 1_200_000, "03_budget.png"),
    4:  ("budget", "Kia Rio", 1_300_000, "04_budget.png"),
    5:  ("budget", "Volkswagen Polo", 1_500_000, "05_budget.png"),

    6:  ("mid", "Skoda Octavia", 2_200_000, "06_mid.png"),
    7:  ("mid", "Toyota Camry", 2_800_000, "07_mid.png"),
    8:  ("mid", "Mazda 6", 2_500_000, "08_mid.png"),
    9:  ("mid", "Volkswagen Passat", 2_600_000, "09_mid.png"),
    10: ("mid", "Honda Accord", 2_900_000, "10_mid.png"),

    11: ("comfort", "BMW 3 Series", 4_500_000, "11_comfort.png"),
    12: ("comfort", "Mercedes-Benz C-Class", 4_800_000, "12_comfort.png"),
    13: ("comfort", "Audi A4", 4_600_000, "13_comfort.png"),
    14: ("comfort", "Lexus ES", 5_200_000, "14_comfort.png"),
    15: ("comfort", "Volvo S60", 4_300_000, "15_comfort.png"),

    16: ("business", "Mercedes-Benz S-Class", 12_000_000, "16_business.png"),
    17: ("business", "BMW 7 Series", 11_500_000, "17_business.png"),
    18: ("business", "Audi A8", 10_800_000, "18_business.png"),
    19: ("business", "Genesis G90", 9_500_000, "19_business.png"),
    20: ("business", "Bentley Flying Spur", 25_000_000, "20_business.png"),

    21: ("sport", "Porsche 911", 15_000_000, "21_sport.png"),
    22: ("sport", "Nissan GT-R", 14_000_000, "22_sport.png"),
    23: ("sport", "BMW M4", 13_000_000, "23_sport.png"),
    24: ("sport", "Chevrolet Corvette", 12_500_000, "24_sport.png"),
    25: ("sport", "Audi RS7", 16_000_000, "25_sport.png"),

    26: ("hyper", "Lamborghini Aventador", 45_000_000, "26_hyper.png"),
    27: ("hyper", "Ferrari SF90", 55_000_000, "27_hyper.png"),
    28: ("hyper", "McLaren 720S", 40_000_000, "28_hyper.png"),
    29: ("hyper", "Bugatti Chiron", 250_000_000, "29_hyper.png"),
    30: ("hyper", "Koenigsegg Jesko", 300_000_000, "30_hyper.png"),
}
