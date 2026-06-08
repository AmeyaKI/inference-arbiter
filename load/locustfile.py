"""Locust load test for inference-arbiter.

For interactive benchmarking, use the unified console instead:

  ./arbiter         # or: make dev
  open http://localhost:8080/console

Headless CI / advanced usage:

  make bench SCENARIO=baseline USERS=10 DURATION=3m
  make bench SCENARIO=arbiter
  make bench SCENARIO=round_robin
"""

from __future__ import annotations

import itertools
import random

from locust import HttpUser, between, task

from inference_arbiter.benchmark.prompts import (
    COMPLEX_PROMPTS,
    MEDIUM_PROMPTS,
    SIMPLE_PROMPTS,
    mixed_prompt,
)

__all__ = [
    "ArbiterUser",
    "BaselineUser",
    "RoundRobinUser",
    "mixed_prompt",
]


class ArbiterUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(7)
    def simple_auto(self):
        self._chat(
            mixed_prompt() if random.random() < 0.3 else random.choice(SIMPLE_PROMPTS),
            "auto",
        )

    @task(2)
    def medium_auto(self):
        self._chat(random.choice(MEDIUM_PROMPTS), "auto")

    @task(1)
    def complex_auto(self):
        self._chat(random.choice(COMPLEX_PROMPTS), "auto")

    @task(1)
    def slo_pressure(self):
        self._chat(mixed_prompt(), "auto", slo_ms=800)

    def _chat(self, content: str, model: str, slo_ms: int | None = None):
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 64,
            "x_priority": random.choice(["standard", "standard", "batch", "critical"]),
        }
        if slo_ms:
            payload["x_slo_deadline_ms"] = slo_ms
        with self.client.post(
            "/v1/chat/completions",
            json=payload,
            catch_response=True,
            timeout=120,
        ) as resp:
            if resp.status_code >= 500:
                resp.failure(f"status {resp.status_code}")


class RoundRobinUser(HttpUser):
    wait_time = between(0.1, 0.5)
    _tier_cycle = itertools.cycle(["small", "medium", "large"])

    @task
    def round_robin(self):
        tier = next(self._tier_cycle)
        payload = {
            "model": tier,
            "messages": [{"role": "user", "content": mixed_prompt()}],
            "max_tokens": 64,
        }
        with self.client.post(
            "/v1/chat/completions",
            json=payload,
            catch_response=True,
            timeout=120,
        ) as resp:
            if resp.status_code >= 500:
                resp.failure(f"status {resp.status_code}")


class BaselineUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task
    def all_large(self):
        payload = {
            "model": "large",
            "messages": [{"role": "user", "content": mixed_prompt()}],
            "max_tokens": 64,
        }
        with self.client.post(
            "/v1/chat/completions",
            json=payload,
            catch_response=True,
            timeout=120,
        ) as resp:
            if resp.status_code >= 500:
                resp.failure(f"status {resp.status_code}")
