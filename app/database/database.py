"""SQLite connection + schema initialisation + migrations."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app import config

SCHEMA_VERSION = 3
SCHEMA_FILE = Path(__file__).with_name("schema.sql")

# Columns added to `students` in schema v2 (Telegram / n8n integration).
V2_STUDENT_COLUMNS = [
    ("telegram_username", "TEXT"),
    ("join_code", "TEXT"),
    ("join_code_expires_at", "TEXT"),
    ("preferred_send_time", "TEXT NOT NULL DEFAULT '08:00'"),
    ("lesson_delivered_at", "TEXT"),          # NULL = current lesson not sent to the student yet
    ("last_daily_on", "TEXT"),                # YYYY-MM-DD of the last daily message / /next release
    ("daily_failures", "INTEGER NOT NULL DEFAULT 0"),
    ("daily_failure_on", "TEXT"),
    ("released_date", "TEXT"),                # for the "max lessons per day" limit
    ("released_today", "INTEGER NOT NULL DEFAULT 0"),
]

# Column added to `students` in schema v3 (Gmail): the address the student writes from.
V3_STUDENT_COLUMNS = [
    ("email", "TEXT"),
]

V2_SQL = """
CREATE TABLE IF NOT EXISTS processed_updates (
    update_id   INTEGER PRIMARY KEY,          -- Telegram update_id (also gives the next offset)
    chat_id     INTEGER,
    received_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_students_join_code
    ON students(join_code) WHERE join_code IS NOT NULL;
"""

V3_SQL = """
CREATE TABLE IF NOT EXISTS processed_emails (
    message_id  TEXT PRIMARY KEY,             -- Gmail / RFC Message-ID: one email is evaluated at most once
    sender      TEXT,
    received_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_students_email
    ON students(lower(email)) WHERE email IS NOT NULL;
"""


def connect(db_path: str | Path | None = None, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a connection. Use ':memory:' for tests.

    The API passes check_same_thread=False because FastAPI may run a request's
    dependency and endpoint on different worker threads. Each connection is still
    used by one request at a time.
    """
    path = str(db_path if db_path is not None else config.DB_PATH)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring any older database up to SCHEMA_VERSION. Safe to run repeatedly."""
    existing = _columns(conn, "students")
    added_any = False
    for name, decl in V2_STUDENT_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE students ADD COLUMN {name} {decl}")
            added_any = True
    if added_any:
        # Students created before v2 who are mid-lesson already saw their lesson:
        # mark it delivered so the new "answers only after delivery" rule does not lock them out.
        conn.execute(
            "UPDATE students SET lesson_delivered_at = COALESCE(last_activity_at, created_at) "
            "WHERE current_lesson_id IS NOT NULL AND lesson_delivered_at IS NULL "
            "AND last_activity_at IS NOT NULL"
        )
    conn.executescript(V2_SQL)
    existing = _columns(conn, "students")
    for name, decl in V3_STUDENT_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE students ADD COLUMN {name} {decl}")
    conn.executescript(V3_SQL)


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables/indexes if they do not exist yet, then migrate (idempotent)."""
    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    _migrate(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
