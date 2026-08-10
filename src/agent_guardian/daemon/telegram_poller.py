"""Long-poll Telegram getUpdates and settle decisions on Daemon."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent_guardian.daemon.channels.telegram import TelegramChannel
from agent_guardian.daemon.service import InterventionService
from agent_guardian.daemon.store import StoreError
from agent_guardian.schemas import DecisionSource, ErrorCode, InterventionDecision, utc_now

logger = logging.getLogger(__name__)


class TelegramPoller:
    def __init__(
        self,
        channel: TelegramChannel,
        service: InterventionService,
        *,
        poll_timeout: int = 20,
    ) -> None:
        self.channel = channel
        self.service = service
        self.poll_timeout = poll_timeout
        self._offset: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self._run(), name="telegram-poller")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        logger.info("Telegram poller started")
        fail_delay = 2.0
        while not self._stopped.is_set():
            try:
                updates = await self.channel.get_updates(
                    offset=self._offset,
                    timeout=self.poll_timeout,
                )
                fail_delay = 2.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "getUpdates failed: %s (retry in %.0fs; "
                    "若在国内请设置 TELEGRAM_PROXY=http://127.0.0.1:端口)",
                    exc,
                    fail_delay,
                )
                await asyncio.sleep(fail_delay)
                fail_delay = min(fail_delay * 2, 60.0)
                continue

            for update in updates:
                await self._handle_update(update)
                upd_id = update.get("update_id")
                if isinstance(upd_id, int):
                    self._offset = upd_id + 1

    async def _handle_update(self, update: dict[str, Any]) -> None:
        cq = update.get("callback_query")
        if not isinstance(cq, dict):
            return
        callback_id = str(cq.get("id", ""))
        data = cq.get("data")
        if not isinstance(data, str):
            return

        resolved = await self.service.store.resolve_callback_data(data)
        if resolved is None:
            await self.channel.answer_callback(callback_id, "已失效或无效")
            return

        intervention_id, option_id = resolved
        from_user = cq.get("from") or {}
        operator = f"tg:{from_user.get('id', 'unknown')}"
        message = cq.get("message") or {}
        message_id = message.get("message_id")

        decision = InterventionDecision(
            intervention_id=intervention_id,
            option_id=option_id,
            source=DecisionSource.TELEGRAM,
            decided_at=utc_now(),
            operator_id=operator,
            channel_message_id=str(message_id) if message_id is not None else None,
        )
        try:
            await self.service.decide(decision)
            await self.channel.answer_callback(callback_id, f"已选择: {option_id}")
            await self.channel.revoke_card(
                intervention_id=intervention_id,
                channel_message_id=str(message_id) if message_id is not None else None,
            )
        except StoreError as exc:
            if exc.code == ErrorCode.AG_ALREADY_TERMINAL:
                await self.channel.answer_callback(callback_id, "已处理")
            else:
                logger.info("telegram decision rejected: %s", exc.message)
                await self.channel.answer_callback(callback_id, "处理失败")
