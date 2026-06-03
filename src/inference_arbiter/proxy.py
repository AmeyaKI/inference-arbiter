"""Proxy chat completions to OpenAI-compatible backends."""

from __future__ import annotations

import time
from typing import AsyncIterator

import httpx
import structlog
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from inference_arbiter.config import Settings
from inference_arbiter.endpoint_state import EndpointRegistry
from inference_arbiter.models import DegradationReason
from inference_arbiter.router import RoutingDecision

logger = structlog.get_logger(__name__)


class BackendProxy:
    def __init__(self, registry: EndpointRegistry, settings: Settings, client: httpx.AsyncClient) -> None:
        self.registry = registry
        self.settings = settings
        self.client = client

    async def forward(
        self,
        decision: RoutingDecision,
        payload: dict,
        stream: bool,
    ):
        state = self.registry.get(decision.endpoint_name)
        ep = state.config
        url = f"{ep.base_url}/chat/completions"
        state.record_dispatch()
        start = time.perf_counter()

        try:
            if stream:
                return await self._stream(decision, url, payload, state, start)
            return await self._json(decision, url, payload, state, start)
        except HTTPException:
            state.record_failure(self.settings)
            raise
        except Exception as exc:
            state.record_failure(self.settings)
            logger.exception("backend_proxy_error", endpoint=ep.name, error=str(exc))
            raise HTTPException(status_code=502, detail=f"Backend error: {exc}") from exc

    async def _json(self, decision: RoutingDecision, url: str, payload: dict, state, start: float):
        response = await self.client.post(url, json=payload)
        latency_ms = (time.perf_counter() - start) * 1000
        if response.status_code >= 500:
            state.record_failure(self.settings)
            raise HTTPException(status_code=response.status_code, detail=response.text)
        if response.status_code >= 400:
            # 4xx is a client/config error, not an endpoint failure — do not penalise the circuit breaker.
            state.record_success(latency_ms, self.settings.latency_ema_alpha)
            try:
                content = response.json()
            except Exception:
                content = {"error": response.text}
            return JSONResponse(
                status_code=response.status_code,
                content=content,
                headers=decision.response_headers,
            )
        state.record_success(latency_ms, self.settings.latency_ema_alpha)
        try:
            content = response.json()
        except Exception:
            content = {"error": response.text}
        return JSONResponse(
            status_code=response.status_code,
            content=content,
            headers=decision.response_headers,
        )

    async def _stream(
        self, decision: RoutingDecision, url: str, payload: dict, state, start: float
    ) -> StreamingResponse:
        request = self.client.build_request("POST", url, json=payload)
        response = await self.client.send(request, stream=True)
        if response.status_code >= 500:
            body = await response.aread()
            await response.aclose()
            state.record_failure(self.settings)
            raise HTTPException(status_code=response.status_code, detail=body.decode())
        if response.status_code >= 400:
            # 4xx: client/config error — record success so the circuit breaker is not penalised.
            body = await response.aread()
            await response.aclose()
            latency_ms = (time.perf_counter() - start) * 1000
            state.record_success(latency_ms, self.settings.latency_ema_alpha)
            raise HTTPException(status_code=response.status_code, detail=body.decode())

        async def generate() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                latency_ms = (time.perf_counter() - start) * 1000
                await response.aclose()
                if response.status_code < 500:
                    state.record_success(latency_ms, self.settings.latency_ema_alpha)
                else:
                    state.record_failure(self.settings)

        headers = {
            **decision.response_headers,
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(
            generate(),
            status_code=response.status_code,
            media_type="text/event-stream",
            headers=headers,
        )
