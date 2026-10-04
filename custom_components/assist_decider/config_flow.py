"""Config flow: server URL and token; options: thresholds and fallback agent."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    ConversationAgentSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import DeciderConfigEntry
from .client import AuthError, DeciderClient, ProtocolMismatch, ServerUnavailable
from .const import (
    CONF_FALLBACK_AGENT,
    CONF_MEMORY_SECONDS,
    CONF_VERIFY_SSL,
    DEFAULT_CONDITIONS,
    DEFAULT_THRESHOLDS,
    DOMAIN,
    THRESHOLD_OPTIONS,
)
from .protocol import ServerInfo

TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _normalize_url(url: str) -> str:
    return url.strip().rstrip("/")


class AssistDeciderConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(
        self, url: str, token: str, verify_ssl: bool
    ) -> tuple[ServerInfo | None, dict[str, str]]:
        if not url.startswith(("http://", "https://")):
            return None, {CONF_URL: "invalid_url"}
        client = DeciderClient(
            async_get_clientsession(self.hass, verify_ssl=verify_ssl), url, token
        )
        try:
            return await client.info(), {}
        except AuthError:
            return None, {"base": "invalid_auth"}
        except ProtocolMismatch:
            return None, {"base": "protocol_mismatch"}
        except ServerUnavailable:
            return None, {"base": "cannot_connect"}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            self._async_abort_entries_match({CONF_URL: url})
            info, errors = await self._validate(
                url, user_input[CONF_TOKEN], user_input[CONF_VERIFY_SSL]
            )
            if info:
                return self.async_create_entry(
                    title=f"Assist Decider ({info.model})",
                    data={
                        CONF_URL: url,
                        CONF_TOKEN: user_input[CONF_TOKEN],
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    },
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default="http://"): str,
                vol.Required(CONF_TOKEN): TOKEN_SELECTOR,
                vol.Required(CONF_VERIFY_SSL, default=True): bool,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            info, errors = await self._validate(
                entry.data[CONF_URL], user_input[CONF_TOKEN], entry.data.get(CONF_VERIFY_SSL, True)
            )
            if info:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): TOKEN_SELECTOR}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            if url != entry.data[CONF_URL]:
                self._async_abort_entries_match({CONF_URL: url})
            token = user_input.get(CONF_TOKEN) or entry.data[CONF_TOKEN]
            info, errors = await self._validate(url, token, user_input[CONF_VERIFY_SSL])
            if info:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_URL: url,
                        CONF_TOKEN: token,
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    },
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default=entry.data[CONF_URL]): str,
                vol.Optional(CONF_TOKEN): TOKEN_SELECTOR,
                vol.Required(CONF_VERIFY_SSL, default=entry.data.get(CONF_VERIFY_SSL, True)): bool,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: DeciderConfigEntry) -> AssistDeciderOptionsFlow:
        return AssistDeciderOptionsFlow()


class AssistDeciderOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        own_entity = er.async_get(self.hass).async_get_entity_id(
            "conversation", DOMAIN, self.config_entry.entry_id
        )
        if user_input is not None:
            if (
                user_input.get(CONF_FALLBACK_AGENT)
                and user_input[CONF_FALLBACK_AGENT] == own_entity
            ):
                errors[CONF_FALLBACK_AGENT] = "fallback_is_self"
            else:
                return self.async_create_entry(data=user_input)
        slider = NumberSelector(
            NumberSelectorConfig(min=0, max=1, step=0.05, mode=NumberSelectorMode.SLIDER)
        )
        options = user_input or self.config_entry.options
        schema = vol.Schema(
            {
                **{
                    vol.Required(key, default=options.get(key, DEFAULT_THRESHOLDS[lang])): slider
                    for lang, key in THRESHOLD_OPTIONS.items()
                },
                **{
                    vol.Required(key, default=options.get(key, default)): NumberSelector(
                        NumberSelectorConfig(
                            min=0 if key == CONF_MEMORY_SECONDS else -50,
                            max=3600 if key == CONF_MEMORY_SECONDS else 150,
                            step=1 if key == CONF_MEMORY_SECONDS else 0.5,
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                    for key, default in DEFAULT_CONDITIONS.items()
                },
                vol.Optional(CONF_FALLBACK_AGENT): ConversationAgentSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_FALLBACK_AGENT: options.get(CONF_FALLBACK_AGENT)}
            ),
            errors=errors,
        )
