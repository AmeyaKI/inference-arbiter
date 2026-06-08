"""Async benchmark runner for the unified console."""

from __future__ import annotations

import asyncio
import itertools
import random
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from inference_arbiter.benchmark.prompts import (
    COMPLEX_PROMPTS,
    MEDIUM_PROMPTS,
    SIMPLE_PROMPTS,
    arbiter_prompt,
    mixed_prompt,
)

Scenario = Literal["baseline", "arbiter", "round_robin"]


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
        self._stop = asyncio.Event()
        self._tier_cycle = itertools.cycle(["small", "medium", "large"])
        self._completed_runs: dict[str, dict[str, Any]] = {}

    @property
    def completed_runs(self) -> dict[str, dict[str, Any]]:
        return self._completed_runs

    async def start(
        self,
        scenario: Scenario,
        users: int = 10,
        spawn_rate: float = 2.0,
        duration_s: float = 180.0,
    ) -> dict[str, Any]:
        if self.stats.running:
            return {"error": "benchmark already running", "status": self.stats.snapshot()}
        self._stop = asyncio.Event()
        self.stats = BenchmarkStats(
            scenario=scenario,
            running=True,
            started_at=time.time(),
            duration_s=duration_s,
            users=users,
            spawn_rate=spawn_rate,
        )
        self._task = asyncio.create_task(self._run(scenario, users, spawn_rate, duration_s))
        return self.stats.snapshot()

    async def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=30.0)
            except asyncio.TimeoutError:
                self._task.cancel()
        self.stats.running = False
        snap = self.stats.snapshot()
        if self.stats.scenario:
            self._completed_runs[self.stats.scenario] = snap
        return snap

    async def status(self) -> dict[str, Any]:
        data = self.stats.snapshot()
        data["completed_runs"] = self._completed_runs
        return data

    async def _run(
        self,
        scenario: Scenario,
        users: int,
        spawn_rate: float,
        duration_s: float,
    ) -> None:
        deadline = time.time() + duration_s
        workers: list[asyncio.Task[None]] = []
        spawned = 0
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                while spawned < users and time.time() < deadline and not self._stop.is_set():
                    workers.append(
                        asyncio.create_task(self._worker(client, scenario, deadline))
                    )
                    spawned += 1
                    await asyncio.sleep(1.0 / max(spawn_rate, 0.1))

                while time.time() < deadline and not self._stop.is_set():
                    await asyncio.sleep(0.5)

                self._stop.set()
                if workers:
                    await asyncio.gather(*workers, return_exceptions=True)
        finally:
            self.stats.running = False
            snap = self.stats.snapshot()
            if self.stats.scenario:
                self._completed_runs[self.stats.scenario] = snap

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
                "model": "large",
                "messages": [{"role": "user", "content": mixed_prompt()}],
                "max_tokens": 64,
            }
        if scenario == "round_robin":
            return {
                "model": next(self._tier_cycle),
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
