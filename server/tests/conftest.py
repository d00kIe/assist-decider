from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from assist_decider_server.intents import INTENTS
from assist_decider_server.protocol import Area, Entity, Home, ProcessRequest
from assist_decider_server.providers import Answer, Question

ALL_INTENTS = [
    "HassTurnOn",
    "HassTurnOff",
    "HassLightSet",
    "HassClimateSetTemperature",
    "HassSetPosition",
    "HassGetState",
    "HassClimateGetTemperature",
]


class FakeProvider:
    """Picks an option by a rule, with a fixed probability, and records every question."""

    name = "fake"
    model = "fake"
    languages = ("en", "de")
    device = "cpu"

    def __init__(
        self, rule: Callable[[str, Question, dict[str, Any]], str] | None = None, p: float = 0.95
    ) -> None:
        self.rule = rule or self.overlap
        self.p = p
        self.calls: list[tuple[str, Question, dict[str, Any]]] = []

    def load(self) -> None:
        pass

    @staticmethod
    def overlap(key: str, question: Question, state: dict[str, Any]) -> str:
        words = set(state["utterance"].lower().split())
        return max(
            question.options, key=lambda k: len(words & set(question.options[k].lower().split()))
        )

    def predict(
        self, state: dict[str, Any], questions: dict[str, Question], lang: str
    ) -> dict[str, Answer]:
        out = {}
        for key, q in questions.items():
            self.calls.append((key, q, state))
            choice = self.rule(key, q, state)
            choice = INTENTS[choice].key if choice in INTENTS else choice  # rules may use HA names
            rest = (1 - self.p) / max(1, len(q.options) - 1)
            out[key] = Answer(choice, {k: (self.p if k == choice else rest) for k in q.options})
        return out


def intent_rule(intent: str, target: str | None = None) -> Callable[[str, Question, dict], str]:
    """Answer the intent question with `intent`; the target question with the option
    whose description contains `target` (or 'none')."""

    def rule(key: str, q: Question, state: dict) -> str:
        if key == "intent":
            wanted = INTENTS[intent].key if intent in INTENTS else intent
            return wanted if wanted in q.options else "none"
        if target:
            return next((k for k, v in q.options.items() if target.lower() in v.lower()), "none")
        return "none"

    return rule


HOME = Home(
    areas=[
        Area(id="kitchen", name="Kitchen", aliases=["Küche"]),
        Area(id="living_room", name="Living Room", aliases=["Wohnzimmer"]),
        Area(id="bedroom", name="Bedroom", aliases=["Schlafzimmer"]),
        Area(id="hallway", name="Hallway", aliases=["Flur"]),
        Area(id="bathroom", name="Bathroom", aliases=["Bad", "Badezimmer"]),
    ],
    entities=[
        Entity(
            id="light.kitchen_ceiling",
            name="Kitchen Light",
            aliases=["Küchenlicht"],
            area_id="kitchen",
        ),
        Entity(
            id="light.desk_lamp", name="Desk Lamp", aliases=["Schreibtischlampe"], area_id="bedroom"
        ),
        Entity(
            id="light.living_room_floor",
            name="Floor Lamp",
            aliases=["Stehlampe"],
            area_id="living_room",
        ),
        Entity(id="light.hallway", name="Hallway Light", area_id="hallway"),
        Entity(
            id="climate.living_room",
            name="Thermostat",
            aliases=["Heizung Wohnzimmer"],
            area_id="living_room",
        ),
        Entity(
            id="climate.bathroom",
            name="Bathroom Heating",
            aliases=["Heizung Bad"],
            area_id="bathroom",
        ),
        Entity(
            id="cover.living_room_blinds",
            name="Living Room Blinds",
            aliases=["Rollladen Wohnzimmer"],
            area_id="living_room",
            device_class="blind",
        ),
        Entity(
            id="cover.garage_door",
            name="Garage Door",
            aliases=["Garagentor"],
            device_class="garage",
        ),
        Entity(id="lock.front_door", name="Front Door", aliases=["Haustür"]),
        Entity(
            id="switch.coffee_maker",
            name="Coffee Maker",
            aliases=["Kaffeemaschine"],
            area_id="kitchen",
        ),
        Entity(id="media_player.tv", name="TV", aliases=["Fernseher"], area_id="living_room"),
    ],
)


def make_request(text: str, language: str = "en", **kw: Any) -> ProcessRequest:
    data: dict[str, Any] = {
        "protocol_version": 2,
        "text": text,
        "language": language,
        "intents": ALL_INTENTS,
        "home": HOME,
    }
    data.update(kw)
    return ProcessRequest(**data)


@pytest.fixture
def fake() -> FakeProvider:
    return FakeProvider()
