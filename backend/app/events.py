"""A tiny in-process pub/sub: the agent emits, the HTTP stream consumes.

One asyncio.Queue per subscriber, and a publisher that never blocks on a slow consumer —
a browser tab that stops reading must not be able to stall the agent loop.
"""

from __future__ import annotations

import asyncio
from typing import Any


class EventBus:
    def __init__(self, maxsize: int = 1000) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = {}
        self._maxsize = maxsize

    def subscribe(self, topic: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subs.setdefault(topic, []).append(queue)
        return queue

    def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None:
        subs = self._subs.get(topic)
        if not subs:
            return
        if queue in subs:
            subs.remove(queue)
        if not subs:
            self._subs.pop(topic, None)

    def emit(self, topic: str, event: dict[str, Any]) -> None:
        for queue in list(self._subs.get(topic, [])):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A consumer that stopped reading loses events rather than blocking the
                # producer. The run's persisted transcript stays complete regardless.
                pass

    def has_subscribers(self, topic: str) -> bool:
        return bool(self._subs.get(topic))
