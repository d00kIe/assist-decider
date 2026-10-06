"""Assist Decider wire protocol, version 3.

This file is shared verbatim between the decision server
(server/assist_decider_server/protocol.py) and the Home Assistant integration
(custom_components/assist_decider/protocol.py). Keep both copies byte-identical;
a test and CI enforce it. Only pydantic v2 may be imported here.

Requests are strict (extra fields are rejected) because the server is the trust
boundary. Responses ignore unknown fields so an older client can talk to a newer
server with the same protocol version.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

PROTOCOL_VERSION = 3

EntityId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$", max_length=255)]
Ident = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$", min_length=1, max_length=100)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
IntentName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", max_length=64)]
ShortStr = Annotated[str, StringConstraints(max_length=255)]
Aliases = Annotated[list[Name], Field(max_length=10)]
SlotValue = int | float | ShortStr | Annotated[list[ShortStr], Field(max_length=20)]
ContextId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Response(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Floor(_Request):
    id: Ident
    name: Name
    aliases: Aliases = []


class Area(_Request):
    id: Ident
    name: Name
    aliases: Aliases = []
    floor_id: Ident | None = None


class Entity(_Request):
    id: EntityId
    name: Name
    aliases: Aliases = []
    area_id: Ident | None = None
    device_class: Ident | None = None


class Home(_Request):
    """Everything exposed to Assist. Never contains states."""

    entities: Annotated[list[Entity], Field(max_length=3000)]
    areas: Annotated[list[Area], Field(max_length=500)] = []
    floors: Annotated[list[Floor], Field(max_length=50)] = []


class Options(_Request):
    """Behaviour configured in Home Assistant, already resolved for the request language."""

    confidence_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    memory_seconds: int = Field(default=60, ge=0, le=3600)  # 0: no follow-ups


class ProcessRequest(_Request):
    protocol_version: Literal[3]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    language: Literal["en", "de"]
    satellite_area_id: Ident | None = None
    context_id: ContextId | None = None  # satellite or conversation: scopes follow-ups
    intents: Annotated[list[IntentName], Field(max_length=300)]
    home: Home
    options: Options = Options()


class Action(_Response):
    """One intent call for Home Assistant to execute.

    Slots carry IDs, not names: ``name`` is an entity_id, ``area`` an area_id.
    """

    intent: IntentName
    slots: Annotated[dict[Ident, SlotValue], Field(max_length=12)]
    segment: Annotated[str, StringConstraints(max_length=500)]
    confidence: float


class ProcessResponse(_Response):
    protocol_version: int = PROTOCOL_VERSION
    status: Literal["ok", "escalate"]
    actions: Annotated[list[Action], Field(max_length=10)] = []
    unresolved: Annotated[
        list[Annotated[str, StringConstraints(max_length=500)]], Field(max_length=10)
    ] = []
    reason: ShortStr | None = None
    trace_id: ShortStr
    elapsed_ms: float


class ServerInfo(_Response):
    protocol_version: int
    server_version: ShortStr
    provider: ShortStr
    model: ShortStr
    languages: Annotated[list[ShortStr], Field(max_length=20)]
    device: ShortStr
