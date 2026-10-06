"""Probe (2026-10-05): can Laya answer utterance-level feature questions (kind/polarity/unit/asked)?

Run: uv run python tests/eval/probe_features.py [multilingual] [english]
"""

import sys
import time

from assist_decider_server.providers import LayaProvider, Question

Q = {
    "en": {
        "kind": Question(
            "Is the user giving a command or asking a question?",
            {
                "command": "a command to do something",
                "question": "a question about how something is",
            },
        ),
        "polarity": Question(
            "Does the user want something switched on, switched off, or neither?",
            {
                "on": "on, open, start",
                "off": "off, close, stop",
                "neither": "neither / a value or a question",
            },
        ),
        "unit": Question(
            "What kind of value does the user mention?",
            {
                "percent": "a percentage or level",
                "degrees": "a temperature in degrees",
                "none": "no value",
            },
        ),
        "asked": Question(
            "What does the user want to know?",
            {
                "onoff": "whether something is on or off",
                "openclosed": "whether something is open, closed or locked",
                "temperature": "a temperature",
                "brightness": "how bright a light is",
                "position": "how far a cover or blind is open",
                "nothing": "nothing, it is a command",
            },
        ),
    },
    "de": {
        "kind": Question(
            "Gibt der Nutzer einen Befehl oder stellt er eine Frage?",
            {"command": "ein Befehl, etwas zu tun", "question": "eine Frage, wie etwas ist"},
        ),
        "polarity": Question(
            "Soll etwas eingeschaltet, ausgeschaltet oder keins von beiden werden?",
            {
                "on": "an, auf, starten",
                "off": "aus, zu, stoppen",
                "neither": "keins / ein Wert oder eine Frage",
            },
        ),
        "unit": Question(
            "Welche Art von Wert nennt der Nutzer?",
            {
                "percent": "Prozent oder Stufe",
                "degrees": "eine Temperatur in Grad",
                "none": "kein Wert",
            },
        ),
        "asked": Question(
            "Was möchte der Nutzer wissen?",
            {
                "onoff": "ob etwas an oder aus ist",
                "openclosed": "ob etwas offen, zu oder verschlossen ist",
                "temperature": "eine Temperatur",
                "brightness": "wie hell ein Licht ist",
                "position": "wie weit eine Abdeckung oder ein Rollo offen ist",
                "nothing": "nichts, es ist ein Befehl",
            },
        ),
    },
}
# text, kind, polarity, unit, asked   ("?" deliberately left out: STT often drops it)
CASES = [
    ("en", "turn on the kitchen light", "command", "on", "none", "nothing"),
    ("en", "switch off the kitchen light", "command", "off", "none", "nothing"),
    ("en", "kitchen light on", "command", "on", "none", "nothing"),
    ("en", "lights off please", "command", "off", "none", "nothing"),
    ("en", "set the kitchen light to 40 percent", "command", "neither", "percent", "nothing"),
    ("en", "dim the bedroom to 20", "command", "neither", "percent", "nothing"),
    ("en", "set the bathroom to 22 degrees", "command", "neither", "degrees", "nothing"),
    ("en", "make it 21 in the living room", "command", "neither", "degrees", "nothing"),
    ("en", "open the living room blinds to 30%", "command", "neither", "percent", "nothing"),
    ("en", "close the blinds", "command", "off", "none", "nothing"),
    ("en", "open the blinds", "command", "on", "none", "nothing"),
    ("en", "is the kitchen light on", "question", "neither", "none", "onoff"),
    ("en", "are the lights off in the bedroom", "question", "neither", "none", "onoff"),
    ("en", "is the front door locked", "question", "neither", "none", "openclosed"),
    ("en", "are the blinds open", "question", "neither", "none", "openclosed"),
    ("en", "how warm is it in the bathroom", "question", "neither", "none", "temperature"),
    (
        "en",
        "what's the temperature in the living room",
        "question",
        "neither",
        "none",
        "temperature",
    ),
    ("en", "how bright is the kitchen light", "question", "neither", "none", "brightness"),
    ("en", "how far are the blinds open", "question", "neither", "none", "position"),
    ("en", "tell me if the fan is running", "question", "neither", "none", "onoff"),
    ("de", "schalte das Küchenlicht ein", "command", "on", "none", "nothing"),
    ("de", "mach das Licht im Bad aus", "command", "off", "none", "nothing"),
    ("de", "Küchenlicht an", "command", "on", "none", "nothing"),
    ("de", "dimme das Schlafzimmer auf 20 Prozent", "command", "neither", "percent", "nothing"),
    ("de", "stell das Bad auf 22 Grad", "command", "neither", "degrees", "nothing"),
    ("de", "fahr die Rollos auf 30 Prozent", "command", "neither", "percent", "nothing"),
    ("de", "mach die Rollos zu", "command", "off", "none", "nothing"),
    ("de", "ist das Küchenlicht an", "question", "neither", "none", "onoff"),
    ("de", "ist die Haustür abgeschlossen", "question", "neither", "none", "openclosed"),
    ("de", "wie warm ist es im Bad", "question", "neither", "none", "temperature"),
    ("de", "wie hell ist das Licht in der Küche", "question", "neither", "none", "brightness"),
    ("de", "sind die Rollos offen", "question", "neither", "none", "openclosed"),
]
FEATS = ["kind", "polarity", "unit", "asked"]
for model in sys.argv[1:] or ["multilingual"]:
    p = LayaProvider(model)
    p.load()
    hits = {f: 0 for f in FEATS}
    n = 0
    ms = []
    confs = {f: [] for f in FEATS}
    for lang, text, *gold in CASES:
        if lang not in p.languages:
            continue
        n += 1
        t = time.perf_counter()
        a = p.predict({"utterance": text}, Q[lang], lang)
        ms.append((time.perf_counter() - t) * 1000)
        row = []
        for f, g in zip(FEATS, gold, strict=True):
            ok = a[f].choice == g
            hits[f] += ok
            confs[f].append(a[f].probs[a[f].choice])
            row.append(
                ("  " if ok else "✗ ")
                + f"{f}={a[f].choice}({a[f].probs[a[f].choice]:.2f})"
                + ("" if ok else f"[want {g}]")
            )
        print(f"{text:45s} " + " ".join(row))
    ms.sort()
    print(
        f"\n== {model}: n={n} "
        + " ".join(f"{f}={hits[f]}/{n}" for f in FEATS)
        + f" | all4 per call p50={ms[len(ms) // 2]:.0f}ms max={ms[-1]:.0f}ms\n"
    )
