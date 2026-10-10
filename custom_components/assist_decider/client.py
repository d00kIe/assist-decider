"""HTTP client for the Assist Decider server."""

from __future__ import annotations

import json
from typing import TypeVar

import aiohttp
from pydantic import BaseModel, ValidationError

from .const import MAX_RESPONSE_BYTES, MODEL_TIMEOUT, REQUEST_TIMEOUT
from .protocol import (
    PROTOCOL_VERSION,
    ModelRequest,
    ProcessRequest,
    ProcessResponse,
    ServerInfo,
)

_M = TypeVar("_M", bound=BaseModel)


class DeciderError(Exception):
    """Base error."""


class ServerUnavailable(DeciderError):
    """The server cannot be reached or answered with an error."""


class ProtocolMismatch(DeciderError):
    """The server speaks another protocol version."""


async def _detail(resp: aiohttp.ClientResponse) -> str:
    """The server's error message ({"detail": ...}), shortened; else the status."""
    try:
        detail = json.loads(await resp.content.read(4096))["detail"]
    except (ValueError, KeyError, TypeError):
        return f"HTTP {resp.status}"
    return f"HTTP {resp.status}: {str(detail)[:300]}"


class DeciderClient:
    def __init__(self, session: aiohttp.ClientSession, url: str) -> None:
        self._session = session
        self._url = url.rstrip("/")

    async def info(self) -> ServerInfo:
        info = await self._request("GET", "/v1/info", ServerInfo)
        if info.protocol_version != PROTOCOL_VERSION:
            raise ProtocolMismatch(
                f"Server protocol {info.protocol_version}, integration protocol {PROTOCOL_VERSION}"
            )
        return info

    async def process(self, request: ProcessRequest) -> ProcessResponse:
        return await self._request(
            "POST", "/v1/process", ProcessResponse, request.model_dump_json(exclude_none=True)
        )

    async def set_model(self, model: str) -> ServerInfo:
        """Make the server unload its model and load `model`; blocks until it is loaded."""
        body = ModelRequest(model=model).model_dump_json()
        return await self._request("POST", "/v1/model", ServerInfo, body, MODEL_TIMEOUT)

    async def _request(
        self,
        method: str,
        path: str,
        model: type[_M],
        body: str | None = None,
        timeout: float = REQUEST_TIMEOUT,
    ) -> _M:
        headers = {"Content-Type": "application/json"} if body else {}
        try:
            async with self._session.request(
                method,
                self._url + path,
                data=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,
            ) as resp:
                if resp.status != 200:
                    raise ServerUnavailable(await _detail(resp))
                raw = await resp.content.read(MAX_RESPONSE_BYTES + 1)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ServerUnavailable(str(err) or type(err).__name__) from err
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ServerUnavailable("Response too large")
        try:
            return model.model_validate_json(raw)
        except ValidationError as err:
            raise ServerUnavailable(f"Invalid response: {err.error_count()} errors") from err
