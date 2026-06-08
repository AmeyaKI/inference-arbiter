"""Shared domain types."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ComplexityLabel(StrEnum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class ModelTier(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class TrafficPriority(StrEnum):
    INTERACTIVE = "interactive"
    BATCH = "batch"


class Priority(StrEnum):
    """Legacy priority values; mapped to TrafficPriority at admission."""

    CRITICAL = "critical"
    STANDARD = "standard"
    BATCH = "batch"
    INTERACTIVE = "interactive"


class RoutingMode(StrEnum):
    ACTIVE = "active"
    SHADOW = "shadow"
    LEGACY = "legacy"


class RoutingReason(StrEnum):
    COMPLEXITY = "complexity"
    PINNED_MODEL = "pinned_model"
    SLO_PRESSURE = "slo_pressure"
    CIRCUIT_BREAKER = "circuit_breaker"
    SHADOW = "shadow"
    FALLBACK = "fallback"
    DEFAULT = "default"
    BANDIT_POLICY = "bandit_policy"
    SLO_FORCED_ESCALATION = "slo_forced_escalation"
    SLO_SHED = "slo_shed"


class DegradationReason(StrEnum):
    ENDPOINT_SATURATED = "ENDPOINT_SATURATED"
    MODEL_CAPACITY = "MODEL_CAPACITY"
    DEADLINE_TOO_TIGHT = "DEADLINE_TOO_TIGHT"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    BATCH_SHED = "BATCH_SHED"


class CircuitBreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class FailureAttribution(StrEnum):
    QUALITY_FAILURE = "QUALITY_FAILURE"
    LATENCY_FAILURE = "LATENCY_FAILURE"
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
    NONE = "NONE"


class VerificationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED_INVALID_JSON = "FAILED_INVALID_JSON"
    FAILED_LENGTH = "FAILED_LENGTH"
    FAILED_TOOL_CALL = "FAILED_TOOL_CALL"
    FAILED_EMPTY = "FAILED_EMPTY"
    SKIPPED = "SKIPPED"


def traffic_priority_from_legacy(priority: Priority) -> TrafficPriority:
    if priority == Priority.BATCH:
        return TrafficPriority.BATCH
    return TrafficPriority.INTERACTIVE


def tier_order(tier: ModelTier) -> int:
    return {ModelTier.SMALL: 0, ModelTier.MEDIUM: 1, ModelTier.LARGE: 2}[tier]


def complexity_to_tier(label: ComplexityLabel) -> ModelTier:
    return {
        ComplexityLabel.SIMPLE: ModelTier.SMALL,
        ComplexityLabel.MEDIUM: ModelTier.MEDIUM,
        ComplexityLabel.COMPLEX: ModelTier.LARGE,
    }[label]


def snapshot_state(state: Any) -> dict[str, Any]:
    return {
        "in_flight": state.in_flight,
        "latency_ema_ms": round(state.latency_ema_ms, 2),
        "ttft_ema_ms": round(getattr(state, "ttft_ema_ms", 0.0), 2),
        "error_rate_ema": round(getattr(state, "error_rate_ema", 0.0), 4),
        "verification_pass_rate": round(getattr(state, "verification_pass_rate", 1.0), 4),
        "p95_latency_ms": round(getattr(state, "p95_latency_ms", 0.0), 2),
        "queue_depth_estimate": state.queue_depth_estimate,
        "circuit_breaker": state.circuit_breaker.value,
        "consecutive_failures": state.consecutive_failures,
    }
