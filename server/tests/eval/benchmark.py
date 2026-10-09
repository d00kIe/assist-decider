"""The benchmark of BENCHMARK.md: 65 sentences through the server's own pipeline.

Prints, per model, how many sentences are right, handed to Home Assistant, or wrong at each
confidence threshold, the median time per sentence, and every mistake.

Run from the server folder (models load one after another; the big ones take minutes):
    uv run python tests/eval/benchmark.py multilingual intern-decision-0.8b ...
"""

import statistics
import sys
import time

from assist_decider_server.intents import ACTIONS, intent_for
from assist_decider_server.pipeline import decide
from assist_decider_server.providers import MODELS, make_provider

sys.path.insert(0, "tests")
from conftest import HOME, make_request  # noqa: E402

from assist_decider_server.protocol import Area, Entity, Floor  # noqa: E402

KL, DL, FL, HL = (
    "light.kitchen_ceiling",
    "light.desk_lamp",
    "light.living_room_floor",
    "light.hallway",
)
TH, BH, BL, GD, FD, CM, TV = (
    "climate.living_room",
    "climate.bathroom",
    "cover.living_room_blinds",
    "cover.garage_door",
    "lock.front_door",
    "switch.coffee_maker",
    "media_player.tv",
)
# lang, text, gold {device: (action, value)}. No "?": speech-to-text often drops it.
CASES = [
    (
        "en",
        "set the thermostat to 23 degrees and turn on the light in the kitchen",
        {TH: ("set_temperature", 23), KL: ("turn_on", None)},
    ),
    ("en", "turn on the kitchen light", {KL: ("turn_on", None)}),
    ("en", "turn off the hallway light", {HL: ("turn_off", None)}),
    (
        "en",
        "turn on the kitchen light and turn off the hallway light",
        {KL: ("turn_on", None), HL: ("turn_off", None)},
    ),
    (
        "en",
        "turn off the kitchen and hallway lights",
        {KL: ("turn_off", None), HL: ("turn_off", None)},
    ),
    ("en", "set the kitchen light to 40 percent", {KL: ("set_brightness", 40)}),
    ("en", "set the bathroom heating to 21 degrees", {BH: ("set_temperature", 21)}),
    ("en", "close the living room blinds", {BL: ("close", None)}),
    ("en", "open the blinds to 30 percent", {BL: ("set_position", 30)}),
    ("en", "lock the front door", {FD: ("lock", None)}),
    ("en", "unlock the front door", {FD: ("unlock", None)}),
    (
        "en",
        "start the coffee maker and turn off the tv",
        {CM: ("turn_on", None), TV: ("turn_off", None)},
    ),
    ("en", "is the kitchen light on", {KL: ("query", None)}),
    ("en", "what's the temperature in the bathroom", {BH: ("query", None)}),
    (
        "en",
        "dim the desk lamp to 20 and close the garage door",
        {DL: ("set_brightness", 20), GD: ("close", None)},
    ),
    (
        "en",
        "switch off the tv, the floor lamp and the hallway light",
        {TV: ("turn_off", None), FL: ("turn_off", None), HL: ("turn_off", None)},
    ),
    (
        "en",
        "set the thermostat to 22 degrees and the bathroom heating to 24",
        {TH: ("set_temperature", 22), BH: ("set_temperature", 24)},
    ),
    ("en", "turn on the coffee maker", {CM: ("turn_on", None)}),
    ("en", "is the front door locked", {FD: ("query", None)}),
    (
        "en",
        "turn the floor lamp off and open the garage door",
        {FL: ("turn_off", None), GD: ("open", None)},
    ),
    ("de", "schalte das Küchenlicht ein", {KL: ("turn_on", None)}),
    ("de", "mach das Licht im Flur aus", {HL: ("turn_off", None)}),
    (
        "de",
        "stell die Heizung Wohnzimmer auf 23 Grad und mach das Küchenlicht an",
        {TH: ("set_temperature", 23), KL: ("turn_on", None)},
    ),
    (
        "de",
        "mach das Küchenlicht an und den Fernseher aus",
        {KL: ("turn_on", None), TV: ("turn_off", None)},
    ),
    ("de", "fahr den Rollladen Wohnzimmer auf 30 Prozent", {BL: ("set_position", 30)}),
    ("de", "schließ das Garagentor", {GD: ("close", None)}),
    ("de", "sperr die Haustür ab", {FD: ("lock", None)}),
    ("de", "ist das Küchenlicht an", {KL: ("query", None)}),
    ("de", "dimme die Schreibtischlampe auf 20 Prozent", {DL: ("set_brightness", 20)}),
    (
        "de",
        "stell die Heizung Bad auf 22 Grad und schalte die Kaffeemaschine ein",
        {BH: ("set_temperature", 22), CM: ("turn_on", None)},
    ),
    ("de", "wie warm ist es im Bad", {BH: ("query", None)}),
    (
        "de",
        "mach die Stehlampe und das Küchenlicht aus",
        {FL: ("turn_off", None), KL: ("turn_off", None)},
    ),
]
# lang, text, speaker's room, gold: only a room or nothing named
ROOM_CASES = [
    ("en", "turn on the light", "kitchen", {KL: ("turn_on", None)}),
    ("en", "open the blinds to 30 percent", "living_room", {BL: ("set_position", 30)}),
    ("en", "set the temperature to 22 degrees", "bathroom", {BH: ("set_temperature", 22)}),
    ("en", "make it 23 degrees in here", "living_room", {TH: ("set_temperature", 23)}),
    ("en", "turn off the lights", "bedroom", {DL: ("turn_off", None)}),
    ("en", "close the blinds", "living_room", {BL: ("close", None)}),
    ("en", "dim the light to 30 percent", "living_room", {FL: ("set_brightness", 30)}),
    ("en", "turn on the coffee", "kitchen", {CM: ("turn_on", None)}),
    ("en", "switch off the light in the bedroom", "kitchen", {DL: ("turn_off", None)}),
    (
        "en",
        "turn off the kitchen and hallway lights",
        None,
        {KL: ("turn_off", None), HL: ("turn_off", None)},
    ),
    ("en", "what's the temperature in here", "bathroom", {BH: ("query", None)}),
    ("en", "is the light on", "kitchen", {KL: ("query", None)}),
    ("en", "turn off the tv", "kitchen", {TV: ("turn_off", None)}),
    ("en", "turn on the music", "living_room", {TV: ("turn_on", None)}),
    ("de", "mach das Licht an", "kitchen", {KL: ("turn_on", None)}),
    ("de", "fahr die Rollos auf 30 Prozent", "living_room", {BL: ("set_position", 30)}),
    ("de", "stell die Heizung auf 22 Grad", "bathroom", {BH: ("set_temperature", 22)}),
    ("de", "Licht aus", "bedroom", {DL: ("turn_off", None)}),
    ("de", "schließ die Rollläden", "living_room", {BL: ("close", None)}),
    ("de", "mach die Kaffeemaschine an", "hallway", {CM: ("turn_on", None)}),
    ("de", "mach das Licht im Schlafzimmer aus", "kitchen", {DL: ("turn_off", None)}),
    ("de", "wie warm ist es hier", "bathroom", {BH: ("query", None)}),
    ("de", "mach es heller, 70 Prozent", "living_room", {FL: ("set_brightness", 70)}),
]

# The test home with floors, two more living room lights and blinds upstairs, for NEW_CASES only:
# the 55 sentences above keep their home, so their results stay comparable.
LL, RL, OB, KB = (
    "light.living_room_left",
    "light.living_room_right",
    "cover.office_blinds",
    "cover.kids_room_blinds",
)
UPSTAIRS = {"bedroom", "office", "kids_room"}
HOME_FLOORS = HOME.model_copy(
    update={
        "floors": [
            Floor(id="ground", name="Ground Floor", aliases=["Erdgeschoss"]),
            Floor(id="second", name="Second Floor", aliases=["Obergeschoss", "zweiter Stock"]),
        ],
        "areas": [
            a.model_copy(update={"floor_id": "second" if a.id in UPSTAIRS else "ground"})
            for a in HOME.areas
        ]
        + [
            Area(id="office", name="Office", aliases=["Büro"], floor_id="second"),
            Area(id="kids_room", name="Kids Room", aliases=["Kinderzimmer"], floor_id="second"),
        ],
        "entities": [
            *HOME.entities,
            Entity(id=LL, name="Left Light", aliases=["Linkes Licht"], area_id="living_room"),
            Entity(id=RL, name="Right Light", aliases=["Rechtes Licht"], area_id="living_room"),
            Entity(
                id=OB,
                name="Office Blinds",
                aliases=["Rollladen Büro"],
                area_id="office",
                device_class="blind",
            ),
            Entity(
                id=KB,
                name="Kids Room Blinds",
                aliases=["Rollladen Kinderzimmer"],
                area_id="kids_room",
                device_class="blind",
            ),
        ],
    }
)
ON, OFF, CLOSE = ("turn_on", None), ("turn_off", None), ("close", None)
LIVING_LIGHTS = {FL: ON, LL: ON, RL: ON}
GROUND_LIGHTS = {KL: ON, FL: ON, LL: ON, RL: ON, HL: ON}  # the speaker is on the ground floor
# lang, text, speaker's room, gold: politeness, "all", left/right and floors
NEW_CASES = [
    ("en", "can you turn on the light please", "kitchen", {KL: ON}),
    ("en", "turn on all the lights in the living room", None, LIVING_LIGHTS),
    ("en", "turn off the left light but turn on the right one", "living_room", {LL: OFF, RL: ON}),
    ("en", "turn on all the lights on the floor", "living_room", GROUND_LIGHTS),
    ("en", "close all the covers on the second floor", None, {OB: CLOSE, KB: CLOSE}),
    ("de", "kannst du bitte das Licht einschalten", "kitchen", {KL: ON}),
    ("de", "schalte alle Lichter im Wohnzimmer ein", None, LIVING_LIGHTS),
    ("de", "mach das linke Licht aus, aber das rechte an", "living_room", {LL: OFF, RL: ON}),
    ("de", "schalte alle Lichter auf dieser Etage ein", "living_room", GROUND_LIGHTS),
    ("de", "schließ alle Rollläden im Obergeschoss", None, {OB: CLOSE, KB: CLOSE}),
]

SENSITIVE = {GD, FD}
THRESHOLDS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5)
ALL_CASES = (
    [(lang, text, None, gold, HOME) for lang, text, gold in CASES]
    + [(*case, HOME) for case in ROOM_CASES]
    + [(*case, HOME_FLOORS) for case in NEW_CASES]
)


def expected(gold: dict) -> dict[str, tuple]:
    """{entity: (action, value)} -> {entity: (intent, slots)}, as the server answers."""
    out = {}
    for eid, (action, value) in gold.items():
        slots = {"name": eid}
        if value is not None:
            slots[ACTIONS[action].number.slot] = value
        out[eid] = (intent_for(action, eid.split(".")[0]), slots)
    return out


def short(actions: dict[str, tuple]) -> str:
    return ", ".join(
        f"{eid.split('.')[1]}:{intent}"
        + "".join(f" {k}={v:g}" for k, v in slots.items() if k != "name")
        for eid, (intent, slots) in actions.items()
    )


def run(model: str) -> list[dict]:
    provider = make_provider(model)
    provider.load()
    rows = []
    for lang, text, sat, gold, home in ALL_CASES:
        if lang not in provider.languages:
            continue
        # The threshold is applied below, so one run gives the result for every threshold.
        req = make_request(
            text, lang, home=home, satellite_area_id=sat, options={"confidence_threshold": 0.0}
        )
        started = time.perf_counter()
        response, _ = decide(req, provider)
        ms = (time.perf_counter() - started) * 1000
        got = {a.slots["name"]: (a.intent, a.slots) for a in response.actions}
        want = expected(gold)
        rows.append(
            {
                "text": text,
                "new": home is HOME_FLOORS,
                "sat": sat,
                "want": short(want),
                "got": short(got) if got else f"handed off ({response.reason})",
                "handoff": not got,
                "ok": got == want,
                "conf": min((a.confidence for a in response.actions), default=1.0),
                "unsafe": any(
                    e in got and got[e][0] != "HassGetState" and got[e] != want.get(e)
                    for e in SENSITIVE
                ),
                "ms": ms,
            }
        )
    return rows


def cell(rows: list[dict], threshold: float) -> str:
    """right/total · handed off · wrong (wrong on the lock or garage door)"""
    right = handoff = wrong = unsafe = 0
    for r in rows:
        if r["handoff"] or r["conf"] < threshold:
            handoff += 1
        elif r["ok"]:
            right += 1
        else:
            wrong += 1
            unsafe += r["unsafe"]
    return f"{right}/{len(rows)} · {handoff} · **{wrong}**" + (f" ({unsafe}🔒)" if unsafe else "")


if __name__ == "__main__":
    models = sys.argv[1:] or ["multilingual"]
    if unknown := set(models) - set(MODELS):
        sys.exit(f"unknown model(s) {sorted(unknown)}, choose from {', '.join(MODELS)}")
    results = {m: run(m) for m in models}
    print("\n| Model | " + " | ".join(f"check {t:.1f}" for t in THRESHOLDS) + " | Median |")
    print("|---|" + "---:|" * (len(THRESHOLDS) + 1))
    for m, rows in results.items():
        cells = " | ".join(cell(rows, t) for t in THRESHOLDS)
        print(f"| {m} | {cells} | {statistics.median(r['ms'] for r in rows):.0f} ms |")
    print("\n| Model | 55 sentences | 10 new sentences |\n|---|---:|---:|")
    for m, rows in results.items():
        old, new = [r for r in rows if not r["new"]], [r for r in rows if r["new"]]
        print(f"| {m} | {cell(old, 0.0)} | {cell(new, 0.0)} |")
    for m, rows in results.items():
        print(f"\n**{m}**\n")
        for r in rows:
            if not r["ok"]:
                room = f" (speaker in {r['sat']})" if r["sat"] else ""
                print(f'- "{r["text"]}"{room}: wanted {r["want"]}, got {r["got"]}')
