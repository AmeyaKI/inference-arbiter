"""Export benchmark sessions to repo-tracked files under benchmarks/."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BENCHMARKS_DIR = PROJECT_ROOT / "benchmarks"
RESULTS_DIR = BENCHMARKS_DIR / "results"
SESSIONS_DIR = BENCHMARKS_DIR / "sessions"
LATEST_JSON = BENCHMARKS_DIR / "latest.json"
LATEST_MD = BENCHMARKS_DIR / "latest.md"

DEFAULT_TIER_WEIGHTS = {"small": 1.0, "medium": 3.0, "large": 8.0}
SCENARIO_ORDER = ("baseline", "round_robin", "random", "arbiter")

ROUTING_CSV_FIELDS = [
    "request_id",
    "timestamp",
    "priority",
    "requested_model",
    "final_tier",
    "routing_reason",
    "tiers_attempted",
    "elapsed_ms",
    "status",
    "degraded",
    "failure_attribution",
    "prompt_preview",
    "response_preview",
    "estimated_tokens",
]


def _pct_improvement(baseline: float, value: float) -> float | None:
    if not baseline or not value:
        return None
    return round((baseline - value) / baseline * 100, 1)


def _repo_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def filter_since(records: list[dict[str, Any]], since_ts: float) -> list[dict[str, Any]]:
    if since_ts <= 0:
        return list(records)
    return [r for r in records if float(r.get("timestamp") or 0) >= since_ts]


def tier_distribution(
    audit_records: list[dict[str, Any]],
    *,
    requested_model: str | None = None,
) -> dict[str, float]:
    counts: Counter[str] = Counter()
    for record in audit_records:
        if requested_model and record.get("requested_model") != requested_model:
            continue
        tier = record.get("final_tier")
        if tier:
            counts[str(tier).lower()] += 1
    total = sum(counts.values())
    if not total:
        return {}
    return {tier: round(count / total, 4) for tier, count in sorted(counts.items())}


def baseline_tier_distribution(baseline_model: str = "large") -> dict[str, float]:
    tier = (baseline_model or "large").strip().lower()
    if tier not in DEFAULT_TIER_WEIGHTS:
        tier = "large"
    return {tier: 1.0}


def cost_proxy(
    distribution: dict[str, float],
    tier_weights: dict[str, float] | None = None,
) -> float | None:
    if not distribution:
        return None
    weights = tier_weights or DEFAULT_TIER_WEIGHTS
    return round(sum(distribution.get(tier, 0.0) * weights.get(tier, 1.0) for tier in weights), 3)


def build_comparison(completed_runs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    baseline = completed_runs.get("baseline")
    arbiter = completed_runs.get("arbiter")
    if not baseline or not arbiter:
        return {}

    baseline_p50 = float(baseline.get("p50_ms") or 0)
    baseline_p95 = float(baseline.get("p95_ms") or 0)
    arbiter_p50 = float(arbiter.get("p50_ms") or 0)
    arbiter_p95 = float(arbiter.get("p95_ms") or 0)
    baseline_rps = float(baseline.get("rps") or 0)
    arbiter_rps = float(arbiter.get("rps") or 0)

    rps_delta_pct = None
    if baseline_rps:
        rps_delta_pct = round((arbiter_rps - baseline_rps) / baseline_rps * 100, 1)

    return {
        "p50_improvement_pct": _pct_improvement(baseline_p50, arbiter_p50),
        "p95_improvement_pct": _pct_improvement(baseline_p95, arbiter_p95),
        "rps_delta_pct": rps_delta_pct,
        "baseline_p50_ms": baseline_p50,
        "arbiter_p50_ms": arbiter_p50,
        "baseline_p95_ms": baseline_p95,
        "arbiter_p95_ms": arbiter_p95,
    }


def routing_records_to_csv(records: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=ROUTING_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        payload = record.get("payload") or {}
        metrics = record.get("metrics") or {}
        writer.writerow(
            {
                "request_id": record.get("request_id", ""),
                "timestamp": (
                    datetime.fromtimestamp(record["timestamp"]).isoformat()
                    if record.get("timestamp")
                    else ""
                ),
                "priority": record.get("priority", ""),
                "requested_model": record.get("requested_model", ""),
                "final_tier": record.get("final_tier", ""),
                "routing_reason": record.get("routing_reason", ""),
                "tiers_attempted": ",".join(record.get("tiers_attempted") or []),
                "elapsed_ms": metrics.get("current_elapsed_ms", ""),
                "status": record.get("status", ""),
                "degraded": record.get("degraded", ""),
                "failure_attribution": record.get("failure_attribution", ""),
                "prompt_preview": payload.get("prompt_preview", ""),
                "response_preview": record.get("response_preview", ""),
                "estimated_tokens": payload.get("estimated_tokens", ""),
            }
        )
    return buf.getvalue()


def build_run_archive_payload(
    snap: dict[str, Any],
    *,
    label: str = "",
    baseline_model: str = "large",
    timeseries: list[dict[str, Any]] | None = None,
    latencies_ms: list[float] | None = None,
    audit_records: list[dict[str, Any]] | None = None,
    live_events: list[dict[str, Any]] | None = None,
    metrics_summary: dict[str, Any] | None = None,
    metrics_timeseries: dict[str, Any] | None = None,
    completed_runs: dict[str, dict[str, Any]] | None = None,
    tier_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    saved_at = datetime.now(timezone.utc).isoformat()
    audit_records = audit_records or []
    live_events = live_events or []
    weights = tier_weights or DEFAULT_TIER_WEIGHTS
    completed_runs = completed_runs or {}

    run_tiers = tier_distribution(audit_records)
    arbiter_tiers = tier_distribution(audit_records, requested_model="auto")
    baseline_cost = cost_proxy(baseline_tier_distribution(baseline_model), weights)
    arbiter_cost = cost_proxy(arbiter_tiers, weights)
    cost_reduction_pct = None
    if baseline_cost and arbiter_cost is not None and baseline_cost > 0:
        cost_reduction_pct = round((baseline_cost - arbiter_cost) / baseline_cost * 100, 1)

    benchmark = {
        **snap,
        "baseline_model": baseline_model,
        "timeseries": timeseries or [],
        "latencies_ms": latencies_ms or [],
    }

    return {
        "saved_at": saved_at,
        "label": label.strip() or None,
        "benchmark": benchmark,
        "session_completed_runs": completed_runs,
        "comparison": build_comparison(completed_runs),
        "routing_breakdown": audit_records,
        "live_feed": live_events,
        "metrics": {
            "summary": metrics_summary or {"available": False},
            "timeseries": metrics_timeseries or {"available": False},
        },
        "tier_distribution": {
            "run": run_tiers,
            "arbiter_auto": arbiter_tiers,
        },
        "cost_proxy": {
            "baseline_model": baseline_model,
            "baseline_weighted": baseline_cost,
            "arbiter_weighted": arbiter_cost,
            "reduction_pct": cost_reduction_pct,
            "tier_weights": weights,
        },
    }


def render_run_markdown(payload: dict[str, Any]) -> str:
    bench = payload.get("benchmark") or {}
    lines = [
        "# Benchmark run",
        "",
        f"Saved: {payload.get('saved_at', '—')}",
        f"Scenario: **{bench.get('scenario', '—')}**",
    ]
    if payload.get("label"):
        lines.append(f"Label: {payload['label']}")
    lines.extend(
        [
            "",
            "## Latency",
            "",
            f"- P50: **{bench.get('p50_ms', '—')}ms**",
            f"- P95: **{bench.get('p95_ms', '—')}ms**",
            f"- P99: **{bench.get('p99_ms', '—')}ms**",
            f"- RPS: **{bench.get('rps', '—')}**",
            f"- Requests: **{bench.get('requests', '—')}** (failures: {bench.get('failures', 0)})",
            f"- Users: **{bench.get('users', '—')}** · duration: **{bench.get('duration_s', '—')}s**",
            "",
            "## Routing breakdown",
            "",
            f"- Requests captured: **{len(payload.get('routing_breakdown') or [])}**",
            f"- Live feed events: **{len(payload.get('live_feed') or [])}**",
        ]
    )

    tiers = (payload.get("tier_distribution") or {}).get("run") or {}
    if tiers:
        parts = [f"{tier} {pct * 100:.1f}%" for tier, pct in sorted(tiers.items())]
        lines.extend(["", "## Tier mix (this run)", "", "- " + ", ".join(parts)])

    metrics = payload.get("metrics") or {}
    summary = metrics.get("summary") or {}
    if summary.get("available"):
        lines.extend(["", "## Metrics snapshot", ""])
        slo = summary.get("slo_attainment_rate")
        if slo is not None:
            lines.append(f"- SLO attainment: **{slo * 100:.1f}%**")
        lines.append(
            f"- Bandit policy: **{'active' if summary.get('bandit_policy_active') else 'heuristic'}**"
        )
        tier_rate = summary.get("tier_rate") or summary.get("tier_totals") or {}
        if tier_rate:
            lines.append(
                "- Tier rates: "
                + ", ".join(f"{tier} {rate:.3f}" for tier, rate in sorted(tier_rate.items()))
            )
    else:
        lines.extend(
            [
                "",
                "## Metrics snapshot",
                "",
                f"- Prometheus unavailable: {summary.get('error', 'not reachable')}",
            ]
        )

    lines.append("")
    return "\n".join(lines)


async def save_benchmark_run_archive(
    snap: dict[str, Any],
    *,
    label: str = "",
    baseline_model: str = "large",
    run_started_at: float,
    timeseries: list[dict[str, Any]],
    latencies_ms: list[float],
    get_audit_records: Any,
    get_live_events: Any,
    fetch_metrics_summary: Any,
    fetch_metrics_timeseries: Any,
    completed_runs: dict[str, dict[str, Any]],
    tier_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    if not snap.get("scenario") or snap.get("requests", 0) == 0:
        raise ValueError("benchmark run has no requests to archive")

    audit_all = get_audit_records() if get_audit_records else []
    live_all = get_live_events() if get_live_events else []
    audit_records = filter_since(audit_all, run_started_at)
    live_events = filter_since(live_all, run_started_at)

    metrics_summary: dict[str, Any] = {"available": False}
    metrics_timeseries: dict[str, Any] = {"available": False}
    if fetch_metrics_summary:
        try:
            metrics_summary = await fetch_metrics_summary()
        except Exception as exc:
            metrics_summary = {"available": False, "error": str(exc)}
    if fetch_metrics_timeseries:
        try:
            metrics_timeseries = await fetch_metrics_timeseries()
        except Exception as exc:
            metrics_timeseries = {"available": False, "error": str(exc)}

    payload = build_run_archive_payload(
        snap,
        label=label,
        baseline_model=baseline_model,
        timeseries=timeseries,
        latencies_ms=latencies_ms,
        audit_records=audit_records,
        live_events=live_events,
        metrics_summary=metrics_summary,
        metrics_timeseries=metrics_timeseries,
        completed_runs=completed_runs,
        tier_weights=tier_weights,
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    scenario = str(snap.get("scenario", "run"))
    run_dir = RESULTS_DIR / f"{scenario}_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "run.json": json.dumps(payload, indent=2) + "\n",
        "summary.md": render_run_markdown(payload),
        "routing.csv": routing_records_to_csv(audit_records),
        "live_feed.json": json.dumps(live_events, indent=2) + "\n",
        "benchmark_timeseries.json": json.dumps(timeseries, indent=2) + "\n",
        "metrics_summary.json": json.dumps(metrics_summary, indent=2) + "\n",
        "metrics_timeseries.json": json.dumps(metrics_timeseries, indent=2) + "\n",
    }
    for name, content in files.items():
        (run_dir / name).write_text(content)

    return {
        "saved_at": payload["saved_at"],
        "label": payload.get("label"),
        "scenario": scenario,
        "run_dir": _repo_path(run_dir),
        "files": {name: _repo_path(run_dir / name) for name in files},
        "routing_count": len(audit_records),
        "live_feed_count": len(live_events),
        "timeseries_points": len(timeseries),
    }


def build_session_payload(
    completed_runs: dict[str, dict[str, Any]],
    *,
    label: str = "",
    audit_records: list[dict[str, Any]] | None = None,
    tier_weights: dict[str, float] | None = None,
    baseline_model: str = "large",
) -> dict[str, Any]:
    saved_at = datetime.now(timezone.utc).isoformat()
    audit_records = audit_records or []
    weights = tier_weights or DEFAULT_TIER_WEIGHTS
    baseline_run = completed_runs.get("baseline") or {}
    baseline_model = baseline_run.get("baseline_model") or baseline_model

    arbiter_tiers = tier_distribution(audit_records, requested_model="auto")
    session_tiers = tier_distribution(audit_records)
    baseline_cost = cost_proxy(baseline_tier_distribution(baseline_model), weights)
    arbiter_cost = cost_proxy(arbiter_tiers, weights)

    cost_reduction_pct = None
    if baseline_cost and arbiter_cost is not None and baseline_cost > 0:
        cost_reduction_pct = round((baseline_cost - arbiter_cost) / baseline_cost * 100, 1)

    runs = {
        name: completed_runs[name]
        for name in SCENARIO_ORDER
        if name in completed_runs
    }
    for name, snap in completed_runs.items():
        if name not in runs:
            runs[name] = snap

    return {
        "saved_at": saved_at,
        "label": label.strip() or None,
        "runs": runs,
        "comparison": build_comparison(completed_runs),
        "tier_distribution": {
            "session": session_tiers,
            "arbiter_auto": arbiter_tiers,
        },
        "cost_proxy": {
            "baseline_model": baseline_model,
            "baseline_weighted": baseline_cost,
            "arbiter_weighted": arbiter_cost,
            "reduction_pct": cost_reduction_pct,
            "tier_weights": weights,
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Benchmark results",
        "",
        f"Saved: {payload.get('saved_at', '—')}",
    ]
    if payload.get("label"):
        lines.append(f"Label: {payload['label']}")
    lines.extend(["", "## Runs", ""])

    header = "| Scenario | Users | P50 | P95 | RPS | Requests | Failures |"
    sep = "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"
    lines.extend([header, sep])

    for name, run in (payload.get("runs") or {}).items():
        lines.append(
            f"| {name} | {run.get('users', '—')} "
            f"| {run.get('p50_ms', '—')}ms | {run.get('p95_ms', '—')}ms "
            f"| {run.get('rps', '—')} | {run.get('requests', '—')} "
            f"| {run.get('failures', '—')} |"
        )

    comparison = payload.get("comparison") or {}
    if comparison:
        lines.extend(["", "## Baseline vs arbiter", ""])
        if comparison.get("p50_improvement_pct") is not None:
            lines.append(f"- P50 improvement: **{comparison['p50_improvement_pct']}%**")
        if comparison.get("p95_improvement_pct") is not None:
            lines.append(f"- P95 improvement: **{comparison['p95_improvement_pct']}%**")
        if comparison.get("rps_delta_pct") is not None:
            lines.append(f"- RPS delta: **{comparison['rps_delta_pct']}%**")

    cost = payload.get("cost_proxy") or {}
    tiers = (payload.get("tier_distribution") or {}).get("arbiter_auto") or {}
    if tiers or cost.get("reduction_pct") is not None:
        lines.extend(["", "## Arbiter tier mix (auto requests)", ""])
        if tiers:
            parts = [f"{tier} {pct * 100:.1f}%" for tier, pct in sorted(tiers.items())]
            lines.append("- " + ", ".join(parts))
        if cost.get("arbiter_weighted") is not None:
            baseline_label = cost.get("baseline_model", "large")
            lines.append(
                f"- Cost proxy (weighted): **{cost['arbiter_weighted']}** "
                f"vs baseline ({baseline_label}) **{cost.get('baseline_weighted')}**"
            )
        if cost.get("reduction_pct") is not None:
            lines.append(f"- Estimated cost reduction: **{cost['reduction_pct']}%**")

    lines.append("")
    return "\n".join(lines)


def save_benchmark_session(
    completed_runs: dict[str, dict[str, Any]],
    *,
    label: str = "",
    audit_records: list[dict[str, Any]] | None = None,
    audit_since: float = 0.0,
    tier_weights: dict[str, float] | None = None,
    baseline_model: str = "large",
) -> dict[str, Any]:
    if not completed_runs:
        raise ValueError("no completed benchmark runs to save")

    scoped_audit = filter_since(audit_records or [], audit_since)
    payload = build_session_payload(
        completed_runs,
        label=label,
        audit_records=scoped_audit,
        tier_weights=tier_weights,
        baseline_model=baseline_model,
    )
    markdown = render_markdown(payload)

    BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_path = SESSIONS_DIR / f"{ts}.json"

    latest_json = json.dumps(payload, indent=2) + "\n"
    LATEST_JSON.write_text(latest_json)
    LATEST_MD.write_text(markdown)
    session_path.write_text(latest_json)

    return {
        "saved_at": payload["saved_at"],
        "label": payload.get("label"),
        "paths": {
            "latest_json": _repo_path(LATEST_JSON),
            "latest_md": _repo_path(LATEST_MD),
            "session_json": _repo_path(session_path),
        },
        "run_count": len(payload["runs"]),
        "comparison": payload.get("comparison"),
        "cost_proxy": payload.get("cost_proxy"),
    }
