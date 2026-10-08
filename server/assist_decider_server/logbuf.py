"""In-memory event bus behind the live log UI: log records and decision traces."""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
from collections import deque
from typing import Any

SUBSCRIBER_QUEUE = 500
MAX_MESSAGE = 4000


class EventBus:
    """Ring buffer + fan-out to SSE subscribers. `publish` is safe from any thread."""

    def __init__(self, maxlen: int) -> None:
        self._buffer: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            event = {"id": next(self._ids), **event}
            self._buffer.append(event)
            subscribers = list(self._subscribers)
        if self._loop is None:
            return
        for queue in subscribers:
            try:
                self._loop.call_soon_threadsafe(_offer, queue, event)
            except RuntimeError:  # loop closed during shutdown
                return

    def subscribe(self) -> tuple[list[dict[str, Any]], asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE)
        with self._lock:
            self._subscribers.add(queue)
            return list(self._buffer), queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(queue)


def _offer(queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
    """A slow viewer loses its oldest events instead of growing memory."""
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(event)


class BusHandler(logging.Handler):
    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self.bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.bus.publish(
                {
                    "type": "log",
                    "ts": record.created,
                    "level": record.levelname,
                    "logger": record.name,
                    # Bounded: one DEBUG home dump must not fill memory 2000 times over.
                    "msg": self.format(record)[:MAX_MESSAGE],
                }
            )
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)
