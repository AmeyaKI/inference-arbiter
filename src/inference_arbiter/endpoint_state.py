"""Per-endpoint local state: in-flight, EMA latency, circuit breaker."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from inference_arbiter.config import EndpointConfig, Settings
from inference_arbiter.models import CircuitBreakerState


@dataclass
class EndpointState:
    config: EndpointConfig
    in_flight: int = 0
    latency_ema_ms: float = 0.0
    circuit_breaker: CircuitBreakerState = CircuitBreakerState.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None
    _initialized_ema: bool = False

    @property
    def queue_depth_estimate(self) -> int:
        return max(self.in_flight, 0)

    def eta_ms(self) -> float:
        base = self.config.base_latency_ms
        if self.latency_ema_ms > 0:
            per_slot = self.latency_ema_ms
        else:
            per_slot = base
        return self.queue_depth_estimate * per_slot + base

    def is_available(self, settings: Settings, now: float | None = None) -> bool:
        ts = now if now is not None else time.monotonic()
        if self.circuit_breaker == CircuitBreakerState.OPEN:
            if self.opened_at is None:
                return False
            if ts - self.opened_at >= settings.circuit_recovery_timeout_s:
                self.circuit_breaker = CircuitBreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_dispatch(self) -> None:
        self.in_flight += 1

    def record_success(self, latency_ms: float, alpha: float) -> None:
        self.in_flight = max(0, self.in_flight - 1)
        self.consecutive_failures = 0
        if not self._initialized_ema:
            self.latency_ema_ms = latency_ms
            self._initialized_ema = True
        else:
            self.latency_ema_ms = alpha * latency_ms + (1 - alpha) * self.latency_ema_ms
        if self.circuit_breaker == CircuitBreakerState.HALF_OPEN:
            self.circuit_breaker = CircuitBreakerState.CLOSED
            self.opened_at = None

    def record_failure(self, settings: Settings, now: float | None = None) -> None:
        ts = now if now is not None else time.monotonic()
        self.in_flight = max(0, self.in_flight - 1)
        self.consecutive_failures += 1
        if self.consecutive_failures >= settings.circuit_failure_threshold:
            self.circuit_breaker = CircuitBreakerState.OPEN
            self.opened_at = ts

    def under_pressure(self, threshold: int) -> bool:
        return self.queue_depth_estimate >= threshold


class EndpointRegistry:
    def __init__(self, endpoints: list[EndpointConfig], settings: Settings) -> None:
        self.settings = settings
        self._states: dict[str, EndpointState] = {
            ep.name: EndpointState(config=ep) for ep in endpoints
        }
        self._by_tier = {ep.tier: ep.name for ep in endpoints}
        self.endpoints = endpoints

    def get(self, name: str) -> EndpointState:
        return self._states[name]

    def by_tier(self, tier) -> EndpointState:
        return self._states[self._by_tier[tier]]

    def all_states(self) -> list[EndpointState]:
        return list(self._states.values())

    def any_under_pressure(self) -> bool:
        t = self.settings.queue_pressure_threshold
        return any(s.under_pressure(t) for s in self.all_states())

    def tier_names_ordered_fastest_first(self) -> list[str]:
        from inference_arbiter.models import ModelTier, tier_order

        return [
            self._by_tier[t]
            for t in sorted(ModelTier, key=tier_order)
        ]
