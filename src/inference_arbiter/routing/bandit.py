"""LinUCB contextual bandit router (Subsystem B)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from inference_arbiter.config import Settings
from inference_arbiter.models import ComplexityLabel, ModelTier, complexity_to_tier, tier_order
from inference_arbiter.routing.features import FeatureExtractor, FeatureResult
from inference_arbiter.routing.state import EndpointRegistry


@dataclass
class BanditDecision:
    ranked_tiers: list[ModelTier]
    scores: dict[str, float]
    used_heuristic: bool
    feature_result: FeatureResult


class LinUCBBandit:
    """Contextual bandit with heuristic cold start."""

    def __init__(
        self,
        registry: EndpointRegistry,
        settings: Settings,
        feature_extractor: FeatureExtractor,
    ) -> None:
        self.registry = registry
        self.settings = settings
        self.feature_extractor = feature_extractor
        self.d = settings.feature_dim
        self.alpha = settings.linucb_alpha
        self.arms = list(ModelTier)
        self.observations_per_tier: dict[ModelTier, int] = {t: 0 for t in self.arms}
        self._a: dict[ModelTier, np.ndarray] = {
            t: np.identity(self.d) for t in self.arms
        }
        self._b: dict[ModelTier, np.ndarray] = {t: np.zeros(self.d) for t in self.arms}
        self._policy_active = False
        self._total_updates = 0

    @property
    def policy_active(self) -> bool:
        return self._policy_active

    @property
    def total_updates(self) -> int:
        return self._total_updates

    def _heuristic_ranking(
        self,
        features: FeatureResult,
        requested_model: str,
        allowed_tiers: set[ModelTier] | None,
    ) -> list[ModelTier]:
        model = requested_model.strip().lower()
        if model not in ("auto", "auto-degraded-ok"):
            pinned = self._resolve_pinned_tier(model)
            if pinned:
                tiers = sorted(ModelTier, key=tier_order)
                pref_idx = tier_order(pinned)
                ranked = [pinned] + [t for t in tiers if t != pinned]
                if allowed_tiers:
                    ranked = [t for t in ranked if t in allowed_tiers]
                return ranked or list(ModelTier)

        preferred = complexity_to_tier(features.complexity)
        tiers = sorted(ModelTier, key=tier_order)
        pref_idx = tier_order(preferred)
        faster = [t for t in tiers if tier_order(t) <= pref_idx]
        slower = [t for t in tiers if tier_order(t) > pref_idx]
        ranked = faster + slower
        if allowed_tiers:
            ranked = [t for t in ranked if t in allowed_tiers]
        return ranked or list(ModelTier)

    def _resolve_pinned_tier(self, model: str) -> ModelTier | None:
        aliases = {
            "small": ModelTier.SMALL,
            "1b": ModelTier.SMALL,
            "7b": ModelTier.SMALL,
            "medium": ModelTier.MEDIUM,
            "3b": ModelTier.MEDIUM,
            "13b": ModelTier.MEDIUM,
            "large": ModelTier.LARGE,
            "8b": ModelTier.LARGE,
            "70b": ModelTier.LARGE,
        }
        if model in aliases:
            return aliases[model]
        for ep in self.registry.endpoints:
            if model == ep.backend_model.lower() or model == ep.name.lower():
                return ep.tier
        return None

    def _linucb_scores(self, x: np.ndarray) -> dict[ModelTier, float]:
        scores: dict[ModelTier, float] = {}
        for arm in self.arms:
            a_inv = np.linalg.inv(self._a[arm])
            theta = a_inv @ self._b[arm]
            mean = float(theta @ x)
            uncertainty = float(self.alpha * np.sqrt(x @ a_inv @ x))
            scores[arm] = mean + uncertainty
        return scores

    def select(
        self,
        messages: list[dict],
        requested_model: str,
        allowed_tiers: set[ModelTier] | None = None,
    ) -> BanditDecision:
        features = self.feature_extractor.extract(messages)
        cold_start = any(
            self.observations_per_tier[t] < self.settings.cold_start_min_observations_per_tier
            for t in self.arms
        )
        use_heuristic = (
            cold_start
            or features.heuristic_confidence >= self.settings.heuristic_confidence_threshold
        )

        if use_heuristic or not self._policy_active:
            ranked = self._heuristic_ranking(features, requested_model, allowed_tiers)
            scores = {t.value: 1.0 / (i + 1) for i, t in enumerate(ranked)}
            return BanditDecision(
                ranked_tiers=ranked,
                scores=scores,
                used_heuristic=True,
                feature_result=features,
            )

        x = np.array(features.vector, dtype=float)
        ucb_scores = self._linucb_scores(x)
        viable = self.registry.viable_tiers(allowed_tiers)
        if not viable:
            viable = list(ModelTier)
        ranked = sorted(viable, key=lambda t: ucb_scores.get(t, 0.0), reverse=True)
        scores = {t.value: float(ucb_scores.get(t, 0.0)) for t in ranked}
        return BanditDecision(
            ranked_tiers=ranked,
            scores=scores,
            used_heuristic=False,
            feature_result=features,
        )

    def update(
        self,
        feature_vector: list[float],
        tier: ModelTier,
        reward: float,
    ) -> None:
        x = np.array(feature_vector, dtype=float)
        self._a[tier] += np.outer(x, x)
        self._b[tier] += reward * x
        self.observations_per_tier[tier] += 1
        self._total_updates += 1
        if all(
            self.observations_per_tier[t] >= self.settings.cold_start_min_observations_per_tier
            for t in self.arms
        ):
            self._policy_active = True

    def compute_reward(
        self,
        *,
        success: bool,
        cost_proxy: float,
        failure_attribution: str,
    ) -> float:
        if failure_attribution == "INFRASTRUCTURE_FAILURE":
            return 0.0
        if failure_attribution == "QUALITY_FAILURE":
            return -0.5
        if failure_attribution == "LATENCY_FAILURE":
            return -0.3
        if success:
            return 1.0 - min(cost_proxy, 1.0)
        return -0.2
