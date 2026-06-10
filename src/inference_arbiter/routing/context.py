"""Atomic RequestContext threaded through all subsystems."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from inference_arbiter.models import FailureAttribution, TrafficPriority, VerificationStatus


@dataclass
class Timestamps:
    arrival_epoch_ms: int
    deadline_epoch_ms: int | None = None


@dataclass
class BudgetMetrics:
    total_slo_budget_ms: int | None
    current_elapsed_ms: float = 0.0

    @property
    def remaining_ms(self) -> float | None:
        if self.total_slo_budget_ms is None:
            return None
        return max(0.0, self.total_slo_budget_ms - self.current_elapsed_ms)


@dataclass
class PayloadMeta:
    prompt_hash: str
    estimated_tokens: int
    raw_prompt_preview: str = ""

    @classmethod
    def from_messages(cls, messages: list[dict]) -> PayloadMeta:
        text_parts: list[str] = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                text_parts.append(content)
        text = "\n".join(text_parts)
        prompt_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        estimated_tokens = max(1, len(text) // 4)
        preview = text[:120] + ("..." if len(text) > 120 else "")
        return cls(
            prompt_hash=prompt_hash,
            estimated_tokens=estimated_tokens,
            raw_prompt_preview=preview,
        )


@dataclass
class TierAttempt:
    tier: str
    backend_model: str
    latency_ms: float
    ttft_ms: float | None
    verification_status: VerificationStatus
    failure_attribution: FailureAttribution = FailureAttribution.NONE
    budget_remaining_ms: float | None = None


@dataclass
class RequestContext:
    request_id: str
    priority: TrafficPriority
    timestamps: Timestamps
    metrics: BudgetMetrics
    routing_history: list[TierAttempt] = field(default_factory=list)
    payload: PayloadMeta | None = None
    bandit_scores: dict[str, float] | None = None
    failure_attribution: FailureAttribution | None = None
    requested_model: str = "auto"
    allow_degraded_ok: bool = False
    status: str = "pending"
    error: str | None = None
    final_tier: str | None = None
    final_backend_model: str | None = None
    routing_reason: str | None = None
    degraded: bool = False
    degradation_reason: str | None = None
    tiers_attempted: list[str] = field(default_factory=list)
    feature_vector: list[float] | None = None
    shadow_would_route_to: str | None = None
    response_text: str | None = None

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        priority: TrafficPriority,
        slo_deadline_ms: int | None,
        messages: list[dict],
        requested_model: str = "auto",
        allow_degraded_ok: bool = False,
    ) -> RequestContext:
        now_ms = int(time.time() * 1000)
        deadline = now_ms + slo_deadline_ms if slo_deadline_ms is not None else None
        return cls(
            request_id=request_id,
            priority=priority,
            timestamps=Timestamps(arrival_epoch_ms=now_ms, deadline_epoch_ms=deadline),
            metrics=BudgetMetrics(total_slo_budget_ms=slo_deadline_ms),
            payload=PayloadMeta.from_messages(messages),
            requested_model=requested_model,
            allow_degraded_ok=allow_degraded_ok,
        )

    def elapsed_ms(self) -> float:
        return (time.time() * 1000) - self.timestamps.arrival_epoch_ms

    def update_elapsed(self) -> None:
        self.metrics.current_elapsed_ms = self.elapsed_ms()

    def remaining_budget_ms(self) -> float | None:
        if self.timestamps.deadline_epoch_ms is None:
            return None
        return max(0.0, self.timestamps.deadline_epoch_ms - time.time() * 1000)

    def set_response_text(self, text: str | None) -> None:
        if not text:
            self.response_text = None
            return
        self.response_text = text.strip()

    @property
    def response_preview(self) -> str | None:
        return self.response_text

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "priority": self.priority.value,
            "timestamps": {
                "arrival_epoch_ms": self.timestamps.arrival_epoch_ms,
                "deadline_epoch_ms": self.timestamps.deadline_epoch_ms,
            },
            "metrics": {
                "total_slo_budget_ms": self.metrics.total_slo_budget_ms,
                "current_elapsed_ms": round(self.metrics.current_elapsed_ms, 2),
                "remaining_ms": (
                    round(self.remaining_budget_ms(), 2)
                    if self.remaining_budget_ms() is not None
                    else None
                ),
            },
            "routing_history": [
                {
                    "tier": a.tier,
                    "backend_model": a.backend_model,
                    "latency_ms": round(a.latency_ms, 2),
                    "ttft_ms": round(a.ttft_ms, 2) if a.ttft_ms is not None else None,
                    "verification_status": a.verification_status.value,
                    "failure_attribution": a.failure_attribution.value,
                    "budget_remaining_ms": (
                        round(a.budget_remaining_ms, 2) if a.budget_remaining_ms is not None else None
                    ),
                }
                for a in self.routing_history
            ],
            "payload": {
                "prompt_hash": self.payload.prompt_hash if self.payload else None,
                "estimated_tokens": self.payload.estimated_tokens if self.payload else None,
                "prompt_preview": self.payload.raw_prompt_preview if self.payload else None,
            },
            "bandit_scores": self.bandit_scores,
            "failure_attribution": (
                self.failure_attribution.value if self.failure_attribution else None
            ),
            "requested_model": self.requested_model,
            "status": self.status,
            "error": self.error,
            "final_tier": self.final_tier,
            "final_backend_model": self.final_backend_model,
            "routing_reason": self.routing_reason,
            "degraded": self.degraded,
            "degradation_reason": self.degradation_reason,
            "tiers_attempted": self.tiers_attempted,
            "shadow_would_route_to": self.shadow_would_route_to,
            "response_text": self.response_text,
            "response_preview": self.response_preview,
        }
