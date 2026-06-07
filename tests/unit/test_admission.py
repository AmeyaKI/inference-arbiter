"""Unit tests for admission control."""

import pytest

from inference_arbiter.config import Settings, build_default_endpoints
from inference_arbiter.models import Priority, TrafficPriority
from inference_arbiter.routing.admission import AdmissionController
from inference_arbiter.routing.state import EndpointRegistry


@pytest.fixture
def admission():
    settings = Settings(batch_immediate_shed=True, queue_pressure_threshold=2)
    registry = EndpointRegistry(build_default_endpoints(settings), settings)
    return AdmissionController(registry, settings), registry


@pytest.mark.asyncio
async def test_interactive_admitted_under_pressure(admission):
    controller, registry = admission
    for state in registry.all_states():
        state.in_flight = 10
    decision = await controller.admit(Priority.STANDARD)
    assert decision.admitted
    assert decision.allowed_tiers is not None


@pytest.mark.asyncio
async def test_batch_shed_immediately(admission):
    controller, registry = admission
    for state in registry.all_states():
        state.in_flight = 10
    decision = await controller.admit(Priority.BATCH)
    assert not decision.admitted
    assert decision.retry_after_s is not None


def test_priority_mapping():
    settings = Settings()
    registry = EndpointRegistry(build_default_endpoints(settings), settings)
    controller = AdmissionController(registry, settings)
    assert controller.classify_priority(Priority.BATCH) == TrafficPriority.BATCH
    assert controller.classify_priority(Priority.CRITICAL) == TrafficPriority.INTERACTIVE
