"""End-to-end decisions with the real Laya weights. Run with: uv run pytest -m slow -s"""

from __future__ import annotations

import time

import pytest

from assist_decider_server.pipeline import decide
from assist_decider_server.providers import LayaProvider

from .conftest import make_request

pytestmark = pytest.mark.slow

KITCHEN = {"name": "light.kitchen_ceiling"}
CASES = {
    "english": [
        ("en", "turn on the kitchen light", [("HassTurnOn", KITCHEN)]),
        ("en", "switch off the kitchen light", [("HassTurnOff", KITCHEN)]),
        (
            "en",
            "set the kitchen light to 40 percent",
            [("HassLightSet", KITCHEN | {"brightness": 40})],
        ),
        (
            "en",
            "set the bathroom to 22 degrees",
            [("HassClimateSetTemperature", {"area": "bathroom", "temperature": 22.0})],
        ),
        (
            "en",
            "open the living room blinds to 30%",
            [("HassSetPosition", {"name": "cover.living_room_blinds", "position": 30})],
        ),
        ("en", "is the front door locked?", [("HassGetState", {"name": "lock.front_door"})]),
        (
            "en",
            "how warm is it in the bathroom",
            [("HassClimateGetTemperature", {"area": "bathroom"})],
        ),
        (
            "en",
            "turn off the lights in the hallway",
            [("HassTurnOff", {"area": "hallway", "domain": ["light"]})],
        ),
        (
            "en",
            "turn off the kitchen light and set the bathroom to 21 degrees",
            [
                ("HassTurnOff", KITCHEN),
                ("HassClimateSetTemperature", {"area": "bathroom", "temperature": 21.0}),
            ],
        ),
        ("en", "is the desk lamp on?", [("HassGetState", {"name": "light.desk_lamp"})]),
        ("en", "what is the capital of france", []),
    ],
    "multilingual": [
        ("de", "Schalte das Küchenlicht an", [("HassTurnOn", KITCHEN)]),
        ("de", "Mach das Küchenlicht aus", [("HassTurnOff", KITCHEN)]),
        (
            "de",
            "Stell das Küchenlicht auf fünfzig Prozent",
            [("HassLightSet", KITCHEN | {"brightness": 50})],
        ),
        (
            "de",
            "Stell die Heizung im Bad auf 21,5 Grad",
            [("HassClimateSetTemperature", {"area": "bathroom", "temperature": 21.5})],
        ),
        (
            "de",
            "Fahre den Rollladen Wohnzimmer auf 30 Prozent",
            [("HassSetPosition", {"name": "cover.living_room_blinds", "position": 30})],
        ),
        ("de", "Ist die Haustür abgeschlossen?", [("HassGetState", {"name": "lock.front_door"})]),
        ("de", "Wie warm ist es im Bad?", [("HassClimateGetTemperature", {"area": "bathroom"})]),
        (
            "de",
            "Schalte das Licht in der Küche und im Flur aus",
            [
                ("HassTurnOff", {"area": "kitchen", "domain": ["light"]}),
                ("HassTurnOff", {"area": "hallway", "domain": ["light"]}),
            ],
        ),
        (
            "de",
            "Mach die Kaffeemaschine an und stell das Bad auf 22 Grad",
            [
                ("HassTurnOn", {"name": "switch.coffee_maker"}),
                ("HassClimateSetTemperature", {"area": "bathroom", "temperature": 22.0}),
            ],
        ),
        ("de", "Ist die Schreibtischlampe an?", [("HassGetState", {"name": "light.desk_lamp"})]),
        ("de", "Was ist die Hauptstadt von Frankreich?", []),
    ],
}


@pytest.mark.parametrize("model", ["english", "multilingual"])
def test_live(model: str) -> None:
    provider = LayaProvider(model)
    provider.load()
    failures, latencies = [], []
    for lang, text, expected in CASES[model]:
        threshold = 0.4 if lang == "en" else 0.5
        started = time.perf_counter()
        response, trace = decide(
            make_request(text, lang, options={"confidence_threshold": threshold}), provider
        )
        latencies.append((time.perf_counter() - started) * 1000)
        got = [(a.intent, a.slots) for a in response.actions]
        mark = "ok " if got == expected else "BAD"
        print(f"{mark} {latencies[-1]:6.1f} ms  {text!r} -> {got or response.reason}")
        if got != expected:
            failures.append((text, got, response.reason))
    print(
        f"{model}: p50 {sorted(latencies)[len(latencies) // 2]:.0f} ms, max {max(latencies):.0f} ms"
    )
    assert not failures, failures


# (lang, text, expected); follow-ups, run on the multilingual model
FOLLOW_UPS = [
    ("en", "turn on the kitchen light", [("HassTurnOn", KITCHEN)]),
    ("en", "turn it off", [("HassTurnOff", KITCHEN)]),
    ("en", "and the hallway too", [("HassTurnOff", {"area": "hallway", "domain": ["light"]})]),
    ("de", "Mach das Küchenlicht an", [("HassTurnOn", KITCHEN)]),
    ("de", "und im Flur auch", [("HassTurnOn", {"area": "hallway", "domain": ["light"]})]),
]


def test_live_follow_ups() -> None:
    provider = LayaProvider("multilingual")
    provider.load()
    failures = []
    for lang, text, expected in FOLLOW_UPS:
        req = make_request(text, lang, context_id=f"live_{lang}")
        response, _ = decide(req, provider)
        got = [(a.intent, a.slots) for a in response.actions]
        print(f"{'ok ' if got == expected else 'BAD'} {text!r} -> {got or response.reason}")
        if got != expected:
            failures.append((text, got, response.reason))
    assert not failures, failures
