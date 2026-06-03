"""Bounded in-memory routing decision audit store."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any

from inference_arbiter.models import snapshot_state


@dataclass
class RoutingAuditRecord:
    request_id: str
    timestamp: float
    requested_model: str
    priority: str
    endpoint_name: str
    tier: str
    backend_model: str
    complexity: str | None
    complexity_confidence: float | None
    routing_reason: str
    slo_deadline_ms: int | None
    estimated_eta_ms: float
    actual_latency_ms: float | None = None
    degraded: bool = False
    degradation_reason: str | None = None
    shadow_would_route_to: str | None = None
    routing_mode: str = "active"
    endpoint_snapshot: dict[str, Any] = field(default_factory=dict)
    classifier_signals: dict[str, Any] = field(default_factory=dict)
    status: str = "dispatched"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditStore:
    def __init__(self, max_records: int = 10_000) -> None:
        self._max = max_records
        self._records: OrderedDict[str, RoutingAuditRecord] = OrderedDict()

    def put(self, record: RoutingAuditRecord) -> None:
        if record.request_id in self._records:
            del self._records[record.request_id]
        self._records[record.request_id] = record
        while len(self._records) > self._max:
            self._records.popitem(last=False)

    def update(
        self,
        request_id: str,
        *,
        actual_latency_ms: float | None = None,
        status: str | None = None,
        error: str | None = None,
        endpoint_snapshot: dict[str, Any] | None = None,
    ) -> None:
        rec = self._records.get(request_id)
        if not rec:
            return
        if actual_latency_ms is not None:
            rec.actual_latency_ms = actual_latency_ms
        if status is not None:
            rec.status = status
        if error is not None:
            rec.error = error
        if endpoint_snapshot is not None:
            rec.endpoint_snapshot = endpoint_snapshot

    def get(self, request_id: str) -> RoutingAuditRecord | None:
        return self._records.get(request_id)

    def build_record(
        self,
        decision,
        *,
        priority: str,
        routing_mode: str,
        endpoint_state,
    ) -> RoutingAuditRecord:
        return RoutingAuditRecord(
            request_id=decision.request_id,
            timestamp=time.time(),
            requested_model=decision.requested_model,
            priority=priority,
            endpoint_name=decision.endpoint_name,
            tier=decision.tier.value,
            backend_model=decision.backend_model,
            complexity=decision.complexity.value if decision.complexity else None,
            complexity_confidence=decision.complexity_confidence,
            routing_reason=decision.routing_reason.value,
            slo_deadline_ms=decision.slo_deadline_ms,
            estimated_eta_ms=decision.estimated_eta_ms,
            degraded=decision.degraded,
            degradation_reason=(
                decision.degradation_reason.value if decision.degradation_reason else None
            ),
            shadow_would_route_to=decision.shadow_would_route_to,
            routing_mode=routing_mode,
            endpoint_snapshot=snapshot_state(endpoint_state),
            classifier_signals=decision.classifier_signals,
        )
