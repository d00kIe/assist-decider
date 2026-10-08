"""Per-language data and text folding.

To add a language: add a `Lang` entry to LANGS, number words come from unicode-rbnf.
All word lists are written *folded* (see `fold`): lowercase, umlauts as ae/oe/ue, ß as ss.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})
TOKEN_RE = re.compile(r"\d+(?:\.\d+)?|\w+|[,;?%°]")


def fold(text: str) -> str:
    """Normalize text for matching: lowercase, umlauts as ae/oe/ue, ß as ss, "21,5" as "21.5"."""
    text = unicodedata.normalize("NFKC", text).casefold().translate(_UMLAUTS)
    return re.sub(r"(?<=\d),(?=\d)", ".", text)  # German decimal comma


def tokenize(folded: str) -> list[str]:
    return TOKEN_RE.findall(folded)


@dataclass(frozen=True)
class Lang:
    # The questions the model answers, and the wording of their options.
    kind_question: str
    kinds: dict[str, str]  # "command" / "question" -> description
    which_question: str
    action_question: str  # format(device=)
    actions: dict[str, str]  # action (see intents.ACTIONS) -> description
    value_question: str  # format(device=)
    no_value: str
    entity_option: str  # format(name=, kind=, area=)
    entity_option_no_area: str
    # domain -> (spoken label, words). Words >= 5 chars also match inside compounds
    # ("kuechenlicht" contains "licht"); shorter words must match a whole token.
    domains: dict[str, tuple[str, tuple[str, ...]]]
    on_words: frozenset[str]
    off_words: frozenset[str]
    # Used instead of on/off words for a lock: "close/zu/ab" lock it, "open/auf" unlock it.
    lock_words: frozenset[str]
    unlock_words: frozenset[str]
    condition_words: frozenset[str]  # "if": unsupported


EN = Lang(
    kind_question="Is the user giving a command or asking a question?",
    kinds={"command": "a command to do something", "question": "a question about how something is"},
    which_question="Which device does the user mean?",
    action_question="What does the user want with the {device}?",
    actions={
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
    value_question="Which value should the {device} be set to?",
    no_value="no value given for this device",
    entity_option="{name} ({kind}) in {area}",
    entity_option_no_area="{name} ({kind})",
    domains={
        "light": ("light", ("light", "lights", "lamp", "lamps", "lighting")),
        "switch": ("switch", ("switch", "plug", "socket", "outlet")),
        "fan": ("fan", ("fan", "fans")),
        "cover": (
            "blind",
            (
                "blind",
                "blinds",
                "shutter",
                "shutters",
                "shade",
                "shades",
                "curtain",
                "curtains",
                "cover",
                "covers",
                "awning",
                "garage",
                "gate",
            ),
        ),
        "climate": (
            "thermostat",
            ("thermostat", "heating", "heater", "heat", "climate", "ac", "air conditioning"),
        ),
        "media_player": (
            "media player",
            ("tv", "television", "speaker", "speakers", "music", "radio", "player"),
        ),
        "lock": ("lock", ("lock", "locks")),
        "valve": ("valve", ("valve", "valves", "sprinkler", "sprinklers")),
        "humidifier": ("humidifier", ("humidifier",)),
        "scene": ("scene", ("scene",)),
        "script": ("script", ("script",)),
        "automation": ("automation", ("automation",)),
        "input_boolean": ("switch", ()),
        "sensor": ("sensor", ("sensor",)),
        "weather": ("weather", ("weather",)),
        "binary_sensor": ("sensor", ()),
    },
    on_words=frozenset({"on", "open", "activate", "start", "lock"}),
    off_words=frozenset({"off", "close", "shut", "deactivate", "stop", "unlock"}),
    condition_words=frozenset({"if", "when", "whenever"}),
    lock_words=frozenset({"lock", "close", "shut"}),
    unlock_words=frozenset({"unlock", "open"}),
)

DE = Lang(
    kind_question="Gibt der Nutzer einen Befehl oder stellt er eine Frage?",
    kinds={"command": "ein Befehl, etwas zu tun", "question": "eine Frage, wie etwas ist"},
    which_question="Welches Gerät meint der Nutzer?",
    action_question="Was will der Nutzer mit {device}?",
    actions={
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
    value_question="Auf welchen Wert soll {device} gestellt werden?",
    no_value="kein Wert für dieses Gerät",
    entity_option="{name} ({kind}) in {area}",
    entity_option_no_area="{name} ({kind})",
    domains={
        "light": (
            "Licht",
            ("licht", "lichter", "lampe", "lampen", "leuchte", "leuchten", "beleuchtung"),
        ),
        "switch": ("Schalter", ("schalter", "steckdose", "stecker")),
        "fan": ("Ventilator", ("ventilator", "luefter")),
        "cover": (
            "Rollladen",
            (
                "rollladen",
                "rolladen",
                "rolllaeden",
                "rollaeden",
                "rollo",
                "rollos",
                "jalousie",
                "jalousien",
                "markise",
                "vorhang",
                "vorhaenge",
                "garagentor",
                "tor",
            ),
        ),
        "climate": ("Heizung", ("heizung", "thermostat", "klima", "klimaanlage")),
        "media_player": ("Mediaplayer", ("fernseher", "tv", "lautsprecher", "musik", "radio")),
        "lock": ("Schloss", ("schloss", "tuerschloss")),
        "valve": ("Ventil", ("ventil", "bewaesserung")),
        "humidifier": ("Luftbefeuchter", ("luftbefeuchter",)),
        "scene": ("Szene", ("szene",)),
        "script": ("Skript", ("skript",)),
        "automation": ("Automatisierung", ("automatisierung",)),
        "input_boolean": ("Schalter", ()),
        "sensor": ("Sensor", ("sensor",)),
        "weather": ("Wetter", ("wetter",)),
        "binary_sensor": ("Sensor", ()),
    },
    on_words=frozenset(
        {
            "an",
            "ein",
            "einschalten",
            "anschalten",
            "anmachen",
            "oeffne",
            "oeffnen",
            "aktiviere",
            "auf",
            "starte",
            "sperre",
            "zusperren",
        }
    ),
    off_words=frozenset(
        {
            "aus",
            "ab",
            "ausschalten",
            "abschalten",
            "ausmachen",
            "schliesse",
            "schliess",
            "schliessen",
            "zu",
            "deaktiviere",
            "stoppe",
            "entsperre",
        }
    ),
    condition_words=frozenset({"wenn", "falls", "sobald"}),
    # "sperr"/"schliess" are neutral: the particle decides ("sperr ab" vs "sperr auf").
    lock_words=frozenset(
        {"ab", "zu", "zusperren", "absperren", "abschliessen", "verriegle", "verriegeln"}
    ),
    unlock_words=frozenset(
        {
            "auf",
            "entsperre",
            "entsperr",
            "entsperren",
            "aufsperren",
            "aufschliessen",
            "entriegle",
            "entriegeln",
            "oeffne",
            "oeffnen",
        }
    ),
)

LANGS: dict[str, Lang] = {"en": EN, "de": DE}


def domain_words_in(tokens: list[str], lang: Lang) -> set[str]:
    """Domains whose words occur in `tokens` (whole token, or inside a compound for long words)."""
    found: set[str] = set()
    joined = " ".join(tokens)
    for domain, (_label, words) in lang.domains.items():
        for word in words:
            if " " in word:
                if f" {word} " in f" {joined} ":
                    found.add(domain)
                    break
            elif word in tokens or (len(word) >= 5 and any(word in t for t in tokens)):
                found.add(domain)
                break
    return found
