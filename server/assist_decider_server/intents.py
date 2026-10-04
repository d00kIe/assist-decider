"""The Home Assistant intents Assist Decider can produce, and the slots they need.

Slot names follow Home Assistant core (homeassistant/helpers/intent.py and platform intent.py).
Descriptions shown to the model live in lang.py.
"""

from __future__ import annotations

from dataclasses import dataclass

ON_OFF_DOMAINS = frozenset(
    {
        "light",
        "switch",
        "fan",
        "input_boolean",
        "media_player",
        "cover",
        "valve",
        "lock",
        "humidifier",
        "climate",
        "automation",
    }
)


@dataclass(frozen=True)
class NumberSlot:
    slot: str
    units: frozenset[str]  # accepted units; "" is a bare number
    lo: float
    hi: float
    integer: bool = True


@dataclass(frozen=True)
class IntentSpec:
    name: str
    key: str  # option key shown to the model; plain words score better than "HassTurnOn"
    domains: frozenset[str] | None  # None: any domain
    number: NumberSlot | None = None
    implied_domain: str | None = None  # area commands may omit the domain word
    query: bool = False
    entity_only: bool = False  # area targets not supported (yet)
    target_optional: bool = False  # HA picks the device (satellite area, single entity)
    polarity: str | None = None  # "on" / "off": dropped when the opposite word is spoken
    domain_slot: bool = True  # HA's handler accepts a "domain" slot


PCT = frozenset({"pct", ""})
CLIMATE = frozenset({"climate"})

INTENTS: dict[str, IntentSpec] = {
    spec.name: spec
    for spec in (
        IntentSpec("HassTurnOn", "turn_on", ON_OFF_DOMAINS | {"scene", "script"}, polarity="on"),
        IntentSpec("HassTurnOff", "turn_off", ON_OFF_DOMAINS, polarity="off"),
        IntentSpec(
            "HassLightSet", "set_brightness", frozenset({"light"}),
            number=NumberSlot("brightness", PCT, 0, 100), implied_domain="light",
        ),
        IntentSpec(
            "HassClimateSetTemperature", "set_temperature", CLIMATE,
            number=NumberSlot("temperature", frozenset({"deg", ""}), 0, 100, integer=False),
            implied_domain="climate", target_optional=True, domain_slot=False,
        ),
        IntentSpec(
            "HassSetPosition", "set_position", frozenset({"cover", "valve"}),
            number=NumberSlot("position", PCT, 0, 100), implied_domain="cover",
        ),
        IntentSpec("HassGetState", "get_state", None, query=True, entity_only=True),
        IntentSpec(
            "HassClimateGetTemperature", "get_temperature", CLIMATE, query=True,
            implied_domain="climate", target_optional=True, domain_slot=False,
        ),
    )
}  # fmt: skip
INTENT_KEYS = {spec.key: spec for spec in INTENTS.values()}

# Never chosen by the model, only by an exact spoken name: a misheard command must not
# unlock a door or open the garage.
SENSITIVE_DOMAINS = frozenset({"lock", "alarm_control_panel"})
SENSITIVE_COVER_CLASSES = frozenset({"garage", "gate", "door"})


def is_sensitive(domain: str, device_class: str | None) -> bool:
    return domain in SENSITIVE_DOMAINS or (
        domain == "cover" and device_class in SENSITIVE_COVER_CLASSES
    )
