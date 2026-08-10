"""Persist Force Takeover / inject-instruction events for Phase 8 export."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import aiosqlite

from agent_guardian.schemas import utc_now


@dataclass(frozen=True)
class TakeoverEventRecord:
    signal_id: str
    agent_id: str
    kind: str
    instruction: str | None
    operator_id: str | None
    before_thought: str | None
    before_action: str | None
    screenshot_path: str | None
    tenant_id: str | None
    agent_type: str | None
    meta: dict[str, Any]
    created_at: str


class TakeoverStore:
    """SQLite-backed log of swarm Force Takeover signals."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def insert(
        self,
        *,
        signal_id: str,
        agent_id: str,
        kind: str,
        instruction: str | None = None,
        operator_id: str | None = None,
        before_thought: str | None = None,
        before_action: str | None = None,
        screenshot_path: str | None = None,
        tenant_id: str | None = None,
        agent_type: str | None = None,
        meta: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> TakeoverEventRecord:
        ts = created_at or utc_now().isoformat()
        payload = dict(meta or {})
        await self._conn.execute(
            """
            INSERT INTO takeover_events (
                signal_id, agent_id, kind, instruction, operator_id,
                before_thought, before_action, screenshot_path,
                tenant_id, agent_type, meta_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                agent_id,
                kind,
                instruction,
                operator_id,
                before_thought,
                before_action,
                screenshot_path,
                tenant_id,
                agent_type,
                json.dumps(payload, ensure_ascii=False),
                ts,
            ),
        )
        await self._conn.commit()
        return TakeoverEventRecord(
            signal_id=signal_id,
            agent_id=agent_id,
            kind=kind,
            instruction=instruction,
            operator_id=operator_id,
            before_thought=before_thought,
            before_action=before_action,
            screenshot_path=screenshot_path,
            tenant_id=tenant_id,
            agent_type=agent_type,
            meta=payload,
            created_at=ts,
        )

    async def get(self, signal_id: str) -> TakeoverEventRecord | None:
        cur = await self._conn.execute(
            "SELECT * FROM takeover_events WHERE signal_id = ?",
            (signal_id,),
        )
        row = await cur.fetchone()
        return _row_to_record(row) if row else None

    async def list_all(self, *, agent_id: str | None = None) -> list[TakeoverEventRecord]:
        if agent_id is None:
            cur = await self._conn.execute(
                "SELECT * FROM takeover_events ORDER BY created_at ASC"
            )
        else:
            cur = await self._conn.execute(
                "SELECT * FROM takeover_events WHERE agent_id = ? ORDER BY created_at ASC",
                (agent_id,),
            )
        rows = await cur.fetchall()
        return [_row_to_record(r) for r in rows]


def _row_to_record(row: aiosqlite.Row) -> TakeoverEventRecord:
    raw = row["meta_json"] or "{}"
    try:
        meta = json.loads(raw)
    except json.JSONDecodeError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return TakeoverEventRecord(
        signal_id=row["signal_id"],
        agent_id=row["agent_id"],
        kind=row["kind"],
        instruction=row["instruction"],
        operator_id=row["operator_id"],
        before_thought=row["before_thought"],
        before_action=row["before_action"],
        screenshot_path=row["screenshot_path"],
        tenant_id=row["tenant_id"],
        agent_type=row["agent_type"],
        meta=meta,
        created_at=row["created_at"],
    )
