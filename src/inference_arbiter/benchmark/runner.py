"""Async benchmark runner for the unified console."""

from __future__ import annotations

import asyncio
import itertools
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

import httpx

from inference_arbiter.benchmark.export import save_benchmark_run_archive
from inference_arbiter.benchmark.prompts import (
    arbiter_prompt,
    mixed_prompt,
)

Scenario = Literal["baseline", "arbiter", "round_robin", "random"]

AuditGetter = Callable[[], list[dict[str, Any]]]
LiveEventsGetter = Callable[[], list[dict[str, Any]]]
MetricsFetcher = Callable[[], Awaitable[dict[str, Any]]]


@dataclass
class BenchmarkArchiveHooks:
    get_audit_records: AuditGetter
    get_live_events: LiveEventsGetter
    fetch_metrics_summary: MetricsFetcher
    fetch_metrics_timeseries: MetricsFetcher
    tier_weights: dict[str, float] = field(default_factory=dict)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(len(ordered) * pct / 100)
    idx = min(idx, len(ordered) - 1)
    return ordered[idx]


@dataclass
class BenchmarkStats:
    scenario: str = ""
    running: bool = False
    started_at: float | None = None
    duration_s: float = 0.0
    max_requests: int = 0
    users: int = 0
    spawn_rate: float = 0.0
    requests: int = 0
    failures: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    last_snapshot: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        elapsed = time.time() - self.started_at if self.started_at else 0.0
        rps = self.requests / elapsed if elapsed > 0 else 0.0
        data = {
            "scenario": self.scenario,
            "running": self.running,
            "duration_s": self.duration_s,
            "max_requests": self.max_requests,
            "elapsed_s": round(elapsed, 2),
            "users": self.users,
            "spawn_rate": self.spawn_rate,
            "requests": self.requests,
            "failures": self.failures,
            "rps": round(rps, 2),
            "p50_ms": round(_percentile(self.latencies_ms, 50), 2),
            "p95_ms": round(_percentile(self.latencies_ms, 95), 2),
            "p99_ms": round(_percentile(self.latencies_ms, 99), 2),
        }
        self.last_snapshot = data
        return data


class BenchmarkRunner:
    def __init__(self, base_url: str = "http://127.0.0.1:8080") -> None:
        self.base_url = base_url.rstrip("/")
        self.stats = BenchmarkStats()
        self._task: asyncio.Task[None] | None = None
        self._sampler_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._tier_cycle = itertools.cycle(["small", "medium", "large"])
        self._completed_runs: dict[str, dict[str, Any]] = {}
        self._baseline_model: str = "large"
        self._session_label: str = ""
        self._timeseries: list[dict[str, Any]] = []
        self._archive_hooks: BenchmarkArchiveHooks | None = None
        self._last_archive: dict[str, Any] | None = None
        self._archive_done = False

    def configure_archive(self, hooks: BenchmarkArchiveHooks) -> None:
        self._archive_hooks = hooks

    @property
    def completed_runs(self) -> dict[str, dict[str, Any]]:
        return self._completed_runs

    @property
    def last_archive(self) -> dict[str, Any] | None:
        return self._last_archive

    async def start(
        self,
        scenario: Scenario,
        users: int = 10,
        spawn_rate: float = 2.0,
        duration_s: float = 180.0,
        baseline_model: str = "large",
        max_requests: int = 0,
        label: str = "",
    ) -> dict[str, Any]:
        if self.stats.running:
            return {"error": "benchmark already running", "status": self.stats.snapshot()}
        self._stop = asyncio.Event()
        self._baseline_model = baseline_model
        self._session_label = label.strip()
        self._timeseries = []
        self._last_archive = None
        self._archive_done = False
        self.stats = BenchmarkStats(
            scenario=scenario,
            running=True,
            started_at=time.time(),
            duration_s=duration_s,
            max_requests=max_requests,
            users=users,
            spawn_rate=spawn_rate,
        )
        self._task = asyncio.create_task(self._run(scenario, users, spawn_rate, duration_s, max_requests))
        return self.stats.snapshot()

    async def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=60.0)
            except asyncio.TimeoutError:
                self._task.cancel()
        snap = self.stats.snapshot()
        if self.stats.scenario:
            self._completed_runs[self.stats.scenario] = snap
        if not self._archive_done and snap.get("requests", 0) > 0:
            await self._archive_run(snap, self.stats.started_at or time.time())
        self.stats.running = False
        return snap

    def stop_nowait(self) -> dict[str, Any]:
        self._stop.set()
        snap = self.stats.snapshot()
        if self.stats.scenario:
            self._completed_runs[self.stats.scenario] = snap
        return snap

    async def status(self) -> dict[str, Any]:
        data = self.stats.snapshot()
        data["completed_runs"] = self._completed_runs
        data["last_archive"] = self._last_archive
        return data

    async def _sample_timeseries(self, deadline: float) -> None:
        while not self._stop.is_set() and time.time() < deadline:
            snap = self.stats.snapshot()
            self._timeseries.append(
                {
                    "elapsed_s": snap["elapsed_s"],
                    "requests": snap["requests"],
                    "failures": snap["failures"],
                    "rps": snap["rps"],
                    "p50_ms": snap["p50_ms"],
                    "p95_ms": snap["p95_ms"],
                    "p99_ms": snap["p99_ms"],
                }
            )
            await asyncio.sleep(1.0)

    async def _run(
        self,
        scenario: Scenario,
        users: int,
        spawn_rate: float,
        duration_s: float,
        max_requests: int = 0,
    ) -> None:
        deadline = time.time() + duration_s
        workers: list[asyncio.Task[None]] = []
        spawned = 0
        run_started_at = self.stats.started_at or time.time()
        try:
            self._sampler_task = asyncio.create_task(self._sample_timeseries(deadline))
            async with httpx.AsyncClient(timeout=300.0) as client:
                while spawned < users and time.time() < deadline and not self._stop.is_set():
                    workers.append(
                        asyncio.create_task(self._worker(client, scenario, deadline))
                    )
                    spawned += 1
                    await asyncio.sleep(1.0 / max(spawn_rate, 0.1))

                while time.time() < deadline and not self._stop.is_set():
                    if max_requests > 0 and self.stats.requests >= max_requests:
                        self._stop.set()
                        break
                    await asyncio.sleep(0.5)

                self._stop.set()
                if workers:
                    await asyncio.gather(*workers, return_exceptions=True)
        finally:
            if self._sampler_task:
                self._sampler_task.cancel()
                try:
                    await self._sampler_task
                except asyncio.CancelledError:
                    pass
            snap = self.stats.snapshot()
            if self.stats.scenario:
                self._completed_runs[self.stats.scenario] = snap
            await self._archive_run(snap, run_started_at)
            self.stats.running = False

    async def _worker(
        self,
        client: httpx.AsyncClient,
        scenario: Scenario,
        deadline: float,
    ) -> None:
        while time.time() < deadline and not self._stop.is_set():
            payload = self._build_payload(scenario)
            start = time.perf_counter()
            try:
                resp = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                )
                latency_ms = (time.perf_counter() - start) * 1000
                self.stats.requests += 1
                if len(self.stats.latencies_ms) < 5000:
                    self.stats.latencies_ms.append(latency_ms)
                if resp.status_code >= 500:
                    self.stats.failures += 1
            except Exception:
                self.stats.requests += 1
                self.stats.failures += 1
            await asyncio.sleep(random.uniform(0.1, 0.5))

    def _build_payload(self, scenario: Scenario) -> dict[str, Any]:
        if scenario == "baseline":
            return {
                "model": self._baseline_model,
                "messages": [{"role": "user", "content": mixed_prompt()}],
                "max_tokens": 64,
            }
        if scenario == "round_robin":
            return {
                "model": next(self._tier_cycle),
                "messages": [{"role": "user", "content": mixed_prompt()}],
                "max_tokens": 64,
            }
        if scenario == "random":
            return {
                "model": random.choice(["small", "medium", "large"]),
                "messages": [{"role": "user", "content": mixed_prompt()}],
                "max_tokens": 64,
            }
        prompt, slo_ms = arbiter_prompt()
        payload: dict[str, Any] = {
            "model": "auto",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 64,
            "x_priority": random.choice(["standard", "standard", "batch", "critical"]),
        }
        if slo_ms:
            payload["x_slo_deadline_ms"] = slo_ms
        return payload

    async def _archive_run(self, snap: dict[str, Any], run_started_at: float) -> None:
        if self._archive_done:
            return
        if not snap.get("scenario") or snap.get("requests", 0) == 0:
            return
        if self._archive_hooks is None:
            return
        self._archive_done = True
        hooks = self._archive_hooks
        try:
            self._last_archive = await save_benchmark_run_archive(
                snap,
                label=self._session_label,
                baseline_model=self._baseline_model,
                run_started_at=run_started_at,
                timeseries=list(self._timeseries),
                latencies_ms=list(self.stats.latencies_ms),
                get_audit_records=hooks.get_audit_records,
                get_live_events=hooks.get_live_events,
                fetch_metrics_summary=hooks.fetch_metrics_summary,
                fetch_metrics_timeseries=hooks.fetch_metrics_timeseries,
                completed_runs=dict(self._completed_runs),
                tier_weights=hooks.tier_weights or None,
            )
        except Exception:
            self._last_archive = {"error": "archive_failed", "scenario": snap.get("scenario")}
