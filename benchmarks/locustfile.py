"""Locust load test for inference-arbiter.

Run:
  locust -f benchmarks/locustfile.py --host http://127.0.0.1:8080

Scenarios (set ARBITER_SCENARIO env on gateway or use model pin in tasks):
  - baseline: pin model=large on all requests (configure gateway ROUTING_MODE or client model)
  - arbiter: model=auto (default)
"""

from __future__ import annotations

import random

from locust import HttpUser, between, task

SIMPLE_PROMPTS = [
    "What is the capital of France?",
    "How many days are in a leap year?",
    "Define photosynthesis in one sentence.",
]

MEDIUM_PROMPTS = [
    "Summarize the causes of the French Revolution in three paragraphs.",
    "Explain the difference between TCP and UDP for a junior engineer.",
    "Describe how gradient descent works without heavy math.",
]

COMPLEX_PROMPTS = [
    (
        "Compare the epistemological frameworks of Kant and Hegel across five dimensions. "
        "Provide step-by-step reasoning and cite conceptual tradeoffs."
    ),
    (
        "Debug this Python async snippet and prove correctness:\n```python\n"
        "async def worker():\n    await asyncio.gather(*tasks)\n```"
    ),
    "Analyze the time complexity of a nested graph traversal with dynamic programming.",
]


def mixed_prompt() -> str:
    r = random.random()
    if r < 0.70:
        return random.choice(SIMPLE_PROMPTS)
    if r < 0.90:
        return random.choice(MEDIUM_PROMPTS)
    return random.choice(COMPLEX_PROMPTS)


class ArbiterUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(7)
    def simple_auto(self):
        self._chat(mixed_prompt() if random.random() < 0.3 else random.choice(SIMPLE_PROMPTS), "auto")

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


class BaselineUser(HttpUser):
    """Pin all traffic to large tier — run with: locust -f benchmarks/locustfile.py BaselineUser"""

    wait_time = between(0.1, 0.5)

    @task
    def all_large(self):
        payload = {
            "model": "large",
            "messages": [{"role": "user", "content": mixed_prompt()}],
            "max_tokens": 64,
        }
        self.client.post("/v1/chat/completions", json=payload, timeout=120)
