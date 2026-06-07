"""Unit tests for telemetry ring buffer."""

from inference_arbiter.models import FailureAttribution, ModelTier
from inference_arbiter.telemetry.ring_buffer import TelemetryRecord, TelemetryRingBuffer


def test_ring_buffer_bounded():
    buf = TelemetryRingBuffer(max_size=3)
    for i in range(5):
        buf.append(
            TelemetryRecord(
                prompt_features=[0.1],
                tier=ModelTier.SMALL,
                passed_verification=True,
                observed_latency_ms=100.0,
                cost_proxy=1.0,
                failure_attribution=FailureAttribution.NONE,
                reward=0.5,
            )
        )
    assert len(buf) == 3


def test_ring_buffer_drain():
    buf = TelemetryRingBuffer(max_size=10)
    buf.append(
        TelemetryRecord(
            prompt_features=[0.1],
            tier=ModelTier.MEDIUM,
            passed_verification=False,
            observed_latency_ms=200.0,
            cost_proxy=2.0,
            failure_attribution=FailureAttribution.QUALITY_FAILURE,
            reward=-0.5,
        )
    )
    drained = buf.drain()
    assert len(drained) == 1
    assert len(buf) == 0
