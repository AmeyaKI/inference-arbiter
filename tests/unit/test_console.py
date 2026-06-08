"""Console API smoke tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from inference_arbiter.gateway.app import create_app


@pytest.fixture
def app():
    return create_app()


def _transport(app):
    return ASGITransport(app=app)


@pytest.mark.asyncio
async def test_console_index(app):
    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        resp = await client.get("/console")
    assert resp.status_code == 200
    assert "inference-arbiter" in resp.text


@pytest.mark.asyncio
async def test_console_health(app):
    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        resp = await client.get("/console/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_benchmark_status_idle(app):
    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        resp = await client.get("/console/api/benchmark/status")
    assert resp.status_code == 200
    assert resp.json()["running"] is False
