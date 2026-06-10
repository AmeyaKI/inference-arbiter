"""Unit tests for built-in benchmark runner."""

from __future__ import annotations

import pytest

from inference_arbiter.benchmark.runner import BenchmarkRunner, _percentile


def test_percentile_empty():
    assert _percentile([], 50) == 0.0


def test_percentile_values():
    values = [10.0, 20.0, 30.0, 40.0, 100.0]
    assert _percentile(values, 50) == 30.0
    assert _percentile(values, 95) == 100.0


def test_build_payload_scenarios():
    runner = BenchmarkRunner()
    baseline = runner._build_payload("baseline")
    assert baseline["model"] == "large"

    rr = runner._build_payload("round_robin")
    assert rr["model"] in ("small", "medium", "large")

    arbiter = runner._build_payload("arbiter")
    assert arbiter["model"] == "auto"


def test_reset_session_clears_completed_runs():
    runner = BenchmarkRunner()
    runner._completed_runs["baseline"] = {"scenario": "baseline"}
    runner._session_started_at = 100.0
    runner.reset_session()
    assert runner.completed_runs == {}
    assert runner.session_started_at is None


@pytest.mark.asyncio
async def test_benchmark_start_stop_without_server():
    runner = BenchmarkRunner(base_url="http://127.0.0.1:1")
    status = await runner.start("baseline", users=1, spawn_rate=10, duration_s=1.0)
    assert status["running"] is True
    assert status["scenario"] == "baseline"
    await runner.stop()
    final = await runner.status()
    assert final["running"] is False
    assert "baseline" in final["completed_runs"]
    assert runner.session_started_at is not None
    assert "timeseries" in final
