"""Smoke test proving the `testnet_server` subprocess fixture boots and tears down for real."""

from __future__ import annotations

import httpx
import pytest

from tests.fixtures.testnet_server import testnet_server

pytestmark = pytest.mark.e2e

__all__ = ["testnet_server"]


def test_testnet_health(testnet_server: str) -> None:
    response = httpx.get(f"{testnet_server}/testnet/health", timeout=2.0)
    assert response.status_code == 200
