"""Conversation agent: send the utterance and the exposed home to the server, run the answer.

The server only *proposes* intent calls. This module validates them against what was
exposed, executes them in order through Home Assistant's intent handlers (which enforce
exposure again), and renders the spoken reply from Home Assistant's own response templates.

Speech rendering follows homeassistant/components/conversation/default_agent.py
(Apache-2.0, Copyright Home Assistant contributors).
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from home_assistant_intents import get_intents
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import intent, template
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pydantic import ValidationError

from . import DeciderConfigEntry
from .client import AuthError, ServerUnavailable
from .const import (
    CONF_FALLBACK_AGENT,
    DEFAULT_THRESHOLDS,
    DOMAIN,
    MESSAGES,
    QUERY_INTENTS,
    SUPPORTED_INTENTS,
    THRESHOLD_OPTIONS,
)
from .protocol import Action, Area, Entity, Floor, Home, Options, ProcessRequest

_LOGGER = logging.getLogger(__name__)
NAME_MAX = 100


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeciderConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([AssistDeciderAgent(entry)])


def _clip(names: list[str]) -> list[str]:
    return list(dict.fromkeys(n.strip()[:NAME_MAX] for n in names if n and n.strip()))[:10]


def build_home(hass: HomeAssistant) -> Home:
    """Everything exposed to Assist, as names and IDs only (no states)."""
    ent_reg = er.async_get(hass)
    areas, floors = [], []
    for floor in fr.async_get(hass).async_list_floors():
        try:
            floors.append(
                Floor(
                    id=floor.floor_id,
                    name=floor.name[:NAME_MAX],
                    aliases=_clip(list(floor.aliases)),
                )
            )
        except ValidationError:
            _LOGGER.debug("Skipping floor %s: not representable", floor.floor_id)
    for area in ar.async_get(hass).async_list_areas():
        try:
            areas.append(
                Area(
                    id=area.id,
                    name=area.name[:NAME_MAX],
                    aliases=_clip(list(area.aliases)),
                    floor_id=area.floor_id if area.floor_id in {f.id for f in floors} else None,
                )
            )
        except ValidationError:
            _LOGGER.debug("Skipping area %s: not representable", area.id)
    area_ids = {a.id for a in areas}
    entities = []
    for state in hass.states.async_all():
        if not async_should_expose(hass, conversation.DOMAIN, state.entity_id):
            continue
        entry = ent_reg.async_get(state.entity_id)
        aliases = er.async_get_entity_aliases(hass, entry) if entry else []
        area_id = er.async_get_effective_area_id(hass, entry) if entry else None
        try:
            entities.append(
                Entity(
                    id=state.entity_id,
                    name=state.name[:NAME_MAX] or state.entity_id,
                    aliases=[a for a in _clip(aliases) if a != state.name],
                    area_id=area_id if area_id in area_ids else None,
                    device_class=state.attributes.get("device_class"),
                )
            )
        except ValidationError:
            _LOGGER.debug("Skipping %s: not representable", state.entity_id)
    return Home(entities=entities[:3000], areas=areas[:500], floors=floors[:50])


def _speech(response: intent.IntentResponse) -> str:
    return response.speech.get("plain", {}).get("speech", "") or ""


class AssistDeciderAgent(conversation.ConversationEntity, conversation.AbstractConversationAgent):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: DeciderConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        info = entry.runtime_data.info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Assist Decider",
            model=f"{info.provider} {info.model} on {info.device}",
            sw_version=info.server_version,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return [
            lang for lang in self.entry.runtime_data.info.languages if lang in THRESHOLD_OPTIONS
        ]

    async def _intents_json(self, language: str) -> dict[str, Any]:
        cache = self.entry.runtime_data.intents_json
        if language not in cache:
            cache[language] = await self.hass.async_add_executor_job(get_intents, language) or {}
        return cache[language]

    async def _async_handle_message(
        self, user_input: conversation.ConversationInput, chat_log: conversation.ChatLog
    ) -> conversation.ConversationResult:
        language = (user_input.language or self.hass.config.language).split("-")[0].lower()
        intents_json = await self._intents_json(language)
        satellite_area_id, device_id = self._satellite_area_and_device(user_input)
        home = build_home(self.hass)
        options = self.entry.options
        request = ProcessRequest(
            protocol_version=1,
            text=user_input.text[:500] or "-",
            language=language if language in THRESHOLD_OPTIONS else "en",
            satellite_area_id=satellite_area_id
            if satellite_area_id in {a.id for a in home.areas}
            else None,
            intents=sorted(
                h.intent_type
                for h in intent.async_get(self.hass)
                if h.intent_type in SUPPORTED_INTENTS
            ),
            home=home,
            options=Options(
                confidence_threshold=options.get(
                    THRESHOLD_OPTIONS.get(language, ""), DEFAULT_THRESHOLDS.get(language, 0.5)
                )
            ),
        )

        try:
            result = await self.entry.runtime_data.client.process(request)
        except AuthError:
            self.entry.async_start_reauth(self.hass)
            return await self._fallback_or_error(user_input, chat_log, language, "unavailable")
        except ServerUnavailable as err:
            _LOGGER.warning("Decision server unavailable: %s", err)
            return await self._fallback_or_error(user_input, chat_log, language, "unavailable")
        _LOGGER.debug("Decision %s: %s", result.trace_id, result)

        if result.status == "escalate" or (result.unresolved and options.get(CONF_FALLBACK_AGENT)):
            return await self._fallback_or_error(user_input, chat_log, language, "no_intent")

        known = {e.id for e in home.entities}
        known_areas = {a.id for a in home.areas}
        responses = []
        for action in result.actions:
            if not self._valid(action, known, known_areas):
                continue
            responses.append(
                await self._execute(
                    action, user_input, language, device_id, satellite_area_id, intents_json
                )
            )
        if not responses:
            return await self._fallback_or_error(user_input, chat_log, language, "no_intent")

        response = _merge(responses, language)
        speech = " ".join(s for r in responses if (s := _speech(r)))
        if result.unresolved:
            speech = (
                f"{speech} {MESSAGES.get(language, MESSAGES['en'])['rest_not_understood']}".strip()
            )
        response.async_set_speech(speech)
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=speech)
        )
        return conversation.ConversationResult(
            response=response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=chat_log.continue_conversation,
        )

    def _valid(self, action: Action, known: set[str], known_areas: set[str]) -> bool:
        allowed = SUPPORTED_INTENTS.get(action.intent)
        problem = None
        if allowed is None:
            problem = "intent not allowed"
        elif set(action.slots) - allowed:
            problem = f"slots not allowed: {set(action.slots) - allowed}"
        elif "name" in action.slots and action.slots["name"] not in known:
            problem = "entity not exposed"
        elif "area" in action.slots and action.slots["area"] not in known_areas:
            problem = "unknown area"
        if problem:
            _LOGGER.warning(
                "Ignoring server action %s %s: %s", action.intent, action.slots, problem
            )
        return problem is None

    def _satellite_area_and_device(
        self, user_input: conversation.ConversationInput
    ) -> tuple[str | None, str | None]:
        area_id, device_id = None, user_input.device_id
        if user_input.satellite_id and (
            entry := er.async_get(self.hass).async_get(user_input.satellite_id)
        ):
            area_id, device_id = entry.area_id, entry.device_id or device_id
        if (
            area_id is None
            and device_id
            and (device := dr.async_get(self.hass).async_get(device_id))
        ):
            area_id = dr.async_get_effective_area_id(self.hass, device)
        return area_id, device_id

    async def _execute(
        self,
        action: Action,
        user_input: conversation.ConversationInput,
        language: str,
        device_id: str | None,
        satellite_area_id: str | None,
        intents_json: dict[str, Any],
    ) -> intent.IntentResponse:
        slots: dict[str, Any] = {key: {"value": value} for key, value in action.slots.items()}
        speech_slots: dict[str, Any] = {
            key: _spoken_number(value, language) if isinstance(value, float) else value
            for key, value in action.slots.items()
        }
        state = self.hass.states.get(action.slots["name"]) if "name" in action.slots else None
        if state:
            slots["name"]["text"] = speech_slots["name"] = state.name
            if device_class := state.attributes.get("device_class"):
                speech_slots["device_class"] = device_class
        if "area" in action.slots and (
            area := ar.async_get(self.hass).async_get_area(action.slots["area"])
        ):
            slots["area"]["text"] = speech_slots["area"] = area.name
        if satellite_area_id:
            slots["preferred_area_id"] = {"value": satellite_area_id}
        errors = intents_json.get("responses", {}).get("errors", {})
        try:
            response = await intent.async_handle(
                self.hass,
                DOMAIN,
                action.intent,
                slots,
                action.segment,
                user_input.context,
                language,
                assistant=conversation.DOMAIN,
                device_id=device_id,
                satellite_id=user_input.satellite_id,
                conversation_agent_id=user_input.agent_id,
            )
        except intent.MatchFailedError as err:
            return self._error(
                language,
                intent.IntentResponseErrorCode.NO_VALID_TARGETS,
                _match_error_text(self.hass, err, errors),
            )
        except intent.IntentHandleError as err:
            _LOGGER.warning("Intent %s failed: %s", action.intent, err)
            text = errors.get(err.response_key or "handle_error") or errors.get("handle_error", "")
            return self._error(
                language, intent.IntentResponseErrorCode.FAILED_TO_HANDLE, self._render(text, {})
            )
        except intent.IntentError:
            _LOGGER.exception("Unexpected error handling %s", action.intent)
            return self._error(
                language,
                intent.IntentResponseErrorCode.UNKNOWN,
                self._render(errors.get("handle_error", ""), {}),
            )

        if not _speech(response):
            templates = intents_json.get("responses", {}).get("intents", {}).get(action.intent, {})
            domain = state.domain if state else (action.slots.get("domain") or [None])[0]
            key = next(
                (k for k in _response_keys(action, domain, speech_slots) if k in templates), None
            )
            speech = self._render_response(templates[key], response, speech_slots) if key else ""
            response.async_set_speech(speech or MESSAGES.get(language, MESSAGES["en"])["done"])
        return response

    def _error(
        self, language: str, code: intent.IntentResponseErrorCode, text: str
    ) -> intent.IntentResponse:
        response = intent.IntentResponse(language=language)
        response.async_set_error(
            code, text or MESSAGES.get(language, MESSAGES["en"])["unavailable"]
        )
        return response

    def _render(self, text: str, variables: dict[str, Any]) -> str:
        try:
            return " ".join(
                str(
                    template.Template(text, self.hass).async_render(variables, parse_result=False)
                ).split()
            )
        except Exception:
            _LOGGER.debug("Could not render %r", text, exc_info=True)
            return ""

    def _render_response(
        self, text: str, response: intent.IntentResponse, speech_slots: dict[str, Any]
    ) -> str:
        states = response.matched_states or response.unmatched_states
        first: State | None = states[0] if states else None
        return self._render(
            text,
            {
                "slots": speech_slots | response.speech_slots,
                "state": template.TemplateState(self.hass, first) if first else None,
                "query": {
                    "matched": [
                        template.TemplateState(self.hass, s) for s in response.matched_states
                    ],
                    "unmatched": [
                        template.TemplateState(self.hass, s) for s in response.unmatched_states
                    ],
                },
            },
        )

    async def _fallback_or_error(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
        language: str,
        error_key: str,
    ) -> conversation.ConversationResult:
        fallback = self.entry.options.get(CONF_FALLBACK_AGENT)
        if fallback and fallback != self.entity_id:
            _LOGGER.debug("Handing %r to %s", user_input.text, fallback)
            return await conversation.async_converse(
                self.hass,
                user_input.text,
                chat_log.conversation_id,
                user_input.context,
                user_input.language,
                agent_id=fallback,
                device_id=user_input.device_id,
                satellite_id=user_input.satellite_id,
            )
        if error_key == "unavailable":
            text = MESSAGES.get(language, MESSAGES["en"])["unavailable"]
            code = intent.IntentResponseErrorCode.FAILED_TO_HANDLE
        else:
            errors = (await self._intents_json(language)).get("responses", {}).get("errors", {})
            text = (
                self._render(errors.get("no_intent", ""), {}) or "Sorry, I couldn't understand that"
            )
            code = intent.IntentResponseErrorCode.NO_INTENT_MATCH
        response = self._error(language, code, text)
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=_speech(response))
        )
        return conversation.ConversationResult(
            response=response, conversation_id=chat_log.conversation_id
        )


def _spoken_number(value: float, language: str) -> str:
    """22.0 -> "22"; 21.5 -> "21.5" (en) / "21,5" (de), as people say it."""
    text = str(int(value)) if value.is_integer() else f"{value:g}"
    return text.replace(".", ",") if language == "de" else text


def _response_keys(action: Action, domain: str | None, speech_slots: dict[str, Any]) -> list[str]:
    """Candidate response template keys, most specific first (they differ per language)."""
    name, area = action.intent, "area" in action.slots
    if name in ("HassTurnOn", "HassTurnOff"):
        if area:
            return [f"{domain}s_area", f"{domain}_area", str(domain), "default"]
        if domain == "cover" and "device_class" in speech_slots:
            return ["cover_device_class", "cover", "default"]
        return (
            [str(domain), "default"]
            if domain in ("cover", "lock", "scene", "script", "valve")
            else ["default"]
        )
    if name == "HassLightSet":
        return ["brightness"]
    if name == "HassSetPosition":
        return ["cover_device_class", "default"] if "device_class" in speech_slots else ["default"]
    if name == "HassGetState":
        return ["one", "default"]
    if name == "HassClimateGetTemperature":
        return ["default", "current_temperature"]
    return ["default"]


def _match_error_text(
    hass: HomeAssistant, err: intent.MatchFailedError, errors: dict[str, str]
) -> str:
    """Home Assistant's own wording for 'no such device', with names instead of IDs."""
    constraints, reason = err.constraints, err.result.no_match_reason
    area = None
    if constraints.area_name:
        entry = ar.async_get(hass).async_get_area(constraints.area_name)
        area = entry.name if entry else constraints.area_name
    domain = next(iter(constraints.domains), None) if constraints.domains else None
    Reason = intent.MatchFailedReason
    if reason in (Reason.AREA, Reason.DOMAIN, Reason.DEVICE_CLASS) and domain:
        key, args = (
            ("no_domain_in_area", {"domain": domain, "area": area})
            if area
            else ("no_domain", {"domain": domain})
        )
    elif reason is Reason.FEATURE:
        key, args = "feature_not_supported", {}
    else:
        try:  # private helper: covers the rarer reasons
            from homeassistant.components.conversation.default_agent import (
                _get_match_error_response,
            )

            error_key, args = _get_match_error_response(hass, err)
            key = getattr(error_key, "value", str(error_key))
        except Exception:  # noqa: BLE001
            _LOGGER.debug("No specific error text for %s", err, exc_info=True)
            key, args = "no_intent", {}
        if "area" in args:
            args["area"] = area
        if "entity" in args and (state := hass.states.get(str(args["entity"]))):
            args["entity"] = state.name
    try:
        text = errors.get(key) or errors.get("no_intent", "")
        return " ".join(
            str(template.Template(text, hass).async_render(args, parse_result=False)).split()
        )
    except Exception:  # noqa: BLE001 - a broken template must not break the reply
        _LOGGER.debug("Could not render error %s", key, exc_info=True)
        return errors.get("no_intent", "")


def _merge(responses: list[intent.IntentResponse], language: str) -> intent.IntentResponse:
    if len(responses) == 1:
        return responses[0]
    merged = intent.IntentResponse(language=language)
    for r in responses:
        merged.success_results += r.success_results
        merged.failed_results += r.failed_results
        merged.matched_states += r.matched_states
        merged.unmatched_states += r.unmatched_states
    errors = [r for r in responses if r.response_type == intent.IntentResponseType.ERROR]
    if len(errors) == len(responses):
        merged.response_type = intent.IntentResponseType.ERROR
        merged.error_code = errors[0].error_code
    elif any(r.intent and r.intent.intent_type in QUERY_INTENTS for r in responses):
        merged.response_type = intent.IntentResponseType.QUERY_ANSWER
    return merged
