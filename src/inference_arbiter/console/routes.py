"""Console API routes."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from inference_arbiter.benchmark.runner import BenchmarkRunner
from inference_arbiter.console.metrics_proxy import fetch_metrics_summary
from inference_arbiter.observability.events import RoutingEventBus

STATIC_DIR = Path(__file__).resolve().parent / "static"


class BenchmarkStartRequest(BaseModel):
    scenario: Literal["baseline", "arbiter", "round_robin", "random"] = "arbiter"
    users: int = Field(default=10, ge=1, le=200)
    spawn_rate: float = Field(default=2.0, ge=0.1, le=50)
    duration_s: float = Field(default=180.0, ge=10, le=3600)
    baseline_model: str = Field(default="large", pattern="^(small|medium|large)$")
    max_requests: int = Field(default=0, ge=0, le=10000)


def create_console_router(
    event_bus: RoutingEventBus,
    benchmark_runner: BenchmarkRunner,
    prometheus_url: str,
    get_audit_record,
    get_health,
    bandit=None,
    bandit_checkpoint_path: str = "",
) -> APIRouter:
    router = APIRouter()

    @router.get("/console")
    async def console_index():
        return FileResponse(STATIC_DIR / "index.html")

    @router.get("/console/static/{path:path}")
    async def console_static(path: str):
        file_path = STATIC_DIR / path
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(file_path)

    @router.get("/console/api/health")
    async def console_health():
        return await get_health()

    @router.get("/console/api/events")
    async def console_events(request: Request):
        async def event_stream():
            try:
                async for event in event_bus.subscribe():
                    if await request.is_disconnected():
                        break
                    yield event_bus.format_sse(event)
                    await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/console/api/routing/{request_id}")
    async def console_routing_detail(request_id: str):
        record = get_audit_record(request_id)
        if not record:
            raise HTTPException(status_code=404, detail="Routing decision not found")
        return record

    @router.get("/console/api/metrics/summary")
    async def console_metrics_summary():
        return await fetch_metrics_summary(prometheus_url)

    @router.post("/console/api/benchmark/start")
    async def benchmark_start(body: BenchmarkStartRequest):
        return await benchmark_runner.start(
            scenario=body.scenario,  # type: ignore[arg-type]
            users=body.users,
            spawn_rate=body.spawn_rate,
            duration_s=body.duration_s,
            baseline_model=body.baseline_model,
            max_requests=body.max_requests,
        )

    @router.post("/console/api/benchmark/stop")
    async def benchmark_stop():
        return benchmark_runner.stop_nowait()

    @router.get("/console/api/benchmark/status")
    async def benchmark_status():
        return await benchmark_runner.status()

    @router.get("/console/api/bandit/checkpoint")
    async def bandit_checkpoint():
        path = bandit_checkpoint_path
        exists = bool(path and os.path.exists(path))
        result: dict[str, Any] = {
            "path": path or None,
            "exists": exists,
        }
        if bandit is not None:
            result["total_updates"] = bandit.total_updates
            result["policy_active"] = bandit.policy_active
            result["observations_per_tier"] = {
                t.value: bandit.observations_per_tier[t] for t in bandit.arms
            }
        return result

    return router
