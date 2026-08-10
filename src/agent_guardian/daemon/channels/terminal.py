"""Terminal stdout/stdin fallback channel."""

from __future__ import annotations

import asyncio
import sys

from agent_guardian.daemon.channels.base import Channel, DeliveryResult, DeliveryStatus
from agent_guardian.schemas import ChannelName, InterventionRequest


class TerminalChannel(Channel):
    """Prints an interaction card to stderr; optional stdin reader via helper."""

    name = ChannelName.TERMINAL

    async def send_card(
        self,
        *,
        intervention_id: str,
        request: InterventionRequest,
        callback_token: str | None = None,
    ) -> DeliveryResult:
        lines = [
            "",
            "=" * 60,
            f"[Agent Guardian] {request.title}",
            f"intervention_id: {intervention_id}",
            f"reason: {request.reason}",
            "options:",
        ]
        for i, opt in enumerate(request.options):
            lines.append(f"  [{i}] {opt.id} — {opt.label}")
        if request.snapshot and request.snapshot.url:
            lines.append(f"snapshot: {request.snapshot.url}")
        elif request.snapshot and request.snapshot.size_bytes:
            lines.append(f"snapshot: attached ({request.snapshot.size_bytes} bytes)")
        lines.append(
            "Decide via: Web UI http://127.0.0.1:8787/ui/  or stdin / "
            f"POST /v1/interventions/{intervention_id}/decision"
        )
        lines.append("=" * 60)
        print("\n".join(lines), file=sys.stderr, flush=True)
        return DeliveryResult(
            status=DeliveryStatus.DELIVERED,
            channel=self.name,
            detail="printed",
            channel_message_id="stderr",
        )

    async def prompt_choice(
        self, request: InterventionRequest, timeout_seconds: float
    ) -> str | None:
        """Block in a thread for one stdin line; return option_id or None."""

        def _read() -> str:
            return sys.stdin.readline().strip()

        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(_read),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return None
        return self.parse_choice(raw, request)

    @staticmethod
    def parse_choice(raw: str, request: InterventionRequest) -> str | None:
        if not raw:
            return None
        if raw.isdigit():
            idx = int(raw)
            if 0 <= idx < len(request.options):
                return request.options[idx].id
            return None
        valid = {o.id for o in request.options}
        return raw if raw in valid else None
