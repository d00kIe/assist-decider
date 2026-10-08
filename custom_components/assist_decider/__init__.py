"""Assist Decider: a remote decision server as a Home Assistant conversation agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .client import DeciderClient, ProtocolMismatch, ServerUnavailable
from .const import CONF_VERIFY_SSL, DOMAIN
from .protocol import PROTOCOL_VERSION, ServerInfo

PLATFORMS = [Platform.CONVERSATION]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class DeciderData:
    client: DeciderClient
    info: ServerInfo
    intents_json: dict[str, Any] = field(default_factory=dict)  # language -> intents package data


type DeciderConfigEntry = ConfigEntry[DeciderData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DeciderConfigEntry) -> bool:
    client = DeciderClient(
        async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)),
        entry.data[CONF_URL],
    )
    issue_id = f"protocol_mismatch_{entry.entry_id}"
    try:
        info = await client.info()
    except ProtocolMismatch as err:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="protocol_mismatch",
            translation_placeholders={
                "expected": str(PROTOCOL_VERSION),
                "url": entry.data[CONF_URL],
            },
        )
        raise ConfigEntryError(str(err)) from err
    except ServerUnavailable as err:
        raise ConfigEntryNotReady(f"Decision server not reachable: {err}") from err
    ir.async_delete_issue(hass, DOMAIN, issue_id)

    entry.runtime_data = DeciderData(client, info)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DeciderConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
