"""Config flow: server URL; options: thresholds and fallback agent."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    ConversationAgentSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
)

from . import DeciderConfigEntry
from .client import DeciderClient, DeciderError, ProtocolMismatch, ServerUnavailable
from .const import (
    CONF_FALLBACK_AGENT,
    CONF_MEMORY_SECONDS,
    CONF_MODEL,
    CONF_VERIFY_SSL,
    DEFAULT_MEMORY_SECONDS,
    DEFAULT_THRESHOLDS,
    DOMAIN,
    THRESHOLD_OPTIONS,
)
from .protocol import ServerInfo


def _normalize_url(url: str) -> str:
    return url.strip().rstrip("/")


class AssistDeciderConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(
        self, url: str, verify_ssl: bool
    ) -> tuple[ServerInfo | None, dict[str, str]]:
        if not url.startswith(("http://", "https://")):
            return None, {CONF_URL: "invalid_url"}
        client = DeciderClient(async_get_clientsession(self.hass, verify_ssl=verify_ssl), url)
        try:
            return await client.info(), {}
        except ProtocolMismatch:
            return None, {"base": "protocol_mismatch"}
        except ServerUnavailable:
            return None, {"base": "cannot_connect"}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            self._async_abort_entries_match({CONF_URL: url})
            info, errors = await self._validate(url, user_input[CONF_VERIFY_SSL])
            if info:
                return self.async_create_entry(
                    title=f"Assist Decider ({info.model})",
                    data={
                        CONF_URL: url,
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    },
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default="http://"): str,
                vol.Required(CONF_VERIFY_SSL, default=True): bool,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
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
            info, errors = await self._validate(url, user_input[CONF_VERIFY_SSL])
            if info:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_URL: url,
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    },
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default=entry.data[CONF_URL]): str,
                vol.Required(CONF_VERIFY_SSL, default=entry.data.get(CONF_VERIFY_SSL, True)): bool,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: DeciderConfigEntry) -> AssistDeciderOptionsFlow:
        return AssistDeciderOptionsFlow()


class AssistDeciderOptionsFlow(OptionsFlowWithReload):
    _options: dict[str, Any] | None = None  # the other options, saved once the model switched
    _switch: asyncio.Task[ServerInfo]
    _switch_error: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        # Model choice needs the loaded entry, and the server's current state: the model may
        # have changed since setup (server settings, another Home Assistant).
        data = getattr(self.config_entry, "runtime_data", None)
        info: ServerInfo | None = None
        if data:
            try:
                info = await data.client.info()
            except DeciderError:
                info = data.info
            if info.model != data.info.model:
                self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        if self._switch_error and user_input is None:
            errors["base"] = "model_failed"
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
                model = user_input.pop(CONF_MODEL, None)
                if data and info and model and model != info.model:
                    self._options = user_input
                    # Not eager: finishing within this submit would re-run init with its input.
                    self._switch = self.hass.async_create_task(
                        data.client.set_model(model), eager_start=False
                    )
                    return await self.async_step_switch_model()
                return self.async_create_entry(data=user_input)
        slider = NumberSelector(
            NumberSelectorConfig(min=0, max=1, step=0.05, mode=NumberSelectorMode.SLIDER)
        )
        options = user_input or self._options or self.config_entry.options
        schema = vol.Schema(
            {
                **{
                    vol.Required(key, default=options.get(key, DEFAULT_THRESHOLDS[lang])): slider
                    for lang, key in THRESHOLD_OPTIONS.items()
                },
                vol.Required(
                    CONF_MEMORY_SECONDS,
                    default=options.get(CONF_MEMORY_SECONDS, DEFAULT_MEMORY_SECONDS),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=3600, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_FALLBACK_AGENT): ConversationAgentSelector(),
            }
        )
        if info and info.models:
            schema = schema.extend(
                {
                    vol.Required(CONF_MODEL, default=info.model): SelectSelector(
                        SelectSelectorConfig(options=info.models)
                    )
                }
            )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_FALLBACK_AGENT: options.get(CONF_FALLBACK_AGENT)}
            ),
            errors=errors,
            description_placeholders={"error": self._switch_error or ""},
        )

    async def async_step_switch_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._switch.done():
            return self.async_show_progress(
                step_id="switch_model", progress_action="switch_model", progress_task=self._switch
            )
        try:
            self._switch.result()
        except DeciderError as err:
            self._switch_error = str(err)
            return self.async_show_progress_done(next_step_id="init")
        # Unchanged options do not reload the entry, but title, languages and device info
        # follow the model: reload anyway.
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return self.async_show_progress_done(next_step_id="done")

    async def async_step_done(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._options is not None
        return self.async_create_entry(data=self._options)
