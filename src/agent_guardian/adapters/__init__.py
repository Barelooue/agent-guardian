"""Thin adapters for popular agent frameworks."""

from agent_guardian.adapters.browser_use import (
    GuardianBrowserHook,
    register_browser_use_tools,
)

__all__ = [
    "GuardianBrowserHook",
    "register_browser_use_tools",
]
