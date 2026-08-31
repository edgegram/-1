import sqlite3
import os
from contextlib import contextmanager

# Point this at a mounted persistent volume in production (e.g. DB_PATH=/data/casino.db on Railway),
# otherwise the database lives on the container's ephemeral disk and resets on every redeploy/restart.
DB_PATH = os.getenv("DB_PATH", "casino.db")
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)
START_BALANCE = 100  # fallback default; the live value lives in settings["start_balance"]

DEFAULT_CRYPTO_PRICES = {
    "BTC": 6_000_000.0,
    "ETH": 250_000.0,
    "TON": 300.0,
    "DOGE": 10.0,
}

DEFAULT_SETTINGS = {
    "start_balance": "100",
    "daily_bonus": "500",
    "daily_streak_step": "50",   # extra golda per consecutive daily-streak day
    "daily_streak_cap": "9",     # streak days after which the bonus stops growing
    "min_bet": "1000",
    "max_bet": "200000",
    "max_bet_percent_of_balance": "15",
    "max_single_payout": "5000000",
    "lottery_duration_seconds": "120",
    "lottery_house_edge": "0.05",
    "coinflip_dice_house_edge": "0.05",
    "work_cooldown_minutes": "30",
    "work_pay_multiplier": "1.0",
    "currency_code": "RUB",
    "property_tax_rate": "0.00001",
    "car_tax_rate": "0.000005",
    "business_tax_percent": "10",
    "bills_block_threshold": "5000",
    "deposit_rate_1d": "0.02",
    "deposit_rate_7d": "0.10",
    "deposit_rate_30d": "0.35",
    "loan_max_amount": "50000",
    "loan_interest_rate": "0.15",
    "loan_term_days": "7",
    "loan_penalty_rate_per_day": "0.05",
    "warn_threshold": "3",
    "warn_action": "mute",
    "warn_mute_minutes": "60",
    "mute_default_minutes": "60",
    "business_max_quantity": "10",
}


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER NOT NULL DEFAULT 1000,
                games_played INTEGER NOT NULL DEFAULT 0,
                total_won INTEGER NOT NULL DEFAULT 0,
                total_lost INTEGER NOT NULL DEFAULT 0,
                last_bonus TEXT,
                streak_days INTEGER NOT NULL DEFAULT 0
            )
        """)
        # migration safety net for DBs created before newer columns existed
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "last_bonus" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_bonus TEXT")
        if "streak_days" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN streak_days INTEGER NOT NULL DEFAULT 0")
        if "banned" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN banned INTEGER NOT NULL DEFAULT 0")
        if "last_work" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_work TEXT")
        if "last_bills_paid" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_bills_paid TEXT")
        if "nickname" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS group_seen (
                chat_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY (chat_id, user_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

        conn.execute("""
            CREATE TABLE IF NOT EXISTS businesses_owned (
                user_id INTEGER,
                business_id INTEGER,
                quantity INTEGER NOT NULL DEFAULT 0,
                last_collect TEXT,
                PRIMARY KEY (user_id, business_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS property_owned (
                user_id INTEGER,
                property_id INTEGER,
                quantity INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, property_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS cars_owned (
                user_id INTEGER,
                car_id INTEGER,
                quantity INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, car_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER NOT NULL,
                rate REAL NOT NULL,
                term_days INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                withdrawn INTEGER NOT NULL DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS loans (
                user_id INTEGER PRIMARY KEY,
                principal INTEGER NOT NULL,
                remaining_debt INTEGER NOT NULL,
                taken_at TEXT NOT NULL,
                due_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS crypto_prices (
                symbol TEXT PRIMARY KEY,
                price REAL NOT NULL,
                prev_price REAL NOT NULL
            )
        """)
        for symbol, price in DEFAULT_CRYPTO_PRICES.items():
            conn.execute("INSERT OR IGNORE INTO crypto_prices (symbol, price, prev_price) VALUES (?, ?, ?)",
                         (symbol, price, price))

        conn.execute("""
            CREATE TABLE IF NOT EXISTS crypto_holdings (
                user_id INTEGER,
                symbol TEXT,
                amount REAL NOT NULL DEFAULT 0,
                avg_price REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, symbol)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                reason TEXT,
                warned_by INTEGER,
                warned_at TEXT
            )
        """)
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_or_create_user(user_id: int, username: str) -> sqlite3.Row:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            start = int(get_setting("start_balance", START_BALANCE))
            conn.execute(
                "INSERT INTO users (user_id, username, balance) VALUES (?, ?, ?)",
                (user_id, username, start),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        else:
            # keep username fresh
            conn.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
            conn.commit()
        return row


def get_balance(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row["balance"] if row else 0


def change_balance(user_id: int, delta: int, won: bool | None = None):
    with get_conn() as conn:
        conn.execute("UPDATE users SET balance = balance + ?, games_played = games_played + 1 WHERE user_id = ?",
                     (delta, user_id))
        if won is True:
            conn.execute("UPDATE users SET total_won = total_won + ? WHERE user_id = ?", (delta, user_id))
        elif won is False:
            conn.execute("UPDATE users SET total_lost = total_lost + ? WHERE user_id = ?", (-delta, user_id))
        conn.commit()


def set_balance(user_id: int, value: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET balance = ? WHERE user_id = ?", (value, user_id))
        conn.commit()


def top_players(limit: int = 10):
    with get_conn() as conn:
        return conn.execute(
            "SELECT username, balance FROM users ORDER BY balance DESC LIMIT ?", (limit,)
        ).fetchall()


def top_players_excluding(exclude_ids: set[int], limit: int = 10):
    """Leaderboard of regular players only — admins/owner excluded so staff balances
    (topped up via /give etc.) don't crowd out real players."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, username, nickname, balance FROM users ORDER BY balance DESC"
        ).fetchall()
    return [r for r in rows if r["user_id"] not in exclude_ids][:limit]


def top_players_all(limit: int = 30):
    """Full leaderboard including staff — used by /tops full for admins/owner."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT user_id, username, nickname, balance FROM users ORDER BY balance DESC LIMIT ?", (limit,)
        ).fetchall()


def get_all_users_full():
    """Every user row — used to compute net worth for /forbes and /richlist."""
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users").fetchall()


def take_balance(user_id: int, amount: int) -> int:
    """Deduct up to `amount`, clamped so balance never goes below 0. Returns the amount actually removed."""
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            return 0
        removed = min(amount, row["balance"])
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (removed, user_id))
        conn.commit()
        return removed


def admin_add_balance(user_id: int, amount: int):
    """Admin credit/debit that does NOT count as a game (no games_played/won/lost change)."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()


def get_last_bonus(user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT last_bonus FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row["last_bonus"] if row else None


def set_last_bonus(user_id: int, iso_timestamp: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET last_bonus = ? WHERE user_id = ?", (iso_timestamp, user_id))
        conn.commit()


def get_last_work(user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT last_work FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row["last_work"] if row else None


def get_last_bills_paid(user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT last_bills_paid FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row["last_bills_paid"] if row else None


def set_last_bills_paid(user_id: int, iso_timestamp: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET last_bills_paid = ? WHERE user_id = ?", (iso_timestamp, user_id))
        conn.commit()


def set_last_work(user_id: int, iso_timestamp: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET last_work = ? WHERE user_id = ?", (iso_timestamp, user_id))
        conn.commit()


def get_all_user_ids() -> list[int]:
    with get_conn() as conn:
        return [r["user_id"] for r in conn.execute("SELECT user_id FROM users").fetchall()]


def user_exists(user_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is not None


def add_admin(user_id: int, added_by: int):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)", (user_id, added_by))
        conn.commit()


def remove_admin(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        conn.commit()


def is_admin_db(user_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone() is not None


def list_admins() -> list[int]:
    with get_conn() as conn:
        return [r["user_id"] for r in conn.execute("SELECT user_id FROM admins").fetchall()]


def record_group_activity(chat_id: int, user_id: int):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO group_seen (chat_id, user_id) VALUES (?, ?)", (chat_id, user_id))
        conn.commit()


def get_group_top(chat_id: int, exclude_ids: set[int] = frozenset(), limit: int = 10):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT u.user_id, u.username, u.nickname, u.balance
            FROM users u
            JOIN group_seen g ON g.user_id = u.user_id
            WHERE g.chat_id = ?
            ORDER BY u.balance DESC
        """, (chat_id,)).fetchall()
    return [r for r in rows if r["user_id"] not in exclude_ids][:limit]


def get_group_user_ids(chat_id: int) -> list[int]:
    with get_conn() as conn:
        return [r["user_id"] for r in
                conn.execute("SELECT user_id FROM group_seen WHERE chat_id = ?", (chat_id,)).fetchall()]


# ---------------- Settings (economy config, owner-tunable) ----------------

def get_setting(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value):
    with get_conn() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
        conn.commit()


def get_all_settings() -> dict:
    with get_conn() as conn:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings").fetchall()}


# ---------------- Daily bonus streak ----------------

def get_streak(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT streak_days FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row["streak_days"] if row else 0


def set_streak(user_id: int, streak: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET streak_days = ? WHERE user_id = ?", (streak, user_id))
        conn.commit()


# ---------------- Stats / admin lookups ----------------

def get_user_row(user_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()


def get_bot_stats() -> dict:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS total_users,
                   COALESCE(SUM(balance), 0) AS total_balance,
                   COALESCE(SUM(games_played), 0) AS total_games
            FROM users
        """).fetchone()
        return dict(row)


# ---------------- Bans ----------------

def set_banned(user_id: int, banned: bool):
    with get_conn() as conn:
        conn.execute("UPDATE users SET banned = ? WHERE user_id = ?", (1 if banned else 0, user_id))
        conn.commit()


def is_banned(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT banned FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return bool(row["banned"]) if row else False


# ---------------- Owner: full economy reset ----------------

def reset_all_balances(new_balance: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET balance = ?", (new_balance,))
        conn.commit()


# ---------------- Businesses (passive income) ----------------

def get_user_businesses(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM businesses_owned WHERE user_id = ? AND quantity > 0", (user_id,)
        ).fetchall()


def get_business_owned(user_id: int, business_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM businesses_owned WHERE user_id = ? AND business_id = ?", (user_id, business_id)
        ).fetchone()


def buy_business(user_id: int, business_id: int, qty: int, now_iso: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT quantity FROM businesses_owned WHERE user_id = ? AND business_id = ?",
            (user_id, business_id)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO businesses_owned (user_id, business_id, quantity, last_collect) VALUES (?, ?, ?, ?)",
                (user_id, business_id, qty, now_iso)
            )
        else:
            conn.execute(
                "UPDATE businesses_owned SET quantity = quantity + ? WHERE user_id = ? AND business_id = ?",
                (qty, user_id, business_id)
            )
        conn.commit()


def set_business_last_collect(user_id: int, business_id: int, now_iso: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE businesses_owned SET last_collect = ? WHERE user_id = ? AND business_id = ?",
            (now_iso, user_id, business_id)
        )
        conn.commit()


# ---------------- Property (cosmetic status items) ----------------

def get_user_property(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM property_owned WHERE user_id = ? AND quantity > 0", (user_id,)
        ).fetchall()


def buy_property(user_id: int, property_id: int, qty: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT quantity FROM property_owned WHERE user_id = ? AND property_id = ?",
            (user_id, property_id)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO property_owned (user_id, property_id, quantity) VALUES (?, ?, ?)",
                (user_id, property_id, qty)
            )
        else:
            conn.execute(
                "UPDATE property_owned SET quantity = quantity + ? WHERE user_id = ? AND property_id = ?",
                (qty, user_id, property_id)
            )
        conn.commit()


# ---------------- Cars (tiered garage — cosmetic status items) ----------------

def get_user_cars(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cars_owned WHERE user_id = ? AND quantity > 0", (user_id,)
        ).fetchall()


def buy_car(user_id: int, car_id: int, qty: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT quantity FROM cars_owned WHERE user_id = ? AND car_id = ?",
            (user_id, car_id)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO cars_owned (user_id, car_id, quantity) VALUES (?, ?, ?)",
                (user_id, car_id, qty)
            )
        else:
            conn.execute(
                "UPDATE cars_owned SET quantity = quantity + ? WHERE user_id = ? AND car_id = ?",
                (qty, user_id, car_id)
            )
        conn.commit()


# ---------------- Bank: deposits (вклады) ----------------

def create_deposit(user_id: int, amount: int, rate: float, term_days: int, created_at: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO deposits (user_id, amount, rate, term_days, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, rate, term_days, created_at)
        )
        conn.commit()
        return cur.lastrowid


def get_active_deposits(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM deposits WHERE user_id = ? AND withdrawn = 0", (user_id,)
        ).fetchall()


def get_deposit(deposit_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM deposits WHERE id = ?", (deposit_id,)).fetchone()


def mark_deposit_withdrawn(deposit_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE deposits SET withdrawn = 1 WHERE id = ?", (deposit_id,))
        conn.commit()


# ---------------- Bank: loans (кредиты) ----------------

def get_active_loan(user_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM loans WHERE user_id = ?", (user_id,)).fetchone()


def create_loan(user_id: int, principal: int, remaining_debt: int, taken_at: str, due_at: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO loans (user_id, principal, remaining_debt, taken_at, due_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, principal, remaining_debt, taken_at, due_at)
        )
        conn.commit()


def update_loan_debt(user_id: int, new_remaining: int):
    with get_conn() as conn:
        conn.execute("UPDATE loans SET remaining_debt = ? WHERE user_id = ?", (new_remaining, user_id))
        conn.commit()


def close_loan(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM loans WHERE user_id = ?", (user_id,))
        conn.commit()


# ---------------- Crypto ----------------

def get_crypto_price(symbol: str):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM crypto_prices WHERE symbol = ?", (symbol,)).fetchone()


def get_all_crypto_prices():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM crypto_prices ORDER BY symbol").fetchall()


def update_crypto_price(symbol: str, new_price: float, prev_price: float):
    with get_conn() as conn:
        conn.execute("UPDATE crypto_prices SET price = ?, prev_price = ? WHERE symbol = ?",
                     (new_price, prev_price, symbol))
        conn.commit()


def get_crypto_holding(user_id: int, symbol: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM crypto_holdings WHERE user_id = ? AND symbol = ?", (user_id, symbol)
        ).fetchone()


def get_user_crypto_holdings(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM crypto_holdings WHERE user_id = ? AND amount > 0", (user_id,)
        ).fetchall()


def upsert_crypto_buy(user_id: int, symbol: str, bought_amount: float, price: float):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT amount, avg_price FROM crypto_holdings WHERE user_id = ? AND symbol = ?",
            (user_id, symbol)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO crypto_holdings (user_id, symbol, amount, avg_price) VALUES (?, ?, ?, ?)",
                (user_id, symbol, bought_amount, price)
            )
        else:
            old_amount, old_avg = row["amount"], row["avg_price"]
            new_amount = old_amount + bought_amount
            new_avg = (old_amount * old_avg + bought_amount * price) / new_amount if new_amount > 0 else price
            conn.execute(
                "UPDATE crypto_holdings SET amount = ?, avg_price = ? WHERE user_id = ? AND symbol = ?",
                (new_amount, new_avg, user_id, symbol)
            )
        conn.commit()


def crypto_sell(user_id: int, symbol: str, sell_amount: float):
    with get_conn() as conn:
        conn.execute(
            "UPDATE crypto_holdings SET amount = amount - ? WHERE user_id = ? AND symbol = ?",
            (sell_amount, user_id, symbol)
        )
        conn.commit()


# ---------------- Warnings (group moderation) ----------------

def add_warning(chat_id: int, user_id: int, reason: str, warned_by: int, warned_at: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO warnings (chat_id, user_id, reason, warned_by, warned_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, user_id, reason, warned_by, warned_at)
        )
        conn.commit()


def get_warnings(chat_id: int, user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM warnings WHERE chat_id = ? AND user_id = ? ORDER BY id", (chat_id, user_id)
        ).fetchall()


def count_warnings(chat_id: int, user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)
        ).fetchone()
        return row["c"] if row else 0


def remove_last_warning(chat_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM warnings WHERE chat_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1",
            (chat_id, user_id)
        ).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM warnings WHERE id = ?", (row["id"],))
        conn.commit()
        return True


def clear_warnings(chat_id: int, user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        conn.commit()


# ---------------- Nicknames ----------------

def set_nickname(user_id: int, nickname: str | None):
    with get_conn() as conn:
        conn.execute("UPDATE users SET nickname = ? WHERE user_id = ?", (nickname, user_id))
        conn.commit()


def get_nickname(user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT nickname FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row["nickname"] if row else None
