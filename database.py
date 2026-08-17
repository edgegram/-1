import sqlite3
from contextlib import contextmanager

DB_PATH = "casino.db"
START_BALANCE = 1000


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
                last_bonus TEXT
            )
        """)
        # migration safety net for DBs created before last_bonus existed
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "last_bonus" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_bonus TEXT")

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
            conn.execute(
                "INSERT INTO users (user_id, username, balance) VALUES (?, ?, ?)",
                (user_id, username, START_BALANCE),
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


def get_group_top(chat_id: int, limit: int = 10):
    with get_conn() as conn:
        return conn.execute("""
            SELECT u.username, u.balance
            FROM users u
            JOIN group_seen g ON g.user_id = u.user_id
            WHERE g.chat_id = ?
            ORDER BY u.balance DESC
            LIMIT ?
        """, (chat_id, limit)).fetchall()
