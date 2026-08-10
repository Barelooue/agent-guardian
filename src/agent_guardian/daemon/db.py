"""SQLite schema bootstrap (WAL)."""

from __future__ import annotations

import aiosqlite

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS interventions (
    intervention_id TEXT PRIMARY KEY,
    client_request_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    decision_json TEXT,
    active_channel TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS callback_tokens (
    callback_token TEXT PRIMARY KEY,
    intervention_id TEXT NOT NULL,
    option_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (intervention_id) REFERENCES interventions(intervention_id)
);

CREATE TABLE IF NOT EXISTS takeover_events (
    signal_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    instruction TEXT,
    operator_id TEXT,
    before_thought TEXT,
    before_action TEXT,
    screenshot_path TEXT,
    tenant_id TEXT,
    agent_type TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_interventions_status
    ON interventions(status);
CREATE INDEX IF NOT EXISTS idx_callback_tokens_intervention
    ON callback_tokens(intervention_id);
CREATE INDEX IF NOT EXISTS idx_takeover_events_agent
    ON takeover_events(agent_id);
CREATE INDEX IF NOT EXISTS idx_takeover_events_created
    ON takeover_events(created_at);
"""


async def init_db(db_path: str) -> aiosqlite.Connection:
    # isolation_level=None → manual BEGIN/COMMIT in store (CAS)
    conn = await aiosqlite.connect(db_path, isolation_level=None)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(SCHEMA_SQL)
    return conn
