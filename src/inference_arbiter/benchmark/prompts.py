"""Shared benchmark prompts (used by console runner and Locust)."""

from __future__ import annotations

import json
import random
from pathlib import Path

_PROMPTS_PATH = Path(__file__).resolve().parents[3] / "benchmarks" / "prompts.json"

_DEFAULT = {
    "simple": [
        "What is the capital of France?",
        "How many days are in a leap year?",
        "Define photosynthesis in one sentence.",
    ],
    "medium": [
        "Summarize the causes of the French Revolution in three paragraphs.",
        "Explain the difference between TCP and UDP for a junior engineer.",
        "Describe how gradient descent works without heavy math.",
    ],
    "complex": [
        (
            "Compare the epistemological frameworks of Kant and Hegel across five dimensions. "
            "Provide step-by-step reasoning and cite conceptual tradeoffs."
        ),
        (
            "Debug this Python async snippet and prove correctness:\n```python\n"
            "async def worker():\n    await asyncio.gather(*tasks)\n```"
        ),
        "Analyze the time complexity of a nested graph traversal with dynamic programming.",
    ],
}


def _load_prompts() -> dict[str, list[str]]:
    if _PROMPTS_PATH.exists():
        data = json.loads(_PROMPTS_PATH.read_text())
        return {
            "simple": data.get("simple", _DEFAULT["simple"]),
            "medium": data.get("medium", _DEFAULT["medium"]),
            "complex": data.get("complex", _DEFAULT["complex"]),
        }
    return _DEFAULT


PROMPTS = _load_prompts()
SIMPLE_PROMPTS = PROMPTS["simple"]
MEDIUM_PROMPTS = PROMPTS["medium"]
COMPLEX_PROMPTS = PROMPTS["complex"]


def mixed_prompt() -> str:
    r = random.random()
    if r < 0.70:
        return random.choice(SIMPLE_PROMPTS)
    if r < 0.90:
        return random.choice(MEDIUM_PROMPTS)
    return random.choice(COMPLEX_PROMPTS)


def arbiter_prompt() -> tuple[str, str | None]:
    """Return (prompt, optional slo_ms)."""
    r = random.random()
    if r < 0.50:
        return (
            mixed_prompt() if random.random() < 0.3 else random.choice(SIMPLE_PROMPTS),
            None,
        )
    if r < 0.75:
        return random.choice(MEDIUM_PROMPTS), None
    if r < 0.90:
        return random.choice(COMPLEX_PROMPTS), None
    return mixed_prompt(), 800
