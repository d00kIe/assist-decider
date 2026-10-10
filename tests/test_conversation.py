"""The conversation agent end to end, with real HA intent handlers and a mocked server."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.assist_decider.const import CONF_FALLBACK_AGENT

from .conftest import INFO, URL, action, process_response

AGENT = "conversation.assist_decider_multilingual"


@pytest.fixture
async def home(hass: HomeAssistant) -> dict[str, Any]:
    assert await async_setup_component(hass, "intent", {})
    assert await async_setup_component(hass, "light", {})
    areas = ar.async_get(hass)
    kitchen = areas.async_create("Kitchen")
    hallway = areas.async_create("Hallway")
    ents = er.async_get(hass)
    for eid, name, area, state in (
        ("light.kitchen", "Kitchen Light", kitchen.id, "off"),
        ("light.hallway", "Hallway Light", hallway.id, "on"),
        ("light.secret", "Secret Light", None, "off"),
    ):
        domain, obj = eid.split(".")
        ents.async_get_or_create(domain, "test", obj, suggested_object_id=obj)
        ents.async_update_entity(eid, area_id=area)
        hass.states.async_set(eid, state, {"friendly_name": name})
        async_expose_entity(hass, conversation.DOMAIN, eid, eid != "light.secret")
    return {
        "turn_on": async_mock_service(hass, "light", "turn_on"),
        "turn_off": async_mock_service(hass, "light", "turn_off"),
        "kitchen": kitchen.id,
        "hallway": hallway.id,
    }


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    home: dict[str, Any],
) -> MockConfigEntry:
    aioclient_mock.get(f"{URL}/v1/info", json=INFO)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


def mock_process(aioclient_mock: AiohttpClientMocker, **kw: Any) -> None:
    aioclient_mock.post(f"{URL}/v1/process", **kw)


def sent_request(aioclient_mock: AiohttpClientMocker) -> dict[str, Any]:
    return json.loads(aioclient_mock.mock_calls[-1][2])


async def converse(
    hass: HomeAssistant, text: str, language: str = "en"
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass, text, None, Context(), language=language, agent_id=AGENT
    )


def speech(result: conversation.ConversationResult) -> str:
    return result.response.speech["plain"]["speech"]


async def test_turn_on_entity(hass, aioclient_mock, setup_entry, home) -> None:
    mock_process(aioclient_mock, json=process_response(action("HassTurnOn", name="light.kitchen")))
    result = await converse(hass, "turn on the kitchen light")
    assert result.response.response_type is intent.IntentResponseType.ACTION_DONE
    assert [c.data["entity_id"] for c in home["turn_on"]] == [["light.kitchen"]]
    assert speech(result) == "Turned on the light"


async def test_model_switched_elsewhere_reloads(hass, aioclient_mock, setup_entry, home) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/v1/info", json=INFO | {"model": "english", "languages": ["en"]})
    mock_process(
        aioclient_mock,
        json=process_response(action("HassTurnOn", name="light.kitchen"), model="english"),
    )
    await converse(hass, "turn on the kitchen light")
    await hass.async_block_till_done()
    assert setup_entry.runtime_data.info.languages == ["en"]
    assert setup_entry.title == "Assist Decider (english)"


async def test_request_contains_only_exposed_entities(
    hass, aioclient_mock, setup_entry, home
) -> None:
    mock_process(aioclient_mock, json=process_response(action("HassTurnOn", name="light.kitchen")))
    await converse(hass, "turn on the kitchen light")
    request = sent_request(aioclient_mock)
    ids = {e["id"] for e in request["home"]["entities"]}
    assert ids == {"light.kitchen", "light.hallway"}
    assert {"HassTurnOn", "HassTurnOff", "HassLightSet"} <= set(request["intents"])
    assert request["language"] == "en"
    assert request["options"] == {
        "confidence_threshold": 0.4,
        "memory_seconds": 60,
    }
    kitchen = next(e for e in request["home"]["entities"] if e["id"] == "light.kitchen")
    assert kitchen["name"] == "Kitchen Light" and kitchen["area_id"] == home["kitchen"]
    assert "state" not in kitchen  # the snapshot never carries states
    assert len(request["context_id"]) == 32  # hashed conversation id


async def test_german_speech_from_templates(hass, aioclient_mock, setup_entry, home) -> None:
    mock_process(aioclient_mock, json=process_response(action("HassTurnOff", name="light.hallway")))
    result = await converse(hass, "Schalte das Flurlicht aus", "de-CH")
    assert sent_request(aioclient_mock)["language"] == "de"
    assert [c.data["entity_id"] for c in home["turn_off"]] == [["light.hallway"]]
    assert speech(result) == "Hallway Light ausgeschaltet"


async def test_area_with_domain(hass, aioclient_mock, setup_entry, home) -> None:
    mock_process(
        aioclient_mock,
        json=process_response(action("HassTurnOff", area=home["hallway"], domain=["light"])),
    )
    result = await converse(hass, "turn off the hallway lights")
    assert [c.data["entity_id"] for c in home["turn_off"]] == [["light.hallway"]]
    assert speech(result) == "Turned off the lights"


async def test_brightness(hass, aioclient_mock, setup_entry, home) -> None:
    mock_process(
        aioclient_mock,
        json=process_response(action("HassLightSet", name="light.kitchen", brightness=40)),
    )
    result = await converse(hass, "set the kitchen light to 40%")
    assert home["turn_on"][0].data == {"entity_id": "light.kitchen", "brightness_pct": 40}
    assert speech(result) == "Brightness set"


async def test_compound_runs_in_order(hass, aioclient_mock, setup_entry, home) -> None:
    mock_process(
        aioclient_mock,
        json=process_response(
            action("HassTurnOff", name="light.hallway"), action("HassTurnOn", name="light.kitchen")
        ),
    )
    result = await converse(hass, "turn off the hallway light and turn on the kitchen light")
    assert [c.data["entity_id"] for c in home["turn_off"]] == [["light.hallway"]]
    assert [c.data["entity_id"] for c in home["turn_on"]] == [["light.kitchen"]]
    assert speech(result) == "Turned off the light Turned on the light"
    assert {t.id for t in result.response.success_results} >= {"light.hallway", "light.kitchen"}


async def test_numbers_are_spoken_naturally() -> None:
    from custom_components.assist_decider.conversation import _spoken_number

    assert _spoken_number(22.0, "en") == "22"
    assert _spoken_number(21.5, "en") == "21.5"
    assert _spoken_number(21.5, "de") == "21,5"


async def test_query_answer(hass, aioclient_mock, setup_entry, home) -> None:
    mock_process(
        aioclient_mock, json=process_response(action("HassGetState", name="light.hallway"))
    )
    result = await converse(hass, "is the hallway light on?")
    assert result.response.response_type is intent.IntentResponseType.QUERY_ANSWER
    assert speech(result) == "Hallway light is on"  # HA's template capitalizes


async def test_unexposed_or_unknown_actions_are_dropped(
    hass, aioclient_mock, setup_entry, home
) -> None:
    mock_process(
        aioclient_mock,
        json=process_response(
            action("HassTurnOn", name="light.secret"),
            action("HassTurnOn", name="lock.front_door"),
            action("HassShoppingListAddItem", item="x"),
            action("HassTurnOn", name="light.kitchen", evil="1"),
        ),
    )
    result = await converse(hass, "turn on the secret light")
    assert home["turn_on"] == []
    assert result.response.error_code is intent.IntentResponseErrorCode.NO_INTENT_MATCH


async def test_partial_unresolved_is_spoken(hass, aioclient_mock, setup_entry, home) -> None:
    mock_process(
        aioclient_mock,
        json=process_response(
            action("HassTurnOn", name="light.kitchen"), unresolved=["tell me a joke"]
        ),
    )
    result = await converse(hass, "turn on the kitchen light and tell me a joke")
    assert speech(result) == "Turned on the light I didn't understand the rest."


async def test_escalate_without_fallback(hass, aioclient_mock, setup_entry, home) -> None:
    mock_process(aioclient_mock, json=process_response(status="escalate", reason="none_chosen"))
    result = await converse(hass, "what is the capital of france")
    assert result.response.error_code is intent.IntentResponseErrorCode.NO_INTENT_MATCH
    assert speech(result) == "Sorry, I couldn't understand that"


async def test_escalate_goes_to_fallback(hass, aioclient_mock, setup_entry, home) -> None:
    hass.config_entries.async_update_entry(
        setup_entry, options={CONF_FALLBACK_AGENT: "conversation.other"}
    )
    await hass.async_block_till_done()
    mock_process(aioclient_mock, json=process_response(status="escalate", reason="none_chosen"))
    fallback = conversation.ConversationResult(response=intent.IntentResponse("en"))
    original = conversation.async_converse

    async def route(hass, text, conversation_id, context, language=None, agent_id=None, **kw):
        if agent_id == AGENT:  # the test's own call into our agent
            return await original(hass, text, conversation_id, context, language, agent_id, **kw)
        return fallback

    with patch.object(conversation, "async_converse", AsyncMock(side_effect=route)) as delegate:
        result = await converse(hass, "what is the capital of france")
    assert result is fallback
    assert delegate.call_args.kwargs["agent_id"] == "conversation.other"


async def test_server_unavailable(hass, aioclient_mock, setup_entry, home) -> None:
    mock_process(aioclient_mock, exc=aiohttp.ClientError())
    result = await converse(hass, "Licht an", "de")
    assert speech(result) == "Entschuldigung, der Entscheidungsserver ist nicht erreichbar."


async def test_invalid_server_response(hass, aioclient_mock, setup_entry, home) -> None:
    mock_process(aioclient_mock, json={"status": "ok", "actions": "nope"})
    result = await converse(hass, "turn on the kitchen light")
    assert result.response.response_type is intent.IntentResponseType.ERROR
    assert home["turn_on"] == []


async def test_supported_languages(hass, setup_entry) -> None:
    agent = conversation.async_get_agent_info(hass, AGENT)
    assert agent is not None
    assert agent.supports_streaming is False
    state = hass.states.get(AGENT)
    assert state is not None
