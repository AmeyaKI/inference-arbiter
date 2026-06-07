"""Async httpx client for OpenAI-compatible backends."""

from __future__ import annotations

import time
from typing import AsyncIterator

import httpx
import structlog
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from inference_arbiter.config import Settings
from inference_arbiter.models import ModelTier
from inference_arbiter.routing.context import RequestContext
from inference_arbiter.routing.state import EndpointRegistry
from inference_arbiter.telemetry.metrics import record_ttft

logger = structlog.get_logger(__name__)


class BackendClient:
    def __init__(
        self,
        registry: EndpointRegistry,
        settings: Settings,
        client: httpx.AsyncClient,
    ) -> None:
        self.registry = registry
        self.settings = settings
        self.client = client

    async def invoke(
        self,
        *,
        tier: ModelTier,
        payload: dict,
        stream: bool,
        ctx: RequestContext,
        force: bool = False,  # noqa: ARG002 — reserved for future admission bypass
    ) -> tuple[Any, dict | str | None, float | None]:
        state = self.registry.by_tier(tier)
        ep = state.config
        url = f"{ep.base_url}/chat/completions"
        state.record_dispatch()
        start = time.perf_counter()

        try:
            if stream:
                return await self._stream(tier, url, payload, state, start, ctx)
            return await self._json(tier, url, payload, state, start, ctx)
        except HTTPException:
            state.record_failure(self.settings)
            raise
        except Exception as exc:
            state.record_failure(self.settings)
            logger.exception("backend_client_error", endpoint=ep.name, error=str(exc))
            raise HTTPException(status_code=502, detail=f"Backend error: {exc}") from exc

    async def _json(
        self,
        tier: ModelTier,
        url: str,
        payload: dict,
        state,
        start: float,
        ctx: RequestContext,
    ):
        response = await self.client.post(url, json=payload)
        latency_ms = (time.perf_counter() - start) * 1000
        ttft_ms = latency_ms

        if response.status_code >= 500:
            state.record_failure(self.settings)
            raise HTTPException(status_code=response.status_code, detail=response.text)

        verification_passed = None
        if response.status_code < 400:
            state.record_success(
                latency_ms,
                self.settings,
                ttft_ms=ttft_ms,
                verification_passed=verification_passed,
            )
            record_ttft(tier.value, ttft_ms / 1000.0)
        else:
            state.record_success(latency_ms, self.settings, ttft_ms=ttft_ms)

        try:
            content = response.json()
        except Exception:
            content = {"error": response.text}

        http_response = JSONResponse(status_code=response.status_code, content=content)
        return http_response, content, ttft_ms

    async def _stream(
        self,
        tier: ModelTier,
        url: str,
        payload: dict,
        state,
        start: float,
        ctx: RequestContext,
    ):
        request = self.client.build_request("POST", url, json=payload)
        response = await self.client.send(request, stream=True)
        if response.status_code >= 500:
            body = await response.aread()
            await response.aclose()
            state.record_failure(self.settings)
            raise HTTPException(status_code=response.status_code, detail=body.decode())
        if response.status_code >= 400:
            body = await response.aread()
            await response.aclose()
            latency_ms = (time.perf_counter() - start) * 1000
            state.record_success(latency_ms, self.settings, ttft_ms=latency_ms)
            raise HTTPException(status_code=response.status_code, detail=body.decode())

        ttft_recorded = False
        ttft_ms: float | None = None
        collected: list[bytes] = []

        async def generate() -> AsyncIterator[bytes]:
            nonlocal ttft_recorded, ttft_ms
            try:
                async for chunk in response.aiter_bytes():
                    if not ttft_recorded:
                        ttft_ms = (time.perf_counter() - start) * 1000
                        ttft_recorded = True
                        record_ttft(tier.value, ttft_ms / 1000.0)
                    collected.append(chunk)
                    yield chunk
            finally:
                latency_ms = (time.perf_counter() - start) * 1000
                await response.aclose()
                if response.status_code < 500:
                    state.record_success(
                        latency_ms,
                        self.settings,
                        ttft_ms=ttft_ms or latency_ms,
                    )

        headers = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        streaming = StreamingResponse(
            generate(),
            status_code=response.status_code,
            media_type="text/event-stream",
            headers=headers,
        )
        return streaming, None, ttft_ms
