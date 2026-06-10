# Benchmark Results

## Quick run (console — recommended)

```bash
make start   # opens http://localhost:8080/console
```

1. **Benchmark tab** → run **Baseline** (10 users · 180s · large model)
2. Run **Arbiter** with the same load parameters
3. Comparison card appears automatically (P50/P95/RPS)
4. Click **Save** → writes `benchmarks/latest.json` and `benchmarks/latest.md` on the host
5. Each run also auto-archives to `benchmarks/results/<scenario>_<timestamp>/`

Per-run archives include: `run.json`, `routing.csv`, `benchmark_timeseries.json`, `summary.md`.

## Publish to GitHub

Commit the session summary (not ephemeral per-run dirs):

```bash
git add benchmarks/latest.json benchmarks/latest.md benchmarks/results.md
git commit -m "Add benchmark results: baseline vs arbiter"
```

## Results

Prompt distribution: 70% simple · 20% medium · 10% complex (`max_tokens=64`)

| scenario | P50 (ms) | P95 (ms) | RPS | requests | failures |
|----------|----------|----------|-----|----------|----------|
| baseline | — | — | — | — | — |
| arbiter  | — | — | — | — | — |

_Fill from `benchmarks/latest.md` after running both scenarios._

## Headless (Locust)

```bash
make compare                              # quick 2-user 60s baseline + arbiter
make bench SCENARIO=baseline USERS=10 DURATION=3m
make bench SCENARIO=arbiter  USERS=10 DURATION=3m
```

Locust does not write `latest.json` — use the console **Save** button for publishable artifacts.
