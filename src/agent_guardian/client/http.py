"""Thin HTTP client for Daemon APIs."""

from __future__ import annotations

from typing import Any

import httpx

from agent_guardian.exceptions import AgentGuardianError, ProtocolError
from agent_guardian.schemas import (
    Envelope,
    ErrorPayload,
    InterventionCancel,
    InterventionCreated,
    InterventionDecision,
    InterventionRequest,
    InterventionUpdated,
    MessageType,
    make_envelope,
)


class DaemonHttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        trust_env: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # trust_env=False: ignore Windows/system HTTP proxy for local Daemon.
        # Otherwise httpx may route 127.0.0.1 via a proxy and get empty 502.
        kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "timeout": timeout,
            "trust_env": trust_env,
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, Any]:
        resp = await self._client.get("/health")
        resp.raise_for_status()
        return resp.json()

    async def create(self, request: InterventionRequest) -> InterventionCreated:
        env = make_envelope(MessageType.INTERVENTION_CREATE, request)
        resp = await self._client.post(
            "/v1/interventions",
            json=env.model_dump(mode="json"),
        )
        return self._parse_created(resp)

    async def get(self, intervention_id: str) -> InterventionUpdated:
        resp = await self._client.get(f"/v1/interventions/{intervention_id}")
        return self._parse_updated(resp)

    async def decide(self, decision: InterventionDecision) -> InterventionUpdated:
        env = make_envelope(MessageType.INTERVENTION_DECISION, decision)
        resp = await self._client.post(
            f"/v1/interventions/{decision.intervention_id}/decision",
            json=env.model_dump(mode="json"),
        )
        return self._parse_updated(resp)

    async def cancel(self, cancel: InterventionCancel) -> InterventionUpdated:
        env = make_envelope(MessageType.INTERVENTION_CANCEL, cancel)
        resp = await self._client.post(
            f"/v1/interventions/{cancel.intervention_id}/cancel",
            json=env.model_dump(mode="json"),
        )
        return self._parse_updated(resp, allow_error_terminal=True)

    def _parse_envelope(self, resp: httpx.Response) -> Envelope:
        try:
            data = resp.json()
            return Envelope.model_validate(data)
        except Exception as exc:
            preview = resp.text[:200] if resp.text else "<empty>"
            raise ProtocolError(
                f"invalid daemon response (HTTP {resp.status_code}): {exc}; body={preview!r}",
                code="AG_INVALID_REQUEST",
            ) from exc

    def _raise_error(self, envelope: Envelope) -> None:
        err = ErrorPayload.model_validate(envelope.payload)
        raise AgentGuardianError(
            err.message,
            code=err.code.value,
            intervention_id=err.intervention_id,
            details=err.details,
        )

    def _parse_created(self, resp: httpx.Response) -> InterventionCreated:
        envelope = self._parse_envelope(resp)
        if envelope.message_type == MessageType.ERROR:
            self._raise_error(envelope)
        if envelope.message_type != MessageType.INTERVENTION_CREATED:
            raise ProtocolError(
                f"unexpected message_type: {envelope.message_type}",
                code="AG_INVALID_REQUEST",
            )
        return InterventionCreated.model_validate(envelope.payload)

    def _parse_updated(
        self, resp: httpx.Response, *, allow_error_terminal: bool = False
    ) -> InterventionUpdated:
        envelope = self._parse_envelope(resp)
        if envelope.message_type == MessageType.ERROR:
            # cancel against terminal may 409; surface as error unless allowed
            if allow_error_terminal and resp.status_code == 409:
                # Caller treats cancel as best-effort
                self._raise_error(envelope)
            self._raise_error(envelope)
        if envelope.message_type != MessageType.INTERVENTION_UPDATED:
            raise ProtocolError(
                f"unexpected message_type: {envelope.message_type}",
                code="AG_INVALID_REQUEST",
            )
        return InterventionUpdated.model_validate(envelope.payload)
