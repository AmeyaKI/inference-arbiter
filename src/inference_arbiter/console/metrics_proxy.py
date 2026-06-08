"""Prometheus query proxy for the console metrics tab."""

from __future__ import annotations

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


def _scalar(results: list[dict[str, Any]]) -> float | None:
    if not results:
        return None
    value = results[0].get("value")
    if not value or len(value) < 2:
        return None
    try:
        return float(value[1])
    except (TypeError, ValueError):
        return None


def _labeled_series(results: list[dict[str, Any]], label_key: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in results:
        labels = row.get("metric", {})
        label = labels.get(label_key, "unknown")
        value = row.get("value")
        if value and len(value) >= 2:
            try:
                out[label] = float(value[1])
            except (TypeError, ValueError):
                continue
    return out


async def fetch_metrics_summary(prometheus_url: str) -> dict[str, Any]:
    try:
        tier_rate = await _query(
            prometheus_url,
            "sum by (tier) (rate(requests_routed_total[1m]))",
        )
        slo_met = await _query(prometheus_url, "sum(slo_met_total)")
        slo_eval = await _query(prometheus_url, "sum(slo_evaluated_total)")
        cost_rate = await _query(
            prometheus_url,
            "sum by (tier) (rate(cost_proxy_total[1m]))",
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

        met = _scalar(slo_met) or 0.0
        evaluated = _scalar(slo_eval) or 0.0
        slo_rate = (met / evaluated) if evaluated > 0 else None

        return {
            "available": True,
            "tier_rate": _labeled_series(tier_rate, "tier"),
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
