"""Background bandit weight updater (non-blocking hot path)."""

from __future__ import annotations

import asyncio
import logging

from inference_arbiter.routing.bandit import LinUCBBandit
from inference_arbiter.telemetry.ring_buffer import TelemetryRingBuffer

logger = logging.getLogger(__name__)


class BanditUpdater:
    def __init__(
        self,
        ring_buffer: TelemetryRingBuffer,
        bandit: LinUCBBandit,
        interval_s: float = 5.0,
    ) -> None:
        self.ring_buffer = ring_buffer
        self.bandit = bandit
        self.interval_s = interval_s
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                records = self.ring_buffer.drain()
                for record in records:
                    self.bandit.update(
                        record.prompt_features,
                        record.tier,
                        record.reward,
                    )
                if records:
                    logger.debug("bandit_updated", count=len(records))
            except Exception:
                logger.exception("bandit_updater_error")
            await asyncio.sleep(self.interval_s)
