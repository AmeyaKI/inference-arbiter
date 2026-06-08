"""Dynamic tier executor with SLO budget-decay cascade (Subsystem C)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from inference_arbiter.config import Settings
from inference_arbiter.models import (
    DegradationReason,
    FailureAttribution,
    ModelTier,
    RoutingMode,
    RoutingReason,
    VerificationStatus,
)
from inference_arbiter.routing.bandit import BanditDecision
from inference_arbiter.routing.context import RequestContext, TierAttempt
from inference_arbiter.routing.state import EndpointRegistry
from inference_arbiter.verification.verifiers import extract_response_text, verify_response

InvokeBackend = Callable[..., Awaitable[tuple[Any, dict | str | None, float | None]]]


@dataclass
class ExecutionResult:
    response: Any
    ctx: RequestContext
    routing_reason: RoutingReason
    degraded: bool
    degradation_reason: DegradationReason | None = None
    backend_model: str = ""
    endpoint_name: str = ""
    tier: ModelTier = ModelTier.SMALL

    @property
    def response_headers(self) -> dict[str, str]:
        headers = {
            "X-Request-ID": self.ctx.request_id,
            "X-Model-Tier": self.tier.value,
            "X-Arbiter-Model-Tier": self.tier.value,
            "X-Degraded-Mode": "true" if self.degraded else "false",
            "X-Routing-Reason": self.routing_reason.value,
            "X-Tiers-Attempted": ",".join(self.ctx.tiers_attempted) or self.tier.value,
            "X-Elapsed-Ms": str(int(self.ctx.metrics.current_elapsed_ms)),
        }
        if self.degradation_reason:
            headers["X-Degradation-Reason"] = self.degradation_reason.value
        return headers


@dataclass
class TierExecutor:
    registry: EndpointRegistry
    settings: Settings
    client: Any = field(repr=False, default=None)

    def preflight_budget_ok(self, ctx: RequestContext, tier: ModelTier) -> bool:
        remaining = ctx.remaining_budget_ms()
        if remaining is None:
            return True
        state = self.registry.by_tier(tier)
        required = state.estimated_ttft_ms() + self.settings.verification_overhead_ms
        return remaining >= required

    def _tier_viable(self, tier: ModelTier) -> bool:
        state = self.registry.by_tier(tier)
        return (
            state.is_available(self.settings)
            and state.in_flight < state.config.max_concurrency
        )

    async def execute(
        self,
        ctx: RequestContext,
        payload: dict,
        bandit_decision: BanditDecision,
        stream: bool,
        invoke_backend: InvokeBackend,
    ) -> ExecutionResult:
        ctx.feature_vector = bandit_decision.feature_result.vector
        ctx.bandit_scores = bandit_decision.scores

        ranked = list(bandit_decision.ranked_tiers)
        routing_reason = (
            RoutingReason.BANDIT_POLICY
            if not bandit_decision.used_heuristic
            else RoutingReason.COMPLEXITY
        )
        degraded = False
        degradation_reason: DegradationReason | None = None

        if self.settings.routing_mode == RoutingMode.SHADOW:
            ctx.shadow_would_route_to = ranked[0].value if ranked else None
            ranked = [self.settings.shadow_default_tier]
            routing_reason = RoutingReason.SHADOW
            return await self._execute_single(
                ctx,
                payload,
                ranked[0],
                invoke_backend,
                stream,
                routing_reason,
            )

        if stream:
            return await self._execute_streaming(
                ctx, payload, ranked, invoke_backend, routing_reason
            )

        best_response = None
        best_tier = ranked[0] if ranked else ModelTier.SMALL

        for tier in ranked:
            if not self._tier_viable(tier):
                continue
            if not self.preflight_budget_ok(ctx, tier):
                ctx.routing_history.append(
                    TierAttempt(
                        tier=tier.value,
                        backend_model=self.registry.by_tier(tier).config.backend_model,
                        latency_ms=0.0,
                        ttft_ms=None,
                        verification_status=VerificationStatus.SKIPPED,
                        failure_attribution=FailureAttribution.LATENCY_FAILURE,
                        budget_remaining_ms=ctx.remaining_budget_ms(),
                    )
                )
                degraded = True
                degradation_reason = DegradationReason.DEADLINE_TOO_TIGHT
                routing_reason = RoutingReason.SLO_FORCED_ESCALATION
                continue

            ctx.tiers_attempted.append(tier.value)
            attempt_start = time.perf_counter()
            try:
                response, response_body, ttft_ms = await invoke_backend(
                    tier=tier, payload=payload, stream=False, ctx=ctx
                )
            except Exception:
                latency_ms = (time.perf_counter() - attempt_start) * 1000
                ctx.routing_history.append(
                    TierAttempt(
                        tier=tier.value,
                        backend_model=self.registry.by_tier(tier).config.backend_model,
                        latency_ms=latency_ms,
                        ttft_ms=None,
                        verification_status=VerificationStatus.SKIPPED,
                        failure_attribution=FailureAttribution.INFRASTRUCTURE_FAILURE,
                        budget_remaining_ms=ctx.remaining_budget_ms(),
                    )
                )
                ctx.failure_attribution = FailureAttribution.INFRASTRUCTURE_FAILURE
                continue

            latency_ms = (time.perf_counter() - attempt_start) * 1000
            ctx.update_elapsed()

            text = ""
            if isinstance(response_body, dict):
                text = extract_response_text(response_body)
            elif isinstance(response_body, str):
                text = response_body

            expect_json = "json" in str(payload.get("response_format", "")).lower()
            verification = verify_response(
                response_text=text,
                estimated_tokens=ctx.payload.estimated_tokens if ctx.payload else 64,
                expect_json=expect_json,
                max_length_multiplier=self.settings.max_output_length_multiplier,
            )

            ctx.routing_history.append(
                TierAttempt(
                    tier=tier.value,
                    backend_model=self.registry.by_tier(tier).config.backend_model,
                    latency_ms=latency_ms,
                    ttft_ms=ttft_ms,
                    verification_status=verification.status,
                    failure_attribution=verification.failure_attribution,
                    budget_remaining_ms=ctx.remaining_budget_ms(),
                )
            )

            best_response = response
            best_tier = tier

            if verification.passed:
                ctx.failure_attribution = FailureAttribution.NONE
                break

            ctx.failure_attribution = verification.failure_attribution
            routing_reason = RoutingReason.SLO_FORCED_ESCALATION
            remaining = ctx.remaining_budget_ms()
            if remaining is not None and not self._can_escalate(ctx, tier, ranked):
                degraded = True
                degradation_reason = DegradationReason.DEADLINE_TOO_TIGHT
                break

        if best_response is None:
            best_tier = ModelTier.SMALL
            degraded = True
            degradation_reason = DegradationReason.MODEL_CAPACITY
            routing_reason = RoutingReason.FALLBACK
            best_response, _, _ = await invoke_backend(
                tier=best_tier, payload=payload, stream=False, ctx=ctx, force=True
            )

        return self._finalize(
            ctx,
            best_response,
            best_tier,
            routing_reason,
            degraded,
            degradation_reason,
        )

    async def _execute_single(
        self,
        ctx: RequestContext,
        payload: dict,
        tier: ModelTier,
        invoke_backend: InvokeBackend,
        stream: bool,
        routing_reason: RoutingReason,
    ) -> ExecutionResult:
        ctx.tiers_attempted.append(tier.value)
        response, _, _ = await invoke_backend(
            tier=tier, payload=payload, stream=stream, ctx=ctx
        )
        return self._finalize(ctx, response, tier, routing_reason, False, None)

    async def _execute_streaming(
        self,
        ctx: RequestContext,
        payload: dict,
        ranked: list[ModelTier],
        invoke_backend: InvokeBackend,
        routing_reason: RoutingReason,
    ) -> ExecutionResult:
        degraded = False
        degradation_reason: DegradationReason | None = None
        best_tier = ranked[0] if ranked else ModelTier.SMALL
        best_response = None

        for tier in ranked:
            if not self._tier_viable(tier):
                continue
            if not self.preflight_budget_ok(ctx, tier):
                degraded = True
                degradation_reason = DegradationReason.DEADLINE_TOO_TIGHT
                routing_reason = RoutingReason.SLO_FORCED_ESCALATION
                continue
            ctx.tiers_attempted.append(tier.value)
            best_tier = tier
            best_response, _, _ = await invoke_backend(
                tier=tier, payload=payload, stream=True, ctx=ctx
            )
            ctx.update_elapsed()
            break

        if best_response is None:
            best_tier = ModelTier.SMALL
            best_response, _, _ = await invoke_backend(
                tier=best_tier, payload=payload, stream=True, ctx=ctx, force=True
            )
            degraded = True
            degradation_reason = DegradationReason.MODEL_CAPACITY
            routing_reason = RoutingReason.FALLBACK

        return self._finalize(
            ctx, best_response, best_tier, routing_reason, degraded, degradation_reason
        )

    def _can_escalate(
        self, ctx: RequestContext, current: ModelTier, ranked: list[ModelTier]
    ) -> bool:
        remaining = ctx.remaining_budget_ms()
        if remaining is None:
            return True
        try:
            idx = ranked.index(current)
        except ValueError:
            return False
        for next_tier in ranked[idx + 1 :]:
            if self.preflight_budget_ok(ctx, next_tier):
                return True
        return False

    def _finalize(
        self,
        ctx: RequestContext,
        response: Any,
        tier: ModelTier,
        routing_reason: RoutingReason,
        degraded: bool,
        degradation_reason: DegradationReason | None,
    ) -> ExecutionResult:
        ctx.update_elapsed()
        ctx.final_tier = tier.value
        ctx.final_backend_model = self.registry.by_tier(tier).config.backend_model
        ctx.routing_reason = routing_reason.value
        ctx.degraded = degraded and not ctx.allow_degraded_ok
        ctx.degradation_reason = (
            degradation_reason.value if ctx.degraded and degradation_reason else None
        )
        ctx.status = "completed"
        endpoint_state = self.registry.by_tier(tier)
        return ExecutionResult(
            response=response,
            ctx=ctx,
            routing_reason=routing_reason,
            degraded=ctx.degraded,
            degradation_reason=degradation_reason if ctx.degraded else None,
            backend_model=endpoint_state.config.backend_model,
            endpoint_name=endpoint_state.config.name,
            tier=tier,
        )
