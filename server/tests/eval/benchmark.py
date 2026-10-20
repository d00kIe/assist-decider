"""The benchmark of BENCHMARK.md: sentences through the server's own pipeline.

Three splits. "dev" is the 65 sentences the pipeline was tuned on. "test" is held out: written on
2026-10-10, before the v2 changes, and never tuned on; report it, don't tune on it. "log" is what
speech-to-text really wrote in a real home on 2026-10-18 (typos included), in a home named the
same way, with Home Assistant set to German.

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
BRIGHTER, DIMMER = ("brighter", None), ("dimmer", None)
WARMER, COOLER = ("warmer", None), ("cooler", None)
# More than one right answer: brighter turns on a light that is off, so "on" is right too.
LIGHTER = [BRIGHTER, ON]


def temp(value: float) -> tuple:
    return ("set_temperature", value)


def bright(value: float) -> tuple:
    return ("set_brightness", value)


ALL_LIGHTS = {KL: OFF, DL: OFF, FL: OFF, HL: OFF, LL: OFF, RL: OFF}
# Held out: written 2026-10-10 before the v2 changes, never tuned on. Home: HOME_FLOORS.
# lang, text, speaker's room, gold (None: must be handed off), previous command (a follow-up)
TEST_CASES = [
    # vague: a step from the current value. Until 2026-10-19 there were no steps: "freezing",
    # "zu warm" and "a bit brighter" had to be handed off.
    ("en", "it's too dark in here", "kitchen", {KL: LIGHTER}, None),
    ("de", "hier ist es viel zu dunkel", "bedroom", {DL: LIGHTER}, None),
    ("en", "I'm freezing", "bathroom", {BH: WARMER}, None),
    ("de", "mir ist zu warm", "living_room", {TH: COOLER}, None),
    ("en", "make it a bit brighter", "bedroom", {DL: BRIGHTER}, None),
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

# A home named like the one the live log came from: KNX dimmer names, "rechts"/"links" in both a
# light's and a blind's name, a TV with an English name.
W_R, W_L, W_T, W_BR, W_BL, W_TH, W_TV, K_L, K_B, K_TH = (
    "light.eg_wohnen_licht_rechts",
    "light.eg_wohnen_licht_links",
    "light.eg_wohnen_esstisch_licht",
    "cover.eg_wohnen_rechts",
    "cover.eg_wohnen_links",
    "climate.eg_wohnen",
    "media_player.living_room_tv",
    "light.eg_kuche_licht",
    "cover.eg_kuche",
    "climate.eg_essen_kuche",
)
HOME_LOG = HOME.model_copy(
    update={
        "floors": [Floor(id="eg", name="EG", aliases=["Erdgeschoss"])],
        "areas": [
            Area(id="wohnzimmer", name="Wohnzimmer", floor_id="eg"),
            Area(id="kuche", name="Küche", floor_id="eg"),
        ],
        "entities": [
            Entity(id=W_R, name="Dimmaktor 2 EG Wohnen Licht Rechts", area_id="wohnzimmer"),
            Entity(id=W_L, name="Dimmaktor 2 EG Wohnen Licht Links", area_id="wohnzimmer"),
            Entity(
                id=W_T,
                name="Dimmaktor 1 EG Wohnen Esstisch Licht",
                aliases=["Table"],
                area_id="wohnzimmer",
            ),
            Entity(id=W_BR, name="EG Wohnen rechts", area_id="wohnzimmer", device_class="blind"),
            Entity(id=W_BL, name="EG Wohnen links", area_id="wohnzimmer", device_class="blind"),
            Entity(id=W_TH, name="EG Wohnen", area_id="wohnzimmer"),
            Entity(
                id="sensor.eg_wohnen_temperature",
                name="EG Wohnen Wandthermostat Temperature",
                area_id="wohnzimmer",
                device_class="temperature",
            ),
            Entity(
                id="sensor.eg_wohnen_humidity",
                name="EG Wohnen Wandthermostat Humidity",
                area_id="wohnzimmer",
                device_class="humidity",
            ),
            Entity(id=W_TV, name="Living Room TV", area_id="wohnzimmer"),
            Entity(id=K_L, name="Dimmaktor 1 EG Küche Licht", area_id="kuche"),
            Entity(id=K_B, name="EG Küche", area_id="kuche", device_class="blind"),
            Entity(id=K_TH, name="EG Essen/Küche", area_id="kuche"),
            Entity(
                id="sensor.eg_kuche_temperature",
                name="EG Küche Wandthermostat Temperature",
                area_id="kuche",
                device_class="temperature",
            ),
        ],
    }
)
W_LIGHTS = (W_R, W_L, W_T)
# lang (as Home Assistant sent it), text as speech-to-text wrote it, speaker's room, gold
LOG_CASES = [
    ("de", "Macht die Rollos im Wohnzimmer runter.", None, {W_BR: CLOSE, W_BL: CLOSE}),
    ("de", "Macht die Rohrläden in der Küche runter.", None, {K_B: CLOSE}),
    ("de", "Macht die Hohlhose in der Küche runter.", None, {K_B: CLOSE}),  # "Rollos", misheard
    ("de", "Dim, die Lichter in der Küche!", None, {K_L: DIMMER}),
    (
        "de",
        "Setzt das Licht auf hundert Prozent.",
        "wohnzimmer",
        dict.fromkeys(W_LIGHTS, bright(100)),
    ),
    ("de", "Setzt das Licht auf 100%.", "wohnzimmer", dict.fromkeys(W_LIGHTS, bright(100))),
    (
        "de",
        "Set the brightness to a hundred percent.",
        "wohnzimmer",
        dict.fromkeys(W_LIGHTS, bright(100)),
    ),
    ("de", "Turn on the lights.", "wohnzimmer", dict.fromkeys(W_LIGHTS, ON)),
    ("de", "Mach's die Lichter an.", "wohnzimmer", dict.fromkeys(W_LIGHTS, ON)),
    ("de", "Macht das Tischlicht an.", "wohnzimmer", {W_T: ON}),
    ("de", "Turn on The table light.", "wohnzimmer", {W_T: ON}),
    ("de", "Es ist zu dunkel hier.", "wohnzimmer", dict.fromkeys(W_LIGHTS, LIGHTER)),
    ("de", "Es ist zu hell hier.", "wohnzimmer", dict.fromkeys(W_LIGHTS, DIMMER)),
    ("de", "Mach die Lichter heller.", "wohnzimmer", dict.fromkeys(W_LIGHTS, BRIGHTER)),
    ("de", "Dimm das Licht in der Küche.", None, {K_L: DIMMER}),
    ("de", "Mir ist kalt.", "wohnzimmer", {W_TH: WARMER}),
    ("de", "dim the lights", "wohnzimmer", dict.fromkeys(W_LIGHTS, DIMMER)),
    ("de", "it's too dark in here", "wohnzimmer", dict.fromkeys(W_LIGHTS, LIGHTER)),
    ("de", "Oh.", "wohnzimmer", None),
    ("de", "Yeah.", "wohnzimmer", None),
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
    + [("log", "log", lang, text, sat, gold, HOME_LOG, None) for lang, text, sat, gold in LOG_CASES]
)
SPLITS = ("dev", "test", "log")


def _call(eid: str, action: str, value: float | None) -> tuple:
    if action not in ACTIONS:  # an older server, run with this script: it can't be right
        return (action, {})
    spec = ACTIONS[action]
    if getattr(spec, "step", None):
        slots = {spec.step[0]: spec.step[1]}
    else:
        slots = {spec.number.slot: value} if value is not None else {}
    return (intent_for(action, eid.split(".")[0]), slots)


def expected(gold: dict) -> dict[str, list[tuple]]:
    """{entity: (action, value) or a list of them} -> {entity: [(intent, value slots)]}."""
    return {
        eid: [_call(eid, *a) for a in (want if isinstance(want, list) else [want])]
        for eid, want in gold.items()
    }


def matches(got: dict[str, tuple], want: dict[str, list[tuple]]) -> bool:
    return got.keys() == want.keys() and all(got[e] in want[e] for e in want)


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


def short(actions: dict[str, tuple | list[tuple]]) -> str:
    def one(intent: str, slots: dict) -> str:
        return intent + "".join(f" {k}={v:g}" for k, v in slots.items())

    return ", ".join(
        f"{eid.split('.')[1]}:"
        + " or ".join(one(*c) for c in (calls if isinstance(calls, list) else [calls]))
        for eid, calls in sorted(actions.items())
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
                "ok": not got if want is None else matches(got, want),
                "conf": min((a.confidence for a in response.actions), default=1.0),
                "unsafe": any(
                    e in got
                    and got[e][0] != "HassGetState"
                    and (want is None or got[e] not in want.get(e, []))
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
    for split in SPLITS:
        print(f"\n### {split}\n")
        print("| Model | " + " | ".join(f"check {t:.1f}" for t in THRESHOLDS) + " | Median |")
        print("|---|" + "---:|" * (len(THRESHOLDS) + 1))
        for m, rows in results.items():
            rows = [r for r in rows if r["split"] == split]
            if not rows:  # e.g. the English model on the German log
                continue
            cells = " | ".join(cell(rows, t) for t in THRESHOLDS)
            print(f"| {m} | {cells} | {statistics.median(r['ms'] for r in rows):.0f} ms |")
        print("\n| Model | Right at 0 wrong | Right at ≤1 wrong | Model calls (median / max) |")
        print("|---|---:|---:|---:|")
        for m, rows in results.items():
            rows = [r for r in rows if r["split"] == split]
            if not rows:
                continue
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
        for split in SPLITS:
            print(f"\n**{m}, {split}**\n")
            for r in rows:
                if r["split"] == split and not r["ok"]:
                    room = f" (speaker in {r['sat']})" if r["sat"] else ""
                    prev = f' (after "{r["prev"]}")' if r["prev"] else ""
                    print(
                        f'- "{r["text"]}"{room}{prev}: wanted {r["want"]}, got {r["got"]}'
                        f" · {r['conf']:.2f}"
                    )
