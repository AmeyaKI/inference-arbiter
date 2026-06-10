"""In-process routing event bus for console live feed."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any, AsyncIterator

from inference_arbiter.routing.bandit import BanditDecision
from inference_arbiter.routing.context import RequestContext


def build_routing_event(
    ctx: RequestContext,
    *,
    bandit_decision: BanditDecision | None = None,
    priority: str | None = None,
) -> dict[str, Any]:
    preview = ctx.payload.raw_prompt_preview if ctx.payload else ""
    complexity = None
    bandit_used_heuristic = None
    if bandit_decision is not None:
        complexity = bandit_decision.feature_result.complexity.value
        bandit_used_heuristic = bandit_decision.used_heuristic
    return {
        "request_id": ctx.request_id,
        "timestamp": time.time(),
        "requested_model": ctx.requested_model,
        "prompt_preview": preview,
        "final_tier": ctx.final_tier,
        "tiers_attempted": ctx.tiers_attempted,
        "routing_reason": ctx.routing_reason,
        "elapsed_ms": round(ctx.metrics.current_elapsed_ms, 2),
        "degraded": ctx.degraded,
        "bandit_used_heuristic": bandit_used_heuristic,
        "complexity": complexity,
        "priority": priority or ctx.priority.value,
        "status": ctx.status,
    }


class RoutingEventBus:
    """Bounded fan-out bus for SSE subscribers."""

    def __init__(self, buffer_size: int = 500) -> None:
        self._buffer: deque[dict[str, Any]] = deque(maxlen=buffer_size)
        self._subscribers: list[asyncio.Queue[dict[str, Any] | None]] = []
        self._lock = asyncio.Lock()

    async def publish(self, event: dict[str, Any]) -> None:
        self._buffer.append(event)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.append(queue)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            async with self._lock:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)

    async def close_subscriber(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    def clear(self) -> None:
        self._buffer.clear()

    def format_sse(self, event: dict[str, Any]) -> str:
        return f"data: {json.dumps(event)}\n\n"
