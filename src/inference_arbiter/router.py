"""Routing engine: complexity, SLO ETA, circuit breaker, degradation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from inference_arbiter.classifier import BaseComplexityClassifier, ClassificationResult
from inference_arbiter.config import EndpointConfig, Settings
from inference_arbiter.endpoint_state import EndpointRegistry, EndpointState
from inference_arbiter.models import (
    ComplexityLabel,
    DegradationReason,
    ModelTier,
    RoutingMode,
    RoutingReason,
    complexity_to_tier,
    tier_order,
)


@dataclass
class RoutingDecision:
    request_id: str
    requested_model: str
    endpoint_name: str
    tier: ModelTier
    backend_model: str
    complexity: ComplexityLabel | None
    complexity_confidence: float | None
    routing_reason: RoutingReason
    estimated_eta_ms: float
    slo_deadline_ms: int | None
    degraded: bool
    degradation_reason: DegradationReason | None
    shadow_would_route_to: str | None = None
    classifier_signals: dict[str, Any] = field(default_factory=dict)
    allow_degraded_ok: bool = False

    @property
    def response_headers(self) -> dict[str, str]:
        headers = {
            "X-Request-ID": self.request_id,
            "X-Arbiter-Model-Tier": self.tier.value,
            "X-Degraded-Mode": "true" if self.degraded else "false",
        }
        if self.complexity:
            headers["X-Arbiter-Complexity"] = self.complexity.value
        if self.degradation_reason:
            headers["X-Degradation-Reason"] = self.degradation_reason.value
        return headers


class RoutingEngine:
    def __init__(
        self,
        registry: EndpointRegistry,
        classifier: BaseComplexityClassifier,
        settings: Settings,
    ) -> None:
        self.registry = registry
        self.classifier = classifier
        self.settings = settings

    def route(
        self,
        *,
        request_id: str,
        requested_model: str,
        messages: list[dict],
        slo_deadline_ms: int | None,
        allow_degraded_ok: bool = False,
    ) -> RoutingDecision:
        classification = self.classifier.classify(messages)
        preferred_tier = self._resolve_preferred_tier(requested_model, classification)
        active_tier, reason, degraded, deg_reason, shadow_target = self._select_endpoint(
            preferred_tier=preferred_tier,
            classification=classification,
            requested_model=requested_model,
            slo_deadline_ms=slo_deadline_ms,
        )

        if self.settings.routing_mode == RoutingMode.SHADOW:
            shadow_state = self.registry.by_tier(self.settings.shadow_default_tier)
            endpoint_state = shadow_state
            routing_reason = RoutingReason.SHADOW
            shadow_target = active_tier.value
            active_tier = self.settings.shadow_default_tier
            degraded = False
            deg_reason = None
        else:
            endpoint_state = self.registry.by_tier(active_tier)
            routing_reason = reason

        return RoutingDecision(
            request_id=request_id,
            requested_model=requested_model,
            endpoint_name=endpoint_state.config.name,
            tier=active_tier,
            backend_model=endpoint_state.config.backend_model,
            complexity=classification.label,
            complexity_confidence=classification.confidence,
            routing_reason=routing_reason,
            estimated_eta_ms=endpoint_state.eta_ms(),
            slo_deadline_ms=slo_deadline_ms,
            degraded=degraded and not allow_degraded_ok,
            degradation_reason=deg_reason if degraded and not allow_degraded_ok else None,
            shadow_would_route_to=shadow_target,
            classifier_signals=classification.signals,
            allow_degraded_ok=allow_degraded_ok,
        )

    def _resolve_preferred_tier(
        self, requested_model: str, classification: ClassificationResult
    ) -> ModelTier:
        model = requested_model.strip().lower()
        if model in ("auto", "auto-degraded-ok"):
            return complexity_to_tier(classification.label)
        tier_aliases = {
            "small": ModelTier.SMALL,
            "7b": ModelTier.SMALL,
            "medium": ModelTier.MEDIUM,
            "13b": ModelTier.MEDIUM,
            "large": ModelTier.LARGE,
            "70b": ModelTier.LARGE,
        }
        if model in tier_aliases:
            return tier_aliases[model]
        for ep in self.registry.endpoints:
            if model == ep.backend_model.lower() or model == ep.name.lower():
                return ep.tier
        return complexity_to_tier(classification.label)

    def _select_endpoint(
        self,
        *,
        preferred_tier: ModelTier,
        classification: ClassificationResult,
        requested_model: str,
        slo_deadline_ms: int | None,
    ) -> tuple[ModelTier, RoutingReason, bool, DegradationReason | None, str | None]:
        model = requested_model.strip().lower()
        pinned_models = {ep.backend_model.lower() for ep in self.registry.endpoints} | {
            "small",
            "medium",
            "large",
            "7b",
            "13b",
            "70b",
        }
        pinned = model not in ("auto", "auto-degraded-ok") and model in pinned_models

        candidates = self._ordered_candidates(preferred_tier, pinned=pinned)
        reason = RoutingReason.COMPLEXITY if not pinned else RoutingReason.PINNED_MODEL
        degraded = False
        deg: DegradationReason | None = None

        viable = [c for c in candidates if self._endpoint_viable(c)]
        if not viable:
            fallback = self.registry.by_tier(ModelTier.SMALL)
            return (
                fallback.config.tier,
                RoutingReason.FALLBACK,
                True,
                DegradationReason.MODEL_CAPACITY,
                None,
            )

        chosen = viable[0]
        chosen_state = self.registry.by_tier(chosen)

        if chosen != preferred_tier:
            if not self._endpoint_viable(preferred_tier):
                deg = DegradationReason.CIRCUIT_OPEN
                reason = RoutingReason.CIRCUIT_BREAKER
                degraded = True
            else:
                deg = DegradationReason.ENDPOINT_SATURATED
                reason = RoutingReason.SLO_PRESSURE if slo_deadline_ms else RoutingReason.FALLBACK
                degraded = True

        if slo_deadline_ms is not None:
            chosen, slo_deg, slo_reason = self._apply_slo(
                viable, preferred_tier, slo_deadline_ms, chosen
            )
            if slo_deg:
                degraded = True
                deg = slo_deg
                reason = slo_reason

        return chosen, reason, degraded, deg, preferred_tier.value

    def _ordered_candidates(self, preferred: ModelTier, *, pinned: bool = False) -> list[ModelTier]:
        tiers = sorted(ModelTier, key=tier_order)
        pref_idx = tier_order(preferred)
        if pinned:
            faster = [t for t in tiers if tier_order(t) < pref_idx]
            slower = [t for t in tiers if tier_order(t) > pref_idx]
            return [preferred, *faster, *slower]
        faster = [t for t in tiers if tier_order(t) <= pref_idx]
        slower = [t for t in tiers if tier_order(t) > pref_idx]
        return faster + slower

    def _endpoint_viable(self, tier: ModelTier) -> bool:
        state = self.registry.by_tier(tier)
        return state.is_available(self.settings) and state.in_flight < state.config.max_concurrency

    def _apply_slo(
        self,
        viable: list[ModelTier],
        preferred: ModelTier,
        deadline_ms: int,
        current: ModelTier,
    ) -> tuple[ModelTier, DegradationReason | None, RoutingReason]:
        ordered = sorted(viable, key=tier_order)
        for tier in ordered:
            state = self.registry.by_tier(tier)
            if state.eta_ms() <= deadline_ms:
                if tier != preferred:
                    return tier, DegradationReason.ENDPOINT_SATURATED, RoutingReason.SLO_PRESSURE
                return tier, None, RoutingReason.COMPLEXITY

        fastest = ordered[0]
        return fastest, DegradationReason.DEADLINE_TOO_TIGHT, RoutingReason.SLO_PRESSURE

    def endpoint_config_for(self, decision: RoutingDecision) -> EndpointConfig:
        return self.registry.get(decision.endpoint_name).config

    def endpoint_state_for(self, decision: RoutingDecision) -> EndpointState:
        return self.registry.get(decision.endpoint_name)
