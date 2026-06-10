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


@pytest.mark.asyncio
async def test_benchmark_save_without_runs(app):
    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        resp = await client.post("/console/api/benchmark/save", json={"label": "x"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_benchmark_save_with_runs(app, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "inference_arbiter.console.routes.save_benchmark_session",
        lambda completed, **kwargs: {
            "run_count": len(completed),
            "paths": {"latest_json": "benchmarks/latest.json"},
        },
    )
    app.state.arbiter.benchmark_runner._completed_runs = {
        "baseline": {"scenario": "baseline", "p50_ms": 100, "requests": 10},
    }
    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        resp = await client.post("/console/api/benchmark/save", json={"label": "ci"})
    assert resp.status_code == 200
    assert resp.json()["run_count"] == 1
