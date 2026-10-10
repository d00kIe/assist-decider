"""End-to-end decisions with real model weights. Run with: uv run pytest -m slow -s"""

from __future__ import annotations

import time

import pytest

from assist_decider_server.pipeline import decide
from assist_decider_server.providers import make_provider

from .conftest import make_request

pytestmark = pytest.mark.slow

KITCHEN = {"name": "light.kitchen_ceiling"}
HALLWAY = {"name": "light.hallway"}
BATHROOM = {"name": "climate.bathroom"}
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
            [("HassClimateSetTemperature", BATHROOM | {"temperature": 22.0})],
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
            [("HassClimateGetTemperature", BATHROOM)],
        ),
        (
            "en",
            "turn off the lights in the hallway",
            [("HassTurnOff", HALLWAY)],
        ),
        (
            "en",
            "turn off the kitchen light and set the bathroom to 21 degrees",
            [
                ("HassTurnOff", KITCHEN),
                ("HassClimateSetTemperature", BATHROOM | {"temperature": 21.0}),
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
            [("HassClimateSetTemperature", BATHROOM | {"temperature": 21.5})],
        ),
        (
            "de",
            "Fahre den Rollladen Wohnzimmer auf 30 Prozent",
            [("HassSetPosition", {"name": "cover.living_room_blinds", "position": 30})],
        ),
        ("de", "Ist die Haustür abgeschlossen?", [("HassGetState", {"name": "lock.front_door"})]),
        ("de", "Wie warm ist es im Bad?", [("HassClimateGetTemperature", BATHROOM)]),
        (
            "de",
            "Schalte das Licht in der Küche und im Flur aus",
            [
                ("HassTurnOff", KITCHEN),
                ("HassTurnOff", HALLWAY),
            ],
        ),
        (
            "de",
            "Mach die Kaffeemaschine an und stell das Bad auf 22 Grad",
            [
                ("HassTurnOn", {"name": "switch.coffee_maker"}),
                ("HassClimateSetTemperature", BATHROOM | {"temperature": 22.0}),
            ],
        ),
        ("de", "Ist die Schreibtischlampe an?", [("HassGetState", {"name": "light.desk_lamp"})]),
        ("de", "Was ist die Hauptstadt von Frankreich?", []),
    ],
}


# model -> confidence threshold, as BENCHMARK.md suggests for it
THRESHOLDS = {
    "english": 0.2,
    "multilingual": 0.4,
    "intern-decision-0.8b": 0.3,
    "d1-3b": 0.2,
    "d1-omni-600m": 0.4,
}
# Mistakes BENCHMARK.md already lists for a model. Any other wrong action is a regression.
KNOWN_WRONG = {
    ("english", "open the living room blinds to 30%"),  # opens fully
    ("multilingual", "open the living room blinds to 30%"),
    # No word lists since v2: German "… aus" at the end is read as "on" (BENCHMARK.md, v2).
    ("intern-decision-0.8b", "Mach das Küchenlicht aus"),
    ("multilingual", "Mach es aus"),
}


@pytest.mark.parametrize("model", THRESHOLDS)
def test_live(model: str) -> None:
    """Every sentence is done right or handed to Home Assistant, never done wrong."""
    provider = make_provider(model)
    provider.load()
    cases = [c for cases in CASES.values() for c in cases if c[0] in provider.languages]
    right, handed_off, wrong, latencies = 0, 0, [], []
    for lang, text, expected in cases:
        started = time.perf_counter()
        response, _ = decide(
            make_request(text, lang, options={"confidence_threshold": THRESHOLDS[model]}),
            provider,
        )
        latencies.append((time.perf_counter() - started) * 1000)
        got = [(a.intent, a.slots) for a in response.actions]
        if got == expected:
            mark, right = "ok ", right + 1
        elif not got:
            mark, handed_off = "off", handed_off + 1
        else:
            mark = "BAD"
            if (model, text) not in KNOWN_WRONG:
                wrong.append((text, got))
        print(f"{mark} {latencies[-1]:6.1f} ms  {text!r} -> {got or response.reason}")
    print(
        f"{model}: {right}/{len(cases)} right, "
        f"{handed_off} handed off, p50 {sorted(latencies)[len(latencies) // 2]:.0f} ms"
    )
    assert not wrong, wrong


# (lang, text, expected); follow-ups, run on the multilingual model
FOLLOW_UPS = [
    ("en", "turn on the kitchen light", [("HassTurnOn", KITCHEN)]),
    ("en", "turn it off", [("HassTurnOff", KITCHEN)]),
    ("de", "Mach das Küchenlicht an", [("HassTurnOn", KITCHEN)]),
    ("de", "Mach es aus", [("HassTurnOff", KITCHEN)]),
]


def test_live_follow_ups() -> None:
    provider = make_provider("multilingual")
    provider.load()
    failures = []
    for lang, text, expected in FOLLOW_UPS:
        req = make_request(text, lang, context_id=f"live_{lang}")
        response, _ = decide(req, provider)
        got = [(a.intent, a.slots) for a in response.actions]
        print(f"{'ok ' if got == expected else 'BAD'} {text!r} -> {got or response.reason}")
        # The follow-up must find the previous command's device; a known wrong action is listed.
        if got != expected and ("multilingual", text) not in KNOWN_WRONG:
            failures.append((text, got, response.reason))
        assert [a.slots for a in response.actions] == [slots for _, slots in expected], text
    assert not failures, failures
