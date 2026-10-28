"""SQLite memory: every turn persisted, restored on next launch. Zero ORM."""
from __future__ import annotations
import sqlite3, threading, time, pathlib

DB_PATH = pathlib.Path.home() / ".mahoraga" / "memory.db"

# One connection PER THREAD. Turns run on a worker thread while the UI thread
# keeps reading history, and sqlite3.Connection is bound to its creating thread —
# a shared connection raises "SQLite objects created in a thread can only be
# used in that same thread". Thread-local connections avoid that without a lock.
_local = threading.local()


def _db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")     # readers don't block the writer
        conn.execute("""CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY, role TEXT, content TEXT, ts REAL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS kv(
            key TEXT PRIMARY KEY, value TEXT)""")
        conn.commit()
        _local.conn = conn
    return conn

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

def search_messages(query: str, limit: int = 20) -> list[tuple[str, str]]:
    """Grep-ish search over stored conversation history (newest first)."""
    rows = _db().execute(
        "SELECT role, content FROM messages WHERE content LIKE ? "
        "ORDER BY id DESC LIMIT ?", (f"%{query}%", limit)).fetchall()
    return [(r[0], r[1]) for r in rows]


def clear_messages():
    _db().execute("DELETE FROM messages")
    _db().commit()

def kv_set(key: str, value: str):
    _db().execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, value))
    _db().commit()

def kv_get(key: str, default: str = "") -> str:
    row = _db().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row[0] if row else default
