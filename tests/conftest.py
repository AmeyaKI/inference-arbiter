"""Shared test fixtures."""

from __future__ import annotations

import pytest

from inference_arbiter.classifier import HeuristicComplexityClassifier
from inference_arbiter.config import Settings, build_default_endpoints, reset_settings
from inference_arbiter.endpoint_state import EndpointRegistry
from inference_arbiter.router import RoutingEngine


@pytest.fixture(autouse=True)
def _reset_settings():
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def settings() -> Settings:
    return Settings(routing_mode="active", queue_pressure_threshold=2)


@pytest.fixture
def registry(settings: Settings) -> EndpointRegistry:
    return EndpointRegistry(build_default_endpoints(settings), settings)


@pytest.fixture
def router(registry: EndpointRegistry, settings: Settings) -> RoutingEngine:
    return RoutingEngine(registry, HeuristicComplexityClassifier(), settings)
