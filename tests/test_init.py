"""Setup, unload and failure modes of the config entry."""

from __future__ import annotations

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.assist_decider.const import DOMAIN
from custom_components.assist_decider.diagnostics import async_get_config_entry_diagnostics

from .conftest import INFO, URL


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_unload(hass, aioclient_mock: AiohttpClientMocker, config_entry) -> None:
    aioclient_mock.get(f"{URL}/v1/info", json=INFO)
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("conversation.assist_decider_multilingual") is not None
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_server_down_retries(hass, aioclient_mock, config_entry) -> None:
    aioclient_mock.get(f"{URL}/v1/info", exc=aiohttp.ClientError())
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_protocol_mismatch_raises_repair(hass, aioclient_mock, config_entry) -> None:
    aioclient_mock.get(f"{URL}/v1/info", json=INFO | {"protocol_version": 99})
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"protocol_mismatch_{config_entry.entry_id}")


async def test_diagnostics(hass, aioclient_mock, config_entry) -> None:
    aioclient_mock.get(f"{URL}/v1/info", json=INFO)
    await setup(hass, config_entry)
    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diag["server"]["model"] == "multilingual"
