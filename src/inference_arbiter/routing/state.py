"""Per-endpoint local state: in-flight, EMAs, circuit breaker, P95 window."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from inference_arbiter.config import EndpointConfig, Settings
from inference_arbiter.models import CircuitBreakerState, ModelTier, tier_order


@dataclass
class EndpointState:
    config: EndpointConfig
    in_flight: int = 0
    latency_ema_ms: float = 0.0
    ttft_ema_ms: float = 0.0
    error_rate_ema: float = 0.0
    verification_pass_rate: float = 1.0
    circuit_breaker: CircuitBreakerState = CircuitBreakerState.CLOSED
    consecutive_failures: int = 0
    verification_attempts: int = 0
    verification_passes: int = 0
    opened_at: float | None = None
    _initialized_latency_ema: bool = False
    _initialized_ttft_ema: bool = False
    _latency_samples: deque[float] = field(default_factory=deque)

    @property
    def queue_depth_estimate(self) -> int:
        return max(self.in_flight, 0)

    @property
    def p95_latency_ms(self) -> float:
        if not self._latency_samples:
            return self.latency_ema_ms or self.config.base_latency_ms
        sorted_samples = sorted(self._latency_samples)
        idx = int(0.95 * (len(sorted_samples) - 1))
        return sorted_samples[idx]

    def estimated_ttft_ms(self) -> float:
        if self.ttft_ema_ms > 0:
            return self.ttft_ema_ms
        if self.latency_ema_ms > 0:
            return self.latency_ema_ms * 0.3
        return self.config.base_latency_ms * 0.3

    def eta_ms(self) -> float:
        base = self.config.base_latency_ms
        per_slot = self.latency_ema_ms if self.latency_ema_ms > 0 else base
        error_penalty = self.error_rate_ema * 500.0
        return self.queue_depth_estimate * per_slot + base + error_penalty

    def is_saturated(self, settings: Settings) -> bool:
        if self.in_flight >= self.config.max_concurrency:
            return True
        if self.p95_latency_ms > settings.p95_spike_threshold_ms:
            return True
        return self.under_pressure(settings.queue_pressure_threshold)

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

    def _record_latency_sample(self, latency_ms: float, settings: Settings) -> None:
        self._latency_samples.append(latency_ms)
        max_size = settings.p95_window_size
        while len(self._latency_samples) > max_size:
            self._latency_samples.popleft()

    def record_success(
        self,
        latency_ms: float,
        settings: Settings | None = None,
        *,
        alpha: float | None = None,
        ttft_ms: float | None = None,
        verification_passed: bool | None = None,
    ) -> None:
        if settings is None:
            from inference_arbiter.config import get_settings

            settings = get_settings()
        ema_alpha = alpha if alpha is not None else settings.latency_ema_alpha
        ttft_alpha = settings.ttft_ema_alpha
        self.in_flight = max(0, self.in_flight - 1)
        self.consecutive_failures = 0
        self._record_latency_sample(latency_ms, settings)

        if not self._initialized_latency_ema:
            self.latency_ema_ms = latency_ms
            self._initialized_latency_ema = True
        else:
            self.latency_ema_ms = ema_alpha * latency_ms + (1 - ema_alpha) * self.latency_ema_ms

        if ttft_ms is not None:
            if not self._initialized_ttft_ema:
                self.ttft_ema_ms = ttft_ms
                self._initialized_ttft_ema = True
            else:
                self.ttft_ema_ms = ttft_alpha * ttft_ms + (1 - ttft_alpha) * self.ttft_ema_ms

        if verification_passed is not None:
            self.verification_attempts += 1
            if verification_passed:
                self.verification_passes += 1
            if self.verification_attempts > 0:
                self.verification_pass_rate = self.verification_passes / self.verification_attempts

        if self.circuit_breaker == CircuitBreakerState.HALF_OPEN:
            self.circuit_breaker = CircuitBreakerState.CLOSED
            self.opened_at = None

        self._update_error_rate(success=True, settings=settings)

    def record_failure(self, settings: Settings, now: float | None = None) -> None:
        ts = now if now is not None else time.monotonic()
        self.in_flight = max(0, self.in_flight - 1)
        self.consecutive_failures += 1
        self._update_error_rate(success=False, settings=settings)
        if (
            self.consecutive_failures >= settings.circuit_failure_threshold
            or self.error_rate_ema >= settings.circuit_error_rate_threshold
        ):
            self.circuit_breaker = CircuitBreakerState.OPEN
            self.opened_at = ts

    def _update_error_rate(self, *, success: bool, settings: Settings) -> None:
        alpha = settings.error_rate_ema_alpha
        observation = 0.0 if success else 1.0
        self.error_rate_ema = alpha * observation + (1 - alpha) * self.error_rate_ema

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

    def by_tier(self, tier: ModelTier) -> EndpointState:
        return self._states[self._by_tier[tier]]

    def all_states(self) -> list[EndpointState]:
        return list(self._states.values())

    def any_under_pressure(self) -> bool:
        t = self.settings.queue_pressure_threshold
        return any(s.under_pressure(t) for s in self.all_states())

    def any_saturated(self) -> bool:
        return any(s.is_saturated(self.settings) for s in self.all_states())

    def saturated_tiers(self) -> set[ModelTier]:
        return {s.config.tier for s in self.all_states() if s.is_saturated(self.settings)}

    def tier_names_ordered_fastest_first(self) -> list[str]:
        return [self._by_tier[t] for t in sorted(ModelTier, key=tier_order)]

    def viable_tiers(self, allowed: set[ModelTier] | None = None) -> list[ModelTier]:
        tiers = sorted(ModelTier, key=tier_order)
        result: list[ModelTier] = []
        for tier in tiers:
            if allowed is not None and tier not in allowed:
                continue
            state = self.by_tier(tier)
            if state.is_available(self.settings) and state.in_flight < state.config.max_concurrency:
                result.append(tier)
        return result
