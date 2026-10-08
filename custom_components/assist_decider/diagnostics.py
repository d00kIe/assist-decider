"""Diagnostics."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import DeciderConfigEntry
from .conversation import build_home


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DeciderConfigEntry
) -> dict[str, Any]:
    home = build_home(hass)
    return {
        "data": dict(entry.data),
        "options": dict(entry.options),
        "server": entry.runtime_data.info.model_dump(),
        "exposed": {
            "entities": len(home.entities),
            "areas": len(home.areas),
            "floors": len(home.floors),
        },
    }
