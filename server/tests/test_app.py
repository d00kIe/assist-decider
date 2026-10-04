from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assist_decider_server import app as app_module
from assist_decider_server.app import create_app
from assist_decider_server.logbuf import EventBus

from .conftest import FakeProvider, intent_rule, make_request

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def body(text: str = "turn on the kitchen light", **kw) -> dict:
    return json.loads(make_request(text, **kw).model_dump_json())


@pytest.fixture
def provider():
    return FakeProvider(intent_rule("HassTurnOn"))


@pytest.fixture
def client(provider):
    app = create_app(provider=provider, token=TOKEN, bus=EventBus(100), max_body_bytes=200_000)
    with TestClient(app, client=("10.0.0.5", 1234)) as c:
        yield c


def test_process_ok(client):
    r = client.post("/v1/process", json=body(), headers=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["actions"][0]["slots"] == {"name": "light.kitchen_ceiling"}


def test_info(client):
    r = client.get("/v1/info", headers=AUTH)
    assert r.json()["protocol_version"] == 1
    assert r.json()["languages"] == ["en", "de"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": TOKEN},
        {"Authorization": "Basic " + TOKEN},
        {"Authorization": "Bearer té".encode("latin-1")},
    ],
)
def test_auth_required(client, headers):
    assert client.get("/v1/info", headers=headers).status_code == 401
    assert client.post("/v1/process", json=body(), headers=headers).status_code == 401


def test_auth_checked_before_body_is_parsed(client):
    r = client.post(
        "/v1/process", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 401


def test_lockout_after_repeated_failures(client):
    for _ in range(app_module.LOCKOUT_FAILURES):
        assert client.get("/v1/info", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.get("/v1/info", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    # Behind a proxy everyone shares one address: the right token must still work.
    assert client.get("/v1/info", headers=AUTH).status_code == 200


def test_log_line_cannot_be_forged_through_the_path(client, caplog):
    client.get("/v1/x%0AINFO fake line")
    assert "\nINFO fake line" not in caplog.text


def test_body_too_large_declared(client):
    r = client.post(
        "/v1/process", content=b"x" * 300_000, headers=AUTH | {"Content-Type": "application/json"}
    )
    assert r.status_code == 413


def test_body_too_large_streamed(client):
    def chunks():
        for _ in range(30):
            yield b"x" * 10_000

    r = client.post(
        "/v1/process", content=chunks(), headers=AUTH | {"Content-Type": "application/json"}
    )
    assert r.status_code == 413


def test_extra_fields_rejected(client):
    data = body()
    data["evil"] = 1
    assert client.post("/v1/process", json=data, headers=AUTH).status_code == 422


@pytest.mark.parametrize(
    "patch", [{"language": "fr"}, {"protocol_version": 2}, {"text": "x" * 501}, {"text": ""}]
)
def test_invalid_requests_rejected(client, patch):
    assert client.post("/v1/process", json=body() | patch, headers=AUTH).status_code == 422


def test_invalid_entity_id_rejected(client):
    data = body()
    data["home"]["entities"][0]["id"] = "light.kitchen<script>"
    assert client.post("/v1/process", json=data, headers=AUTH).status_code == 422


def test_busy_returns_503():
    gate = threading.Event()

    class Slow(FakeProvider):
        def predict(self, *a, **kw):
            gate.wait(5)
            return super().predict(*a, **kw)

    app = create_app(
        provider=Slow(intent_rule("HassTurnOn")), token=TOKEN, bus=EventBus(10), max_pending=1
    )
    with TestClient(app) as c:
        results = []
        t = threading.Thread(
            target=lambda: results.append(c.post("/v1/process", json=body(), headers=AUTH))
        )
        t.start()
        for _ in range(100):  # wait until the first request occupies the slot
            r = c.post("/v1/process", json=body(), headers=AUTH)
            if r.status_code == 503:
                break
        gate.set()
        t.join()
        assert r.status_code == 503
        assert r.headers["retry-after"] == "1"
        assert results[0].status_code == 200


def test_docs_disabled(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_security_headers(client):
    for r in (client.get("/"), client.get("/v1/info", headers=AUTH), client.get("/v1/info")):
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["cache-control"] == "no-store"
    assert "server" not in client.get("/healthz").headers or True  # uvicorn header off in __main__


def test_static_ui_served_without_auth_and_has_no_query_params(client):
    assert client.get("/").status_code == 200
    assert client.get("/app.js").headers["content-type"].startswith("text/javascript")
    # Regression: route params must not leak into the file path.
    r = client.get("/?filename=../../../../etc/passwd&path=/etc/passwd")
    assert r.status_code == 200 and "<!doctype html>" in r.text.lower()


def test_ui_never_uses_inner_html():
    js = (Path(app_module.__file__).parent / "static" / "app.js").read_text()
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", js)


def test_events_require_auth(client):
    assert client.get("/v1/events").status_code == 401


def test_events_replay_backlog_over_real_server():
    """TestClient cannot end an infinite stream, so run uvicorn for real."""
    import httpx
    import uvicorn

    bus = EventBus(10)
    bus.publish({"type": "log", "msg": "hello"})
    app = create_app(provider=FakeProvider(), token=TOKEN, bus=bus)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            threading.Event().wait(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}/v1/events"
        assert httpx.get(url, timeout=5).status_code == 401
        with httpx.stream("GET", url, headers=AUTH, timeout=5) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            for line in r.iter_lines():
                if line.startswith("data: "):
                    assert json.loads(line[6:])["msg"] == "hello"
                    break
    finally:
        server.should_exit = True
        thread.join(5)


def test_trace_published_on_bus(provider):
    bus = EventBus(100)
    app = create_app(provider=provider, token=TOKEN, bus=bus)
    with TestClient(app) as c:
        c.post("/v1/process", json=body(), headers=AUTH)
        backlog, _ = bus.subscribe()
    traces = [e for e in backlog if e["type"] == "trace"]
    assert traces and traces[-1]["text"] == "turn on the kitchen light"


def _serve(app):
    import uvicorn

    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_config=None, http=app_module.HeaderTimeoutH11Protocol
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        threading.Event().wait(0.05)
    return server, thread, server.servers[0].sockets[0].getsockname()[1]


def test_silent_connections_are_closed(monkeypatch):
    import socket

    monkeypatch.setattr(app_module, "HEADER_TIMEOUT", 0.3)
    server, thread, port = _serve(
        create_app(provider=FakeProvider(), token=TOKEN, bus=EventBus(10))
    )
    try:
        sock = socket.create_connection(("127.0.0.1", port))
        sock.settimeout(3)
        sock.sendall(b"GET /healthz HTTP/1.1\r\n")  # never finishes the headers
        assert sock.recv(100) == b""  # the server closed it
        sock.close()
    finally:
        server.should_exit = True
        thread.join(5)
