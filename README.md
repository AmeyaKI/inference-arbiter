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

| Subsystem | Module | Role |
|-----------|--------|------|
| A | `routing/admission.py` | INTERACTIVE/BATCH admission, P95 spike shedding |
| B | `routing/bandit.py` | LinUCB ranked tier selection, heuristic cold start |
| C | `routing/executor.py` | SLO cascade loop with `RequestContext` |
| D | `verification/`, `telemetry/` | Fast verifiers + ring buffer + background updater |

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

docker compose up -d          # Ollama + gateway + Prometheus + Grafana
make test                     # unit + integration tests
make run                      # gateway on :8080
```

### Chat request

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

## Configuration

All tunables in [`config.yaml`](config.yaml). Environment overrides use `ARBITER_*` prefix (env wins over YAML).

## Load testing

Three Locust scenarios (70/20/10 prompt mix):

```bash
make load-baseline   # all → largest tier
make load-arbiter    # full inference-arbiter system
```

See [`load/locustfile.py`](load/locustfile.py).

## KPIs (Prometheus / Grafana)

- `slo_met_total` / `slo_evaluated_total` — SLO attainment rate
- `cost_proxy_total` — tier-weighted cost proxy
- `bandit_policy_active`, `bandit_observations_total` — convergence

Grafana dashboard: `observability/dashboard/inference-arbiter.json`

## Interview answers (implemented in code)

1. **Wall-clock SLO across a cascade?** Pre-flight TTFT check in `routing/executor.py` using atomic `RequestContext`.
2. **Decouple quality vs latency failures?** Three-way `FailureAttribution` in telemetry with separate bandit rewards.
3. **Bandit cold start?** Heuristic prior until `cold_start_min_observations_per_tier` in `routing/bandit.py`.

## Development

```bash
.venv/bin/pytest tests/ -v
.venv/bin/ruff check src/
```
