from inference_arbiter.models import (
    ComplexityLabel,
    DegradationReason,
    ModelTier,
    RoutingReason,
)
from inference_arbiter.router import RoutingEngine


def _msgs(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def test_auto_routes_simple_to_small(router: RoutingEngine):
    decision = router.route(
        request_id="r1",
        requested_model="auto",
        messages=_msgs("What is 2+2?"),
        slo_deadline_ms=None,
    )
    assert decision.tier == ModelTier.SMALL
    assert decision.routing_reason == RoutingReason.COMPLEXITY


def test_pin_large_model(router: RoutingEngine):
    decision = router.route(
        request_id="r2",
        requested_model="large",
        messages=_msgs("hi"),
        slo_deadline_ms=None,
    )
    assert decision.tier == ModelTier.LARGE
    assert decision.routing_reason == RoutingReason.PINNED_MODEL


def test_slo_downgrade(registry, settings):
    from inference_arbiter.classifier import HeuristicComplexityClassifier

    large = registry.by_tier(ModelTier.LARGE)
    large.in_flight = 5
    large.latency_ema_ms = 2000
    router = RoutingEngine(registry, HeuristicComplexityClassifier(), settings)
    decision = router.route(
        request_id="r3",
        requested_model="auto",
        messages=_msgs("Compare Kant and Hegel across five dimensions with proofs."),
        slo_deadline_ms=500,
    )
    assert decision.tier != ModelTier.LARGE or decision.degraded


def test_circuit_breaker_fallback(registry, settings):
    import time

    from inference_arbiter.classifier import HeuristicComplexityClassifier
    from inference_arbiter.models import CircuitBreakerState

    large = registry.by_tier(ModelTier.LARGE)
    large.circuit_breaker = CircuitBreakerState.OPEN
    large.opened_at = time.monotonic()
    router = RoutingEngine(registry, HeuristicComplexityClassifier(), settings)
    decision = router.route(
        request_id="r4",
        requested_model="large",
        messages=_msgs("Explain quantum field theory in detail."),
        slo_deadline_ms=None,
    )
    assert decision.tier != ModelTier.LARGE
    assert decision.degraded
    assert decision.degradation_reason == DegradationReason.CIRCUIT_OPEN


def test_auto_degraded_ok_suppresses_flag(router: RoutingEngine):
    decision = router.route(
        request_id="r5",
        requested_model="auto-degraded-ok",
        messages=_msgs("Compare and analyze multiple datasets step by step."),
        slo_deadline_ms=1,
        allow_degraded_ok=True,
    )
    assert decision.allow_degraded_ok
    assert decision.degraded is False
