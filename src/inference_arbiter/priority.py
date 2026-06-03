"""Priority admission control for batch traffic under pressure."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from inference_arbiter.config import Settings
from inference_arbiter.endpoint_state import EndpointRegistry
from inference_arbiter.models import Priority


@dataclass
class AdmissionResult:
    admitted: bool
    waited_ms: float = 0.0
    retry_after_s: int | None = None
    reason: str | None = None


class PriorityGate:
    def __init__(self, registry: EndpointRegistry, settings: Settings) -> None:
        self.registry = registry
        self.settings = settings
        self._batch_waiting = 0

    async def admit(self, priority: Priority) -> AdmissionResult:
        if priority in (Priority.CRITICAL, Priority.STANDARD):
            return AdmissionResult(admitted=True)

        if not self.registry.any_under_pressure():
            return AdmissionResult(admitted=True)

        max_wait = self.settings.batch_queue_max_wait_s
        deadline = time.monotonic() + max_wait
        self._batch_waiting += 1
        try:
            while time.monotonic() < deadline:
                if not self.registry.any_under_pressure():
                    waited = (max_wait - (deadline - time.monotonic())) * 1000
                    return AdmissionResult(admitted=True, waited_ms=max(0, waited))
                await asyncio.sleep(0.05)
            return AdmissionResult(
                admitted=False,
                waited_ms=max_wait * 1000,
                retry_after_s=self.settings.batch_retry_after_s,
                reason="batch_shed_under_pressure",
            )
        finally:
            self._batch_waiting = max(0, self._batch_waiting - 1)

    @property
    def batch_waiting(self) -> int:
        return self._batch_waiting
