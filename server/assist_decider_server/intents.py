"""What each kind of device can do, and the Home Assistant intent that does it.

Slot names follow Home Assistant core (homeassistant/helpers/intent.py and platform intent.py).
Descriptions shown to the model live in lang.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NumberSlot:
    slot: str
    units: frozenset[str]  # accepted units; "" is a bare number
    lo: float
    hi: float
    integer: bool = True

    def accepts(self, value: float, unit: str) -> bool:
        return unit in self.units and self.lo <= value <= self.hi

    def clean(self, value: float) -> float | int:
        return round(value) if self.integer else round(value * 2) / 2


@dataclass(frozen=True)
class ActionSpec:
    intent: str  # the Home Assistant intent that performs it
    polarity: str | None = None  # "on" / "off": ruled out when only the opposite word is said
    number: NumberSlot | None = None


PCT = frozenset({"pct", ""})

ACTIONS: dict[str, ActionSpec] = {
    "turn_on": ActionSpec("HassTurnOn", "on"),
    "turn_off": ActionSpec("HassTurnOff", "off"),
    "open": ActionSpec("HassTurnOn", "on"),
    "close": ActionSpec("HassTurnOff", "off"),
    "lock": ActionSpec("HassTurnOn", "on"),  # HassTurnOn locks a lock
    "unlock": ActionSpec("HassTurnOff", "off"),
    "set_brightness": ActionSpec("HassLightSet", number=NumberSlot("brightness", PCT, 0, 100)),
    # ponytail: Celsius only; 70 °F does not fit. Widen the range when Fahrenheit homes matter.
    "set_temperature": ActionSpec(
        "HassClimateSetTemperature",
        number=NumberSlot("temperature", frozenset({"deg", ""}), 5, 35, integer=False),
    ),
    "set_position": ActionSpec("HassSetPosition", number=NumberSlot("position", PCT, 0, 100)),
    "query": ActionSpec("HassGetState"),
}

ON_OFF = ("turn_on", "turn_off", "query")
OPEN_CLOSE = ("open", "close", "set_position", "query")
QUERY_ONLY = ("query",)

# Device kind (Home Assistant domain) -> the actions offered to the model, in this order.
DOMAIN_ACTIONS: dict[str, tuple[str, ...]] = {
    "light": ("turn_on", "turn_off", "set_brightness", "query"),
    "climate": ("turn_on", "turn_off", "set_temperature", "query"),
    "cover": OPEN_CLOSE,
    "valve": OPEN_CLOSE,
    "lock": ("lock", "unlock", "query"),
    "switch": ON_OFF,
    "fan": ON_OFF,
    "input_boolean": ON_OFF,
    "media_player": ON_OFF,
    "humidifier": ON_OFF,
    "automation": ON_OFF,
    "scene": ("turn_on",),
    "script": ("turn_on",),
    "sensor": QUERY_ONLY,
    "binary_sensor": QUERY_ONLY,
    "weather": QUERY_ONLY,
}


def intent_for(action: str, domain: str) -> str:
    # ponytail: a question about a thermostat is answered with its temperature, even "is the
    # heating on?". Ask the model which one when that matters.
    if action == "query" and domain == "climate":
        return "HassClimateGetTemperature"
    return ACTIONS[action].intent


# Never picked from a room, only by an exact spoken name: a misheard command must not
# unlock a door or open the garage.
SENSITIVE_DOMAINS = frozenset({"lock", "alarm_control_panel"})
SENSITIVE_COVER_CLASSES = frozenset({"garage", "gate", "door"})


def is_sensitive(domain: str, device_class: str | None) -> bool:
    return domain in SENSITIVE_DOMAINS or (
        domain == "cover" and device_class in SENSITIVE_COVER_CLASSES
    )
