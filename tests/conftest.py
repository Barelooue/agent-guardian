"""Shared pytest fixtures for Phase 1 tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
