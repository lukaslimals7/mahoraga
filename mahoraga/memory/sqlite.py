"""SQLite memory: every turn persisted, restored on next launch. Zero ORM."""
from __future__ import annotations
import sqlite3, time, pathlib

DB_PATH = pathlib.Path.home() / ".mahoraga" / "memory.db"
_conn: sqlite3.Connection | None = None

def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH)
        _conn.execute("""CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY, role TEXT, content TEXT, ts REAL)""")
        _conn.execute("""CREATE TABLE IF NOT EXISTS kv(
            key TEXT PRIMARY KEY, value TEXT)""")
        _conn.commit()
    return _conn

def init():
    _db()

def save_message(role: str, content: str):
    _db().execute("INSERT INTO messages(role, content, ts) VALUES(?,?,?)",
                  (role, content, time.time()))
    _db().commit()

def load_recent(n: int = 30) -> list[tuple[str, str]]:
    rows = _db().execute(
        "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    return list(reversed(rows))

def clear_messages():
    _db().execute("DELETE FROM messages")
    _db().commit()

def kv_set(key: str, value: str):
    _db().execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, value))
    _db().commit()

def kv_get(key: str, default: str = "") -> str:
    row = _db().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row[0] if row else default
