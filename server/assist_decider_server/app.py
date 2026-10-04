"""HTTP API, live log stream and the static log UI."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uvicorn.protocols.http.h11_impl import H11Protocol

from . import __version__
from .logbuf import EventBus
from .pipeline import decide
from .protocol import PROTOCOL_VERSION, ProcessRequest, ProcessResponse, ServerInfo
from .providers import DecisionProvider

_LOGGER = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

LOCKOUT_FAILURES = 10
LOCKOUT_WINDOW = 300.0
LOCKOUT_SECONDS = 900.0
SSE_PING_SECONDS = 15.0
HEADER_TIMEOUT = 5.0  # a client must send its request line and headers within this
BODY_TIMEOUT = 10.0  # ... and its body within this

SECURITY_HEADERS = [
    (
        b"content-security-policy",
        b"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        b"img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    ),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
]


async def _send_json(
    send: Send, status: int, detail: str, headers: list[tuple[bytes, bytes]] = ()
) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                *headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = [
                    *message.get("headers", []),
                    *((k, v) for k, v in SECURITY_HEADERS if k not in present),
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)


class HeaderTimeoutH11Protocol(H11Protocol):
    """uvicorn never times out a connection that has not sent a request yet, so a handful
    of silent sockets could hold the server. Close them after HEADER_TIMEOUT."""

    def connection_made(self, transport: Any) -> None:  # type: ignore[override]
        super().connection_made(transport)
        self._header_timer = self.loop.call_later(HEADER_TIMEOUT, self._close_if_silent)

    def _close_if_silent(self) -> None:
        if self.cycle is None and not self.transport.is_closing():
            self.transport.close()

    def connection_lost(self, exc: Exception | None) -> None:
        self._header_timer.cancel()
        super().connection_lost(exc)


class BearerAuth:
    """Rejects /v1/* requests without the token *before* the body is read.

    Repeated failures from one address lock that address out, but the correct token always
    gets through: behind a proxy every client shares one address, and an attacker must not
    be able to lock Home Assistant out.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token.encode()
        self.failures: dict[str, list[float]] = {}
        self.locked: dict[str, float] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            return await self.app(scope, receive, send)
        if self._authorized(scope):
            return await self.app(scope, receive, send)
        ip = (scope.get("client") or ("unknown",))[0]
        now = time.monotonic()
        if self.locked.get(ip, 0) > now:
            return await _send_json(
                send,
                429,
                "Too many failed attempts",
                [(b"retry-after", str(int(self.locked[ip] - now) + 1).encode())],
            )
        self._record_failure(ip, now)
        _LOGGER.warning("Rejected unauthenticated request to %r from %s", scope["path"], ip)
        await _send_json(send, 401, "Unauthorized", [(b"www-authenticate", b"Bearer")])

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope["headers"]:
            if name == b"authorization":
                scheme, _, supplied = value.partition(b" ")
                # Compare bytes: str comparison raises on non-ASCII header values.
                return scheme.lower() == b"bearer" and hmac.compare_digest(
                    supplied.strip(), self.token
                )
        return False

    def _record_failure(self, ip: str, now: float) -> None:
        if len(self.failures) > 10_000 or len(self.locked) > 10_000:
            # ponytail: crude bound against address spraying; per-IP state is best effort
            self.failures = {k: v for k, v in self.failures.items() if now - v[-1] < LOCKOUT_WINDOW}
            self.locked = {k: v for k, v in self.locked.items() if v > now}
        recent = [t for t in self.failures.get(ip, []) if now - t < LOCKOUT_WINDOW] + [now]
        self.failures[ip] = recent
        if len(recent) >= LOCKOUT_FAILURES:
            self.locked[ip] = now + LOCKOUT_SECONDS
            self.failures.pop(ip, None)
            _LOGGER.warning(
                "Locked out %s for %d s after repeated failed authentication", ip, LOCKOUT_SECONDS
            )


class BodyLimit:
    """FastAPI has no request size limit; enforce one for declared and streamed bodies."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for name, value in scope["headers"]:
            if name == b"content-length":
                if not value.isdigit():
                    return await _send_json(send, 400, "Invalid Content-Length")
                if int(value) > self.max_bytes:
                    return await _send_json(send, 413, "Request body too large")
        received = 0
        body_done = False

        async def limited_receive() -> Message:
            nonlocal received, body_done
            if body_done:  # later receives wait for disconnects (SSE) and must not time out
                return await receive()
            try:
                message = await asyncio.wait_for(receive(), BODY_TIMEOUT)
            except TimeoutError:
                raise StarletteHTTPException(408, "Request body timeout") from None
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise StarletteHTTPException(413, "Request body too large")
                body_done = not message.get("more_body", False)
            return message

        await self.app(scope, limited_receive, send)


def _static(filename: str, media: str) -> Any:
    # A closure with no parameters: FastAPI must not turn anything into a query parameter.
    path = STATIC / filename

    async def serve() -> FileResponse:
        return FileResponse(path, media_type=media)

    return serve


def _sse(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"id: {event['id']}\nevent: {event['type']}\ndata: {data}\n\n"


def create_app(
    *,
    provider: DecisionProvider,
    token: str,
    bus: EventBus,
    max_pending: int = 4,
    max_body_bytes: int = 1_048_576,
) -> FastAPI:
    # One worker: the GPU runs one inference at a time and model calls are not thread-safe.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference")
    pending = 0

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        bus.bind(asyncio.get_running_loop())
        await asyncio.get_running_loop().run_in_executor(executor, provider.load)
        _LOGGER.info(
            "Ready: provider=%s model=%s device=%s languages=%s",
            provider.name,
            provider.model,
            provider.device,
            ",".join(provider.languages),
        )
        yield
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="Assist Decider", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/v1/info")
    async def info() -> ServerInfo:
        return ServerInfo(
            protocol_version=PROTOCOL_VERSION,
            server_version=__version__,
            provider=provider.name,
            model=provider.model,
            languages=list(provider.languages),
            device=provider.device,
        )

    @app.post("/v1/process")
    async def process(request: ProcessRequest) -> ProcessResponse:
        nonlocal pending
        if pending >= max_pending:
            raise HTTPException(503, "Busy, try again", headers={"Retry-After": "1"})
        pending += 1
        try:
            response, trace = await asyncio.get_running_loop().run_in_executor(
                executor, decide, request, provider
            )
        except Exception:
            _LOGGER.exception("Decision failed")
            raise HTTPException(500, "Decision failed") from None
        finally:
            pending -= 1
        bus.publish(trace)
        summary = "; ".join(f"{a.intent} {a.slots}" for a in response.actions) or response.reason
        _LOGGER.info(
            "[%s] %s %r -> %s: %s (%.0f ms)",
            response.trace_id,
            request.language,
            request.text,
            response.status,
            summary,
            response.elapsed_ms,
        )
        _LOGGER.debug("[%s] home: %s", response.trace_id, request.home.model_dump_json())
        return response

    @app.get("/v1/events")
    async def events() -> StreamingResponse:
        backlog, queue = bus.subscribe()

        async def stream() -> AsyncIterator[str]:
            try:
                for event in backlog:
                    yield _sse(event)
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), SSE_PING_SECONDS)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield _sse(event)
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    for route, filename, media in (
        ("/", "index.html", "text/html"),
        ("/app.js", "app.js", "text/javascript"),
        ("/app.css", "app.css", "text/css"),
    ):
        app.add_api_route(route, _static(filename, media), methods=["GET"], include_in_schema=False)

    # Last added runs first: headers wrap everything, auth runs before the body is read.
    app.add_middleware(BodyLimit, max_bytes=max_body_bytes)
    app.add_middleware(BearerAuth, token=token)
    app.add_middleware(SecurityHeaders)
    return app
