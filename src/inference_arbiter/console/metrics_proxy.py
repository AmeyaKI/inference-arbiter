"""Prometheus query proxy for the console metrics tab."""

from __future__ import annotations

import math
from typing import Any

import httpx


async def _query(prometheus_url: str, promql: str) -> list[dict[str, Any]]:
    url = f"{prometheus_url.rstrip('/')}/api/v1/query"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url, params={"query": promql})
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            return []
        return data.get("data", {}).get("result", [])


def _safe_float(v: Any) -> float | None:
    """Parse a Prometheus value string, returning None for NaN/Inf/unparseable."""
    try:
        f = float(v)
        return None if not math.isfinite(f) else f
    except (TypeError, ValueError):
        return None


def _scalar(results: list[dict[str, Any]]) -> float | None:
    if not results:
        return None
    value = results[0].get("value")
    if not value or len(value) < 2:
        return None
    return _safe_float(value[1])


def _labeled_series(results: list[dict[str, Any]], label_key: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in results:
        labels = row.get("metric", {})
        label = labels.get(label_key, "unknown")
        value = row.get("value")
        if value and len(value) >= 2:
            f = _safe_float(value[1])
            if f is not None:
                out[label] = f
    return out


async def _query_range(
    prometheus_url: str,
    promql: str,
    range_minutes: int = 10,
    step: str = "15s",
) -> list[dict[str, Any]]:
    import time as _time
    end = _time.time()
    start = end - range_minutes * 60
    url = f"{prometheus_url.rstrip('/')}/api/v1/query_range"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            url, params={"query": promql, "start": start, "end": end, "step": step}
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            return []
        return data.get("data", {}).get("result", [])


def _timeseries(result: list[dict[str, Any]]) -> list[list]:
    """Extract [[epoch_ms, value], ...] from a query_range result, dropping NaN."""
    if not result:
        return []
    values = result[0].get("values", [])
    out = []
    for ts, v in values:
        f = _safe_float(v)
        if f is not None:
            out.append([int(float(ts) * 1000), f])
    return out


async def fetch_timeseries_data(prometheus_url: str) -> dict[str, Any]:
    try:
        rps_r = await _query_range(
            prometheus_url, "sum(rate(requests_routed_total[30s]))"
        )
        fail_r = await _query_range(
            prometheus_url,
            "sum(rate(slo_breach_total[30s])) + sum(rate(batch_shed_total[30s]))",
        )
        p50_r = await _query_range(
            prometheus_url,
            "histogram_quantile(0.5, sum by (le) (rate(request_latency_seconds_bucket[30s]))) * 1000",
        )
        p95_r = await _query_range(
            prometheus_url,
            "histogram_quantile(0.95, sum by (le) (rate(request_latency_seconds_bucket[30s]))) * 1000",
        )
        inflight_r = await _query_range(prometheus_url, "sum(endpoint_in_flight)")
        return {
            "available": True,
            "rps": _timeseries(rps_r),
            "failures_per_s": _timeseries(fail_r),
            "p50_ms": _timeseries(p50_r),
            "p95_ms": _timeseries(p95_r),
            "in_flight": _timeseries(inflight_r),
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


async def fetch_metrics_summary(prometheus_url: str) -> dict[str, Any]:
    try:
        tier_rate = await _query(
            prometheus_url,
            "sum by (tier) (rate(requests_routed_total[5m]))",
        )
        slo_met = await _query(prometheus_url, "sum(slo_met_total)")
        slo_eval = await _query(prometheus_url, "sum(slo_evaluated_total)")
        cost_rate = await _query(
            prometheus_url,
            "sum by (tier) (rate(cost_proxy_total[5m]))",
        )
        bandit_active = await _query(prometheus_url, "bandit_policy_active")
        bandit_obs = await _query(
            prometheus_url,
            "sum by (tier) (bandit_observations_total)",
        )
        in_flight = await _query(prometheus_url, "endpoint_in_flight")
        circuit = await _query(prometheus_url, "circuit_breaker_state")
        routing_reasons = await _query(
            prometheus_url,
            "sum by (reason) (increase(routing_decision_total[5m]))",
        )
        ttft_p95 = await _query(
            prometheus_url,
            "histogram_quantile(0.95, sum by (le, tier) (rate(time_to_first_token_seconds_bucket[5m])))",
        )
        # Cumulative totals — safe fallback for sparse/new deployments
        tier_totals = await _query(
            prometheus_url,
            "sum by (tier) (requests_routed_total)",
        )

        met = _scalar(slo_met) or 0.0
        evaluated = _scalar(slo_eval) or 0.0
        slo_rate = (met / evaluated) if evaluated > 0 else None

        tier_rate_data = _labeled_series(tier_rate, "tier")
        # Fall back to cumulative totals when rate window has no data
        tier_totals_data = _labeled_series(tier_totals, "tier")

        return {
            "available": True,
            "tier_rate": tier_rate_data,
            "tier_totals": tier_totals_data,
            "cost_rate": _labeled_series(cost_rate, "tier"),
            "slo_attainment_rate": slo_rate,
            "slo_met": met,
            "slo_evaluated": evaluated,
            "bandit_policy_active": bool(_scalar(bandit_active)),
            "bandit_observations": _labeled_series(bandit_obs, "tier"),
            "endpoint_in_flight": _labeled_series(in_flight, "endpoint"),
            "circuit_breaker_state": _labeled_series(circuit, "endpoint"),
            "routing_reasons": _labeled_series(routing_reasons, "reason"),
            "ttft_p95_seconds": _labeled_series(ttft_p95, "tier"),
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}
