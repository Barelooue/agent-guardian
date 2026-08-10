"""Daemon application service: create / decide / cancel / notify."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from agent_guardian.daemon.channels.base import Channel, DeliveryStatus
from agent_guardian.daemon.channels.terminal import TerminalChannel
from agent_guardian.daemon.events import EventHub
from agent_guardian.daemon.media import MediaStore
from agent_guardian.daemon.store import InterventionRecord, InterventionStore, StoreError
from agent_guardian.schemas import (
    ChannelName,
    DecisionSource,
    ErrorCode,
    InterventionCancel,
    InterventionCreated,
    InterventionDecision,
    InterventionRequest,
    InterventionStatus,
    InterventionUpdated,
    utc_now,
)
from agent_guardian.schemas.summary import InterventionSummary

logger = logging.getLogger(__name__)


class InterventionService:
    def __init__(
        self,
        store: InterventionStore,
        hub: EventHub,
        channels: dict[ChannelName, Channel] | None = None,
        *,
        enable_terminal_stdin: bool = True,
        default_channels: Sequence[ChannelName] | None = None,
        media: MediaStore | None = None,
    ) -> None:
        self.store = store
        self.hub = hub
        self.channels = channels or {ChannelName.TERMINAL: TerminalChannel()}
        self.enable_terminal_stdin = enable_terminal_stdin
        self.default_channels: list[ChannelName] = list(default_channels or [ChannelName.TERMINAL])
        self.media = media
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._message_ids: dict[str, str] = {}

    async def create(self, request: InterventionRequest) -> InterventionCreated:
        created = await self.store.create(request)
        if created.reused:
            return created

        # Materialize snapshot to disk URL (strip large base64 from persistence)
        deliver_request = request
        if self.media is not None and request.snapshot is not None:
            materialized = self.media.materialize(created.intervention_id, request.snapshot)
            deliver_request = request.model_copy(update={"snapshot": materialized})
            await self.store.update_request(created.intervention_id, deliver_request)

        task = asyncio.create_task(
            self._deliver(created.intervention_id, deliver_request),
            name=f"deliver-{created.intervention_id}",
        )
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return created

    async def list_open_summaries(self) -> list[InterventionSummary]:
        # Lazy-timeout pass
        opens = await self.store.list_open()
        out: list[InterventionSummary] = []
        for record in opens:
            timed = await self.store.apply_timeout_if_expired(record.intervention_id)
            if timed is not None:
                await self._revoke_remote(record.intervention_id)
                await self.hub.publish(timed)
                continue
            out.append(_to_summary(record))
        return out

    def _preferred_channels(self, request: InterventionRequest) -> list[ChannelName]:
        preferred = list(request.channels) if request.channels else list(self.default_channels)
        if ChannelName.TERMINAL not in preferred:
            preferred.append(ChannelName.TERMINAL)
        return preferred

    async def _deliver(self, intervention_id: str, request: InterventionRequest) -> None:
        preferred = self._preferred_channels(request)
        last_error: str | None = None

        for name in preferred:
            channel = self.channels.get(name)
            if channel is None:
                last_error = f"channel not configured: {name}"
                logger.info("%s — skip", last_error)
                continue

            token = None
            if name == ChannelName.TELEGRAM:
                option_ids = [o.id for o in request.options]
                token = await self.store.allocate_callback_token(intervention_id, option_ids)

            try:
                result = await channel.send_card(
                    intervention_id=intervention_id,
                    request=request,
                    callback_token=token,
                )
            except Exception as exc:
                logger.exception("channel %s failed", name)
                last_error = str(exc)
                continue

            if result.status == DeliveryStatus.DELIVERED:
                if result.channel_message_id:
                    self._message_ids[intervention_id] = result.channel_message_id
                try:
                    updated = await self.store.mark_awaiting(
                        intervention_id,
                        channel=name,
                        notified=True,
                    )
                    await self.hub.publish(updated)
                except StoreError as exc:
                    logger.warning("mark_awaiting failed: %s", exc.message)
                    return

                if name == ChannelName.TERMINAL and self.enable_terminal_stdin:
                    self._spawn_terminal_reader(intervention_id, request)
                logger.info("intervention %s delivered via %s", intervention_id, name.value)
                return

            last_error = result.detail or result.status.value
            logger.warning(
                "channel %s delivery not successful: %s — try next",
                name.value,
                last_error,
            )

        # All remote failed — protocol requires terminal fallback if available
        terminal = self.channels.get(ChannelName.TERMINAL)
        if terminal is not None and ChannelName.TERMINAL not in preferred[:-1]:
            # already tried terminal as last; if we got here terminal also failed
            pass

        try:
            updated = await self.store.cas_update(
                intervention_id,
                target=InterventionStatus.FAILED,
            )
            await self.hub.publish(updated)
        except StoreError:
            logger.error("failed to mark FAILED after channel errors: %s", last_error)

    def _spawn_terminal_reader(self, intervention_id: str, request: InterventionRequest) -> None:
        terminal = self.channels.get(ChannelName.TERMINAL)
        if not isinstance(terminal, TerminalChannel):
            return

        async def _run() -> None:
            choice = await terminal.prompt_choice(
                request, timeout_seconds=float(request.timeout_seconds)
            )
            if choice is None:
                return
            decision = InterventionDecision(
                intervention_id=intervention_id,
                option_id=choice,
                source=DecisionSource.TERMINAL,
                decided_at=utc_now(),
                operator_id="terminal:local",
                channel_message_id="stdin",
            )
            try:
                await self.decide(decision)
            except StoreError as exc:
                logger.info("terminal decision ignored: %s", exc.message)

        task = asyncio.create_task(_run(), name=f"stdin-{intervention_id}")
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def get_updated(self, intervention_id: str) -> InterventionUpdated:
        timed = await self.store.apply_timeout_if_expired(intervention_id)
        if timed is not None:
            await self._revoke_remote(intervention_id)
            await self.hub.publish(timed)
            return timed
        record = await self.store.get(intervention_id)
        if record is None:
            raise StoreError(
                ErrorCode.AG_NOT_FOUND,
                f"intervention not found: {intervention_id}",
                intervention_id=intervention_id,
            )
        return record.to_updated(idempotent=True)

    async def decide(self, decision: InterventionDecision) -> InterventionUpdated:
        await self.store.apply_timeout_if_expired(decision.intervention_id)
        updated = await self.store.cas_update(
            decision.intervention_id,
            target=InterventionStatus.RESOLVED,
            decision=decision,
        )
        if decision.channel_message_id:
            self._message_ids[decision.intervention_id] = decision.channel_message_id
        await self._revoke_remote(
            decision.intervention_id,
            channel_message_id=decision.channel_message_id,
        )
        await self.hub.publish(updated)
        return updated

    async def cancel(self, payload: InterventionCancel) -> InterventionUpdated:
        try:
            updated = await self.store.cas_update(
                payload.intervention_id,
                target=InterventionStatus.CANCELLED,
            )
        except StoreError as exc:
            if exc.code == ErrorCode.AG_ALREADY_TERMINAL:
                record = await self.store.get(payload.intervention_id)
                if record is None:
                    raise
                return record.to_updated(idempotent=True)
            raise

        await self._revoke_remote(payload.intervention_id)
        await self.hub.publish(updated)
        return updated

    async def _revoke_remote(
        self,
        intervention_id: str,
        *,
        channel_message_id: str | None = None,
    ) -> None:
        record = await self.store.get(intervention_id)
        if record is None or record.active_channel is None:
            return
        channel = self.channels.get(record.active_channel)
        if channel is None:
            return
        mid = channel_message_id or self._message_ids.get(intervention_id)
        try:
            await channel.revoke_card(
                intervention_id=intervention_id,
                channel_message_id=mid,
            )
        except Exception as exc:
            logger.debug("revoke_card failed: %s", exc)

    async def recover_open(self) -> None:
        """On startup: timeout expired opens; re-await the rest."""
        for record in await self.store.list_open():
            timed = await self.store.apply_timeout_if_expired(record.intervention_id)
            if timed is not None:
                await self._revoke_remote(record.intervention_id)
                await self.hub.publish(timed)
                continue
            if record.status in {
                InterventionStatus.PENDING,
                InterventionStatus.NOTIFIED,
                InterventionStatus.AWAITING_HUMAN,
            }:
                task = asyncio.create_task(
                    self._deliver(record.intervention_id, record.request),
                    name=f"recover-{record.intervention_id}",
                )
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)

    async def aclose(self) -> None:
        for task in list(self._bg_tasks):
            task.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks.clear()
        for ch in self.channels.values():
            close = getattr(ch, "aclose", None)
            if close is not None:
                await close()


def _to_summary(record: InterventionRecord) -> InterventionSummary:
    return InterventionSummary(
        intervention_id=record.intervention_id,
        client_request_id=record.client_request_id,
        status=record.status,
        title=record.request.title,
        reason=record.request.reason,
        options=record.request.options,
        snapshot=record.request.snapshot,
        active_channel=record.active_channel,
        expires_at=record.expires_at,  # type: ignore[arg-type]
        created_at=record.created_at,  # type: ignore[arg-type]
        version=record.version,
    )
