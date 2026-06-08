"""Unit tests for RoutingEventBus."""

from __future__ import annotations

import asyncio

import pytest

from inference_arbiter.observability.events import RoutingEventBus, build_routing_event
from inference_arbiter.routing.context import RequestContext
from inference_arbiter.models import TrafficPriority


def test_build_routing_event_minimal():
    ctx = RequestContext.create(
        request_id="abc-123",
        priority=TrafficPriority.INTERACTIVE,
        slo_deadline_ms=None,
        messages=[{"role": "user", "content": "hello world"}],
        requested_model="auto",
    )
    ctx.status = "completed"
    ctx.final_tier = "small"
    ctx.metrics.current_elapsed_ms = 42.0
    event = build_routing_event(ctx, priority="interactive")
    assert event["request_id"] == "abc-123"
    assert event["final_tier"] == "small"
    assert event["elapsed_ms"] == 42.0
    assert "hello" in event["prompt_preview"]


@pytest.mark.asyncio
async def test_event_bus_publish_subscribe():
    bus = RoutingEventBus(buffer_size=10)
    event = {"request_id": "x", "final_tier": "small"}

    async def collect():
        items = []
        async for item in bus.subscribe():
            items.append(item)
            if len(items) >= 1:
                break
        return items

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.01)
    await bus.publish(event)
    results = await asyncio.wait_for(task, timeout=2.0)
    assert results[0]["request_id"] == "x"


@pytest.mark.asyncio
async def test_event_bus_replays_buffer_to_new_subscriber():
    bus = RoutingEventBus(buffer_size=5)
    await bus.publish({"request_id": "1"})
    await bus.publish({"request_id": "2"})

    seen = []
    async for item in bus.subscribe():
        seen.append(item["request_id"])
        if len(seen) >= 2:
            break

    assert seen == ["1", "2"]
