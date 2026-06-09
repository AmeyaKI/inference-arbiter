#!/usr/bin/env python3
"""Run KPI evaluation scenarios and write results JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def main() -> int:
    results = {
        "console_url": "http://localhost:8080/console",
        "recommended_workflow": [
            "bash scripts/arbiter  # or: make dev",
            "Open http://localhost:8080/console",
            "Run Baseline then Arbiter from Benchmark tab",
            "Compare results in footer strip",
        ],
        "headless_scenarios": [
            {
                "name": "baseline_all_large",
                "command": "make bench SCENARIO=baseline USERS=10 DURATION=3m",
            },
            {
                "name": "round_robin",
                "command": "make bench SCENARIO=round_robin USERS=10 DURATION=3m",
            },
            {
                "name": "inference_arbiter_full",
                "command": "make bench SCENARIO=arbiter USERS=10 DURATION=3m",
            },
        ],
        "kpis": [
            "slo_attainment_rate",
            "cost_proxy_per_million_tokens",
            "bandit_convergence_requests",
        ],
        "note": "Use the unified console for visual benchmarking; Metrics tab queries Prometheus automatically.",
    }
    out = RESULTS_DIR / "latest.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"Wrote eval scaffold to {out}")
    print("Recommended: bash scripts/arbiter  →  http://localhost:8080/console")
    return 0


if __name__ == "__main__":
    sys.exit(main())
