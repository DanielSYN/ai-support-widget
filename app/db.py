"""SQLite storage. Plain SQL keeps it easy to read; swap for PostgreSQL when it outgrows one file."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    greeting            TEXT NOT NULL DEFAULT 'Hi! How can I help you today?',
    accent_color        TEXT NOT NULL DEFAULT '#4f46e5',
    allowed_origins     TEXT NOT NULL DEFAULT '',  -- one origin per line
    extra_info          TEXT NOT NULL DEFAULT '',  -- free-form policies, opening hours, etc.
    monthly_message_cap INTEGER NOT NULL DEFAULT 1000,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS faqs (
    id       INTEGER PRIMARY KEY,
    bot_id   TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    bot_id     TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    answered        INTEGER,  -- assistant rows only: 0 when the knowledge base had no answer
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS handoffs (
    id              INTEGER PRIMARY KEY,
    bot_id          TEXT NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
    email           TEXT NOT NULL,
    note            TEXT NOT NULL DEFAULT '',
    resolved        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_conversations_bot ON conversations(bot_id, updated_at);
"""


def connect() -> sqlite3.Connection:
    # check_same_thread=False: FastAPI may open a request's connection on one worker thread
    # and commit on another. Each connection still belongs to exactly one request at a time.
    conn = sqlite3.connect(config.DATABASE_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as conn:
        conn.execute("PRAGMA journal_mode = WAL")  # readers don't block the writer, and vice versa
        conn.executescript(SCHEMA)


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request, committed if the request succeeds."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
