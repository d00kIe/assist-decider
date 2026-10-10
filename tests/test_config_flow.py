"""Config, reconfigure and options flows."""

from __future__ import annotations

from typing import Any

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.assist_decider.const import (
    CONF_FALLBACK_AGENT,
    CONF_MODEL,
    CONF_THRESHOLD_DE,
    CONF_THRESHOLD_EN,
    CONF_VERIFY_SSL,
    DOMAIN,
)

from .conftest import INFO, URL

USER_INPUT = {CONF_URL: URL + "/", CONF_VERIFY_SSL: True}


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
    assert result["data"] == {CONF_URL: URL, CONF_VERIFY_SSL: True}


@pytest.mark.parametrize(
    ("mock", "error"),
    [
        ({"json": INFO | {"protocol_version": 1}}, {"base": "protocol_mismatch"}),
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


async def test_reconfigure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    aioclient_mock.get("http://other:8765/v1/info", json=INFO)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "http://other:8765", CONF_VERIFY_SSL: False}
    )
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data == {CONF_URL: "http://other:8765", CONF_VERIFY_SSL: False}


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


async def test_options_switch_model(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    models = ["d1-3b", "multilingual"]
    server = {"info": INFO | {"models": models}, "fail": True}  # a fake server's state

    async def info(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(method, url, json=server["info"])

    async def set_model(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        if server["fail"]:
            detail = {"detail": "OutOfMemoryError. Still using multilingual"}
            return AiohttpClientMockResponse(method, url, status=500, json=detail)
        server["info"] = INFO | {"model": "d1-3b", "provider": "d1", "models": models}
        return await info(method, url, data)

    aioclient_mock.get(f"{URL}/v1/info", side_effect=info)
    aioclient_mock.post(f"{URL}/v1/model", side_effect=set_model)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    flow = await hass.config_entries.options.async_init(config_entry.entry_id)
    user_input = {CONF_THRESHOLD_EN: 0.3, CONF_THRESHOLD_DE: 0.6, CONF_MODEL: "d1-3b"}

    async def submit() -> dict:
        result = await hass.config_entries.options.async_configure(flow["flow_id"], user_input)
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_configure(flow["flow_id"])
        await hass.async_block_till_done()
        return result

    result = await submit()
    assert result["errors"] == {"base": "model_failed"}
    assert result["description_placeholders"] == {
        "error": "HTTP 500: OutOfMemoryError. Still using multilingual"
    }

    server["fail"] = False
    result = await submit()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert '{"model":"d1-3b"}' in [call[2] for call in aioclient_mock.mock_calls]
    assert config_entry.title == "Assist Decider (d1-3b)"
    assert CONF_MODEL not in config_entry.options  # the server owns the model
    assert config_entry.runtime_data.info.provider == "d1"  # reloaded
