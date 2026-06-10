"""Tests for benchmark session export."""

from __future__ import annotations

import json

import pytest

from inference_arbiter.benchmark.export import (
    build_comparison,
    build_run_archive_payload,
    build_session_payload,
    cost_proxy,
    filter_since,
    render_markdown,
    render_run_markdown,
    save_benchmark_run_archive,
    save_benchmark_session,
    tier_distribution,
)


def _run(scenario: str, **overrides) -> dict:
    base = {
        "scenario": scenario,
        "users": 5,
        "p50_ms": 1000.0,
        "p95_ms": 2000.0,
        "rps": 1.5,
        "requests": 120,
        "failures": 0,
    }
    base.update(overrides)
    return base


def test_tier_distribution_filters_auto():
    records = [
        {"requested_model": "auto", "final_tier": "small"},
        {"requested_model": "auto", "final_tier": "small"},
        {"requested_model": "large", "final_tier": "large"},
    ]
    dist = tier_distribution(records, requested_model="auto")
    assert dist == {"small": 1.0}


def test_cost_proxy_weighted():
    dist = {"small": 0.6, "medium": 0.25, "large": 0.15}
    assert cost_proxy(dist) == pytest.approx(2.55)


def test_build_comparison():
    runs = {
        "baseline": _run("baseline", p50_ms=8000, p95_ms=12000, rps=0.5),
        "arbiter": _run("arbiter", p50_ms=1500, p95_ms=6000, rps=1.2),
    }
    cmp = build_comparison(runs)
    assert cmp["p50_improvement_pct"] == 81.2
    assert cmp["rps_delta_pct"] == 140.0


def test_save_benchmark_session_writes_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "inference_arbiter.benchmark.export.BENCHMARKS_DIR",
        tmp_path,
    )
    monkeypatch.setattr(
        "inference_arbiter.benchmark.export.RESULTS_DIR",
        tmp_path / "results",
    )
    monkeypatch.setattr(
        "inference_arbiter.benchmark.export.SESSIONS_DIR",
        tmp_path / "sessions",
    )
    monkeypatch.setattr(
        "inference_arbiter.benchmark.export.LATEST_JSON",
        tmp_path / "latest.json",
    )
    monkeypatch.setattr(
        "inference_arbiter.benchmark.export.LATEST_MD",
        tmp_path / "latest.md",
    )

    runs = {"baseline": _run("baseline"), "arbiter": _run("arbiter", p50_ms=500)}
    audit = [
        {"requested_model": "auto", "final_tier": "small"},
        {"requested_model": "auto", "final_tier": "medium"},
    ]
    result = save_benchmark_session(runs, label="test-run", audit_records=audit)

    assert result["run_count"] == 2
    assert (tmp_path / "latest.json").exists()
    assert (tmp_path / "latest.md").exists()
    payload = json.loads((tmp_path / "latest.json").read_text())
    assert payload["label"] == "test-run"
    assert "arbiter" in payload["runs"]
    assert "baseline vs arbiter" in render_markdown(payload).lower()


def test_save_benchmark_session_filters_audit_since():
    runs = {"baseline": _run("baseline", baseline_model="medium")}
    audit = [
        {"timestamp": 50.0, "requested_model": "auto", "final_tier": "small"},
        {"timestamp": 150.0, "requested_model": "auto", "final_tier": "medium"},
    ]
    payload = build_session_payload(runs, audit_records=filter_since(audit, 100.0))
    assert payload["tier_distribution"]["arbiter_auto"] == {"medium": 1.0}
    assert payload["cost_proxy"]["baseline_model"] == "medium"


def test_save_benchmark_session_requires_runs():
    with pytest.raises(ValueError, match="no completed"):
        save_benchmark_session({})


def test_filter_since():
    records = [
        {"timestamp": 100.0, "request_id": "a"},
        {"timestamp": 200.0, "request_id": "b"},
    ]
    assert len(filter_since(records, 150.0)) == 1


@pytest.mark.asyncio
async def test_save_benchmark_run_archive_writes_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "inference_arbiter.benchmark.export.RESULTS_DIR",
        tmp_path,
    )
    snap = _run("arbiter", p50_ms=900, requests=42)
    result = await save_benchmark_run_archive(
        snap,
        label="m5-test",
        run_started_at=100.0,
        timeseries=[{"elapsed_s": 1, "rps": 0.5}],
        latencies_ms=[100.0, 200.0],
        get_audit_records=lambda: [
            {"timestamp": 101.0, "request_id": "r1", "final_tier": "small", "requested_model": "auto"},
        ],
        get_live_events=lambda: [
            {"timestamp": 101.5, "request_id": "r1", "final_tier": "small"},
        ],
        fetch_metrics_summary=lambda: _async_dict({"available": True, "tier_rate": {"small": 1.0}}),
        fetch_metrics_timeseries=lambda: _async_dict({"available": True, "rps": [[1, 0.5]]}),
        completed_runs={"arbiter": snap},
    )
    from pathlib import Path

    run_dir = Path(result["run_dir"])
    assert (run_dir / "run.json").exists()
    assert (run_dir / "routing.csv").exists()
    assert (run_dir / "live_feed.json").exists()
    assert (run_dir / "benchmark_timeseries.json").exists()
    assert (run_dir / "metrics_summary.json").exists()
    payload = json.loads((run_dir / "run.json").read_text())
    assert payload["benchmark"]["requests"] == 42
    assert len(payload["routing_breakdown"]) == 1
    assert "Latency" in render_run_markdown(payload)


async def _async_dict(data: dict):
    return data
