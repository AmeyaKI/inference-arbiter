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


class Priority(StrEnum):
    CRITICAL = "critical"
    STANDARD = "standard"
    BATCH = "batch"


class RoutingMode(StrEnum):
    ACTIVE = "active"
    SHADOW = "shadow"


class RoutingReason(StrEnum):
    COMPLEXITY = "complexity"
    PINNED_MODEL = "pinned_model"
    SLO_PRESSURE = "slo_pressure"
    CIRCUIT_BREAKER = "circuit_breaker"
    SHADOW = "shadow"
    FALLBACK = "fallback"
    DEFAULT = "default"


class DegradationReason(StrEnum):
    ENDPOINT_SATURATED = "ENDPOINT_SATURATED"
    MODEL_CAPACITY = "MODEL_CAPACITY"
    DEADLINE_TOO_TIGHT = "DEADLINE_TOO_TIGHT"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"


class CircuitBreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def tier_order(tier: ModelTier) -> int:
    """Lower order = faster/smaller tier."""
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
        "queue_depth_estimate": state.queue_depth_estimate,
        "circuit_breaker": state.circuit_breaker.value,
        "consecutive_failures": state.consecutive_failures,
    }
