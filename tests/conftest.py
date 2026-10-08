"""Fixtures for the Assist Decider integration tests."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.assist_decider.const import CONF_VERIFY_SSL, DOMAIN

URL = "http://decider.local:8765"
INFO = {
    "protocol_version": 3,
    "server_version": "0.1.0",
    "provider": "laya",
    "model": "multilingual",
    "languages": ["en", "de"],
    "device": "mps",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture(autouse=True)
async def setup_core(hass: HomeAssistant) -> None:
    """Real Home Assistant always loads these; conversation needs exposed_entities."""
    assert await async_setup_component(hass, "homeassistant", {})


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Assist Decider (multilingual)",
        data={CONF_URL: URL, CONF_VERIFY_SSL: True},
    )


def process_response(*actions: dict[str, Any], status: str = "ok", **kw: Any) -> dict[str, Any]:
    return {
        "protocol_version": 3,
        "status": status,
        "actions": list(actions),
        "unresolved": [],
        "reason": None,
        "trace_id": "abc",
        "elapsed_ms": 12.0,
    } | kw


def action(intent: str, segment: str = "", **slots: Any) -> dict[str, Any]:
    return {"intent": intent, "slots": slots, "segment": segment or intent, "confidence": 0.9}
