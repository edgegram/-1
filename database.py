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
                total_lost INTEGER NOT NULL DEFAULT 0
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
