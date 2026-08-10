"""RBAC-aware channel routing for swarm interventions."""

from __future__ import annotations

from agent_guardian.swarm.types import AgentMeta, ChannelRoute, RouteRule


class ChannelRouter:
    """
    Map agent metadata → notification channels / web rooms / telegram chats.

    Example: finance agents → telegram finance group + web room ``finance``.
    """

    def __init__(self, rules: list[RouteRule] | None = None) -> None:
        self._rules: list[RouteRule] = list(rules or [])
        self._default = RouteRule(
            name="default",
            priority=-1,
            channels=("terminal", "web_ui"),
            web_room="lobby",
        )

    def add_rule(self, rule: RouteRule) -> None:
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def clear_rules(self) -> None:
        self._rules.clear()

    def resolve(self, agent: AgentMeta) -> ChannelRoute:
        for rule in sorted(self._rules, key=lambda r: r.priority, reverse=True):
            if not self._matches(rule, agent):
                continue
            return ChannelRoute(
                channels=rule.channels,
                web_room=rule.web_room,
                telegram_chat_id=rule.telegram_chat_id,
                reason=rule.name,
            )
        return ChannelRoute(
            channels=self._default.channels,
            web_room=self._default.web_room,
            telegram_chat_id=self._default.telegram_chat_id,
            reason=self._default.name,
        )

    def authorize(self, agent: AgentMeta, *, role: str) -> bool:
        """Return True if ``role`` may operate on this agent under matching rules."""
        for rule in sorted(self._rules, key=lambda r: r.priority, reverse=True):
            if not self._matches(rule, agent):
                continue
            if role in rule.roles_allowed:
                return True
            # highest-priority match denies if role not allowed
            return False
        # no specific rule → default allow operator/admin
        return role in {"admin", "operator", "viewer"}

    @staticmethod
    def _matches(rule: RouteRule, agent: AgentMeta) -> bool:
        if rule.tenant_id is not None and rule.tenant_id != agent.tenant_id:
            return False
        if rule.agent_type is not None and rule.agent_type != agent.agent_type:
            return False
        if rule.agent_id is not None and rule.agent_id != agent.agent_id:
            return False
        for k, v in rule.label_equals.items():
            if agent.labels.get(k) != v:
                return False
        return True
