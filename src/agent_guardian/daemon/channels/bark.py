"""Bark / generic webhook notification channels (push + deep-link style)."""

from __future__ import annotations

import logging

import httpx

from agent_guardian.daemon.channels.backoff import with_exponential_backoff
from agent_guardian.daemon.channels.base import Channel, DeliveryResult, DeliveryStatus
from agent_guardian.schemas import ChannelName, InterventionRequest

logger = logging.getLogger(__name__)


class BarkChannel(Channel):
    """POST to Bark URL; decision still via Terminal/Telegram/HTTP."""

    name = ChannelName.BARK

    def __init__(self, bark_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self.bark_url = bark_url.rstrip("/")
        self._client = client
        self._owns_client = client is None

    async def _client_get(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0, trust_env=False)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send_card(
        self,
        *,
        intervention_id: str,
        request: InterventionRequest,
        callback_token: str | None = None,
    ) -> DeliveryResult:
        opts = ", ".join(f"{o.id}:{o.label}" for o in request.options)
        snap = ""
        if request.snapshot and request.snapshot.url:
            snap = f"\nsnapshot: {request.snapshot.url}"
        payload = {
            "title": request.title,
            "body": f"{request.reason}\noptions: {opts}\nid={intervention_id}{snap}",
            "group": "AgentGuardian",
        }

        async def _post() -> None:
            client = await self._client_get()
            resp = await client.post(self.bark_url, json=payload)
            if resp.status_code in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"bark HTTP {resp.status_code}")
            if resp.status_code >= 400:
                raise ValueError(f"bark HTTP {resp.status_code}: {resp.text[:160]}")

        try:
            await with_exponential_backoff(
                _post,
                is_retryable=lambda e: isinstance(e, RuntimeError),
            )
        except Exception as exc:
            logger.warning("bark send failed: %s", exc)
            return DeliveryResult(
                status=DeliveryStatus.EXHAUSTED,
                channel=self.name,
                detail=str(exc),
            )
        return DeliveryResult(
            status=DeliveryStatus.DELIVERED,
            channel=self.name,
            detail="bark pushed",
        )


class WebhookChannel(Channel):
    name = ChannelName.WEBHOOK

    def __init__(self, webhook_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self.webhook_url = webhook_url
        self._client = client
        self._owns_client = client is None

    async def _client_get(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0, trust_env=False)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send_card(
        self,
        *,
        intervention_id: str,
        request: InterventionRequest,
        callback_token: str | None = None,
    ) -> DeliveryResult:
        body = {
            "intervention_id": intervention_id,
            "title": request.title,
            "reason": request.reason,
            "options": [o.model_dump(mode="json") for o in request.options],
            "callback_token": callback_token,
            "timeout_seconds": request.timeout_seconds,
            "snapshot": request.snapshot.model_dump(mode="json") if request.snapshot else None,
        }

        async def _post() -> None:
            client = await self._client_get()
            resp = await client.post(self.webhook_url, json=body)
            if resp.status_code in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"webhook HTTP {resp.status_code}")
            if resp.status_code >= 400:
                raise ValueError(f"webhook HTTP {resp.status_code}")

        try:
            await with_exponential_backoff(
                _post,
                is_retryable=lambda e: isinstance(e, RuntimeError),
            )
        except Exception as exc:
            return DeliveryResult(
                status=DeliveryStatus.EXHAUSTED,
                channel=self.name,
                detail=str(exc),
            )
        return DeliveryResult(
            status=DeliveryStatus.DELIVERED,
            channel=self.name,
            detail="webhook posted",
        )
