"""Async health checker for endpoint pool."""

from __future__ import annotations

import httpx

from inference_arbiter.routing.state import EndpointRegistry


async def check_endpoint_health(registry: EndpointRegistry, client: httpx.AsyncClient) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for state in registry.all_states():
        url = state.config.base_url.rsplit("/v1", 1)[0] + "/api/tags"
        try:
            resp = await client.get(url, timeout=5.0)
            results[state.config.name] = resp.status_code < 500
        except Exception:
            results[state.config.name] = False
    return results
