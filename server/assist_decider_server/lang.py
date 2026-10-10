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
    # The questions the model answers, and the wording of their options. The model decides;
    # there are no word lists. Wording was chosen by probing Laya and Intern-Decision on the
    # benchmark's dev sentences (BENCHMARK.md): both are sensitive to it, so change it only with
    # the benchmark. Laya reads at most 48 tokens per option.
    kind_question: str
    kinds: dict[str, str]  # "command" / "question" -> description
    topic_question: str
    topics: dict[str, str]  # "home" (format(kinds=): the home's kinds of device) / "other"
    # A room, a floor or the speaker's surroundings: one device or all of a kind, which kind,
    # which device. Nothing named: here, this floor or the whole home; or the previous devices.
    scope_question: str
    scopes: dict[str, str]  # "one" / "all"
    device_kind_question: str  # its options: the kinds present, worded by `domains`
    which_question: str
    place_question: str
    places: dict[str, str]  # "here" / "floor" / "home"
    reference_question: str  # format(previous=): the previous command
    references: dict[str, str]  # "previous" / "other"
    action_question: str  # format(device=)
    actions: dict[str, str]  # action (see intents.ACTIONS) -> description
    value_question: str  # format(device=)
    no_value: str
    entity_option: str  # format(name=, kind=, area=)
    entity_option_no_area: str
    # domain -> (label in the "which device?" options, description in "which kind?")
    domains: dict[str, tuple[str, str]]


EN = Lang(
    kind_question="Is the user giving a command or asking a question?",
    kinds={"command": "a command to do something", "question": "a question about how something is"},
    topic_question="What is the user talking about?",
    topics={
        "home": "{kinds} or other devices at home",
        "other": "something else: chit-chat, knowledge, weather, timers, reminders, shopping lists",
    },
    scope_question="Does the user mean one device or all of one kind?",
    scopes={"one": "one device", "all": "all devices of one kind ('all the lights', 'the blinds')"},
    device_kind_question="Which kind of device does the user mean?",
    which_question="Which device does the user mean?",
    place_question="Where does the user want it?",
    places={
        "here": "here, in this room",
        "floor": "on this whole floor",
        "home": "in the whole home",
    },
    reference_question="The user's previous command was: '{previous}'. "
    "Does the user mean the same devices again?",
    references={
        "previous": "yes, the same devices ('it', 'them', 'again', 'too')",
        "other": "no, other devices",
    },
    action_question="What does the user want with the {device}?",
    actions={
        "turn_on": "turn on, switch on, start",
        "turn_off": "turn off, switch off, stop",
        "set_brightness": "dim or brighten to a value",
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
        "light": ("light", "lights, lamps"),
        "switch": ("switch", "switches, plugs, sockets"),
        "fan": ("fan", "fans"),
        "cover": ("blind", "blinds, shutters, shades, curtains, awnings"),
        "climate": ("thermostat", "heating, thermostat, air conditioning"),
        "media_player": ("media player", "TV, speakers, music, radio"),
        "lock": ("lock", "locks"),
        "valve": ("valve", "valves, sprinklers"),
        "humidifier": ("humidifier", "humidifiers"),
        "scene": ("scene", "scenes"),
        "script": ("script", "scripts"),
        "automation": ("automation", "automations"),
        "input_boolean": ("switch", "switches"),
        "sensor": ("sensor", "sensors"),
        "weather": ("weather", "the weather"),
        "binary_sensor": ("sensor", "sensors"),
    },
)

DE = Lang(
    kind_question="Gibt der Nutzer einen Befehl oder stellt er eine Frage?",
    kinds={"command": "ein Befehl, etwas zu tun", "question": "eine Frage, wie etwas ist"},
    topic_question="Worüber spricht der Nutzer?",
    topics={
        "home": "{kinds} oder andere Geräte im Haus",
        "other": "etwas anderes: Plaudern, Wissen, Wetter, Timer, Erinnerungen, Einkaufslisten",
    },
    scope_question="Meint der Nutzer ein Gerät oder alle einer Art?",
    scopes={"one": "ein Gerät", "all": "alle Geräte einer Art („alle Lichter“, „die Rollläden“)"},
    device_kind_question="Welche Art von Gerät meint der Nutzer?",
    which_question="Welches Gerät meint der Nutzer?",
    place_question="Wo will der Nutzer es?",
    places={
        "here": "hier, in diesem Raum",
        "floor": "auf dieser ganzen Etage",
        "home": "im ganzen Haus",
    },
    reference_question="Der vorige Befehl des Nutzers war: „{previous}“. "
    "Meint der Nutzer wieder dieselben Geräte?",
    references={
        "previous": "ja, dieselben Geräte („es“, „sie“, „wieder“, „auch“)",
        "other": "nein, andere Geräte",
    },
    action_question="Was will der Nutzer mit {device}?",
    actions={
        "turn_on": "einschalten, anmachen, starten (an, ein)",
        "turn_off": "ausschalten, ausmachen, stoppen (aus, ab)",
        "set_brightness": "dimmen oder heller machen auf einen Wert",
        "set_temperature": "Temperatur auf einen Wert stellen",
        "open": "öffnen, hochfahren (auf, hoch)",
        "close": "schließen, runterfahren (zu, runter)",
        "set_position": "auf eine Position oder Prozent fahren",
        "lock": "abschließen, zusperren („sperr … ab“)",
        "unlock": "aufschließen, entsperren („sperr … auf“)",
        "query": "fragt nur, wie es ist, ändert nichts",
    },
    value_question="Auf welchen Wert soll {device} gestellt werden?",
    no_value="kein Wert für dieses Gerät",
    entity_option="{name} ({kind}) in {area}",
    entity_option_no_area="{name} ({kind})",
    domains={
        "light": ("Licht", "Licht, Lampen, Leuchten"),
        "switch": ("Schalter", "Schalter, Steckdosen"),
        "fan": ("Ventilator", "Ventilatoren, Lüfter"),
        "cover": ("Rollladen", "Rollläden, Rollos, Jalousien, Vorhänge, Markisen"),
        "climate": ("Heizung", "Heizung, Thermostat, Klimaanlage"),
        "media_player": ("Mediaplayer", "Fernseher, Lautsprecher, Musik, Radio"),
        "lock": ("Schloss", "Schlösser"),
        "valve": ("Ventil", "Ventile, Bewässerung"),
        "humidifier": ("Luftbefeuchter", "Luftbefeuchter"),
        "scene": ("Szene", "Szenen"),
        "script": ("Skript", "Skripte"),
        "automation": ("Automatisierung", "Automatisierungen"),
        "input_boolean": ("Schalter", "Schalter"),
        "sensor": ("Sensor", "Sensoren"),
        "weather": ("Wetter", "das Wetter"),
        "binary_sensor": ("Sensor", "Sensoren"),
    },
)

LANGS: dict[str, Lang] = {"en": EN, "de": DE}
