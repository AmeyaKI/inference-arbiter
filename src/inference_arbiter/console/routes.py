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

from inference_arbiter.benchmark.export import save_benchmark_session
from inference_arbiter.benchmark.runner import BenchmarkRunner
from inference_arbiter.console.metrics_proxy import fetch_metrics_summary, fetch_timeseries_data
from inference_arbiter.observability.events import RoutingEventBus

STATIC_DIR = Path(__file__).resolve().parent / "static"


class BenchmarkStartRequest(BaseModel):
    scenario: Literal["baseline", "arbiter", "round_robin", "random"] = "arbiter"
    users: int = Field(default=10, ge=1, le=200)
    spawn_rate: float = Field(default=2.0, ge=0.1, le=50)
    duration_s: float = Field(default=180.0, ge=10, le=3600)
    baseline_model: str = Field(default="large", pattern="^(small|medium|large)$")
    max_requests: int = Field(default=0, ge=0, le=10000)
    label: str = Field(default="", max_length=120)


class BenchmarkSaveRequest(BaseModel):
    label: str = Field(default="", max_length=120)


def create_console_router(
    event_bus: RoutingEventBus,
    benchmark_runner: BenchmarkRunner,
    prometheus_url: str,
    get_audit_record,
    get_health,
    get_all_audit_records=None,
    clear_audit=None,
    bandit=None,
    bandit_checkpoint_path: str = "",
    tier_weights: dict[str, float] | None = None,
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

    @router.get("/console/api/events/snapshot")
    async def console_events_snapshot():
        return event_bus.snapshot()

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

    @router.get("/console/api/metrics/timeseries")
    async def console_metrics_timeseries():
        return await fetch_timeseries_data(prometheus_url)

    @router.post("/console/api/benchmark/start")
    async def benchmark_start(body: BenchmarkStartRequest):
        return await benchmark_runner.start(
            scenario=body.scenario,  # type: ignore[arg-type]
            users=body.users,
            spawn_rate=body.spawn_rate,
            duration_s=body.duration_s,
            baseline_model=body.baseline_model,
            max_requests=body.max_requests,
            label=body.label,
        )

    @router.post("/console/api/benchmark/stop")
    async def benchmark_stop():
        await benchmark_runner.stop()
        return await benchmark_runner.status()

    @router.get("/console/api/benchmark/status")
    async def benchmark_status():
        return await benchmark_runner.status()

    @router.post("/console/api/benchmark/save")
    async def benchmark_save(body: BenchmarkSaveRequest):
        completed = benchmark_runner.completed_runs
        if not completed:
            raise HTTPException(status_code=400, detail="no completed benchmark runs to save")
        audit_records = get_all_audit_records() if get_all_audit_records else []
        audit_since = benchmark_runner.session_started_at or 0.0
        try:
            result = save_benchmark_session(
                completed,
                label=body.label,
                audit_records=audit_records,
                audit_since=audit_since,
                tier_weights=tier_weights,
                baseline_model=benchmark_runner.baseline_model,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result

    @router.get("/console/api/routing/all")
    async def console_routing_all():
        records = get_all_audit_records() if get_all_audit_records else []
        return records

    @router.post("/console/api/reset")
    async def console_reset():
        count = clear_audit() if clear_audit else 0
        event_bus.clear()
        benchmark_runner.reset_session()
        return {"cleared_records": count}

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
