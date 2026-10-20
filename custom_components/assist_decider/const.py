"""Constants for Assist Decider."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "assist_decider"

CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_FALLBACK_AGENT: Final = "fallback_agent"
CONF_THRESHOLD_EN: Final = "threshold_en"
CONF_THRESHOLD_DE: Final = "threshold_de"

CONF_MEMORY_SECONDS: Final = "memory_seconds"
CONF_MODEL: Final = "model"  # not stored: the server's current model

DEFAULT_THRESHOLDS: Final = {"en": 0.4, "de": 0.5}
DEFAULT_MEMORY_SECONDS: Final = 60  # follow-ups ("turn it off")
THRESHOLD_OPTIONS: Final = {"en": CONF_THRESHOLD_EN, "de": CONF_THRESHOLD_DE}

REQUEST_TIMEOUT: Final = 10
MODEL_TIMEOUT: Final = 1800  # a model switch may download several GB first
MAX_RESPONSE_BYTES: Final = 256 * 1024

# Intents the server may ask for, with the slots each may carry. Anything else in a server
# response is dropped: Home Assistant only executes what this integration allows.
TARGET_SLOTS: Final = frozenset({"name", "area", "domain"})
SUPPORTED_INTENTS: Final[dict[str, frozenset[str]]] = {
    "HassTurnOn": TARGET_SLOTS,
    "HassTurnOff": TARGET_SLOTS,
    "HassLightSet": TARGET_SLOTS | {"brightness", "brightness_step"},
    "HassClimateSetTemperature": frozenset({"name", "area", "temperature", "temperature_step"}),
    "HassSetPosition": TARGET_SLOTS | {"position"},
    "HassGetState": TARGET_SLOTS | {"state"},
    "HassClimateGetTemperature": frozenset({"name", "area"}),
}
QUERY_INTENTS: Final = frozenset({"HassGetState", "HassClimateGetTemperature"})
# A change from the current value ("brighter", "warmer"): step slot -> (absolute slot, domain,
# largest step). Home Assistant's intents take only absolute values, so the integration reads
# each entity's state and sends the new value.
STEP_SLOTS: Final = {
    "brightness_step": ("brightness", "light", 100),
    "temperature_step": ("temperature", "climate", 10),
}

# Spoken when the intents package has nothing better.
MESSAGES: Final = {
    "en": {
        "unavailable": "Sorry, the decision server is not reachable.",
        "rest_not_understood": "I didn't understand the rest.",
        "done": "Done.",
    },
    "de": {
        "unavailable": "Entschuldigung, der Entscheidungsserver ist nicht erreichbar.",
        "rest_not_understood": "Den Rest habe ich nicht verstanden.",
        "done": "Erledigt.",
    },
}
