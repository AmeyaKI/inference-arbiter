"""Bounded in-memory telemetry ring buffer."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from threading import Lock

from inference_arbiter.models import FailureAttribution, ModelTier


@dataclass(frozen=True)
class TelemetryRecord:
    prompt_features: list[float]
    tier: ModelTier
    passed_verification: bool
    observed_latency_ms: float
    cost_proxy: float
    failure_attribution: FailureAttribution
    reward: float


@dataclass
class TelemetryRingBuffer:
    max_size: int = 10_000
    _buffer: deque[TelemetryRecord] = field(default_factory=deque)
    _lock: Lock = field(default_factory=Lock)

    def append(self, record: TelemetryRecord) -> None:
        with self._lock:
            self._buffer.append(record)
            while len(self._buffer) > self.max_size:
                self._buffer.popleft()

    def drain(self, max_items: int = 1000) -> list[TelemetryRecord]:
        with self._lock:
            items: list[TelemetryRecord] = []
            while self._buffer and len(items) < max_items:
                items.append(self._buffer.popleft())
            return items

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)
