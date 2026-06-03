"""FastAPI application entrypoint."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from inference_arbiter.audit import AuditStore
from inference_arbiter.classifier import HeuristicComplexityClassifier
from inference_arbiter.config import build_default_endpoints, get_settings
from inference_arbiter.endpoint_state import EndpointRegistry
from inference_arbiter.metrics import (
    record_latency,
    record_routing,
    record_slo_breach,
    sync_endpoint_gauges,
)
from inference_arbiter.models import DegradationReason, RoutingMode
from inference_arbiter.openai_types import ChatCompletionRequest, ModelCard, ModelListResponse
from inference_arbiter.priority import PriorityGate
from inference_arbiter.proxy import BackendProxy
from inference_arbiter.router import RoutingEngine

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)
logger = structlog.get_logger(__name__)


class AppState:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.endpoints = build_default_endpoints(self.settings)
        self.registry = EndpointRegistry(self.endpoints, self.settings)
        self.classifier = HeuristicComplexityClassifier()
        self.router = RoutingEngine(self.registry, self.classifier, self.settings)
        self.priority_gate = PriorityGate(self.registry, self.settings)
        self.audit = AuditStore(max_records=self.settings.audit_max_records)
        self.http_client: httpx.AsyncClient | None = None
        self.proxy: BackendProxy | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    state: AppState = app.state.arbiter
    state.http_client = httpx.AsyncClient(timeout=state.settings.http_timeout_s)
    state.proxy = BackendProxy(state.registry, state.settings, state.http_client)
    logger.info(
        "gateway_started",
        routing_mode=state.settings.routing_mode.value,
        endpoints=[e.name for e in state.endpoints],
    )
    yield
    await state.http_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="inference-arbiter",
        description="OpenAI-compatible gateway with complexity-based routing",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.arbiter = AppState()

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "routing_mode": app.state.arbiter.settings.routing_mode.value}

    @app.get("/metrics")
    async def metrics():
        sync_endpoint_gauges(app.state.arbiter.registry)
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/models")
    async def list_models():
        aliases = ["auto", "auto-degraded-ok", "small", "medium", "large"]
        cards = [ModelCard(id=m) for m in aliases]
        for ep in app.state.arbiter.endpoints:
            cards.append(ModelCard(id=ep.backend_model))
        return ModelListResponse(data=cards)

    @app.get("/v1/routing/decisions/{request_id}")
    async def get_routing_decision(request_id: str):
        record = app.state.arbiter.audit.get(request_id)
        if not record:
            raise HTTPException(status_code=404, detail="Routing decision not found")
        return record.to_dict()

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request, body: ChatCompletionRequest):
        state: AppState = app.state.arbiter
        request_id = body.x_request_id or request.headers.get("X-Request-ID") or str(uuid.uuid4())
        priority = body.x_priority

        admission = await state.priority_gate.admit(priority)
        if not admission.admitted:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "message": "Batch request shed under endpoint pressure",
                        "type": "rate_limit_error",
                        "code": "batch_shed",
                    }
                },
                headers={"Retry-After": str(admission.retry_after_s or 30)},
            )

        decision = state.router.route(
            request_id=request_id,
            requested_model=body.model,
            messages=body.messages_as_dicts(),
            slo_deadline_ms=body.x_slo_deadline_ms,
            allow_degraded_ok=body.allow_degraded_ok,
        )

        if decision.degradation_reason == DegradationReason.DEADLINE_TOO_TIGHT:
            record_slo_breach(decision.tier.value, decision.degradation_reason.value)

        endpoint_state = state.router.endpoint_state_for(decision)
        audit_record = state.audit.build_record(
            decision,
            priority=priority.value,
            routing_mode=state.settings.routing_mode.value,
            endpoint_state=endpoint_state,
        )
        state.audit.put(audit_record)
        record_routing(decision, priority.value)
        sync_endpoint_gauges(state.registry)

        logger.info(
            "routing_decision",
            request_id=request_id,
            tier=decision.tier.value,
            endpoint=decision.endpoint_name,
            complexity=decision.complexity.value if decision.complexity else None,
            reason=decision.routing_reason.value,
            degraded=decision.degraded,
            eta_ms=decision.estimated_eta_ms,
            shadow_would_route_to=decision.shadow_would_route_to,
        )

        payload = body.backend_payload(decision.backend_model)
        start = time.perf_counter()
        try:
            assert state.proxy is not None
            result = await state.proxy.forward(decision, payload, body.stream)
            latency_s = time.perf_counter() - start
            record_latency(
                decision.tier.value,
                decision.complexity.value if decision.complexity else None,
                latency_s,
            )
            state.audit.update(
                request_id,
                actual_latency_ms=latency_s * 1000,
                status="completed",
                endpoint_snapshot=audit_record.endpoint_snapshot,
            )
            return result
        except HTTPException as exc:
            state.audit.update(request_id, status="failed", error=str(exc.detail))
            raise
        except Exception as exc:
            state.audit.update(request_id, status="failed", error=str(exc))
            raise

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "inference_arbiter.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
