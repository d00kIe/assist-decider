"""Probe (2026-10-05): Laya questions that could replace word lists (split, conditions…).

Run: uv run python tests/eval/probe_rest.py [multilingual] [english]
"""

import sys
import time

from assist_decider_server.providers import LayaProvider, Question

Q = {
    "own": {
        "en": Question(
            "Does this part of the sentence contain its own action, "
            "or does it only add more devices or a value to the previous action?",
            {"own": "its own action", "more": "only more devices or a value"},
        ),
        "de": Question(
            "Enthält dieser Satzteil eine eigene Aktion, "
            "oder nennt er nur weitere Geräte oder einen Wert für die vorige Aktion?",
            {"own": "eine eigene Aktion", "more": "nur weitere Geräte oder ein Wert"},
        ),
    },
    "test": {
        "en": Question(
            "What does the condition test?",
            {
                "cold": "it is cold",
                "warm": "it is warm or hot",
                "on": "something is on, open or detected",
                "off": "something is off, closed or clear",
                "below": "a value is below a number",
                "above": "a value is above a number",
            },
        ),
        "de": Question(
            "Was prüft die Bedingung?",
            {
                "cold": "es ist kalt",
                "warm": "es ist warm oder heiß",
                "on": "etwas ist an, offen oder erkannt",
                "off": "etwas ist aus, geschlossen oder frei",
                "below": "ein Wert ist unter einer Zahl",
                "above": "ein Wert ist über einer Zahl",
            },
        ),
    },
    "where": {
        "en": Question(
            "Is the condition about outdoors or indoors?",
            {"outside": "outdoors, the weather", "inside": "inside the house"},
        ),
        "de": Question(
            "Geht es in der Bedingung um draußen oder drinnen?",
            {"outside": "draußen, das Wetter", "inside": "drinnen im Haus"},
        ),
    },
    "kind": {
        "en": Question(
            "Which kind of device does the user mean?",
            {
                "light": "lights",
                "cover": "blinds, shutters",
                "climate": "heating, thermostat",
                "media_player": "TV, music, speaker",
                "fan": "fan",
            },
        ),
        "de": Question(
            "Welche Art von Gerät meint der Nutzer?",
            {
                "light": "Licht, Lampen",
                "cover": "Rollos, Jalousien",
                "climate": "Heizung, Thermostat",
                "media_player": "Fernseher, Musik, Lautsprecher",
                "fan": "Ventilator",
            },
        ),
    },
}
CASES = {
    "own": [
        ("en", "and the hallway lights", "more"),
        ("en", "set the heating to 21", "own"),
        ("en", "the kitchen too", "more"),
        ("en", "23 degrees", "more"),
        ("en", "dim the bedroom to 20", "own"),
        ("en", "and the bedroom", "more"),
        ("en", "close the blinds", "own"),
        ("en", "also the fan", "more"),
        ("en", "play some music", "own"),
        ("en", "kill the lights", "own"),
        ("en", "make it brighter", "own"),
        ("en", "the hallway as well", "more"),
        ("de", "und den Flur", "more"),
        ("de", "mach das Radio an", "own"),
        ("de", "die Küche auch", "more"),
        ("de", "22 Grad", "more"),
        ("de", "fahr die Rollos runter", "own"),
        ("de", "und das Schlafzimmer", "more"),
        ("de", "dreh die Heizung auf", "own"),
        ("de", "auch im Bad", "more"),
    ],
    "test": [
        ("en", "if it is cold outside", "cold"),
        ("en", "if it's freezing", "cold"),
        ("en", "when it gets hot", "warm"),
        ("en", "if the window is open", "on"),
        ("en", "if the door is closed", "off"),
        ("en", "if motion is detected", "on"),
        ("en", "when the temperature drops below 18", "below"),
        ("en", "if the humidity is over 60", "above"),
        ("en", "if nobody is in the room", "off"),
        ("en", "if it's chilly in the bedroom", "cold"),
        ("de", "wenn es draußen kalt ist", "cold"),
        ("de", "falls es heiß ist", "warm"),
        ("de", "wenn das Fenster offen ist", "on"),
        ("de", "wenn die Tür zu ist", "off"),
        ("de", "wenn die Temperatur unter 18 Grad fällt", "below"),
        ("de", "wenn die Luftfeuchtigkeit über 60 ist", "above"),
        ("de", "wenn es frisch im Schlafzimmer ist", "cold"),
        ("de", "wenn Bewegung erkannt wird", "on"),
    ],
    "where": [
        ("en", "if it is cold outside", "outside"),
        ("en", "if it's raining", "outside"),
        ("en", "if it's cold in the bedroom", "inside"),
        ("en", "if the window is open", "inside"),
        ("en", "if it's sunny", "outside"),
        ("en", "if the temperature on the balcony is below 5", "outside"),
        ("en", "if it's warm in here", "inside"),
        ("de", "wenn es draußen kalt ist", "outside"),
        ("de", "wenn es regnet", "outside"),
        ("de", "wenn es im Bad kalt ist", "inside"),
        ("de", "wenn die Sonne scheint", "outside"),
        ("de", "wenn es hier drin warm ist", "inside"),
        ("de", "wenn es auf der Terrasse unter 5 Grad hat", "outside"),
    ],
    "kind": [
        ("en", "turn on the kitchen", "light"),
        ("en", "make the living room brighter", "light"),
        ("en", "darken the bedroom", "cover"),
        ("en", "let some sun into the living room", "cover"),
        ("en", "warm up the bathroom", "climate"),
        ("en", "make the bedroom cozy and warm", "climate"),
        ("en", "turn up the volume in the kitchen", "media_player"),
        ("en", "get some air moving in the office", "fan"),
        ("en", "it's too dark in the hallway", "light"),
        ("de", "mach die Küche hell", "light"),
        ("de", "verdunkle das Schlafzimmer", "cover"),
        ("de", "heiz das Bad auf", "climate"),
        ("de", "mach im Wohnzimmer lauter", "media_player"),
        ("de", "lass Licht ins Wohnzimmer", "cover"),
        ("de", "es ist zu dunkel im Flur", "light"),
        ("de", "ich brauche frische Luft im Büro", "fan"),
    ],
}
for model in sys.argv[1:] or ["multilingual"]:
    p = LayaProvider(model)
    p.load()
    print(f"\n######## {model}")
    for q, cases in CASES.items():
        hit = n = 0
        ms = []
        for lang, text, gold in cases:
            if lang not in p.languages:
                continue
            n += 1
            t = time.perf_counter()
            a = p.predict({"utterance": text}, {q: Q[q][lang]}, lang)[q]
            ms.append((time.perf_counter() - t) * 1000)
            ok = a.choice == gold
            hit += ok
            if not ok:
                print(f"  ✗ {q:5s} {text:45s} {a.choice}({a.probs[a.choice]:.2f}) want {gold}")
        print(f"== {q}: {hit}/{n}  p50={sorted(ms)[len(ms) // 2]:.0f}ms")
