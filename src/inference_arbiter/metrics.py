"""Prometheus metrics for routing observability."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

REQUESTS_ROUTED = Counter(
    "requests_routed_total",
    "Total requests routed by the gateway",
    ["tier", "policy", "priority"],
)

ROUTING_DECISIONS = Counter(
    "routing_decision_total",
    "Routing decisions by reason",
    ["reason"],
)

SLO_BREACH = Counter(
    "slo_breach_total",
    "Requests where no endpoint could meet the SLO deadline",
    ["tier", "reason"],
)

ENDPOINT_QUEUE_DEPTH = Gauge(
    "endpoint_queue_depth",
    "Estimated queue depth per endpoint",
    ["endpoint"],
)

ENDPOINT_IN_FLIGHT = Gauge(
    "endpoint_in_flight",
    "In-flight requests per endpoint",
    ["endpoint"],
)

REQUEST_LATENCY = Histogram(
    "request_latency_seconds",
    "End-to-end request latency at the gateway",
    ["tier", "complexity"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

CLASSIFIER_CONFIDENCE = Histogram(
    "classifier_confidence",
    "Classifier confidence scores",
    ["complexity"],
    buckets=(0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.99, 1.0),
)


def record_routing(decision, priority: str) -> None:
    REQUESTS_ROUTED.labels(
        tier=decision.tier.value,
        policy=decision.routing_reason.value,
        priority=priority,
    ).inc()
    ROUTING_DECISIONS.labels(reason=decision.routing_reason.value).inc()
    if decision.complexity and decision.complexity_confidence is not None:
        CLASSIFIER_CONFIDENCE.labels(complexity=decision.complexity.value).observe(
            decision.complexity_confidence
        )


def record_slo_breach(tier: str, reason: str) -> None:
    SLO_BREACH.labels(tier=tier, reason=reason).inc()


def sync_endpoint_gauges(registry) -> None:
    for state in registry.all_states():
        ENDPOINT_QUEUE_DEPTH.labels(endpoint=state.config.name).set(state.queue_depth_estimate)
        ENDPOINT_IN_FLIGHT.labels(endpoint=state.config.name).set(state.in_flight)


def record_latency(tier: str, complexity: str | None, seconds: float) -> None:
    REQUEST_LATENCY.labels(tier=tier, complexity=complexity or "unknown").observe(seconds)
