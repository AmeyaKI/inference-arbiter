import time

from inference_arbiter.config import Settings, build_default_endpoints
from inference_arbiter.endpoint_state import EndpointRegistry
from inference_arbiter.models import CircuitBreakerState


def test_ema_and_in_flight():
    settings = Settings()
    registry = EndpointRegistry(build_default_endpoints(settings), settings)
    state = registry.get("small")
    state.record_dispatch()
    assert state.in_flight == 1
    state.record_success(100.0, alpha=0.5)
    assert state.in_flight == 0
    assert state.latency_ema_ms == 100.0
    state.record_success(200.0, alpha=0.5)
    assert state.latency_ema_ms == 150.0


def test_circuit_breaker_opens():
    settings = Settings(circuit_failure_threshold=2)
    registry = EndpointRegistry(build_default_endpoints(settings), settings)
    state = registry.get("medium")
    state.record_dispatch()
    state.record_failure(settings)
    state.record_dispatch()
    state.record_failure(settings)
    assert state.circuit_breaker == CircuitBreakerState.OPEN


def test_circuit_recovery_half_open():
    settings = Settings(circuit_failure_threshold=1, circuit_recovery_timeout_s=0.01)
    registry = EndpointRegistry(build_default_endpoints(settings), settings)
    state = registry.get("large")
    state.record_failure(settings, now=time.monotonic())
    assert state.circuit_breaker == CircuitBreakerState.OPEN
    time.sleep(0.02)
    assert state.is_available(settings)
