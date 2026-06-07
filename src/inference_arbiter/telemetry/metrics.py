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
    "Requests where SLO could not be met",
    ["tier", "reason"],
)

SLO_EVALUATED = Counter(
    "slo_evaluated_total",
    "Requests with SLO deadline evaluated",
    ["priority"],
)

SLO_MET = Counter(
    "slo_met_total",
    "Requests completed within SLO deadline",
    ["priority"],
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

CIRCUIT_BREAKER_STATE = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state per endpoint (0=closed,1=half_open,2=open)",
    ["endpoint"],
)

BATCH_SHED = Counter("batch_shed_total", "Batch requests shed under pressure")

DEGRADED_MODE = Counter(
    "degraded_mode_total",
    "Requests served in degraded mode",
    ["reason"],
)

TIME_TO_FIRST_TOKEN = Histogram(
    "time_to_first_token_seconds",
    "Time to first token",
    ["tier"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

REQUEST_LATENCY = Histogram(
    "request_latency_seconds",
    "End-to-end request latency at the gateway",
    ["tier", "complexity"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

CLASSIFIER_CONFIDENCE = Histogram(
    "classifier_confidence",
    "Heuristic/bandit confidence scores",
    ["complexity"],
    buckets=(0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.99, 1.0),
)

BANDIT_POLICY_ACTIVE = Gauge(
    "bandit_policy_active",
    "Whether learned bandit policy is active (1) or heuristic (0)",
)

BANDIT_OBSERVATIONS = Counter(
    "bandit_observations_total",
    "Bandit update observations",
    ["tier"],
)

BANDIT_REWARD_EMA = Gauge(
    "bandit_reward_ema",
    "Exponential moving average of bandit reward",
    ["tier"],
)

COST_PROXY = Counter(
    "cost_proxy_total",
    "Tier-weighted token cost proxy",
    ["tier"],
)

VERIFICATION_PASS_RATE = Gauge(
    "verification_pass_rate",
    "Rolling verification pass rate per tier",
    ["tier"],
)


_reward_ema: dict[str, float] = {}


def record_routing(*, tier: str, reason: str, priority: str, complexity: str | None, confidence: float | None) -> None:
    REQUESTS_ROUTED.labels(tier=tier, policy=reason, priority=priority).inc()
    ROUTING_DECISIONS.labels(reason=reason).inc()
    if complexity and confidence is not None:
        CLASSIFIER_CONFIDENCE.labels(complexity=complexity).observe(confidence)


def record_slo_breach(tier: str, reason: str) -> None:
    SLO_BREACH.labels(tier=tier, reason=reason).inc()


def record_slo_outcome(*, met: bool, priority: str) -> None:
    SLO_EVALUATED.labels(priority=priority).inc()
    if met:
        SLO_MET.labels(priority=priority).inc()


def record_batch_shed() -> None:
    BATCH_SHED.inc()


def record_degraded(reason: str) -> None:
    DEGRADED_MODE.labels(reason=reason).inc()


def record_bandit_update(tier: str, reward: float) -> None:
    BANDIT_OBSERVATIONS.labels(tier=tier).inc()
    prev = _reward_ema.get(tier, reward)
    _reward_ema[tier] = 0.2 * reward + 0.8 * prev
    BANDIT_REWARD_EMA.labels(tier=tier).set(_reward_ema[tier])


def sync_endpoint_gauges(registry) -> None:
    from inference_arbiter.models import CircuitBreakerState

    state_map = {
        CircuitBreakerState.CLOSED: 0,
        CircuitBreakerState.HALF_OPEN: 1,
        CircuitBreakerState.OPEN: 2,
    }
    for state in registry.all_states():
        name = state.config.name
        ENDPOINT_QUEUE_DEPTH.labels(endpoint=name).set(state.queue_depth_estimate)
        ENDPOINT_IN_FLIGHT.labels(endpoint=name).set(state.in_flight)
        CIRCUIT_BREAKER_STATE.labels(endpoint=name).set(state_map[state.circuit_breaker])
        VERIFICATION_PASS_RATE.labels(tier=state.config.tier.value).set(
            state.verification_pass_rate
        )


def record_latency(tier: str, complexity: str | None, seconds: float) -> None:
    REQUEST_LATENCY.labels(tier=tier, complexity=complexity or "unknown").observe(seconds)


def record_ttft(tier: str, seconds: float) -> None:
    TIME_TO_FIRST_TOKEN.labels(tier=tier).observe(seconds)


def record_cost_proxy(tier: str, cost: float) -> None:
    COST_PROXY.labels(tier=tier).inc(cost)


def sync_bandit_gauges(bandit) -> None:
    BANDIT_POLICY_ACTIVE.set(1.0 if bandit.policy_active else 0.0)
