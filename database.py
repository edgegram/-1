import sqlite3
import os
from contextlib import contextmanager

# Point this at a mounted persistent volume in production (e.g. DB_PATH=/data/casino.db on Railway),
# otherwise the database lives on the container's ephemeral disk and resets on every redeploy/restart.
DB_PATH = os.getenv("DB_PATH", "casino.db")
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)
START_BALANCE = 1000  # fallback default; the live value lives in settings["start_balance"]

DEFAULT_SETTINGS = {
    "start_balance": "1000",
    "daily_bonus": "500",
    "daily_streak_step": "50",   # extra golda per consecutive daily-streak day
    "daily_streak_cap": "9",     # streak days after which the bonus stops growing
    "min_bet": "10",
    "max_bet": "50000",
    "lottery_duration_seconds": "120",
    "lottery_house_edge": "0.05",
    "work_cooldown_minutes": "30",
    "work_min": "50",
    "work_max": "300",
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
            "SELECT user_id, username, balance FROM users ORDER BY balance DESC"
        ).fetchall()
    return [r for r in rows if r["user_id"] not in exclude_ids][:limit]


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
            SELECT u.user_id, u.username, u.balance
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
