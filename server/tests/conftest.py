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

Rule = Callable[[str, Question, dict[str, Any]], str | None]
FIRST = ("kind", "condition", "when", "topic")  # asked about every sentence


def question_type(q: Question) -> str:
    """Which of the pipeline's questions this is: "kind", "condition", "when", "topic",
    "scope", "device_kind", "place", "reference", "which", "action" or "value"."""
    for lang in LANGS.values():
        for kind in ("kind", "condition", "when", "topic", "scope", "device_kind", "place"):
            if q.instructions == getattr(lang, f"{kind}_question"):
                return kind
        if q.instructions.startswith(lang.reference_question.split("{")[0]):
            return "reference"
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
        self.batches: list[list[str]] = []  # the question keys of each predict call

    def load(self) -> None:
        pass

    def predict(
        self, state: dict[str, Any], questions: dict[str, Question], lang: str
    ) -> dict[str, Answer]:
        out = {}
        self.batches.append(list(questions))
        picked = []  # the kinds of the devices the which-questions of this call chose
        # Which-questions first, so that a kind question can follow the device picked.
        for key, q in sorted(
            questions.items(), key=lambda kq: question_type(kq[1]) == "device_kind"
        ):
            kind = question_type(q)
            self.calls.append((kind, q, state))
            choice = self.rule(kind, q, state)
            if kind == "which":
                picked.append(str(choice).split(".")[0])
            if kind == "device_kind" and choice is None:
                choice = next((d for d in picked if d in q.options), [*q.options][0])
            rest = (1 - self.p) / max(1, len(q.options) - 1)
            out[key] = Answer(choice, {k: (self.p if k == choice else rest) for k in q.options})
        return {key: out[key] for key in questions}

    def asked(self) -> list[str]:
        """The questions asked, without the four every sentence starts with (FIRST)."""
        return [kind for kind, _, _ in self.calls if kind not in FIRST]


def answer(
    kind: str = "command",
    action: str | dict[str, str] = "turn_on",
    device: str | None = None,
    value: str = "first",
    scope: str = "one",
    of: str | None = None,
    place: str = "here",
    reference: str = "previous",
) -> Rule:
    """A rule: what is asked (`kind`: command, question, conditional or other); `action` for
    every device, or per device name in the question; in a place the option containing `device`
    (else the first), `scope` one or all, `of` which kind (default: the kind of the device it
    picks), `place` here, floor or home; a follow-up's `reference`; `value` is a number option,
    "first" or "none"."""

    def rule(qtype: str, q: Question, state: dict[str, Any]) -> str | None:
        if qtype in ("scope", "place", "reference", "device_kind"):
            return {"scope": scope, "place": place, "reference": reference}.get(qtype, of)
        if qtype == "kind":
            return "question" if kind == "question" else "command"
        if qtype == "condition":
            return "condition" if kind == "conditional" else "none"
        if qtype == "when":
            return "later" if kind == "conditional" else "now"
        if qtype == "topic":
            return "other" if kind == "other" else "home"
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
