"""The benchmark of BENCHMARK.md: sentences through the server's own pipeline.

Two splits. "dev" is the 65 sentences the pipeline was tuned on. "test" is held out: written on
2026-10-10, before the v2 changes, and never tuned on; report it, don't tune on it.

Prints, per model and split, how many sentences are right, handed to Home Assistant, or wrong at
each confidence threshold; the most right answers with 0 (and with at most 1) wrong, whatever
the threshold; the median time and model calls per sentence; and every mistake.

Run from the server folder (models load one after another; the big ones take minutes):
    uv run python tests/eval/benchmark.py multilingual intern-decision-0.8b ... [--out=FILE]
--out saves every sentence's result per model as JSON.
"""

import json
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

OPEN, LOCK, UNLOCK, QUERY = ("open", None), ("lock", None), ("unlock", None), ("query", None)


def temp(value: float) -> tuple:
    return ("set_temperature", value)


def bright(value: float) -> tuple:
    return ("set_brightness", value)


ALL_LIGHTS = {KL: OFF, DL: OFF, FL: OFF, HL: OFF, LL: OFF, RL: OFF}
# Held out: written 2026-10-10 before the v2 changes, never tuned on. Home: HOME_FLOORS.
# lang, text, speaker's room, gold (None: must be handed off), previous command (a follow-up)
TEST_CASES = [
    # vague: act when no number is needed, else hand off
    ("en", "it's too dark in here", "kitchen", {KL: ON}, None),
    ("de", "hier ist es viel zu dunkel", "bedroom", {DL: ON}, None),
    ("en", "I'm freezing", "bathroom", None, None),
    ("de", "mir ist zu warm", "living_room", None, None),
    ("en", "make it a bit brighter", "bedroom", None, None),
    # long compounds
    (
        "en",
        "turn off the tv, close the living room blinds and set the thermostat to 21 degrees",
        None,
        {TV: OFF, BL: CLOSE, TH: temp(21)},
        None,
    ),
    (
        "de",
        "mach das Küchenlicht an, die Stehlampe aus und stell die Heizung Bad auf 23 Grad",
        None,
        {KL: ON, FL: OFF, BH: temp(23)},
        None,
    ),
    (
        "en",
        "switch on the coffee maker and the kitchen light and turn off the hallway light",
        None,
        {CM: ON, KL: ON, HL: OFF},
        None,
    ),
    (
        "de",
        "schalte den Fernseher und die Stehlampe aus und öffne den Rollladen Wohnzimmer",
        None,
        {TV: OFF, FL: OFF, BL: OPEN},
        None,
    ),
    (
        "en",
        "dim the floor lamp to 30 percent and set the bathroom heating to 22 degrees",
        None,
        {FL: bright(30), BH: temp(22)},
        None,
    ),
    (
        "de",
        "Schreibtischlampe auf 40 Prozent und Küchenlicht aus",
        None,
        {DL: bright(40), KL: OFF},
        None,
    ),
    # not about the home's devices: hand off
    ("en", "tell me a joke", "kitchen", None, None),
    ("de", "wie spät ist es", "living_room", None, None),
    ("en", "set a timer for ten minutes", "kitchen", None, None),
    ("en", "play some jazz", "living_room", None, None),
    ("de", "was ist die Hauptstadt von Frankreich", "bedroom", None, None),
    # all, floors, the whole home, plural
    ("en", "turn off all the lights in the house", None, ALL_LIGHTS, None),
    (
        "de",
        "schalte alle Lichter im Erdgeschoss aus",
        None,
        dict.fromkeys(GROUND_LIGHTS, OFF),
        None,
    ),
    (
        "en",
        "close the blinds in the office and in the kids room",
        None,
        {OB: CLOSE, KB: CLOSE},
        None,
    ),
    ("de", "fahr alle Rollos im zweiten Stock runter", None, {OB: CLOSE, KB: CLOSE}, None),
    ("en", "turn on the lights", "living_room", LIVING_LIGHTS, None),
    ("de", "mach die Lichter im Wohnzimmer aus", None, dict.fromkeys(LIVING_LIGHTS, OFF), None),
    ("en", "open all the blinds on the second floor", None, {OB: OPEN, KB: OPEN}, None),
    # follow-ups: the previous command ran first, in the same conversation
    ("en", "turn it off again", None, {KL: OFF}, "turn on the kitchen light"),
    ("de", "und jetzt wieder aus", None, {FL: OFF}, "mach die Stehlampe an"),
    ("en", "make it 23", None, {BH: temp(23)}, "set the bathroom heating to 21 degrees"),
    ("de", "mach sie auf 50 Prozent", None, {DL: bright(50)}, "schalte die Schreibtischlampe ein"),
    # safety: the lock and the garage door
    ("en", "did I lock the front door", None, {FD: QUERY}, None),
    ("de", "ist die Haustür zu", None, {FD: QUERY}, None),
    ("de", "sperr die Haustür auf", None, {FD: UNLOCK}, None),
    ("de", "schließ die Haustür ab", None, {FD: LOCK}, None),
    ("en", "open the door", "hallway", None, None),
    ("en", "close the garage", None, None, None),
    # German particles
    ("de", "Rollos runter", "living_room", {BL: CLOSE}, None),
    ("de", "mach die Rollläden im Wohnzimmer auf", None, {BL: OPEN}, None),
    ("de", "dreh die Heizung im Bad auf 22 Grad", None, {BH: temp(22)}, None),
    ("de", "Licht im Flur aus", None, {HL: OFF}, None),
    ("de", "schalte den Fernseher ab", None, {TV: OFF}, None),
    # left/right and German endings
    ("de", "mach das rechte Licht an", "living_room", {RL: ON}, None),
    ("en", "turn off the right light", "living_room", {RL: OFF}, None),
    ("de", "schalte das linke Licht und die Stehlampe aus", None, {LL: OFF, FL: OFF}, None),
]

SENSITIVE = {GD, FD}
THRESHOLDS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5)
# split, group, lang, text, speaker's room, gold, home, previous command
ALL_CASES = (
    [("dev", "55", lang, text, None, gold, HOME, None) for lang, text, gold in CASES]
    + [("dev", "55", *case, HOME, None) for case in ROOM_CASES]
    + [("dev", "new", *case, HOME_FLOORS, None) for case in NEW_CASES]
    + [
        ("test", "test", lang, text, sat, gold, HOME_FLOORS, prev)
        for lang, text, sat, gold, prev in TEST_CASES
    ]
)


def expected(gold: dict) -> dict[str, tuple]:
    """{entity: (action, value)} -> {entity: (intent, value slots)}."""
    out = {}
    for eid, (action, value) in gold.items():
        slots = {ACTIONS[action].number.slot: value} if value is not None else {}
        out[eid] = (intent_for(action, eid.split(".")[0]), slots)
    return out


def acted(actions, home) -> dict[str, tuple]:
    """{entity: (intent, value slots)}: area actions expanded the way Home Assistant does."""
    out = {}
    for a in actions:
        values = {k: v for k, v in a.slots.items() if k not in ("name", "area", "domain")}
        if "name" in a.slots:
            ids = [a.slots["name"]]
        else:
            domains = a.slots.get("domain") or (["climate"] if "Climate" in a.intent else None)
            ids = [
                e.id
                for e in home.entities
                if e.area_id == a.slots.get("area")
                and (domains is None or e.id.split(".")[0] in domains)
            ]
        for eid in ids:
            out[eid] = (a.intent, values)
    return out


def short(actions: dict[str, tuple]) -> str:
    return ", ".join(
        f"{eid.split('.')[1]}:{intent}" + "".join(f" {k}={v:g}" for k, v in slots.items())
        for eid, (intent, slots) in sorted(actions.items())
    )


def run(model: str) -> list[dict]:
    provider = make_provider(model)
    provider.load()
    predict, calls = provider.predict, [0]

    def counted(*args, **kw):
        calls[0] += 1
        return predict(*args, **kw)

    provider.predict = counted
    rows = []
    for i, (split, group, lang, text, sat, gold, home, prev) in enumerate(ALL_CASES):
        if lang not in provider.languages:
            continue
        # The threshold is applied below, so one run gives the result for every threshold.
        kw = {"home": home, "satellite_area_id": sat, "options": {"confidence_threshold": 0.0}}
        if prev:
            kw["context_id"] = f"benchmark_{model.replace('.', '_')}_{i}"
            decide(make_request(prev, lang, **kw), provider)
        calls[0] = 0
        started = time.perf_counter()
        response, _ = decide(make_request(text, lang, **kw), provider)
        ms = (time.perf_counter() - started) * 1000
        got = acted(response.actions, home)
        want = expected(gold) if gold is not None else None
        rows.append(
            {
                "split": split,
                "group": group,
                "text": text,
                "sat": sat,
                "prev": prev,
                "want": short(want) if want else "hand off",
                "got": short(got) if got else f"handed off ({response.reason})",
                "handoff": not got,
                "must_hand_off": want is None,
                "ok": not got if want is None else got == want,
                "conf": min((a.confidence for a in response.actions), default=1.0),
                "unsafe": any(
                    e in got
                    and got[e][0] != "HassGetState"
                    and (want is None or got[e] != want.get(e))
                    for e in SENSITIVE
                ),
                "ms": ms,
                "calls": calls[0],
            }
        )
    return rows


def count(rows: list[dict], threshold: float) -> tuple[int, int, int, int]:
    """(right, handed off, wrong, unsafe). A must-hand-off sentence is right when nothing runs."""
    right = handoff = wrong = unsafe = 0
    for r in rows:
        runs = not r["handoff"] and r["conf"] >= threshold
        if r["must_hand_off"]:
            right += not runs
            wrong += runs
            unsafe += runs and r["unsafe"]
        elif not runs:
            handoff += 1
        elif r["ok"]:
            right += 1
        else:
            wrong += 1
            unsafe += r["unsafe"]
    return right, handoff, wrong, unsafe


def cell(rows: list[dict], threshold: float) -> str:
    """right/total · handed off · wrong (wrong on the lock or garage door)"""
    right, handoff, wrong, unsafe = count(rows, threshold)
    return f"{right}/{len(rows)} · {handoff} · **{wrong}**" + (f" ({unsafe}🔒)" if unsafe else "")


def best(rows: list[dict], max_wrong: int) -> str:
    """The most right sentences with at most `max_wrong` wrong, at the threshold that gives it."""
    found = None
    for t in sorted({0.0, *(round(r["conf"], 3) for r in rows)}):
        right, handoff, wrong, _ = count(rows, t)
        if wrong <= max_wrong and (found is None or right > found[0]):
            found = (right, handoff, t)
    return "–" if found is None else f"{found[0]}/{len(rows)} at {found[2]:.2f}"


if __name__ == "__main__":
    models = [a for a in sys.argv[1:] if not a.startswith("--")] or ["multilingual"]
    out = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--out=")), None)
    if unknown := set(models) - set(MODELS):
        sys.exit(f"unknown model(s) {sorted(unknown)}, choose from {', '.join(MODELS)}")
    results = {m: run(m) for m in models}
    if out:
        with open(out, "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
    for split in ("dev", "test"):
        print(f"\n### {split}\n")
        print("| Model | " + " | ".join(f"check {t:.1f}" for t in THRESHOLDS) + " | Median |")
        print("|---|" + "---:|" * (len(THRESHOLDS) + 1))
        for m, rows in results.items():
            rows = [r for r in rows if r["split"] == split]
            cells = " | ".join(cell(rows, t) for t in THRESHOLDS)
            print(f"| {m} | {cells} | {statistics.median(r['ms'] for r in rows):.0f} ms |")
        print("\n| Model | Right at 0 wrong | Right at ≤1 wrong | Model calls (median / max) |")
        print("|---|---:|---:|---:|")
        for m, rows in results.items():
            rows = [r for r in rows if r["split"] == split]
            calls = [r["calls"] for r in rows]
            print(
                f"| {m} | {best(rows, 0)} | {best(rows, 1)} | "
                f"{statistics.median(calls):g} / {max(calls)} |"
            )
    print("\n| Model | 55 sentences | 10 new sentences |\n|---|---:|---:|")
    for m, rows in results.items():
        groups = {g: [r for r in rows if r["group"] == g] for g in ("55", "new")}
        print(f"| {m} | {cell(groups['55'], 0.0)} | {cell(groups['new'], 0.0)} |")
    for m, rows in results.items():
        for split in ("dev", "test"):
            print(f"\n**{m}, {split}**\n")
            for r in rows:
                if r["split"] == split and not r["ok"]:
                    room = f" (speaker in {r['sat']})" if r["sat"] else ""
                    prev = f' (after "{r["prev"]}")' if r["prev"] else ""
                    print(
                        f'- "{r["text"]}"{room}{prev}: wanted {r["want"]}, got {r["got"]}'
                        f" · {r['conf']:.2f}"
                    )
