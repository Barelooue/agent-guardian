"""Persistent intervention store with CAS updates."""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

import aiosqlite

from agent_guardian.daemon.state_machine import can_transition, is_open, is_terminal
from agent_guardian.schemas import (
    ChannelName,
    ErrorCode,
    ErrorPayload,
    InterventionCreated,
    InterventionDecision,
    InterventionRequest,
    InterventionStatus,
    InterventionUpdated,
    utc_now,
)


class StoreError(Exception):
    def __init__(self, code: ErrorCode, message: str, **kwargs: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.kwargs = kwargs


@dataclass
class InterventionRecord:
    intervention_id: str
    client_request_id: str
    status: InterventionStatus
    request: InterventionRequest
    decision: InterventionDecision | None
    active_channel: ChannelName | None
    expires_at: str
    created_at: str
    updated_at: str
    version: int

    def to_updated(self, *, idempotent: bool = False) -> InterventionUpdated:
        return InterventionUpdated(
            intervention_id=self.intervention_id,
            status=self.status,
            version=self.version,
            updated_at=self.updated_at,  # type: ignore[arg-type]
            idempotent=idempotent,
            selected_option_id=self.decision.option_id if self.decision else None,
            decision=self.decision,
            error=None,
            active_channel=self.active_channel,
        )

    def to_created(self, *, reused: bool) -> InterventionCreated:
        return InterventionCreated(
            intervention_id=self.intervention_id,
            client_request_id=self.client_request_id,
            status=self.status,
            expires_at=self.expires_at,  # type: ignore[arg-type]
            created_at=self.created_at,  # type: ignore[arg-type]
            reused=reused,
        )


def _row_to_record(row: aiosqlite.Row) -> InterventionRecord:
    decision_raw = row["decision_json"]
    decision = InterventionDecision.model_validate_json(decision_raw) if decision_raw else None
    active = row["active_channel"]
    return InterventionRecord(
        intervention_id=row["intervention_id"],
        client_request_id=row["client_request_id"],
        status=InterventionStatus(row["status"]),
        request=InterventionRequest.model_validate_json(row["request_json"]),
        decision=decision,
        active_channel=ChannelName(active) if active else None,
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


class InterventionStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()

    async def get(self, intervention_id: str) -> InterventionRecord | None:
        async with self._conn.execute(
            "SELECT * FROM interventions WHERE intervention_id = ?",
            (intervention_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_record(row) if row else None

    async def get_by_client_request_id(self, client_request_id: str) -> InterventionRecord | None:
        async with self._conn.execute(
            "SELECT * FROM interventions WHERE client_request_id = ?",
            (client_request_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_record(row) if row else None

    async def create(self, request: InterventionRequest) -> InterventionCreated:
        async with self._lock:
            existing = await self.get_by_client_request_id(request.client_request_id)
            if existing is not None:
                return existing.to_created(reused=True)

            now = utc_now()
            expires = now + timedelta(seconds=request.timeout_seconds)
            intervention_id = str(uuid4())
            record_status = InterventionStatus.PENDING

            try:
                await self._conn.execute("BEGIN IMMEDIATE")
                await self._conn.execute(
                    """
                    INSERT INTO interventions (
                        intervention_id, client_request_id, status, request_json,
                        decision_json, active_channel, expires_at, created_at, updated_at, version
                    ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, 1)
                    """,
                    (
                        intervention_id,
                        request.client_request_id,
                        record_status.value,
                        request.model_dump_json(),
                        expires.isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                await self._conn.commit()
            except aiosqlite.IntegrityError:
                await self._conn.rollback()
                raced = await self.get_by_client_request_id(request.client_request_id)
                if raced is None:
                    raise StoreError(
                        ErrorCode.AG_PERSISTENCE_ERROR,
                        "integrity error without existing row",
                    ) from None
                return raced.to_created(reused=True)
            except Exception:
                await self._conn.rollback()
                raise

            return InterventionCreated(
                intervention_id=intervention_id,
                client_request_id=request.client_request_id,
                status=record_status,
                expires_at=expires,
                created_at=now,
                reused=False,
            )

    async def cas_update(
        self,
        intervention_id: str,
        *,
        target: InterventionStatus,
        decision: InterventionDecision | None = None,
        active_channel: ChannelName | None = None,
        set_active_channel: bool = False,
        error: ErrorPayload | None = None,
    ) -> InterventionUpdated:
        """Compare-and-set status transition under an exclusive transaction."""
        async with self._lock:
            return await self._cas_update_unlocked(
                intervention_id,
                target=target,
                decision=decision,
                active_channel=active_channel,
                set_active_channel=set_active_channel,
                error=error,
            )

    async def _cas_update_unlocked(
        self,
        intervention_id: str,
        *,
        target: InterventionStatus,
        decision: InterventionDecision | None = None,
        active_channel: ChannelName | None = None,
        set_active_channel: bool = False,
        error: ErrorPayload | None = None,
    ) -> InterventionUpdated:
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            async with self._conn.execute(
                "SELECT * FROM interventions WHERE intervention_id = ?",
                (intervention_id,),
            ) as cur:
                row = await cur.fetchone()

            if row is None:
                await self._conn.rollback()
                raise StoreError(
                    ErrorCode.AG_NOT_FOUND,
                    f"intervention not found: {intervention_id}",
                    intervention_id=intervention_id,
                )

            current = _row_to_record(row)

            if current.status == target and (
                decision is None
                or (
                    current.decision is not None
                    and current.decision.option_id == decision.option_id
                )
            ):
                await self._conn.commit()
                return current.to_updated(idempotent=True)

            if is_terminal(current.status):
                await self._conn.commit()
                raise StoreError(
                    ErrorCode.AG_ALREADY_TERMINAL,
                    f"Intervention already in terminal state {current.status.value}",
                    intervention_id=intervention_id,
                    current_status=current.status.value,
                    details={
                        "attempted_transition": f"{current.status.value}->{target.value}",
                    },
                )

            if not can_transition(current.status, target):
                await self._conn.rollback()
                raise StoreError(
                    ErrorCode.AG_STATE_CONFLICT,
                    f"illegal transition {current.status.value} -> {target.value}",
                    intervention_id=intervention_id,
                    current_status=current.status.value,
                )

            if target == InterventionStatus.RESOLVED:
                if decision is None:
                    await self._conn.rollback()
                    raise StoreError(
                        ErrorCode.AG_INVALID_REQUEST,
                        "RESOLVED requires decision",
                        intervention_id=intervention_id,
                    )
                valid_ids = {o.id for o in current.request.options}
                if decision.option_id not in valid_ids:
                    await self._conn.rollback()
                    raise StoreError(
                        ErrorCode.AG_INVALID_REQUEST,
                        f"unknown option_id: {decision.option_id}",
                        intervention_id=intervention_id,
                    )

            now = utc_now().isoformat()
            new_version = current.version + 1
            decision_json = (
                decision.model_dump_json() if decision is not None else row["decision_json"]
            )
            if set_active_channel:
                channel_value = active_channel.value if active_channel is not None else None
            else:
                channel_value = row["active_channel"]

            cur = await self._conn.execute(
                """
                UPDATE interventions
                SET status = ?, decision_json = ?, active_channel = ?,
                    updated_at = ?, version = ?
                WHERE intervention_id = ?
                  AND status = ?
                  AND version = ?
                """,
                (
                    target.value,
                    decision_json,
                    channel_value,
                    now,
                    new_version,
                    intervention_id,
                    current.status.value,
                    current.version,
                ),
            )
            if cur.rowcount != 1:
                await self._conn.rollback()
                latest = await self.get(intervention_id)
                if latest and is_terminal(latest.status):
                    raise StoreError(
                        ErrorCode.AG_ALREADY_TERMINAL,
                        f"Intervention already in terminal state {latest.status.value}",
                        intervention_id=intervention_id,
                        current_status=latest.status.value,
                    )
                raise StoreError(
                    ErrorCode.AG_STATE_CONFLICT,
                    "CAS update lost race",
                    intervention_id=intervention_id,
                    current_status=latest.status.value if latest else None,
                )

            if is_terminal(target):
                await self._conn.execute(
                    """
                    UPDATE callback_tokens
                    SET revoked_at = ?
                    WHERE intervention_id = ? AND revoked_at IS NULL
                    """,
                    (now, intervention_id),
                )

            await self._conn.commit()
        except StoreError:
            raise
        except Exception:
            await self._conn.rollback()
            raise

        updated = await self.get(intervention_id)
        assert updated is not None
        result = updated.to_updated(idempotent=False)
        if error is not None:
            result = result.model_copy(update={"error": error})
        return result

    async def mark_awaiting(
        self,
        intervention_id: str,
        *,
        channel: ChannelName,
        notified: bool = True,
    ) -> InterventionUpdated:
        """PENDING -> NOTIFIED -> AWAITING_HUMAN (or direct PENDING -> AWAITING_HUMAN)."""
        async with self._lock:
            record = await self.get(intervention_id)
            if record is None:
                raise StoreError(
                    ErrorCode.AG_NOT_FOUND,
                    f"intervention not found: {intervention_id}",
                    intervention_id=intervention_id,
                )
            if is_terminal(record.status):
                return record.to_updated(idempotent=True)

            if record.status == InterventionStatus.PENDING and notified:
                await self._cas_update_unlocked(
                    intervention_id,
                    target=InterventionStatus.NOTIFIED,
                    active_channel=channel,
                    set_active_channel=True,
                )
                return await self._cas_update_unlocked(
                    intervention_id,
                    target=InterventionStatus.AWAITING_HUMAN,
                    active_channel=channel,
                    set_active_channel=True,
                )

            if record.status == InterventionStatus.PENDING and not notified:
                return await self._cas_update_unlocked(
                    intervention_id,
                    target=InterventionStatus.AWAITING_HUMAN,
                    active_channel=channel,
                    set_active_channel=True,
                )

            if record.status == InterventionStatus.NOTIFIED:
                return await self._cas_update_unlocked(
                    intervention_id,
                    target=InterventionStatus.AWAITING_HUMAN,
                    active_channel=channel,
                    set_active_channel=True,
                )

            return await self._cas_update_unlocked(
                intervention_id,
                target=InterventionStatus.AWAITING_HUMAN,
                active_channel=channel,
                set_active_channel=True,
            )

    async def apply_timeout_if_expired(self, intervention_id: str) -> InterventionUpdated | None:
        record = await self.get(intervention_id)
        if record is None or not is_open(record.status):
            return None
        expires = record.expires_at
        # stored as isoformat
        from datetime import datetime

        exp_dt = datetime.fromisoformat(expires)
        if utc_now() < exp_dt:
            return None
        try:
            return await self.cas_update(
                intervention_id,
                target=InterventionStatus.TIMEOUT,
            )
        except StoreError as exc:
            if exc.code == ErrorCode.AG_ALREADY_TERMINAL:
                latest = await self.get(intervention_id)
                return latest.to_updated(idempotent=True) if latest else None
            raise

    async def list_open(self) -> list[InterventionRecord]:
        async with self._conn.execute(
            "SELECT * FROM interventions WHERE status IN (?, ?, ?) ORDER BY created_at ASC",
            (
                InterventionStatus.PENDING.value,
                InterventionStatus.NOTIFIED.value,
                InterventionStatus.AWAITING_HUMAN.value,
            ),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_record(r) for r in rows]

    async def list_by_status(self, *statuses: InterventionStatus) -> list[InterventionRecord]:
        if not statuses:
            return []
        placeholders = ", ".join("?" for _ in statuses)
        async with self._conn.execute(
            f"""
            SELECT * FROM interventions
            WHERE status IN ({placeholders})
            ORDER BY created_at ASC
            """,
            tuple(s.value for s in statuses),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_record(r) for r in rows]

    async def update_request(self, intervention_id: str, request: InterventionRequest) -> None:
        """Rewrite request_json (e.g. after snapshot materialization)."""
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            try:
                await self._conn.execute(
                    """
                    UPDATE interventions
                    SET request_json = ?, updated_at = ?
                    WHERE intervention_id = ?
                    """,
                    (request.model_dump_json(), utc_now().isoformat(), intervention_id),
                )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise

    async def allocate_callback_token(self, intervention_id: str, option_ids: list[str]) -> str:
        token = secrets.token_urlsafe(6)  # ~8 chars
        now = utc_now().isoformat()
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            try:
                await self._conn.execute(
                    """
                    INSERT INTO callback_tokens (
                        callback_token, intervention_id, option_ids_json, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (token, intervention_id, json.dumps(option_ids), now),
                )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise
        return token

    async def resolve_callback_data(self, callback_data: str) -> tuple[str, str] | None:
        """Decode `{token}:{opt_index}` into (intervention_id, option_id)."""
        if ":" not in callback_data or len(callback_data.encode("utf-8")) > 64:
            return None
        token, _, index_s = callback_data.partition(":")
        async with self._conn.execute(
            """
            SELECT intervention_id, option_ids_json, revoked_at
            FROM callback_tokens WHERE callback_token = ?
            """,
            (token,),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        try:
            idx = int(index_s)
            option_ids = json.loads(row["option_ids_json"])
            return row["intervention_id"], option_ids[idx]
        except (ValueError, IndexError, TypeError, json.JSONDecodeError):
            return None
