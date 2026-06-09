# inference-arbiter

OpenAI-compatible LLM inference gateway with **deadline-aware cascade routing** and **online contextual bandit learning**.

Routes `POST /v1/chat/completions` across three Ollama model tiers (1b / 3b / 8b) using admission control, a LinUCB contextual bandit, SLO-budget-decay cascade execution, and online reward learning from live telemetry.

---

## Quickstart

**Requirements:** Docker, Docker Compose, Python 3.11+, ~8GB RAM for the three models.

```bash
# 1. Clone and set up the Python environment
git clone https://github.com/your-org/inference-arbiter
cd inference-arbiter
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Start the full stack (pulls models on first run — takes a few minutes)
make start        # or: arbiter → start
```

That's it. The console opens automatically at **http://localhost:8080/console**.

**Entry points** (all installed by `pip install -e ".[dev]"`):

| Command | What it does |
|---|---|
| `arbiter` | Interactive terminal REPL — stack control, benchmarks, debug |
| `make start` / `make dev` | Non-interactive stack bootstrap (same as `arbiter start`) |
| `inference-arbiter` | Run the gateway directly (no Docker; needs Ollama at `:11434`) |

---

## The Console

Everything is accessible from one URL: **http://localhost:8080/console**

| Tab | What it shows |
|---|---|
| **Live** | Every request as it routes in real time. Click any row for the full audit trail. |
| **Benchmark** | Built-in load tester. Run scenarios, watch RPS + latency live, compare results. |
| **Metrics** | SLO attainment, bandit convergence, tier distribution, endpoint health via Prometheus. |
| **Custom** | Send a one-off request and see the routing decision, response, and audit trail. |

---

## Interactive CLI

With your venv activated, run `arbiter` for a Python-style REPL (`make menu` is the same command):

```bash
arbiter
```

The shell script lives at `scripts/arbiter`; the `arbiter` command is a console entry point in `pyproject.toml` that launches it.

```
  inference-arbiter  routing control plane

  ● gateway  http://localhost:8080  mode=active

  ── stack ─────────────────────────────────────────
  start      spin up docker stack + open console
  stop       shut down all services
  rebuild    rebuild images + restart
  status     gateway + per-model readiness
  logs       tail gateway logs

  ── benchmark ─────────────────────────────────────
  compare    quick baseline vs arbiter  (2 users · 60s)
  bench      full run                   (10 users · 3 min)

  ── debug ─────────────────────────────────────────
  test       send one auto-routed request
  console    open console in browser
  exit       quit

  →
```

Commands accept both names and numbers (type `start` or `1`, `stop` or `2`, etc.). Type `help` to reprint the menu.

---

## Make Targets

```bash
arbiter           # interactive CLI REPL
make menu         # same as arbiter
make start        # start the full Docker stack (non-interactive)
make dev          # alias for make start
make stop         # shut everything down
make rebuild      # rebuild images + restart
make status       # check gateway + per-tier readiness
make request      # send one test request (pretty-printed)
make logs         # tail gateway logs
make compare      # quick 2-user 60s baseline vs arbiter comparison
make test         # run unit tests
make install      # create .venv and install dependencies
```

Headless benchmarking:

```bash
make bench SCENARIO=baseline USERS=10 DURATION=3m
make bench SCENARIO=arbiter  USERS=10 DURATION=3m
make bench SCENARIO=round_robin
make bench SCENARIO=random
```

---

## Sending Requests

The gateway is fully OpenAI-compatible. Just point your existing client at `localhost:8080`:

```bash
# Auto-routing (bandit decides the tier)
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Explain quantum entanglement simply."}]
  }'

# Pin to a specific tier
curl http://localhost:8080/v1/chat/completions \
  -d '{"model": "small", "messages": [...]}'

# Set an SLO deadline (ms) and priority
curl http://localhost:8080/v1/chat/completions \
  -d '{
    "model": "auto",
    "messages": [...],
    "x_slo_deadline_ms": 3000,
    "x_priority": "critical"
  }'
```

**Model values:** `auto`, `small` (1b), `medium` (3b), `large` (8b)

**Priority values:** `critical`, `standard` (default), `batch`

**Response headers** on every request:

| Header | Meaning |
|---|---|
| `X-Model-Tier` | Which tier actually served the response |
| `X-Routing-Reason` | Why that tier was chosen |
| `X-Tiers-Attempted` | All tiers tried in order |
| `X-Elapsed-Ms` | Total gateway latency |

---

## Audit Trail

Every request is stored with its full routing history. Query it by request ID:

```bash
curl "http://localhost:8080/v1/routing/decisions?request_id=<id>"
```

Or click any row in the console Live tab. The audit shows every tier attempted, the SLO budget at each step, the feature vector, and the failure attribution.

---

## Configuration

All settings are in `config.yaml`. Environment variables (`ARBITER_*`) override YAML values.

```yaml
models:
  small:  "llama3.2:1b"
  medium: "llama3.2:3b"
  large:  "llama3.1:8b"

bandit:
  linucb_alpha: 0.5                          # exploration vs exploitation
  cold_start_min_observations_per_tier: 500  # heuristic until this many obs
  feature_dim: 16

admission:
  batch_retry_after_s: 30
  batch_immediate_shed: true
```

Bandit state is saved automatically to `data/bandit_checkpoint.npz` on shutdown and reloaded on startup — the bandit doesn't lose its learning between restarts.

Override via environment:

```bash
ARBITER_LINUCB_ALPHA=0.3 \
ARBITER_LARGE_MODEL=llama3.1:70b \
docker compose up -d
```

---

## Benchmarking

### From the console (recommended)

Go to the **Benchmark tab** at `http://localhost:8080/console`. Choose a scenario, set users and duration, hit Start. Results appear live, and a comparison card shows up automatically after running two scenarios.

**Scenarios:**
- **Baseline** — all requests to a single fixed tier (choose large/medium/small)
- **Arbiter** — bandit-routed
- **Round-robin** — cycles small → medium → large
- **Random** — uniform random tier per request

### Headless

```bash
# Quick comparison (2 users, 60 seconds each)
make compare

# Full run
make bench SCENARIO=arbiter USERS=20 DURATION=300
```

---

## Observability

The console Metrics tab covers daily use. For deeper analysis:

- **Prometheus:** `http://localhost:9090`
- **Grafana:** `http://localhost:3000` (admin / admin)
- **Raw metrics:** `http://localhost:8080/metrics` (Prometheus format)

Key metrics exported:
- `arbiter_requests_total{tier, reason}` — routing decisions
- `arbiter_latency_seconds{tier, complexity}` — response latency histogram
- `arbiter_slo_outcome_total{met, priority}` — SLO attainment counters
- `arbiter_bandit_observations{tier}` — learning progress per tier
- `arbiter_cost_proxy_total{tier}` — relative cost tracking

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for a full walkthrough of the four subsystems (Admission → Bandit → Executor → Telemetry), the LinUCB contextual bandit, the SLO-budget-decay cascade, and how every piece fits together.

Short version:

```
POST /v1/chat/completions
        │
   ┌────▼────┐   ┌────────┐   ┌──────────┐   ┌──────────┐
   │Admission│──▶│ Bandit │──▶│ Executor │──▶│Telemetry │
   │    A    │   │   B    │   │    C     │   │    D     │
   └────┬────┘   └────────┘   └────┬─────┘   └──────────┘
        │                          │
     503 shed               Ollama 1b / 3b / 8b
```

---

## Development

```bash
arbiter             # interactive CLI (stack, bench, logs, test request)
make test           # unit + integration tests
.venv/bin/ruff check src/
make run            # gateway only (no Docker; needs Ollama at :11434)
make rebuild        # rebuild Docker image after code changes
```

**Project layout (dev tooling):**

```
scripts/
├── arbiter    # interactive REPL (launched by the arbiter command)
└── dev.sh     # stack bootstrap used by make start / make dev
src/inference_arbiter/
├── cli.py     # console entry point for arbiter
└── gateway/   # FastAPI app + /console UI
```

---

## What Makes This Different

| Feature | inference-arbiter | LiteLLM / Bifrost | RouteLLM / PROTEUS |
|---|---|---|---|
| SLO-budget cascade | ✓ wall-clock deadline per tier | — | — |
| Online bandit learning | ✓ live telemetry | — | offline labels only |
| Batch admission shedding | ✓ router-layer 503 | — | — |
| Production infra | ✓ circuit breakers, audit, SSE | ✓ | — |
| OpenAI compatible | ✓ | ✓ | — |
