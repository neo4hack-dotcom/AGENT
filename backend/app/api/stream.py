"""Server-sent events for one run.

A run outlives the request that started it, so this endpoint is a *view* of a run rather
than the run itself: it can be opened late, closed and reopened, and it replays what was
missed. That is what makes closing the tab mid-answer harmless.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import Request
from fastapi.responses import StreamingResponse

KEEPALIVE_S = 15


def _frame(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def run_stream(container, ctx, request: Request, since: int = 0) -> StreamingResponse:
    # Subscribe *before* snapshotting the buffer, then de-duplicate by sequence number:
    # subscribing after the snapshot would silently drop anything emitted in between.
    queue = container.bus.subscribe(ctx.topic)
    backlog = list(ctx.buffer)

    async def generator() -> AsyncIterator[str]:
        last_seq = since - 1
        try:
            for event in backlog:
                if event.get("seq", 0) >= since:
                    last_seq = max(last_seq, event.get("seq", 0))
                    yield _frame(event)
            if ctx.status != "running" and any(e.get("type") == "done" for e in backlog):
                return
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event.get("seq", 0) <= last_seq:
                    continue
                last_seq = event.get("seq", last_seq)
                yield _frame(event)
                if event.get("type") == "done":
                    return
        finally:
            container.bus.unsubscribe(ctx.topic, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )
