"""Tests for BackendProxy error handling and circuit breaker interaction."""

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


def _transport(application):
    return ASGITransport(app=application)


def _simple_payload():
    return {
        "model": "auto",
        "messages": [{"role": "user", "content": "What is 2+2?"}],
    }


@pytest.mark.asyncio
async def test_backend_timeout_returns_502_and_increments_failures(app, monkeypatch):
    import httpx

    async def raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out", request=None)

    monkeypatch.setattr(app.state.arbiter.proxy.client, "post", raise_timeout)

    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        resp = await client.post("/v1/chat/completions", json=_simple_payload())
        assert resp.status_code == 502

    # The endpoint that was targeted should have recorded a failure.
    any_failure = any(
        s.consecutive_failures > 0 for s in app.state.arbiter.registry.all_states()
    )
    assert any_failure

    request_id = resp.headers.get("X-Request-ID")
    if request_id:
        record = app.state.arbiter.audit.get(request_id)
        if record:
            assert record.status == "failed"


@pytest.mark.asyncio
async def test_backend_connection_error_returns_502_and_increments_failures(app, monkeypatch):
    import httpx

    async def raise_connect(*args, **kwargs):
        raise httpx.ConnectError("connection refused", request=None)

    monkeypatch.setattr(app.state.arbiter.proxy.client, "post", raise_connect)

    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        resp = await client.post("/v1/chat/completions", json=_simple_payload())
        assert resp.status_code == 502

    any_failure = any(
        s.consecutive_failures > 0 for s in app.state.arbiter.registry.all_states()
    )
    assert any_failure


@pytest.mark.asyncio
async def test_backend_4xx_does_not_increment_failure_counter(app, monkeypatch):
    """A 400 from the backend is a client/config error; the circuit breaker must not be penalised."""
    import httpx

    async def return_400(*args, **kwargs):
        return httpx.Response(
            status_code=400,
            content=b'{"error": "bad request"}',
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(app.state.arbiter.proxy.client, "post", return_400)

    # Send three requests — if the bug were present, consecutive_failures would reach the threshold
    # (default 3) and open the circuit breaker.
    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        for _ in range(3):
            resp = await client.post("/v1/chat/completions", json=_simple_payload())
            assert resp.status_code == 400

    for state in app.state.arbiter.registry.all_states():
        assert state.consecutive_failures == 0, (
            f"endpoint '{state.config.name}' had consecutive_failures={state.consecutive_failures}; "
            "4xx responses must not increment the failure counter"
        )


@pytest.mark.asyncio
async def test_backend_500_increments_failure_counter(app, monkeypatch):
    import httpx

    async def return_500(*args, **kwargs):
        return httpx.Response(status_code=500, content=b"internal server error")

    monkeypatch.setattr(app.state.arbiter.proxy.client, "post", return_500)

    async with AsyncClient(transport=_transport(app), base_url="http://test") as client:
        resp = await client.post("/v1/chat/completions", json=_simple_payload())
        assert resp.status_code == 500

    any_failure = any(
        s.consecutive_failures > 0 for s in app.state.arbiter.registry.all_states()
    )
    assert any_failure
