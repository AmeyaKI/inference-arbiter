"""FastAPI application entrypoint."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from inference_arbiter.config import build_default_endpoints, get_settings
from inference_arbiter.benchmark.runner import BenchmarkRunner
from inference_arbiter.console.routes import create_console_router
from inference_arbiter.endpoints.client import BackendClient
from inference_arbiter.gateway.models import ChatCompletionRequest, ModelCard, ModelListResponse
from inference_arbiter.models import DegradationReason, FailureAttribution, ModelTier
from inference_arbiter.gateway.middleware import RequestContextMiddleware
from inference_arbiter.observability.audit import AuditStore
from inference_arbiter.observability.events import RoutingEventBus, build_routing_event
from inference_arbiter.observability.tracer import init_tracer, routing_span
from inference_arbiter.routing.admission import AdmissionController
from inference_arbiter.routing.bandit import LinUCBBandit
from inference_arbiter.routing.context import RequestContext
from inference_arbiter.routing.executor import TierExecutor
from inference_arbiter.routing.features import FeatureExtractor
from inference_arbiter.routing.state import EndpointRegistry
from inference_arbiter.telemetry.metrics import (
    record_bandit_update,
    record_batch_shed,
    record_cost_proxy,
    record_degraded,
    record_latency,
    record_routing,
    record_slo_breach,
    record_slo_outcome,
    sync_bandit_gauges,
    sync_endpoint_gauges,
)
from inference_arbiter.telemetry.ring_buffer import TelemetryRecord, TelemetryRingBuffer
from inference_arbiter.telemetry.updater import BanditUpdater

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
        self.feature_extractor = FeatureExtractor(
            self.registry, self.settings.feature_dim
        )
        self.bandit = LinUCBBandit(self.registry, self.settings, self.feature_extractor)
        self.admission = AdmissionController(self.registry, self.settings)
        self.priority_gate = self.admission
        self.audit = AuditStore(max_records=self.settings.audit_max_records)
        self.event_bus = RoutingEventBus(buffer_size=self.settings.event_buffer_size)
        self.benchmark_runner = BenchmarkRunner(
            base_url=f"http://127.0.0.1:{self.settings.port}"
        )
        self.ring_buffer = TelemetryRingBuffer(max_size=self.settings.ring_buffer_size)
        self.bandit_updater: BanditUpdater | None = None
        self.http_client: httpx.AsyncClient | None = None
        self.client: BackendClient | None = None
        self.executor: TierExecutor | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    state: AppState = app.state.arbiter
    init_tracer(state.settings)
    state.http_client = httpx.AsyncClient(timeout=state.settings.http_timeout_s)
    state.client = BackendClient(state.registry, state.settings, state.http_client)
    state.proxy = state.client
    state.executor = TierExecutor(state.registry, state.settings, state.client)
    state.bandit_updater = BanditUpdater(
        state.ring_buffer,
        state.bandit,
        interval_s=state.settings.updater_interval_s,
    )
    checkpoint = state.settings.bandit_checkpoint_path
    if checkpoint and os.path.exists(checkpoint):
        try:
            state.bandit.load(checkpoint)
            logger.info("bandit_checkpoint_loaded", path=checkpoint,
                        total_updates=state.bandit.total_updates)
        except Exception as exc:
            logger.warning("bandit_checkpoint_load_failed", path=checkpoint, error=str(exc))
    await state.bandit_updater.start()
    logger.info(
        "gateway_started",
        routing_mode=state.settings.routing_mode.value,
        routing_engine=state.settings.routing_engine,
        endpoints=[e.name for e in state.endpoints],
    )
    yield
    if state.bandit_updater:
        await state.bandit_updater.stop()
    if checkpoint and state.bandit.total_updates > 0:
        try:
            state.bandit.save(checkpoint)
            logger.info("bandit_checkpoint_saved", path=checkpoint,
                        total_updates=state.bandit.total_updates)
        except Exception as exc:
            logger.warning("bandit_checkpoint_save_failed", path=checkpoint, error=str(exc))
    if state.http_client:
        await state.http_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="inference-arbiter",
        description="OpenAI-compatible gateway with deadline-aware cascade routing",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.arbiter = AppState()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/")
    async def root():
        return {
            "service": "inference-arbiter",
            "status": "running",
            "console": "/console",
            "docs": "/docs",
            "health": "/healthz",
            "metrics": "/metrics",
            "chat": "POST /v1/chat/completions",
            "models": "/v1/models",
        }

    @app.get("/healthz")
    async def healthz():
        st = app.state.arbiter
        return {
            "status": "ok",
            "routing_mode": st.settings.routing_mode.value,
            "bandit_policy_active": st.bandit.policy_active,
        }

    @app.get("/readyz")
    async def readyz():
        import asyncio
        st = app.state.arbiter

        async def probe_tier(tier: ModelTier) -> dict:
            ep = st.registry.by_tier(tier)
            url = ep.config.base_url.rstrip("/") + "/models"
            try:
                r = await st.http_client.get(url, timeout=2.0)
                ok = r.status_code < 500
            except Exception:
                ok = False
            return {"tier": tier.value, "model": ep.config.backend_model, "ready": ok}

        results = await asyncio.gather(
            *[probe_tier(t) for t in ModelTier], return_exceptions=False
        )
        tiers = list(results)
        all_ready = all(t["ready"] for t in tiers)
        return JSONResponse(
            status_code=200 if all_ready else 503,
            content={"ready": all_ready, "tiers": tiers},
        )

    @app.get("/metrics")
    async def metrics():
        st = app.state.arbiter
        sync_endpoint_gauges(st.registry)
        sync_bandit_gauges(st.bandit)
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/models")
    async def list_models():
        aliases = ["auto", "auto-degraded-ok", "small", "medium", "large", "1b", "3b", "8b"]
        cards = [ModelCard(id=m) for m in aliases]
        for ep in app.state.arbiter.endpoints:
            cards.append(ModelCard(id=ep.backend_model))
        return ModelListResponse(data=cards)

    @app.get("/v1/routing/decisions/{request_id}")
    async def get_routing_decision_path(request_id: str):
        return _get_decision(app, request_id)

    @app.get("/v1/routing/decisions")
    async def get_routing_decision_query(request_id: str = Query(...)):
        return _get_decision(app, request_id)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request, body: ChatCompletionRequest):
        state: AppState = app.state.arbiter
        request_id = body.x_request_id or request.headers.get("X-Request-ID") or str(uuid.uuid4())
        traffic = state.admission.classify_priority(body.x_priority)
        gateway_start = time.perf_counter()

        ctx = RequestContext.create(
            request_id=request_id,
            priority=traffic,
            slo_deadline_ms=body.x_slo_deadline_ms,
            messages=body.messages_as_dicts(),
            requested_model=body.model,
            allow_degraded_ok=body.allow_degraded_ok,
        )

        with routing_span(
            "routing_decision",
            {"request_id": request_id, "priority": traffic.value},
        ):
            admission = await state.admission.admit(body.x_priority)
            if not admission.admitted:
                record_batch_shed()
                ctx.status = "shed"
                ctx.failure_attribution = FailureAttribution.INFRASTRUCTURE_FAILURE
                ctx.degraded = True
                ctx.degradation_reason = DegradationReason.BATCH_SHED.value
                state.audit.put(ctx)
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

            bandit_decision = state.bandit.select(
                body.messages_as_dicts(),
                body.model,
                admission.allowed_tiers,
            )

            if state.executor is None or state.client is None:
                raise HTTPException(status_code=503, detail="Gateway not ready")

            async def invoke_backend(**kwargs):
                tier = kwargs["tier"]
                ep_state = state.registry.by_tier(tier)
                backend_payload = body.backend_payload(ep_state.config.backend_model)
                return await state.client.invoke(
                    tier=tier,
                    payload=backend_payload,
                    stream=kwargs.get("stream", False),
                    ctx=kwargs["ctx"],
                    force=kwargs.get("force", False),
                )

            try:
                result = await state.executor.execute(
                    ctx,
                    body.backend_payload("placeholder"),
                    bandit_decision,
                    body.stream,
                    invoke_backend,
                )
            except HTTPException:
                ctx.status = "failed"
                state.audit.put(ctx)
                await state.event_bus.publish(
                    build_routing_event(
                        ctx,
                        bandit_decision=bandit_decision,
                        priority=traffic.value,
                    )
                )
                raise

        ctx = result.ctx
        tier_state = state.registry.by_tier(result.tier)
        cost_proxy = tier_state.config.tier_weight * (
            ctx.payload.estimated_tokens if ctx.payload else 1
        )

        record_routing(
            tier=result.tier.value,
            reason=result.routing_reason.value,
            priority=traffic.value,
            complexity=bandit_decision.feature_result.complexity.value,
            confidence=bandit_decision.feature_result.heuristic_confidence,
        )
        if result.degraded and result.degradation_reason:
            record_degraded(result.degradation_reason.value)
            if result.degradation_reason == DegradationReason.DEADLINE_TOO_TIGHT:
                record_slo_breach(result.tier.value, result.degradation_reason.value)

        if body.x_slo_deadline_ms is not None:
            met = ctx.metrics.current_elapsed_ms <= body.x_slo_deadline_ms
            record_slo_outcome(met=met, priority=traffic.value)

        record_cost_proxy(result.tier.value, cost_proxy)
        state.audit.put(ctx)
        await state.event_bus.publish(
            build_routing_event(
                ctx,
                bandit_decision=bandit_decision,
                priority=traffic.value,
            )
        )
        sync_endpoint_gauges(state.registry)

        failure_attr = ctx.failure_attribution.value if ctx.failure_attribution else "NONE"
        reward = state.bandit.compute_reward(
            success=ctx.status == "completed" and not ctx.degraded,
            cost_proxy=cost_proxy / 10.0,
            failure_attribution=failure_attr,
        )
        state.ring_buffer.append(
            TelemetryRecord(
                prompt_features=ctx.feature_vector or [],
                tier=result.tier,
                passed_verification=failure_attr == "NONE",
                observed_latency_ms=ctx.metrics.current_elapsed_ms,
                cost_proxy=cost_proxy,
                failure_attribution=ctx.failure_attribution or FailureAttribution.NONE,
                reward=reward,
            )
        )
        record_bandit_update(result.tier.value, reward)

        latency_s = time.perf_counter() - gateway_start
        record_latency(
            result.tier.value,
            bandit_decision.feature_result.complexity.value,
            latency_s,
        )

        logger.info(
            "routing_decision",
            request_id=request_id,
            tier=result.tier.value,
            reason=result.routing_reason.value,
            degraded=result.degraded,
            tiers_attempted=ctx.tiers_attempted,
            bandit_policy_active=state.bandit.policy_active,
        )

        response = result.response
        headers = result.response_headers

        if isinstance(response, JSONResponse):
            response.headers.update(headers)
            return response
        if isinstance(response, StreamingResponse):
            response.headers.update(headers)
            return response
        return JSONResponse(content=response, headers=headers)

    if app.state.arbiter.settings.console_enabled:
        st = app.state.arbiter

        async def console_health():
            return {
                "status": "ok",
                "routing_mode": st.settings.routing_mode.value,
                "bandit_policy_active": st.bandit.policy_active,
            }

        console_router = create_console_router(
            event_bus=st.event_bus,
            benchmark_runner=st.benchmark_runner,
            prometheus_url=st.settings.prometheus_url,
            get_audit_record=st.audit.get,
            get_all_audit_records=st.audit.all,
            clear_audit=st.audit.clear,
            get_health=console_health,
            bandit=st.bandit,
            bandit_checkpoint_path=st.settings.bandit_checkpoint_path,
        )
        app.include_router(console_router)

    return app


def _get_decision(app: FastAPI, request_id: str):
    record = app.state.arbiter.audit.get(request_id)
    if not record:
        raise HTTPException(status_code=404, detail="Routing decision not found")
    return record


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "inference_arbiter.gateway.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
