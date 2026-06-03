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


def _transport(app):
    return ASGITransport(app=app)


@pytest.mark.asyncio
async def test_healthz(app):
    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_models_list(app):
    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        resp = await client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()["data"]
        ids = {m["id"] for m in data}
        assert "auto" in ids
        assert "small" in ids


@pytest.mark.asyncio
async def test_chat_completions_mocked(app, monkeypatch):
    async def fake_forward(decision, payload, stream):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=200,
            content={
                "id": "cmpl-test",
                "object": "chat.completion",
                "choices": [{"message": {"role": "assistant", "content": "Paris"}}],
            },
            headers=decision.response_headers,
        )

    monkeypatch.setattr(app.state.arbiter.proxy, "forward", fake_forward)

    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "Capital of France?"}],
                "x_slo_deadline_ms": 5000,
                "x_priority": "standard",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("X-Arbiter-Model-Tier") in ("small", "medium", "large")
        assert resp.headers.get("X-Request-ID")

        request_id = resp.headers["X-Request-ID"]
        audit = await client.get(f"/v1/routing/decisions/{request_id}")
        assert audit.status_code == 200
        body = audit.json()
        assert body["request_id"] == request_id
        assert body["tier"] == resp.headers["X-Arbiter-Model-Tier"]


@pytest.mark.asyncio
async def test_streaming_mocked(app, monkeypatch):
    from fastapi.responses import StreamingResponse

    async def fake_stream(decision, payload, stream):
        async def gen():
            yield b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers=decision.response_headers)

    monkeypatch.setattr(app.state.arbiter.proxy, "forward", fake_stream)

    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200
            chunks = [line async for line in resp.aiter_lines()]
            assert any("[DONE]" in c or "DONE" in c for c in chunks)


@pytest.mark.asyncio
async def test_batch_shed_under_pressure(app, monkeypatch):
    registry = app.state.arbiter.registry
    for state in registry.all_states():
        state.in_flight = 10

    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "batch job"}],
                "x_priority": "batch",
            },
        )
        assert resp.status_code == 503
        assert resp.headers.get("Retry-After")
