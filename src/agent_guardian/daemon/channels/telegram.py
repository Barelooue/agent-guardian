"""Telegram Bot channel: send cards, poll callbacks, revoke keyboards."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from agent_guardian.daemon.channels.backoff import with_exponential_backoff
from agent_guardian.daemon.channels.base import Channel, DeliveryResult, DeliveryStatus
from agent_guardian.schemas import ChannelName, InterventionRequest, SnapshotRef
from agent_guardian.snapshot import decode_snapshot_bytes

logger = logging.getLogger(__name__)


class TelegramCallbackCodec:
    """Encode/decode compact callback_data: `{token}:{opt_index}` (≤64 bytes)."""

    MAX_BYTES = 64

    @staticmethod
    def encode(token: str, option_index: int) -> str:
        data = f"{token}:{option_index}"
        raw = data.encode("utf-8")
        if len(raw) > TelegramCallbackCodec.MAX_BYTES:
            raise ValueError(f"callback_data exceeds 64 bytes: {len(raw)} ({data!r})")
        return data

    @staticmethod
    def build_inline_keyboard(
        token: str, option_ids: Sequence[str], labels: Sequence[str]
    ) -> list[list[dict[str, str]]]:
        if len(option_ids) != len(labels):
            raise ValueError("option_ids and labels length mismatch")
        row: list[dict[str, str]] = []
        keyboard: list[list[dict[str, str]]] = []
        for idx, label in enumerate(labels):
            row.append(
                {
                    "text": label[:64],
                    "callback_data": TelegramCallbackCodec.encode(token, idx),
                }
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        return keyboard


class TelegramAPIError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class TelegramChannel(Channel):
    name = ChannelName.TELEGRAM

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        api_base: str = "https://api.telegram.org",
        proxy: str | None = None,
        media_root: Path | None = None,
        public_base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.api_base = api_base.rstrip("/")
        self.proxy = proxy
        self.media_root = Path(media_root) if media_root else None
        self.public_base_url = (public_base_url or "").rstrip("/")
        self._client = client
        self._owns_client = client is None
        # intervention_id -> (chat_id, message_id)
        self._messages: dict[str, tuple[str, int]] = {}

    def _url(self, method: str) -> str:
        return f"{self.api_base}/bot{self.bot_token}/{method}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # Outbound Telegram often needs a local proxy in CN networks.
            kwargs: dict = {"timeout": 60.0, "trust_env": False}
            if self.proxy:
                kwargs["proxy"] = self.proxy
            self._client = httpx.AsyncClient(**kwargs)
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
        if not callback_token:
            return DeliveryResult(
                status=DeliveryStatus.FAILED,
                channel=self.name,
                detail="missing callback_token",
            )

        option_ids = [o.id for o in request.options]
        labels = [o.label for o in request.options]
        keyboard = TelegramCallbackCodec.build_inline_keyboard(callback_token, option_ids, labels)
        caption = (
            f"<b>{_escape_html(request.title)}</b>\n"
            f"{_escape_html(request.reason)}\n\n"
            f"<code>id={intervention_id}</code>"
        )
        preview = _snapshot_preview_line(request.snapshot, self.public_base_url)
        if preview:
            caption = f"{caption}\n{preview}"

        photo = _load_photo_bytes(request.snapshot, self.media_root)

        def _retryable(exc: BaseException) -> bool:
            return isinstance(exc, TelegramAPIError) and exc.retryable

        try:
            if photo is not None:
                try:
                    photo_bytes = photo
                    cap = caption
                    kb = keyboard

                    async def _send_photo() -> Any:
                        return await self._api_send_photo(
                            photo=photo_bytes,
                            caption=cap[:1024],
                            reply_markup={"inline_keyboard": kb},
                        )

                    data = await with_exponential_backoff(
                        _send_photo,
                        is_retryable=_retryable,
                    )
                except Exception as photo_exc:
                    logger.warning("sendPhoto failed (%s); degrading to text card", photo_exc)

                    async def _send_text_after_photo_fail() -> Any:
                        return await self._api_call(
                            "sendMessage",
                            {
                                "chat_id": self.chat_id,
                                "text": caption,
                                "parse_mode": "HTML",
                                "reply_markup": {"inline_keyboard": keyboard},
                            },
                        )

                    data = await with_exponential_backoff(
                        _send_text_after_photo_fail,
                        is_retryable=_retryable,
                    )
            else:

                async def _send_text() -> Any:
                    return await self._api_call(
                        "sendMessage",
                        {
                            "chat_id": self.chat_id,
                            "text": caption,
                            "parse_mode": "HTML",
                            "reply_markup": {"inline_keyboard": keyboard},
                        },
                    )

                data = await with_exponential_backoff(
                    _send_text,
                    is_retryable=_retryable,
                )
        except TelegramAPIError as exc:
            status = DeliveryStatus.EXHAUSTED if exc.retryable else DeliveryStatus.FAILED
            return DeliveryResult(
                status=status,
                channel=self.name,
                detail=str(exc),
            )
        except Exception as exc:
            return DeliveryResult(
                status=DeliveryStatus.EXHAUSTED,
                channel=self.name,
                detail=str(exc),
            )

        message_id = data.get("message_id")
        chat = data.get("chat") or {}
        chat_id = str(chat.get("id", self.chat_id))
        if message_id is not None:
            self._messages[intervention_id] = (chat_id, int(message_id))

        return DeliveryResult(
            status=DeliveryStatus.DELIVERED,
            channel=self.name,
            detail="telegram sent",
            channel_message_id=str(message_id) if message_id is not None else None,
        )

    async def _api_send_photo(
        self,
        *,
        photo: bytes,
        caption: str,
        reply_markup: dict[str, Any],
    ) -> Any:
        import json

        client = await self._get_client()
        files = {"photo": ("snapshot.jpg", photo, "image/jpeg")}
        data = {
            "chat_id": self.chat_id,
            "caption": caption,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(reply_markup),
        }
        try:
            resp = await client.post(self._url("sendPhoto"), data=data, files=files)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TelegramAPIError(str(exc), retryable=True) from exc
        if resp.status_code in {429, 500, 502, 503, 504}:
            raise TelegramAPIError(
                f"HTTP {resp.status_code}",
                status_code=resp.status_code,
                retryable=True,
            )
        if resp.status_code >= 400:
            raise TelegramAPIError(
                f"HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
                retryable=False,
            )
        body = resp.json()
        if not body.get("ok"):
            raise TelegramAPIError(str(body.get("description", "sendPhoto failed")))
        return body.get("result")

    async def revoke_card(self, *, intervention_id: str, channel_message_id: str | None) -> None:
        loc = self._messages.get(intervention_id)
        if loc is None and channel_message_id:
            loc = (self.chat_id, int(channel_message_id))
        if loc is None:
            return
        chat_id, message_id = loc
        try:
            await self._api_call(
                "editMessageReplyMarkup",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": {"inline_keyboard": []},
                },
            )
        except Exception as exc:
            logger.debug("revoke telegram markup failed: %s", exc)
        finally:
            self._messages.pop(intervention_id, None)

    async def answer_callback(self, callback_query_id: str, text: str = "已记录") -> None:
        try:
            await self._api_call(
                "answerCallbackQuery",
                {"callback_query_id": callback_query_id, "text": text},
            )
        except Exception as exc:
            logger.debug("answerCallbackQuery failed: %s", exc)

    async def get_updates(self, *, offset: int | None, timeout: int = 20) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        data = await self._api_call("getUpdates", payload, http_timeout=timeout + 10)
        if not isinstance(data, list):
            return []
        return data

    async def _api_call(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        http_timeout: float | None = None,
    ) -> Any:
        client = await self._get_client()
        timeout = httpx.Timeout(http_timeout or 60.0)
        try:
            resp = await client.post(self._url(method), json=payload, timeout=timeout)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TelegramAPIError(str(exc), retryable=True) from exc

        if resp.status_code in {429, 500, 502, 503, 504}:
            raise TelegramAPIError(
                f"HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
                retryable=True,
            )
        if resp.status_code in {401, 403}:
            raise TelegramAPIError(
                f"HTTP {resp.status_code}: auth/config error",
                status_code=resp.status_code,
                retryable=False,
            )
        if resp.status_code >= 400:
            raise TelegramAPIError(
                f"HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
                retryable=False,
            )

        body = resp.json()
        if not body.get("ok"):
            desc = body.get("description", "unknown telegram error")
            # 429 sometimes in JSON
            retryable = "retry" in str(desc).lower() or "too many" in str(desc).lower()
            raise TelegramAPIError(str(desc), retryable=retryable)
        return body.get("result")


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _load_photo_bytes(snapshot: SnapshotRef | None, media_root: Path | None) -> bytes | None:
    if snapshot is None:
        return None
    raw = decode_snapshot_bytes(snapshot)
    if raw:
        return raw
    if snapshot.url and media_root and snapshot.url.startswith("/v1/media/"):
        name = snapshot.url.rsplit("/", 1)[-1]
        path = media_root / name
        if path.is_file():
            return path.read_bytes()
    return None


def _snapshot_preview_line(snapshot: SnapshotRef | None, public_base_url: str) -> str:
    if snapshot is None:
        return ""
    if snapshot.url:
        if snapshot.url.startswith("http"):
            link = snapshot.url
        elif public_base_url:
            link = f"{public_base_url}{snapshot.url}"
        else:
            link = snapshot.url
        return f"🖼 预览: {link}"
    if snapshot.size_bytes:
        return f"🖼 已附带截图 ({snapshot.size_bytes} bytes)"
    return ""
