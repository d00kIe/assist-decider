from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from assist_decider_server.lang import LANGS
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

Rule = Callable[[str, Question, dict[str, Any]], str]


def question_type(q: Question) -> str:
    """ "kind", "which", "action" or "value": which of the pipeline's questions this is."""
    for lang in LANGS.values():
        if q.instructions == lang.kind_question:
            return "kind"
        if q.instructions == lang.which_question:
            return "which"
        if q.instructions.startswith(lang.action_question.split("{")[0]):
            return "action"
        if q.instructions.startswith(lang.value_question.split("{")[0]):
            return "value"
    raise AssertionError(f"unknown question {q.instructions!r}")


class FakeProvider:
    """Answers by a rule, with a fixed probability, and records every question."""

    name = "fake"
    model = "fake"
    languages = ("en", "de")
    device = "cpu"

    def __init__(self, rule: Rule | None = None, p: float = 0.95) -> None:
        self.rule = rule or answer()
        self.p = p
        self.calls: list[tuple[str, Question, dict[str, Any]]] = []  # (type, question, state)

    def load(self) -> None:
        pass

    def predict(
        self, state: dict[str, Any], questions: dict[str, Question], lang: str
    ) -> dict[str, Answer]:
        out = {}
        for key, q in questions.items():
            kind = question_type(q)
            self.calls.append((kind, q, state))
            choice = self.rule(kind, q, state)
            rest = (1 - self.p) / max(1, len(q.options) - 1)
            out[key] = Answer(choice, {k: (self.p if k == choice else rest) for k in q.options})
        return out

    def asked(self) -> list[str]:
        return [kind for kind, _, _ in self.calls]


def answer(
    kind: str = "command",
    action: str | dict[str, str] = "turn_on",
    device: str | None = None,
    value: str = "first",
) -> Rule:
    """A rule: command or question; `action` for every device, or per device name in the
    question; in a room the option containing `device` (else the first); `value` is a
    number option, "first" or "none"."""

    def rule(qtype: str, q: Question, state: dict[str, Any]) -> str:
        if qtype == "kind":
            return kind
        if qtype == "which":
            return next(
                (k for k, v in q.options.items() if device and device in v), [*q.options][0]
            )
        if qtype == "action":
            if isinstance(action, dict):
                return next(a for name, a in action.items() if name in q.instructions)
            return action
        return [*q.options][0] if value == "first" else value

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
        "protocol_version": 3,
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
