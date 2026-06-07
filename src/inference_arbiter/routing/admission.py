"""Admission control and traffic shedding (Subsystem A)."""

from __future__ import annotations

from dataclasses import dataclass

from inference_arbiter.config import Settings
from inference_arbiter.models import ModelTier, Priority, TrafficPriority, traffic_priority_from_legacy
from inference_arbiter.routing.state import EndpointRegistry


@dataclass
class AdmissionDecision:
    admitted: bool
    allowed_tiers: set[ModelTier] | None = None
    waited_ms: float = 0.0
    retry_after_s: int | None = None
    reason: str | None = None


class AdmissionController:
    def __init__(self, registry: EndpointRegistry, settings: Settings) -> None:
        self.registry = registry
        self.settings = settings

    def classify_priority(self, priority: Priority) -> TrafficPriority:
        return traffic_priority_from_legacy(priority)

    async def admit(self, priority: Priority) -> AdmissionDecision:
        traffic = self.classify_priority(priority)
        under_pressure = self.registry.any_saturated() or self.registry.any_under_pressure()

        if not under_pressure:
            return AdmissionDecision(admitted=True)

        if traffic == TrafficPriority.BATCH:
            if self.settings.batch_immediate_shed:
                return AdmissionDecision(
                    admitted=False,
                    retry_after_s=self.settings.batch_retry_after_s,
                    reason="batch_shed_under_pressure",
                )

        saturated = self.registry.saturated_tiers()
        all_tiers = set(ModelTier)
        allowed = all_tiers - saturated if saturated else all_tiers
        if not allowed:
            allowed = all_tiers

        return AdmissionDecision(
            admitted=True,
            allowed_tiers=allowed,
            reason="interactive_restricted_tiers" if saturated else None,
        )
