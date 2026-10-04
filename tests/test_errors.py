"""Error wording comes from Home Assistant's own intents package."""

from __future__ import annotations

from home_assistant_intents import get_intents
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from custom_components.assist_decider.conversation import _match_error_text


async def test_match_error_uses_specific_text(hass: HomeAssistant) -> None:
    hass.states.async_set("climate.x", "heat", {"friendly_name": "X", "supported_features": 0})
    constraints = intent.MatchTargetsConstraints(name="climate.x", domains=["climate"], features=1)
    result = intent.async_match_targets(hass, constraints)
    assert not result.is_match
    errors = get_intents("en")["responses"]["errors"]
    text = _match_error_text(
        hass, intent.MatchFailedError(result=result, constraints=constraints), errors
    )
    assert text and text != errors["no_intent"]


async def test_area_without_matching_device_names_the_area(hass: HomeAssistant) -> None:
    from homeassistant.helpers import area_registry as ar

    ar.async_get(hass).async_create("Living Room")
    constraints = intent.MatchTargetsConstraints(area_name="living_room", domains=["climate"])
    result = intent.async_match_targets(hass, constraints)
    errors = get_intents("en")["responses"]["errors"]
    text = _match_error_text(
        hass, intent.MatchFailedError(result=result, constraints=constraints), errors
    )
    assert "Living Room" in text and "living_room" not in text
