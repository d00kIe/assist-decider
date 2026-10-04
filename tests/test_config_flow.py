"""Config, reauth, reconfigure and options flows."""

from __future__ import annotations

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.assist_decider.const import (
    CONF_FALLBACK_AGENT,
    CONF_THRESHOLD_DE,
    CONF_THRESHOLD_EN,
    CONF_VERIFY_SSL,
    DOMAIN,
)

from .conftest import INFO, TOKEN, URL

USER_INPUT = {CONF_URL: URL + "/", CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True}


async def test_user_flow_creates_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{URL}/v1/info", json=INFO)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Assist Decider (multilingual)"
    assert result["data"] == {CONF_URL: URL, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True}
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    ("mock", "error"),
    [
        ({"status": 401}, {"base": "invalid_auth"}),
        ({"json": INFO | {"protocol_version": 2}}, {"base": "protocol_mismatch"}),
        ({"exc": aiohttp.ClientError()}, {"base": "cannot_connect"}),
        ({"exc": TimeoutError()}, {"base": "cannot_connect"}),
        ({"status": 500}, {"base": "cannot_connect"}),
        ({"text": "not json"}, {"base": "cannot_connect"}),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock, error
) -> None:
    aioclient_mock.get(f"{URL}/v1/info", **mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == error


async def test_invalid_url(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT | {CONF_URL: "decider:8765"}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_duplicate_aborts(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    aioclient_mock.get(f"{URL}/v1/info", json=INFO)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "n" * 40}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_TOKEN] == "n" * 40


async def test_reconfigure_keeps_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    aioclient_mock.get("http://other:8765/v1/info", json=INFO)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "http://other:8765", CONF_VERIFY_SSL: False}
    )
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data == {
        CONF_URL: "http://other:8765",
        CONF_TOKEN: TOKEN,
        CONF_VERIFY_SSL: False,
    }


async def test_options(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{URL}/v1/info", json=INFO)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    own = "conversation.assist_decider_multilingual"
    assert hass.states.get(own) is not None
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_THRESHOLD_EN: 0.3, CONF_THRESHOLD_DE: 0.6, CONF_FALLBACK_AGENT: own},
    )
    assert result["errors"] == {CONF_FALLBACK_AGENT: "fallback_is_self"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_THRESHOLD_EN: 0.3,
            CONF_THRESHOLD_DE: 0.6,
            CONF_FALLBACK_AGENT: "conversation.home_assistant",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_THRESHOLD_DE] == 0.6
