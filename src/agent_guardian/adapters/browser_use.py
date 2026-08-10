"""
Thin Browser-Use adapter: sensitive actions → AgentGuardian.ask_human.

Usage with browser-use custom Tools::

    from agent_guardian import AgentGuardian
    from agent_guardian.adapters.browser_use import (
        GuardianBrowserHook,
        register_browser_use_tools,
    )

    async with AgentGuardian() as guardian:
        hook = GuardianBrowserHook(guardian)
        tools = register_browser_use_tools(hook)
        agent = Agent(task="...", llm=llm, tools=tools)
        await agent.run()
"""

from __future__ import annotations

import base64
import hashlib
import logging
from collections.abc import Sequence
from typing import Any

from agent_guardian import AgentGuardian, InterventionDeniedError
from agent_guardian.schemas import Option, OptionStyle, SnapshotRef

logger = logging.getLogger(__name__)

DEFAULT_SENSITIVE_KEYWORDS = (
    "pay",
    "payment",
    "checkout",
    "purchase",
    "submit",
    "confirm",
    "captcha",
    "verify",
    "登录",
    "支付",
    "提交",
    "验证码",
)


def bytes_to_snapshot(data: bytes, *, content_type: str = "image/jpeg") -> SnapshotRef:
    """Wrap raw screenshot bytes as a protocol SnapshotRef."""
    return SnapshotRef(
        content_type=content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        base64=base64.b64encode(data).decode("ascii"),
    )


def _default_options() -> list[Option]:
    return [
        Option(id="approve", label="继续执行", style=OptionStyle.PRIMARY),
        Option(
            id="deny",
            label="中止并回滚",
            style=OptionStyle.DANGER,
            destructive=True,
        ),
    ]


def _captcha_options() -> list[Option]:
    return [
        Option(id="approve", label="已处理，继续", style=OptionStyle.PRIMARY),
        Option(
            id="deny",
            label="放弃任务",
            style=OptionStyle.DANGER,
            destructive=True,
        ),
    ]


def _normalize_options(
    options: Sequence[Option | dict[str, Any] | str] | None,
    *,
    fallback: list[Option],
) -> list[Option]:
    if not options:
        return list(fallback)
    normalized: list[Option] = []
    for item in options:
        if isinstance(item, Option):
            normalized.append(item)
        elif isinstance(item, str):
            normalized.append(Option(id=item, label=item))
        else:
            normalized.append(Option.model_validate(item))
    return normalized


def _as_snapshot(screenshot: bytes | SnapshotRef | None) -> SnapshotRef | None:
    if isinstance(screenshot, SnapshotRef):
        return screenshot
    if isinstance(screenshot, (bytes, bytearray)):
        ctype = "image/png" if bytes(screenshot[:8]) == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        return bytes_to_snapshot(bytes(screenshot), content_type=ctype)
    return None


async def _page_meta(browser_session: Any) -> tuple[str | None, str | None, bytes | None]:
    if browser_session is None:
        return None, None, None
    url: str | None = None
    title: str | None = None
    shot: bytes | None = None
    try:
        page = await browser_session.must_get_current_page()
        raw_url = getattr(page, "url", None)
        url = await raw_url() if callable(raw_url) else raw_url
        if url is not None and not isinstance(url, str):
            url = str(url)
        raw_title = getattr(page, "title", None)
        title = await raw_title() if callable(raw_title) else raw_title
        if title is not None and not isinstance(title, str):
            title = str(title)
        if hasattr(page, "screenshot"):
            shot = await page.screenshot(format="jpeg", quality=70)
    except Exception as exc:
        logger.warning("browser_session snapshot failed: %s", exc)
    return url, title, shot


class GuardianBrowserHook:
    """
    Framework-agnostic human gate used by Browser-Use tools and Playwright demos.

    Call :meth:`confirm_sensitive_action` before clicks that submit forms, pay,
    or pass captcha walls. On deny, raises :class:`InterventionDeniedError`.
    """

    def __init__(
        self,
        guardian: AgentGuardian,
        *,
        timeout: int = 300,
        channels: Sequence[str] | None = None,
        agent_id: str = "browser-use",
    ) -> None:
        self.guardian = guardian
        self.timeout = timeout
        self.channels = list(channels) if channels else None
        self.agent_id = agent_id

    async def confirm_sensitive_action(
        self,
        *,
        reason: str,
        action: str,
        url: str | None = None,
        selector: str | None = None,
        page_title: str | None = None,
        screenshot: bytes | SnapshotRef | None = None,
        extra_context: dict[str, Any] | None = None,
        options: Sequence[Option | dict[str, Any] | str] | None = None,
    ) -> str:
        """
        Block until a human approves or denies.

        Returns selected ``option_id`` (typically ``"approve"``).
        Raises ``InterventionDeniedError`` when the human picks a deny option.
        """
        context: dict[str, Any] = {
            "framework": "browser-use",
            "action": action,
            "url": url,
            "selector": selector,
            "page_title": page_title,
        }
        if extra_context:
            context.update(extra_context)

        updated = await self.guardian.ask_human(
            reason=reason,
            title=f"Browser-Use · {action}",
            options=_normalize_options(options, fallback=_default_options()),
            context={k: v for k, v in context.items() if v is not None},
            timeout=self.timeout,
            channels=self.channels,
            agent_id=self.agent_id,
            snapshot=_as_snapshot(screenshot),
            deny_option_ids={"deny", "reject", "cancel"},
        )
        option_id = updated.selected_option_id or (
            updated.decision.option_id if updated.decision else "approve"
        )
        logger.info("human approved browser action=%s option=%s", action, option_id)
        return option_id

    async def request_captcha_help(
        self,
        *,
        url: str | None = None,
        screenshot: bytes | SnapshotRef | None = None,
        detail: str = "页面出现验证码 / 人机校验，请在浏览器中完成后点「已处理」",
    ) -> str:
        """Specialized gate for captcha / login walls."""
        return await self.confirm_sensitive_action(
            reason=detail,
            action="captcha_gate",
            url=url,
            screenshot=screenshot,
            options=_captcha_options(),
        )


def register_browser_use_tools(hook: GuardianBrowserHook) -> Any:
    """
    Register Guardian actions on a browser-use ``Tools`` registry.

    Requires optional dependency: ``pip install browser-use``.
    Returns the ``Tools`` instance to pass into ``Agent(..., tools=tools)``.
    """
    try:
        from browser_use import ActionResult, Tools
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "browser-use is not installed. pip install browser-use && playwright install chromium"
        ) from exc

    tools = Tools()

    @tools.action(
        description=(
            "Ask a human via Agent Guardian before high-risk browser actions "
            "(payment, form submit, captcha, login confirm). "
            "Call this BEFORE clicking pay/submit/confirm or when a captcha appears. "
            "If denied, do not continue the risky action."
        )
    )
    async def ask_guardian_before_sensitive_action(
        reason: str,
        action: str = "sensitive_click",
        selector: str | None = None,
        browser_session: Any = None,
    ) -> Any:
        url, title, shot = await _page_meta(browser_session)
        try:
            option = await hook.confirm_sensitive_action(
                reason=reason,
                action=action,
                url=url,
                selector=selector,
                page_title=title,
                screenshot=shot,
            )
        except InterventionDeniedError as exc:
            return ActionResult(
                extracted_content=f"HUMAN_DENIED: {exc}",
                error=str(exc),
            )
        return ActionResult(
            extracted_content=(
                f"HUMAN_APPROVED option={option}. You may proceed with the sensitive action now."
            )
        )

    @tools.action(
        description=(
            "Human-in-the-loop captcha / login-wall helper. "
            "Pause the agent, notify Telegram/Web UI with a screenshot, "
            "and resume only after the human marks the challenge as solved."
        )
    )
    async def ask_guardian_captcha_help(
        detail: str = "Captcha or login wall detected",
        browser_session: Any = None,
    ) -> Any:
        url, _title, shot = await _page_meta(browser_session)
        try:
            option = await hook.request_captcha_help(
                url=url,
                screenshot=shot,
                detail=detail,
            )
        except InterventionDeniedError as exc:
            return ActionResult(
                extracted_content=f"HUMAN_DENIED: {exc}",
                error=str(exc),
            )
        return ActionResult(extracted_content=f"HUMAN_RESOLVED option={option}. Continue the task.")

    return tools
