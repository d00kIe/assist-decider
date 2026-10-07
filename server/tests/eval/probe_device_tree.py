"""Probe (2026-10-06): can Laya alone run the tree devices -> action per device -> value?

Step 1: one yes/no question per exposed device ("is this device involved?").
Step 2: per involved device, a choice over the actions its kind supports.
        Variant A names the device in the question, variant B also puts it in the state.
Step 3: per action with a value, a choice over the numbers in the sentence + "none".
Extra:  a score question for vague changes ("a bit brighter").
Mix:    devices from the home's own names (a room name: Laya picks among that room's devices,
        nothing named: Laya picks among all), Laya "command or question?", then steps 2 and 3,
        with on/off words only masking the opposite action.

Run: uv run python tests/eval/probe_device_tree.py [multilingual] [english] [intern-decision-0.8b]
     [--tree]
"""

import json
import re
import sys
import time
import unicodedata

from assist_decider_server import numbers
from assist_decider_server.lang import LANGS, fold_with_map, tokenize
from assist_decider_server.pipeline import (
    _confidence,
    build_index,
    decide,
    entity_option,
    find_mentions,
)
from assist_decider_server.providers import make_provider

sys.path.insert(0, "tests")
from conftest import HOME, make_request  # noqa: E402

T = {
    "en": {
        "involved": "The user wants something done with the {d}, or asks about it.",
        "action": "What does the user want with the {d}?",
        "value": "Which value should the {d} be set to?",
        "none": "no value given for this device",
        "vague": "How should the {d} change?",
        "levels": ["much lower", "a bit lower", "unchanged", "a bit higher", "much higher"],
        "kind": "Is the user giving a command or asking a question?",
        "kinds": {
            "command": "a command to do something",
            "question": "a question about how something is",
        },
        "which": "Which device does the user mean?",
        "split": "Where does the first command end and the next one begin?",
        "same": "nowhere: it is one command, the second part only names more devices",
        "concrete": {
            "set_brightness": "set the brightness to {n} percent",
            "set_temperature": "set the temperature to {n} degrees",
            "set_position": "move to {n} percent open",
        },
        "acts": {
            "turn_on": "turn on, switch on, start",
            "turn_off": "turn off, switch off, stop",
            "set_brightness": "set the brightness to a value",
            "set_temperature": "set the temperature to a value",
            "open": "open",
            "close": "close",
            "set_position": "open to a position or percentage",
            "lock": "lock",
            "unlock": "unlock",
            "query": "only asks how it is, changes nothing",
        },
    },
    "de": {
        "involved": "Der Nutzer will etwas mit {d} machen oder fragt danach.",
        "action": "Was will der Nutzer mit {d}?",
        "value": "Auf welchen Wert soll {d} gestellt werden?",
        "none": "kein Wert für dieses Gerät",
        "vague": "Wie soll sich {d} ändern?",
        "levels": ["viel weniger", "etwas weniger", "unverändert", "etwas mehr", "viel mehr"],
        "kind": "Gibt der Nutzer einen Befehl oder stellt er eine Frage?",
        "kinds": {"command": "ein Befehl, etwas zu tun", "question": "eine Frage, wie etwas ist"},
        "which": "Welches Gerät meint der Nutzer?",
        "split": "Wo endet der erste Befehl und wo beginnt der nächste?",
        "same": "nirgends: es ist ein Befehl, der zweite Teil nennt nur weitere Geräte",
        "concrete": {
            "set_brightness": "Helligkeit auf {n} Prozent stellen",
            "set_temperature": "Temperatur auf {n} Grad stellen",
            "set_position": "auf {n} Prozent öffnen",
        },
        "acts": {
            "turn_on": "einschalten, anmachen, starten",
            "turn_off": "ausschalten, ausmachen, stoppen",
            "set_brightness": "Helligkeit auf einen Wert stellen",
            "set_temperature": "Temperatur auf einen Wert stellen",
            "open": "öffnen",
            "close": "schließen",
            "set_position": "auf eine Position oder Prozent fahren",
            "lock": "abschließen",
            "unlock": "aufschließen",
            "query": "fragt nur, wie es ist, ändert nichts",
        },
    },
}
# What each device kind can do: comes from Home Assistant, not from the sentence.
ACTIONS = {
    "light": ["turn_on", "turn_off", "set_brightness", "query"],
    "climate": ["turn_on", "turn_off", "set_temperature", "query"],
    "cover": ["open", "close", "set_position", "query"],
    "lock": ["lock", "unlock", "query"],
    "switch": ["turn_on", "turn_off", "query"],
    "media_player": ["turn_on", "turn_off", "query"],
}
ON_ACTS, OFF_ACTS = {"turn_on", "open", "lock"}, {"turn_off", "close", "unlock"}
VALUED = {"set_brightness", "set_temperature", "set_position"}
SENSITIVE = {"lock.front_door", "cover.garage_door"}

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
# text -> {device: (action, value)}   ("?" left out: STT often drops it)
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
# lang, text, satellite room, gold: nothing or only a room named
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
# text, device, gold level index (0 much lower .. 4 much higher)
VAGUE = [
    ("en", "dim the kitchen light a bit", KL, 1),
    ("en", "make the floor lamp much brighter", FL, 4),
    ("en", "it's way too warm, turn the thermostat down a lot", TH, 0),
    ("de", "mach das Küchenlicht etwas heller", KL, 3),
    ("de", "dreh die Heizung Bad ein bisschen runter", BH, 1),
]

ENT = {e.id: e for e in HOME.entities}
NUM = re.compile(r"\d+(?:[.,]\d+)?")


def dname(eid: str, lang: str) -> str:
    e = ENT[eid]
    return e.aliases[0] if lang == "de" and e.aliases else e.name


def ask(p, state, qs, lang):
    if hasattr(p, "score"):  # Intern-Decision, Kev, H2O-Lightning
        return p.score(state, qs)
    return p._agent.predict(state, qs, lang=lang)["answers"]


def involved(p, text, lang) -> dict[str, float]:
    qs = {
        eid: {"type": "noul", "instructions": T[lang]["involved"].format(d=dname(eid, lang))}
        for eid in ENT
    }
    a = ask(p, {"utterance": text}, qs, lang)
    return {eid: a[eid]["noul"] for eid in ENT}


def action(p, text, eid, lang, in_state: bool) -> str:
    d = dname(eid, lang)
    acts = ACTIONS[eid.split(".")[0]]
    q = {
        "type": "choice",
        "instructions": T[lang]["action"].format(d=d),
        "criteria": {k: T[lang]["acts"][k] for k in acts},
    }
    state = {"utterance": text, "device": d} if in_state else {"utterance": text}
    return ask(p, state, {"a": q}, lang)["a"]["choice"]


def value(p, text, eid, lang):
    nums = NUM.findall(text)
    crit = {n: n for n in nums} | {"none": T[lang]["none"]}
    q = {
        "type": "choice",
        "instructions": T[lang]["value"].format(d=dname(eid, lang)),
        "criteria": crit,
    }
    c = ask(p, {"utterance": text}, {"v": q}, lang)["v"]["choice"]
    return None if c == "none" else float(c.replace(",", "."))


INDEX = build_index(HOME)


CONFS: list[float] = []  # confidence of every choice in the current sentence
FLIP = False  # ponytail: probe-only switch, set by mix() for the "flip" context


def choose(p, text, lang, instructions, crit, allowed=None) -> str:
    q = {"type": "choice", "instructions": instructions, "criteria": crit}
    probs = ask(p, {"utterance": text}, {"q": q}, lang)["q"]["probabilities"]
    if FLIP:  # the same question with the options reversed, averaged: less position bias
        q["criteria"] = dict(reversed(crit.items()))
        back = ask(p, {"utterance": text}, {"q": q}, lang)["q"]["probabilities"]
        probs = {k: (probs[k] + back[k]) / 2 for k in probs}
    if allowed:
        total = sum(probs[k] for k in allowed) or 1.0
        probs = {k: probs[k] / total for k in allowed}
    best = max(probs, key=probs.__getitem__)
    CONFS.append(_confidence(probs, best))
    return best


class Unsure(Exception):
    pass


def mix(p, text, lang, sat=None, ctx=()) -> tuple[dict | None, float]:
    """-> (answer, lowest confidence of its choices). None: hand the request to Home Assistant
    (nothing named and no satellite room). The confidence check is applied afterwards, so one
    run gives the results for every threshold. ctx switches on extra context:
      desc  - devices are described by what they do ("TV: plays music, video and sound")
      num   - spoken numbers go into the action options ("set the temperature to 24")
      split - the model picks where one command ends; each device only sees its own part
      near  - on and off words both said: each device takes the one closest to its name
      qopt  - no "command or question?" gate; "only asks" is one more action option
      fit   - a room's devices that can't take the spoken number are not offered
      flip  - every choice is asked twice, options reversed the second time, and averaged
    """
    global FLIP
    FLIP = "flip" in ctx
    CONFS.clear()
    try:
        out = _mix(p, text, lang, sat, ctx)
    except Unsure:
        out = None
    return out, min(CONFS, default=1.0)


KIND_DESC = {
    "en": {
        "light": "a light or lamp: gives light, can be dimmed",
        "climate": "heating or thermostat: sets the room temperature",
        "cover": "blinds, shutters or curtains: open, close or move to a position",
        "cover.garage": "a garage door: opens and closes",
        "lock": "a door lock: locks and unlocks",
        "switch": "a switched plug or appliance: on or off",
        "media_player": "TV or speaker: plays music, video and sound",
    },
    "de": {
        "light": "Licht oder Lampe: macht hell, dimmbar",
        "climate": "Heizung oder Thermostat: stellt die Raumtemperatur",
        "cover": "Rollo, Rollladen oder Jalousie: öffnen, schließen, auf Position fahren",
        "cover.garage": "ein Garagentor: öffnet und schließt",
        "lock": "ein Türschloss: abschließen und aufschließen",
        "switch": "Schaltsteckdose oder Gerät: an oder aus",
        "media_player": "Fernseher oder Lautsprecher: spielt Musik, Video und Ton",
    },
}


# action -> (units, lowest, highest) the device accepts
RANGES = {
    "set_brightness": ({"pct", ""}, 0, 100),
    "set_temperature": ({"deg", ""}, 5, 35),
    "set_position": ({"pct", ""}, 0, 100),
}


def fits(eid, nums) -> bool:
    """Some valued action of the device takes some spoken number."""
    return any(
        n.unit in RANGES[a][0] and RANGES[a][1] <= n.value <= RANGES[a][2]
        for a in ACTIONS[eid.split(".")[0]]
        if a in VALUED
        for n in nums
    )


def polarity(tokens, m, on_w, off_w) -> tuple[bool, bool]:
    """(on, off) from the on/off word nearest to mention m; a tie keeps both."""

    def dist(i):
        return m.start - i if i < m.start else i - m.end + 1

    on = [dist(i) for i, t in enumerate(tokens) if t in on_w and not m.start <= i < m.end]
    off = [dist(i) for i, t in enumerate(tokens) if t in off_w and not m.start <= i < m.end]
    if not on or not off:
        return bool(on), bool(off)
    return min(on) <= min(off), min(off) <= min(on)


def kind_desc(eid, lang):
    e = INDEX.entities[eid]
    return KIND_DESC[lang].get(f"{e.domain}.{e.device_class}") or KIND_DESC[lang][e.domain]


def clauses(p, text, lang, mentions, spans, n_tokens) -> list[tuple[int, int]]:
    """Token ranges, one per command. Between two named things Laya picks the cut."""
    bounds = [0]
    for a, b in zip(mentions, mentions[1:], strict=False):
        opts = {}
        for k in range(a.end, b.start + 1):
            left = text[spans[bounds[-1]][0] : spans[k - 1][1]]
            opts[str(k)] = f'"{left}" | "{text[spans[k][0] :]}"'
        opts["same"] = T[lang]["same"]
        cut = choose(p, text, lang, T[lang]["split"], opts)
        if cut != "same":
            bounds.append(int(cut))
    bounds.append(n_tokens)
    return list(zip(bounds, bounds[1:], strict=False))


def _mix(p, text, lang, sat, ctx) -> dict | None:
    folded, cmap = fold_with_map(text)
    toks = tokenize(folded)
    tokens = [t for t, _, _ in toks]
    text = unicodedata.normalize("NFKC", text)
    spans = [(cmap[s], cmap[e - 1] + 1) for _, s, e in toks]
    mentions = find_mentions(tokens, INDEX)
    segs = [(0, len(tokens))]
    if "split" in ctx and len(mentions) > 1:
        segs = clauses(p, text, lang, mentions, spans, len(tokens))

    def seg_of(i):
        return next(s for s in segs if s[0] <= i < s[1])

    def seg_text(s):
        return text[spans[s[0]][0] : spans[s[1] - 1][1]]

    targets = [(kind, rid, seg_of(m.start), m) for m in mentions for kind, rid in m.refs]
    if not targets and sat:
        targets = [("area", sat, segs[0], None)]  # nothing named: the satellite's room
    if not targets:
        return None
    question = None
    if "qopt" not in ctx:
        question = choose(p, text, lang, T[lang]["kind"], T[lang]["kinds"]) == "question"
    named = {rid for kind, rid, _, _ in targets if kind == "entity"}
    nums = numbers.extract(folded, lang)
    work = []
    for kind, rid, seg, m in targets:
        if kind == "entity":
            work.append((rid, seg, m))
            continue
        inside = [e for e, r in INDEX.entities.items() if r.area_id == rid]
        if any(e in named for e in inside) or not inside:
            continue
        if "fit" in ctx and nums:
            inside = [e for e in inside if fits(e, nums)] or inside
        if len(inside) == 1:
            work.append((inside[0], seg, m))
            continue
        crit = {
            e: f"{dname(e, lang)}: {kind_desc(e, lang)}"
            if "desc" in ctx
            else entity_option(INDEX.entities[e], INDEX, LANGS[lang])
            for e in inside
        }
        work.append((choose(p, seg_text(seg), lang, T[lang]["which"], crit), seg, m))

    out = {}
    for eid, seg, m in work:
        st = seg_text(seg)
        words = set(tokens[seg[0] : seg[1]])
        domain = eid.split(".")[0]
        L = LANGS[lang]
        on_w, off_w = (
            (L.lock_words, L.unlock_words) if domain == "lock" else (L.on_words, L.off_words)
        )
        on, off = bool(words & on_w), bool(words & off_w)
        if "near" in ctx and m and on and off:
            on, off = polarity(tokens, m, on_w, off_w)
        acts = [
            a
            for a in ACTIONS[domain]
            if (question is None or (a == "query") == question)
            and not (a in ON_ACTS and off and not on)
            and not (a in OFF_ACTS and on and not off)
        ]
        d = dname(eid, lang) + (f" ({kind_desc(eid, lang)})" if "desc" in ctx else "")
        if "num" in ctx:
            crit = {}
            for a in ACTIONS[domain]:
                if a in VALUED:
                    for n in NUM.findall(st):
                        crit[f"{a}={n}"] = T[lang]["concrete"][a].format(n=n)
                else:
                    crit[a] = T[lang]["acts"][a]
            allow = [k for k in crit if k.split("=")[0] in acts]
            if not allow:
                raise Unsure("no action")
            a, _, n = choose(p, st, lang, T[lang]["action"].format(d=d), crit, allow).partition("=")
            out[eid] = (a, float(n.replace(",", ".")) if n else None)
        else:
            crit = {k: T[lang]["acts"][k] for k in ACTIONS[domain]}
            a = choose(p, st, lang, T[lang]["action"].format(d=d), crit, acts)
            out[eid] = (a, value(p, st, eid, lang) if a in VALUED else None)
    return out


def fmt(d: dict) -> str:
    return ", ".join(
        f"{k.split('.')[1]}:{a}{'' if v is None else f'={v:g}'}" for k, (a, v) in d.items()
    )


TODAY_ACTION = {  # today's pipeline intents -> (action per domain, value slot)
    "HassTurnOn": ({"cover": "open", "lock": "lock"}, "turn_on", None),
    "HassTurnOff": ({"cover": "close", "lock": "unlock"}, "turn_off", None),
    "HassLightSet": ({}, "set_brightness", "brightness"),
    "HassClimateSetTemperature": ({}, "set_temperature", "temperature"),
    "HassSetPosition": ({}, "set_position", "position"),
    "HassGetState": ({}, "query", None),
    "HassClimateGetTemperature": ({}, "query", None),
}


def today(p, text, lang, sat) -> tuple[dict | None, float]:
    """Today's pipeline (word lists + one model) on the sentence, in the same shape as mix()."""
    extra = {"satellite_area_id": sat} if sat else {}
    # the confidence check is applied afterwards, like for mix()
    req = make_request(text, lang, options={"confidence_threshold": 0.0}, **extra)
    resp, trace = decide(req, p)
    confs = [q["confidence"] for seg in trace["segments"] for q in seg.get("questions", [])]
    if resp.status == "escalate":
        return None, min(confs, default=1.0)
    out = {}
    for a in resp.actions:
        s = a.slots
        if "name" in s:
            ids = [s["name"]]
        else:
            ids = [
                e
                for e, r in ENT.items()
                if r.area_id == s.get("area")
                and (not s.get("domain") or e.split(".")[0] in s["domain"])
            ]
            if a.intent.startswith("HassClimate"):
                ids = [e for e in ids if e.startswith("climate.")]
        by_domain, default, slot = TODAY_ACTION[a.intent]
        for e in ids:
            out[e] = (by_domain.get(e.split(".")[0], default), s.get(slot) if slot else None)
    return out, min(confs, default=1.0)


ALL_CASES = [("named", lg, t, None, g) for lg, t, g in CASES] + [
    ("room", lg, t, sat, g) for lg, t, sat, g in ROOM_CASES
]
THRESHOLDS = (0.0, 0.2, 0.3, 0.4, 0.5)


def run(model: str, tree: bool) -> dict:
    p = make_provider(model)
    p.load()
    res: dict = {"model": model, "device": p.device, "languages": list(p.languages)}
    if tree:
        rows = []
        for lang, text, gold in CASES:
            if lang not in p.languages:
                continue
            t0 = time.perf_counter()
            probs = involved(p, text, lang)
            acts = {e: action(p, text, e, lang, False) for e in gold}
            vals = {e: value(p, text, e, lang) for e, (_, v) in gold.items() if v is not None}
            picked = {e for e, pr in probs.items() if pr >= 0.5}
            e2e = {}
            for e in sorted(picked):
                a = action(p, text, e, lang, False)
                e2e[e] = (a, value(p, text, e, lang) if a in VALUED else None)
            rows.append(
                {
                    "text": text,
                    "yesno": probs,
                    "gold": sorted(gold),
                    "action_ok": [acts[e] == g for e, (g, _) in gold.items()],
                    "value_ok": [vals[e] == v for e, (_, v) in gold.items() if v is not None],
                    "e2e_ok": e2e == gold,
                    "e2e_unsafe": any(
                        e in e2e and e2e[e][0] != "query" and e2e[e] != gold.get(e)
                        for e in SENSITIVE
                    ),
                    "ms": (time.perf_counter() - t0) * 1000,
                }
            )
        vague = []
        for lang, text, eid, g in VAGUE:
            if lang in p.languages:
                q = {
                    "type": "score",
                    "instructions": T[lang]["vague"].format(d=dname(eid, lang)),
                    "criteria": T[lang]["levels"],
                }
                vague.append(round(ask(p, {"utterance": text}, {"s": q}, lang)["s"]["score"]) == g)
        res["tree"] = {"rows": rows, "vague_ok": vague}

    approaches = {"today": lambda lang, text, sat: today(p, text, lang, sat)}
    ctxs = [(), ("desc",), ("num",), ("split",), ("desc", "num", "split")]
    ctxs += [("near",), ("fit",), ("qopt",), ("flip",), ("near", "fit")]
    for ctx in ctxs:
        approaches["mix" + "".join(f"+{c}" for c in ctx)] = lambda lang, text, sat, ctx=ctx: mix(
            p, text, lang, sat, ctx
        )
    for name, fn in approaches.items():
        rows = []
        for kind, lang, text, sat, gold in ALL_CASES:
            if lang not in p.languages:
                continue
            t0 = time.perf_counter()
            got, conf = fn(lang, text, sat)
            ms = (time.perf_counter() - t0) * 1000
            unsafe = got is not None and any(
                e in got and got[e][0] != "query" and got[e] != gold.get(e) for e in SENSITIVE
            )
            rows.append(
                {
                    "set": kind,
                    "lang": lang,
                    "text": text,
                    "sat": sat,
                    "got": None if got is None else fmt(got),
                    "want": fmt(gold),
                    "ok": got == gold,
                    "handoff": got is None,
                    "conf": conf,
                    "unsafe": unsafe,
                    "ms": ms,
                }
            )
            if got != gold:
                print(
                    f"{'↑' if got is None else '✗'} {name}: {text} [{sat}] "
                    f"want {fmt(gold)} | got {'-' if got is None else fmt(got)} ({conf:.2f})"
                )
        res[name] = rows
    return res


def tally(rows, threshold) -> dict:
    """right / handed off / wrong / wrong on lock or garage, with the confidence check at
    threshold."""
    t = {"n": len(rows), "right": 0, "handoff": 0, "wrong": 0, "unsafe": 0}
    for r in rows:
        if r["handoff"] or r["conf"] < threshold:
            t["handoff"] += 1
        elif r["ok"]:
            t["right"] += 1
        else:
            t["wrong"] += 1
            t["unsafe"] += r["unsafe"]
    return t


if __name__ == "__main__":
    out_dir = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--out=")), None)
    for model in [a for a in sys.argv[1:] if not a.startswith("-")] or ["multilingual"]:
        res = run(model, "--tree" in sys.argv)
        print(f"\n== {model} on {res['device']}")
        for name, rows in res.items():
            if isinstance(rows, list) and rows and "conf" in rows[0]:
                for th in (0.0, 0.4):
                    t = tally(rows, th)
                    print(
                        f"  {name:20s} check {th:.1f}: right {t['right']}/{t['n']}  "
                        f"handed off {t['handoff']}  wrong {t['wrong']} "
                        f"(lock/garage {t['unsafe']})"
                    )
        if out_dir:
            with open(f"{out_dir}/{model}.json", "w") as f:
                json.dump(res, f, ensure_ascii=False, indent=1)
