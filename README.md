# inference-arbiter

OpenAI-compatible LLM inference gateway with **deadline-aware cascade routing** and **online contextual bandit learning** from production telemetry.

## Positioning

RouteLLM and PROTEUS/SCORE are research papers with no production implementation. LiteLLM and Bifrost are production gateways with no routing intelligence. **inference-arbiter combines both**: circuit breakers, priority shedding, streaming SSE, and adaptive routing that enforces wall-clock SLO deadlines while learning from live latency signals.

### Three novel open-source claims

1. **SLO-budget-decay enforcement** — `T_remaining = deadline - elapsed` checked before each tier invocation; escalation only when verifier rejects and budget permits.
2. **Active batch shedding at the router layer** — BATCH traffic gets 503 + Retry-After under saturation; INTERACTIVE is restricted to non-saturated tiers.
3. **Online LinUCB bandit** — learns from production telemetry tuples, not offline Arena labels.

## Architecture

```
Client → Gateway → Admission → Bandit → Executor → Ollama (1b/3b/8b)
                      ↓                      ↓
                   shed BATCH          Verifiers + Telemetry → Bandit updater
```

| Subsystem | Module                        | Role                                               |
| --------- | ----------------------------- | -------------------------------------------------- |
| A         | `routing/admission.py`        | INTERACTIVE/BATCH admission, P95 spike shedding    |
| B         | `routing/bandit.py`           | LinUCB ranked tier selection, heuristic cold start |
| C         | `routing/executor.py`         | SLO cascade loop with `RequestContext`             |
| D         | `verification/`, `telemetry/` | Fast verifiers + ring buffer + background updater  |

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

make dev          # starts stack + opens unified console
make test         # unit + integration tests
```

**One URL for everything:** http://localhost:8080/console

From the console you can:
- Watch requests route in real time (Live tab)
- Run Baseline vs Arbiter benchmarks (Benchmark tab)
- View SLO, cost, and bandit KPIs (Metrics tab)

### Manual chat request

```bash
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "What is 2+2?"}],
    "x_slo_deadline_ms": 5000,
    "x_priority": "interactive"
  }' -D -
```

Response headers include `X-Model-Tier`, `X-Routing-Reason`, `X-Tiers-Attempted`, `X-Elapsed-Ms`.

### Audit trail

```bash
curl "http://localhost:8080/v1/routing/decisions?request_id=<id>"
```

Or click any request in the console Live tab.

## Configuration

All tunables in [`config.yaml`](config.yaml). Environment overrides use `ARBITER_*` prefix (env wins over YAML).

## Benchmarking

**Recommended:** use the console Benchmark tab at http://localhost:8080/console

**Headless (CI):**

```bash
make bench SCENARIO=baseline USERS=10 DURATION=3m
make bench SCENARIO=arbiter
make bench SCENARIO=round_robin
```

Legacy aliases: `make load-baseline`, `make load-arbiter`, `make load-round-robin`

## Advanced observability

Prometheus and Grafana still run via Docker for power users:

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (`admin` / `admin`)
- Dashboard: `deploy/grafana/dashboards/inference-arbiter.json`

The console Metrics tab queries Prometheus on your behalf — you don't need to open Grafana for daily use.

## Code details

1. **Wall-clock SLO across a cascade?** Pre-flight TTFT check in `routing/executor.py` using atomic `RequestContext`.
2. **Decouple quality vs latency failures?** Three-way `FailureAttribution` in telemetry with separate bandit rewards.
3. **Bandit cold start?** Heuristic prior until `cold_start_min_observations_per_tier` in `routing/bandit.py`.

## Development

```bash
.venv/bin/pytest tests/ -v
.venv/bin/ruff check src/
make run              # gateway only (needs Ollama at :11434)
make console          # print console URL
```
