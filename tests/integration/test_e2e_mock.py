"""Integration tests against mocked backend."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from inference_arbiter.config import reset_settings
from inference_arbiter.main import create_app


@pytest.fixture
async def app():
    reset_settings()
    application = create_app()
    state = application.state.arbiter
    async with application.router.lifespan_context(application):
        yield application
    if state.http_client is not None:
        await state.http_client.aclose()


@pytest.mark.asyncio
async def test_cascade_escalation_on_verification_failure(app, monkeypatch):
    call_count = {"n": 0}

    async def fake_invoke(**kwargs):
        from fastapi.responses import JSONResponse

        call_count["n"] += 1
        tier = kwargs["tier"].value
        content = "valid answer" if tier == "large" else ""
        return (
            JSONResponse(
                status_code=200,
                content={"choices": [{"message": {"role": "assistant", "content": content}}]},
            ),
            {"choices": [{"message": {"content": content}}]},
            50.0,
        )

    monkeypatch.setattr(app.state.arbiter.client, "invoke", fake_invoke)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "Explain quantum computing."}],
                "x_slo_deadline_ms": 30000,
            },
        )
        assert resp.status_code == 200
        assert call_count["n"] >= 2
        assert "large" in resp.headers.get("X-Tiers-Attempted", "")
