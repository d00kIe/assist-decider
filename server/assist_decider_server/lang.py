"""Per-language data and text folding.

To add a language: add a `Lang` entry to LANGS, number words come from unicode-rbnf.
All word lists are written *folded* (see `fold`): lowercase, umlauts as ae/oe/ue, ß as ss.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import cached_property

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})
TOKEN_RE = re.compile(r"\d+(?:\.\d+)?|\w+|[,;?%°]")


def fold(text: str) -> str:
    """Normalize text for matching. Keeps length mapping simple by folding per character."""
    return fold_with_map(text)[0]


def fold_with_map(text: str) -> tuple[str, list[int]]:
    """Fold `text` and return, for every folded character, its index in the NFKC input."""
    text = unicodedata.normalize("NFKC", text)
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        folded = ch.casefold().translate(_UMLAUTS)
        out.append(folded)
        index.extend([i] * len(folded))
    folded_text = "".join(out)
    # German decimal comma: "21,5" -> "21.5" (same length, so the map stays valid).
    folded_text = re.sub(r"(?<=\d),(?=\d)", ".", folded_text)
    return folded_text, index


def tokenize(folded: str) -> list[tuple[str, int, int]]:
    return [(m.group(), m.start(), m.end()) for m in TOKEN_RE.finditer(folded)]


@dataclass(frozen=True, eq=False)
class Lang:
    intent_question: str
    target_question: str
    none_intent: str
    none_target: str
    entity_option: str  # format(name=, kind=, area=)
    entity_option_no_area: str
    area_option: str
    intents: dict[str, str]
    # domain -> (spoken label, words). Words >= 5 chars also match inside compounds
    # ("kuechenlicht" contains "licht"); shorter words must match a whole token.
    domains: dict[str, tuple[str, tuple[str, ...]]]
    verbs: frozenset[str]  # a clause containing one of these starts a new command
    conjunctions: tuple[tuple[str, ...], ...]
    on_words: frozenset[str]
    off_words: frozenset[str]
    question_words: frozenset[str]
    value_words: frozenset[str]  # "dim" etc.: needs a number, never guess one
    temperature_words: frozenset[str]
    stop_words: frozenset[str] = field(default_factory=frozenset)
    # on/off words that are also prepositions ("the lamp on the desk", "auf dem Flur").
    # They only count as on/off right after a verb or at the end of a clause.
    particles: frozenset[str] = field(default_factory=frozenset)
    # on/off words that also describe a state: "garage door open?" asks, it does not command.
    state_words: frozenset[str] = field(default_factory=frozenset)
    # After one of these, a word like "switch" or "lock" is a device, not a verb.
    articles: frozenset[str] = field(default_factory=frozenset)
    # Pronouns and filler that say nothing new: "turn it off", "and the hallway too".
    filler_words: frozenset[str] = field(default_factory=frozenset)
    # Reject-only lists: a sentence with one of these means something this pipeline cannot
    # express, so the whole sentence goes to the fallback agent instead of being run as if
    # the word were not there. reason -> words.
    blockers: dict[str, frozenset[str]] = field(default_factory=dict)

    @cached_property
    def noun_verbs(self) -> frozenset[str]:
        """Verbs that are also device words: "switch it off" / "the kitchen switch"."""
        nouns = {word for _label, words in self.domains.values() for word in words}
        return self.verbs & nouns

    @cached_property
    def joiners(self) -> frozenset[str]:
        return frozenset(t for c in self.conjunctions for t in c)


EN = Lang(
    intent_question="Which smart home action does the user ask for?",
    target_question="Which device or room does the user mean?",
    none_intent="something else, not a smart home command",
    none_target="none of these",
    entity_option="{name} ({kind}) in {area}",
    entity_option_no_area="{name} ({kind})",
    area_option="{area} (whole room)",
    intents={
        "HassTurnOn": "turn on, switch on, open or activate a device",
        "HassTurnOff": "turn off, switch off, close or deactivate a device",
        "HassLightSet": "change the brightness of a light, dim",
        "HassClimateSetTemperature": "set the target temperature of heating or thermostat",
        "HassSetPosition": "move a blind, shutter or cover to a position",
        "HassGetState": "ask about the current state of a device",
        "HassClimateGetTemperature": "ask how warm or cold it is",
    },
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
    verbs=frozenset(
        {
            "turn",
            "switch",
            "set",
            "dim",
            "brighten",
            "open",
            "close",
            "shut",
            "lock",
            "unlock",
            "activate",
            "deactivate",
            "start",
            "stop",
            "make",
            "put",
            "change",
            "raise",
            "lower",
            "increase",
            "decrease",
            "is",
            "are",
            "what",
            "whats",
            "how",
            "hows",
            "tell",
            "check",
        }
    ),
    conjunctions=(
        ("and", "then"),
        ("and", "also"),
        ("after", "that"),
        ("and",),
        ("then",),
        ("also",),
        ("plus",),
        (",",),
        (";",),
    ),
    on_words=frozenset({"on", "open", "activate", "start", "lock"}),
    off_words=frozenset({"off", "close", "shut", "deactivate", "stop", "unlock"}),
    question_words=frozenset({"is", "are", "what", "whats", "how", "hows", "which", "?"}),
    value_words=frozenset({"dim", "brighten", "brighter", "darker"}),
    temperature_words=frozenset({"warm", "cold", "hot", "temperature", "degrees"}),
    stop_words=frozenset(
        {
            "the",
            "a",
            "an",
            "in",
            "on",
            "off",
            "to",
            "of",
            "my",
            "please",
            "turn",
            "switch",
            "set",
            "all",
            "and",
            "at",
            "is",
            "are",
            "it",
        }
    ),
    particles=frozenset({"on", "off"}),
    state_words=frozenset({"open", "shut"}),
    articles=frozenset(
        {"the", "a", "an", "my", "our", "your", "this", "that", "these", "those", "all", "both"}
    ),
    filler_words=frozenset(
        {"it", "them", "that", "this", "those", "these", "too", "also", "as", "well", "again"}
    ),
    blockers={
        "conditional": frozenset({"if", "when", "whenever", "unless", "until", "till", "while"}),
        # "t" is what is left of "don't", "isn't", "can't" after tokenizing.
        "negation": frozenset({"not", "never", "no", "dont", "cannot", "without", "t"}),
        "exception": frozenset({"except", "excluding", "but", "or", "either", "instead"}),
        "scheduled": frozenset(
            {
                "tomorrow",
                "tonight",
                "later",
                "every",
                "daily",
                "morning",
                "afternoon",
                "evening",
                "noon",
                "midnight",
                "sunrise",
                "sunset",
                "oclock",
                "clock",
            }
        ),
    },
)

DE = Lang(
    intent_question="Welche Smart-Home-Aktion verlangt der Nutzer?",
    target_question="Welches Gerät oder welchen Raum meint der Nutzer?",
    none_intent="etwas anderes, kein Smart-Home-Befehl",
    none_target="keines davon",
    entity_option="{name} ({kind}) in {area}",
    entity_option_no_area="{name} ({kind})",
    area_option="{area} (ganzer Raum)",
    intents={
        "HassTurnOn": "einschalten, anschalten, öffnen oder aktivieren",
        "HassTurnOff": "ausschalten, abschalten, schließen oder deaktivieren",
        "HassLightSet": "Helligkeit einer Lampe ändern, dimmen",
        "HassClimateSetTemperature": "Zieltemperatur der Heizung oder des Thermostats einstellen",
        "HassSetPosition": "Rollladen, Jalousie oder Rollo auf eine Position fahren",
        "HassGetState": "nach dem aktuellen Zustand eines Geräts fragen",
        "HassClimateGetTemperature": "fragen, wie warm oder kalt es ist",
    },
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
    verbs=frozenset(
        {
            "schalte",
            "schalt",
            "schalten",
            "mach",
            "mache",
            "machen",
            "stell",
            "stelle",
            "stellen",
            "setze",
            "setz",
            "dimme",
            "dimm",
            "oeffne",
            "oeffnen",
            "schliesse",
            "schliess",
            "schliessen",
            "fahre",
            "fahr",
            "aktiviere",
            "deaktiviere",
            "starte",
            "stoppe",
            "dreh",
            "drehe",
            "regle",
            "regel",
            "ist",
            "sind",
            "wie",
            "was",
            "welche",
            "sperre",
            "entsperre",
        }
    ),
    conjunctions=(
        ("und", "dann"),
        ("und", "danach"),
        ("und",),
        ("dann",),
        ("danach",),
        ("sowie",),
        ("ausserdem",),
        ("anschliessend",),
        (",",),
        (";",),
    ),
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
    question_words=frozenset({"ist", "sind", "wie", "was", "welche", "welcher", "wieviel", "?"}),
    value_words=frozenset({"dimme", "dimm", "dimmen", "heller", "dunkler"}),
    temperature_words=frozenset({"warm", "kalt", "heiss", "temperatur", "grad"}),
    stop_words=frozenset(
        {
            "der",
            "die",
            "das",
            "den",
            "dem",
            "des",
            "im",
            "in",
            "am",
            "an",
            "aus",
            "auf",
            "ein",
            "eine",
            "einen",
            "bitte",
            "mach",
            "schalte",
            "stell",
            "alle",
            "und",
            "zu",
            "ist",
            "sind",
            "es",
            "mal",
        }
    ),
    particles=frozenset({"an", "ein", "auf", "aus", "ab", "zu"}),
    articles=frozenset(
        {
            "der",
            "die",
            "das",
            "den",
            "dem",
            "des",
            "ein",
            "eine",
            "einen",
            "einem",
            "einer",
            "mein",
            "meine",
            "meinen",
            "meinem",
            "meiner",
            "diese",
            "diesen",
            "diesem",
            "dieser",
            "dieses",
            "alle",
            "beide",
        }
    ),
    filler_words=frozenset(
        {"es", "sie", "ihn", "dies", "diese", "dieses", "auch", "noch", "ebenfalls", "wieder"}
    ),
    blockers={
        "conditional": frozenset(
            {"wenn", "falls", "sobald", "solange", "waehrend", "bevor", "nachdem"}
        ),
        "negation": frozenset(
            {
                "nicht",
                "nie",
                "niemals",
                "nein",
                "kein",
                "keine",
                "keinen",
                "keinem",
                "keiner",
                "keines",
                "ohne",
                "weder",
            }
        ),
        "exception": frozenset(
            {"ausser", "ausgenommen", "aber", "sondern", "oder", "entweder", "statt", "anstatt"}
        ),
        "scheduled": frozenset(
            {
                "morgen",
                "uebermorgen",
                "spaeter",
                "nachher",
                "heute",
                "morgens",
                "mittags",
                "abends",
                "nachts",
                "taeglich",
                "jeden",
                "uhr",
                "mitternacht",
                "sonnenaufgang",
                "sonnenuntergang",
            }
        ),
    },
)

LANGS: dict[str, Lang] = {"en": EN, "de": DE}


def domain_hits(tokens: list[str], lang: Lang) -> list[tuple[int, str]]:
    """(token index, domain) for every device word in `tokens`: a whole token, or inside a
    compound for long words ("kuechenlicht" contains "licht")."""
    hits: list[tuple[int, str]] = []
    for domain, (_label, words) in lang.domains.items():
        for word in words:
            parts = word.split()
            for i, token in enumerate(tokens):
                if len(parts) > 1:
                    if tokens[i : i + len(parts)] == parts:
                        hits += [(i + n, domain) for n in range(len(parts))]
                elif token == word or (len(word) >= 5 and word in token):
                    hits.append((i, domain))
    return hits


def domain_words_in(tokens: list[str], lang: Lang) -> set[str]:
    """Domains whose words occur in `tokens`."""
    return {domain for _i, domain in domain_hits(tokens, lang)}
