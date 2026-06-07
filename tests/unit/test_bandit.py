"""Unit tests for LinUCB bandit."""

from inference_arbiter.config import Settings, build_default_endpoints
from inference_arbiter.models import ModelTier
from inference_arbiter.routing.bandit import LinUCBBandit
from inference_arbiter.routing.features import FeatureExtractor
from inference_arbiter.routing.state import EndpointRegistry


def test_heuristic_cold_start():
    settings = Settings(cold_start_min_observations_per_tier=500)
    registry = EndpointRegistry(build_default_endpoints(settings), settings)
    bandit = LinUCBBandit(registry, settings, FeatureExtractor(registry))
    decision = bandit.select([{"role": "user", "content": "What is 2+2?"}], "auto")
    assert decision.used_heuristic
    assert decision.ranked_tiers[0] == ModelTier.SMALL


def test_bandit_update_activates_policy():
    settings = Settings(cold_start_min_observations_per_tier=2, feature_dim=16)
    registry = EndpointRegistry(build_default_endpoints(settings), settings)
    bandit = LinUCBBandit(registry, settings, FeatureExtractor(registry))
    features = [0.1] * 16
    for _ in range(2):
        bandit.update(features, ModelTier.SMALL, 0.8)
        bandit.update(features, ModelTier.MEDIUM, 0.7)
        bandit.update(features, ModelTier.LARGE, 0.6)
    assert bandit.policy_active


def test_reward_infra_failure_is_neutral():
    settings = Settings()
    registry = EndpointRegistry(build_default_endpoints(settings), settings)
    bandit = LinUCBBandit(registry, settings, FeatureExtractor(registry))
    reward = bandit.compute_reward(
        success=False,
        cost_proxy=0.5,
        failure_attribution="INFRASTRUCTURE_FAILURE",
    )
    assert reward == 0.0
