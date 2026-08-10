"""Python SDK: ask_human + async context manager guard()."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Self
from uuid import uuid4

import httpx

from agent_guardian.client.checkpoints import CheckpointStack
from agent_guardian.client.http import DaemonHttpClient
from agent_guardian.exceptions import (
    AgentGuardianError,
    InterventionCancelledError,
    InterventionDeniedError,
    InterventionFailedError,
    InterventionTimeoutError,
)
from agent_guardian.schemas import (
    TERMINAL_STATUSES,
    CancelReason,
    ChannelName,
    DecisionSource,
    InterventionCancel,
    InterventionDecision,
    InterventionRequest,
    InterventionStatus,
    InterventionUpdated,
    Option,
    OptionStyle,
    SnapshotRef,
    utc_now,
)
from agent_guardian.schemas.spatial import SpatialAnnotation
from agent_guardian.smart import SmartInterventionEngine, SmartSignal
from agent_guardian.snapshot import Region, try_capture_snapshot
from agent_guardian.ui.spatial import SpatialPromptInjector

logger = logging.getLogger(__name__)

_DEFAULT_DENY_IDS = frozenset({"deny", "reject", "cancel", "拒绝", "拒绝并回滚"})


@dataclass
class GuardStepResult:
    """Result of :meth:`AgentGuardian.guard_step`."""

    proceeded: bool
    signal: SmartSignal
    decision: InterventionUpdated | None = None
    spatial: SpatialAnnotation | None = None
    spatial_prompt: str | None = None
    restored_state: Any | None = None


class AgentGuardian:
    """Client facade talking to the local Daemon."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8787",
        *,
        poll_interval: float = 1.0,
        deny_option_ids: Iterable[str] | None = None,
        local_terminal_prompt: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        prefer_websocket: bool = True,
        enable_smart: bool = True,
        smart_engine: SmartInterventionEngine | None = None,
    ) -> None:
        self.base_url = base_url
        self.poll_interval = poll_interval
        self.deny_option_ids = frozenset(deny_option_ids or _DEFAULT_DENY_IDS)
        # Prompt in the SDK process (second terminal). Daemon uvicorn logs drown stdin.
        self.local_terminal_prompt = local_terminal_prompt
        # ASGI transport (unit tests) cannot speak real WebSocket URLs
        self.prefer_websocket = False if transport is not None else prefer_websocket
        self._http = DaemonHttpClient(base_url, transport=transport)
        self.enable_smart = enable_smart
        self.smart = smart_engine or SmartInterventionEngine()
        self.checkpoints = CheckpointStack()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def ask_human(
        self,
        reason: str,
        options: Sequence[Option | dict[str, Any] | str],
        *,
        title: str = "Agent 需要你的确认",
        context: dict[str, Any] | None = None,
        timeout: int = 300,
        channels: Sequence[ChannelName | str] | None = None,
        client_request_id: str | None = None,
        agent_id: str | None = None,
        metadata: dict[str, str | int | float | bool | None] | None = None,
        deny_option_ids: Iterable[str] | None = None,
        snapshot: SnapshotRef | None = None,
        include_screenshot: bool = False,
        screenshot_region: Region | tuple[int, int, int, int] | None = None,
    ) -> InterventionUpdated:
        """Block until intervention reaches a terminal state; raise on deny/timeout/cancel."""
        req = self._build_request(
            reason=reason,
            options=options,
            title=title,
            context=context,
            timeout=timeout,
            channels=channels,
            client_request_id=client_request_id,
            agent_id=agent_id,
            metadata=metadata,
            snapshot=snapshot,
            include_screenshot=include_screenshot,
            screenshot_region=screenshot_region,
        )
        created = await self._http.create(req)
        intervention_id = created.intervention_id
        deny_ids = (
            frozenset(deny_option_ids) if deny_option_ids is not None else self.deny_option_ids
        )
        prompt_task = self._maybe_start_local_prompt(intervention_id, req)

        try:
            updated = await self._wait_terminal(
                intervention_id,
                timeout_seconds=timeout,
            )
            return self._raise_or_return(updated, deny_ids=deny_ids)
        except InterventionTimeoutError:
            await self._best_effort_cancel(
                intervention_id,
                reason=CancelReason.CLIENT_TIMEOUT,
                detail="sdk/local wait timeout",
            )
            raise
        except asyncio.CancelledError:
            await self._best_effort_cancel(
                intervention_id,
                reason=CancelReason.CLIENT_ABORTED,
                detail="await cancelled",
            )
            raise
        finally:
            if prompt_task is not None:
                prompt_task.cancel()
                await asyncio.gather(prompt_task, return_exceptions=True)

    def ask_human_sync(self, *args: Any, **kwargs: Any) -> InterventionUpdated:
        """Synchronous wrapper around ask_human()."""
        return asyncio.run(self.ask_human(*args, **kwargs))

    def checkpoint(self, state: Any, *, label: str | None = None) -> None:
        """Push an agent state snapshot for later :meth:`rollback`."""
        self.checkpoints.push(state, label=label)

    def rollback(self, steps: int = 1) -> Any:
        """
        Restore agent state to ``steps`` checkpoints ago.

        Typically used when a human denies the current action and requests
        rollback via Web UI (``decision.rollback_steps``).
        """
        try:
            return self.checkpoints.rollback(steps)
        except IndexError as exc:
            raise AgentGuardianError(str(exc), code="AG_INVALID_REQUEST") from exc

    async def guard_step(
        self,
        *,
        action_name: str,
        action_args: dict[str, Any] | str | None = None,
        target: str | None = None,
        error: str | BaseException | None = None,
        command: str | None = None,
        dom_action: str | None = None,
        selector: str | None = None,
        url: str | None = None,
        confidence: float | None = None,
        logits: Sequence[float] | None = None,
        snapshot: SnapshotRef | None = None,
        include_screenshot: bool = False,
        timeout: int = 300,
        channels: Sequence[ChannelName | str] | None = None,
        agent_id: str | None = None,
        auto_ask: bool = True,
        options: Sequence[Option | dict[str, Any] | str] | None = None,
        force_ask: bool = False,
        extra_context: dict[str, Any] | None = None,
    ) -> GuardStepResult:
        """
        Evaluate one agent step with :class:`SmartInterventionEngine`.

        When the engine sets ``intervene=True`` (or ``force_ask``), automatically
        call :meth:`ask_human` with the smart warning title/reason.
        """
        if self.enable_smart:
            signal = self.smart.evaluate_step(
                action_name=action_name,
                action_args=action_args,
                target=target,
                error=error,
                command=command,
                dom_action=dom_action,
                selector=selector or target,
                url=url,
                confidence=confidence,
                logits=logits,
            )
        else:
            from agent_guardian.smart.types import SmartReasonCode

            signal = SmartSignal(
                intervene=False,
                code=SmartReasonCode.OK,
                message="smart engine disabled",
            )

        if not signal.intervene and not force_ask:
            return GuardStepResult(proceeded=True, signal=signal)

        if not auto_ask:
            return GuardStepResult(proceeded=False, signal=signal)

        opts = list(
            options
            or [
                Option(id="approve", label="继续执行", style=OptionStyle.PRIMARY),
                Option(
                    id="deny",
                    label="拒绝并回滚",
                    style=OptionStyle.DANGER,
                    destructive=True,
                ),
            ]
        )
        ctx: dict[str, Any] = {
            "smart_code": signal.code.value,
            "smart_score": signal.score,
            "action_name": action_name,
            "target": target,
            "url": url,
            "dom_action": dom_action,
            "command": command,
            "selector": selector,
        }
        if extra_context:
            ctx.update(extra_context)
        if signal.details:
            ctx["smart_details"] = signal.details

        try:
            updated = await self.ask_human(
                reason=signal.message,
                title=signal.human_title,
                options=opts,
                context={k: v for k, v in ctx.items() if v is not None},
                timeout=timeout,
                channels=channels,
                agent_id=agent_id,
                snapshot=snapshot,
                include_screenshot=include_screenshot,
                metadata={"smart_code": signal.code.value},
            )
        except InterventionDeniedError as exc:
            restored = None
            iid = exc.intervention_id
            if iid:
                try:
                    latest = await self._http.get(iid)
                    steps = (
                        latest.decision.rollback_steps
                        if latest.decision is not None
                        else None
                    )
                    if steps:
                        restored = self.rollback(steps)
                except Exception:
                    logger.debug("rollback after deny skipped", exc_info=True)
            return GuardStepResult(
                proceeded=False,
                signal=signal,
                decision=None,
                restored_state=restored,
            )

        spatial = updated.decision.spatial if updated.decision else None
        spatial_prompt = (
            SpatialPromptInjector.to_prompt(spatial) if spatial is not None else None
        )
        restored = None
        steps = updated.decision.rollback_steps if updated.decision else None
        if steps:
            restored = self.rollback(steps)

        return GuardStepResult(
            proceeded=True,
            signal=signal,
            decision=updated,
            spatial=spatial,
            spatial_prompt=spatial_prompt,
            restored_state=restored,
        )

    @asynccontextmanager
    async def guard(
        self,
        reason: str,
        options: Sequence[Option | dict[str, Any] | str],
        *,
        title: str = "Agent 需要你的确认",
        context: dict[str, Any] | None = None,
        timeout: int = 300,
        channels: Sequence[ChannelName | str] | None = None,
        client_request_id: str | None = None,
        agent_id: str | None = None,
        deny_option_ids: Iterable[str] | None = None,
        snapshot: SnapshotRef | None = None,
        include_screenshot: bool = False,
        screenshot_region: Region | tuple[int, int, int, int] | None = None,
    ) -> AsyncIterator[InterventionUpdated]:
        """
        Context manager: enter only when human approves (non-deny RESOLVED).

        On timeout / deny / cancel, raises so the Agent can Safe Rollback.
        On abnormal exit before completion, sends intervention.cancel.
        """
        req = self._build_request(
            reason=reason,
            options=options,
            title=title,
            context=context,
            timeout=timeout,
            channels=channels,
            client_request_id=client_request_id,
            agent_id=agent_id,
            snapshot=snapshot,
            include_screenshot=include_screenshot,
            screenshot_region=screenshot_region,
        )
        created = await self._http.create(req)
        intervention_id = created.intervention_id
        deny_ids = (
            frozenset(deny_option_ids) if deny_option_ids is not None else self.deny_option_ids
        )
        settled = False
        prompt_task = self._maybe_start_local_prompt(intervention_id, req)

        try:
            updated = await self._wait_terminal(
                intervention_id,
                timeout_seconds=timeout,
            )
            settled = True
            decision = self._raise_or_return(updated, deny_ids=deny_ids)
            yield decision
        except InterventionTimeoutError:
            settled = True
            await self._best_effort_cancel(
                intervention_id,
                reason=CancelReason.CLIENT_TIMEOUT,
                detail="guard timeout",
            )
            raise
        except (InterventionDeniedError, InterventionCancelledError, InterventionFailedError):
            settled = True
            raise
        except asyncio.CancelledError:
            if not settled:
                await self._best_effort_cancel(
                    intervention_id,
                    reason=CancelReason.CONTEXT_MANAGER_EXIT,
                    detail="guard cancelled",
                )
            raise
        except Exception:
            if not settled:
                await self._best_effort_cancel(
                    intervention_id,
                    reason=CancelReason.CONTEXT_MANAGER_EXIT,
                    detail="guard error before settle",
                )
            raise
        finally:
            if prompt_task is not None:
                prompt_task.cancel()
                await asyncio.gather(prompt_task, return_exceptions=True)
            # If wait was interrupted without settle, cancel remote hanging controls.
            if not settled:
                await self._best_effort_cancel(
                    intervention_id,
                    reason=CancelReason.CONTEXT_MANAGER_EXIT,
                    detail="guard exit before settle",
                )

    def _maybe_start_local_prompt(
        self, intervention_id: str, req: InterventionRequest
    ) -> asyncio.Task[None] | None:
        channels = req.channels or [ChannelName.TERMINAL]
        if not self.local_terminal_prompt:
            return None
        if ChannelName.TERMINAL not in channels:
            return None
        return asyncio.create_task(
            self._local_terminal_decide(intervention_id, req),
            name=f"local-prompt-{intervention_id}",
        )

    async def _local_terminal_decide(self, intervention_id: str, req: InterventionRequest) -> None:
        """Read choice from the SDK process stdin and POST decision to Daemon."""
        lines = [
            "",
            "=" * 60,
            f"[Agent Guardian] {req.title}",
            f"intervention_id: {intervention_id}",
            f"reason: {req.reason}",
            "请在本终端输入选项序号或 option_id 后回车：",
        ]
        for i, opt in enumerate(req.options):
            lines.append(f"  [{i}] {opt.id} — {opt.label}")
        lines.append("=" * 60)
        print("\n".join(lines), flush=True)

        try:
            raw = await asyncio.to_thread(sys.stdin.readline)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("local terminal read failed: %s", exc)
            return

        choice = self._parse_local_choice(raw.strip(), req.options)
        if choice is None:
            print(f"[Agent Guardian] 无效输入: {raw!r}，请等待超时或重新运行示例", flush=True)
            return

        try:
            await self._http.decide(
                InterventionDecision(
                    intervention_id=intervention_id,
                    option_id=choice,
                    source=DecisionSource.TERMINAL,
                    decided_at=utc_now(),
                    operator_id="terminal:sdk",
                    channel_message_id="client-stdin",
                )
            )
        except AgentGuardianError as exc:
            # Already resolved/cancelled by another path — fine
            logger.debug("local decide ignored: %s", exc.message)

    @staticmethod
    def _parse_local_choice(raw: str, options: Sequence[Option]) -> str | None:
        if not raw:
            return None
        if raw.isdigit():
            idx = int(raw)
            if 0 <= idx < len(options):
                return options[idx].id
            return None
        valid = {o.id for o in options}
        return raw if raw in valid else None

    async def _wait_terminal(
        self,
        intervention_id: str,
        *,
        timeout_seconds: int,
    ) -> InterventionUpdated:
        if self.prefer_websocket:
            try:
                return await self._wait_websocket(intervention_id, timeout_seconds=timeout_seconds)
            except Exception as exc:
                logger.debug("websocket wait fallback to poll: %s", exc)
        return await self._wait_poll(intervention_id, timeout_seconds=timeout_seconds)

    async def _wait_websocket(
        self,
        intervention_id: str,
        *,
        timeout_seconds: int,
    ) -> InterventionUpdated:
        from websockets.asyncio.client import connect

        from agent_guardian.schemas import Envelope, MessageType

        ws_base = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        url = f"{ws_base.rstrip('/')}/v1/ws?intervention_id={intervention_id}"
        deadline = asyncio.get_running_loop().time() + timeout_seconds + 2.0

        async with connect(url, open_timeout=5, close_timeout=2) as ws:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 30.0))
                except TimeoutError:
                    # keep waiting until overall deadline
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                env = Envelope.model_validate_json(raw)
                if env.message_type != MessageType.INTERVENTION_UPDATED:
                    continue
                updated = InterventionUpdated.model_validate(env.payload)
                if updated.status in TERMINAL_STATUSES:
                    return updated

        # Final HTTP truth
        updated = await self._http.get(intervention_id)
        if updated.status in TERMINAL_STATUSES:
            return updated
        raise InterventionTimeoutError(
            f"intervention timed out: {intervention_id}",
            code="AG_TIMEOUT",
            intervention_id=intervention_id,
        )

    async def _wait_poll(
        self,
        intervention_id: str,
        *,
        timeout_seconds: int,
    ) -> InterventionUpdated:
        deadline = asyncio.get_running_loop().time() + timeout_seconds + 2.0
        while True:
            updated = await self._http.get(intervention_id)
            if updated.status in TERMINAL_STATUSES:
                return updated
            if asyncio.get_running_loop().time() >= deadline:
                updated = await self._http.get(intervention_id)
                if updated.status in TERMINAL_STATUSES:
                    return updated
                raise InterventionTimeoutError(
                    f"intervention timed out: {intervention_id}",
                    code="AG_TIMEOUT",
                    intervention_id=intervention_id,
                )
            await asyncio.sleep(self.poll_interval)

    async def _best_effort_cancel(
        self,
        intervention_id: str,
        *,
        reason: CancelReason,
        detail: str | None = None,
    ) -> None:
        try:
            await self._http.cancel(
                InterventionCancel(
                    intervention_id=intervention_id,
                    reason=reason,
                    detail=detail,
                )
            )
        except AgentGuardianError as exc:
            # Already terminal is acceptable (race with TIMEOUT/RESOLVED)
            if exc.code in {"AG_ALREADY_TERMINAL", "AG_CANCELLED"}:
                logger.debug("cancel idempotent: %s", exc.message)
                return
            logger.warning("cancel failed: %s", exc.message)
        except Exception as exc:
            logger.warning("cancel transport failed: %s", exc)

    def _raise_or_return(
        self,
        updated: InterventionUpdated,
        *,
        deny_ids: frozenset[str],
    ) -> InterventionUpdated:
        if updated.status == InterventionStatus.RESOLVED:
            option_id = updated.selected_option_id or (
                updated.decision.option_id if updated.decision else None
            )
            if option_id is not None and option_id in deny_ids:
                raise InterventionDeniedError(
                    f"human denied intervention via option_id={option_id}",
                    code="AG_DENIED",
                    intervention_id=updated.intervention_id,
                    details={"option_id": option_id},
                )
            return updated

        if updated.status == InterventionStatus.TIMEOUT:
            raise InterventionTimeoutError(
                "intervention timed out",
                code="AG_TIMEOUT",
                intervention_id=updated.intervention_id,
            )
        if updated.status == InterventionStatus.CANCELLED:
            raise InterventionCancelledError(
                "intervention cancelled",
                code="AG_CANCELLED",
                intervention_id=updated.intervention_id,
            )
        if updated.status == InterventionStatus.FAILED:
            raise InterventionFailedError(
                "intervention failed",
                code="AG_INTERNAL",
                intervention_id=updated.intervention_id,
            )
        raise AgentGuardianError(
            f"unexpected terminal handling for status={updated.status}",
            intervention_id=updated.intervention_id,
        )

    @staticmethod
    def _build_request(
        *,
        reason: str,
        options: Sequence[Option | dict[str, Any] | str],
        title: str,
        context: dict[str, Any] | None,
        timeout: int,
        channels: Sequence[ChannelName | str] | None,
        client_request_id: str | None,
        agent_id: str | None,
        metadata: dict[str, str | int | float | bool | None] | None = None,
        snapshot: SnapshotRef | None = None,
        include_screenshot: bool = False,
        screenshot_region: Region | tuple[int, int, int, int] | None = None,
    ) -> InterventionRequest:
        normalized: list[Option] = []
        for item in options:
            if isinstance(item, Option):
                normalized.append(item)
            elif isinstance(item, str):
                normalized.append(Option(id=item, label=item))
            else:
                normalized.append(Option.model_validate(item))

        channel_list = None
        if channels is not None:
            channel_list = [ChannelName(c) for c in channels]

        snap = snapshot
        meta = dict(metadata or {})
        if snap is None and include_screenshot:
            snap = try_capture_snapshot(region=screenshot_region)
            if snap is None:
                meta["snapshot_error"] = "capture_unavailable"

        return InterventionRequest(
            client_request_id=client_request_id or str(uuid4()),
            title=title,
            reason=reason,
            options=normalized,
            context=context or {},
            timeout_seconds=timeout,
            channels=channel_list,
            metadata=meta,
            agent_id=agent_id,
            snapshot=snap,
        )


async def ask_human(
    reason: str,
    options: Sequence[Option | dict[str, Any] | str],
    **kwargs: Any,
) -> InterventionUpdated:
    """Module-level helper using default Daemon URL."""
    async with AgentGuardian() as guardian:
        return await guardian.ask_human(reason, options, **kwargs)


# Attach async_ alias for overview API shape: ask_human.async_
ask_human.async_ = ask_human  # type: ignore[attr-defined]
