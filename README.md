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

That's it. The console opens automatically at **[http://localhost:8080/console](http://localhost:8080/console)**.

**Entry points** (all installed by `pip install -e ".[dev]"`):


| Command                   | What it does                                                   |
| ------------------------- | -------------------------------------------------------------- |
| `arbiter`                 | Interactive terminal REPL — stack control, benchmarks, debug   |
| `make start` / `make dev` | Non-interactive stack bootstrap (same as `arbiter start`)      |
| `inference-arbiter`       | Run the gateway directly (no Docker; needs Ollama at `:11434`) |


---

## The Console

Everything is accessible from one URL: **[http://localhost:8080/console](http://localhost:8080/console)**


| Tab           | What it shows                                                                          |
| ------------- | -------------------------------------------------------------------------------------- |
| **Live**      | Every request as it routes in real time. Click any row for the full audit trail.       |
| **Benchmark** | Built-in load tester. Run scenarios, watch RPS + latency live, compare results.        |
| **Metrics**   | SLO attainment, bandit convergence, tier distribution, endpoint health via Prometheus. |
| **Custom**    | Send a one-off request and see the routing decision, response, and audit trail.        |


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


| Header              | Meaning                                 |
| ------------------- | --------------------------------------- |
| `X-Model-Tier`      | Which tier actually served the response |
| `X-Routing-Reason`  | Why that tier was chosen                |
| `X-Tiers-Attempted` | All tiers tried in order                |
| `X-Elapsed-Ms`      | Total gateway latency                   |


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

vLLM, TGI, and llama.cpp **serve** a model. LiteLLM, Bifrost, and OpenRouter **proxy** cloud APIs. inference-arbiter **arbitrates** between capability tiers on your own hardware — choosing which model should answer each request under a wall-clock SLO, escalating on quality failure, and learning from live production telemetry.

It is a routing control plane, not a model server. Your backends (Ollama, vLLM, TGI) stay unchanged; you point your OpenAI client at `:8080` with `model: "auto"`.

### Where it sits in the stack


| Category         | Examples                     | What they optimize                           | What inference-arbiter adds                                                                           |
| ---------------- | ---------------------------- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Model servers    | vLLM, TGI, llama.cpp server  | Throughput, KV-cache, batching for one model | Capability-tier selection across small/medium/large with cost-aware learning                          |
| Replica routers  | vLLM Router                  | Load-balance identical replicas              | Rank tiers by prompt complexity + live load, not round-robin                                          |
| Provider proxies | LiteLLM, Bifrost, OpenRouter | Multi-cloud API failover                     | Self-hosted tier routing with online bandit rewards                                                   |
| Academic routers | RouteLLM, PROTEUS, FrugalGPT | Offline preference labels / cascade papers   | Production control plane: admission shedding, circuit breakers, audit API, Prometheus                 |
| Orchestration    | Ray Serve                    | DIY deployment graphs                        | Opinionated Admission → Bandit → Executor → Telemetry pipeline with explainable per-request decisions |


### Three things only a routing control plane can do

**1. SLO-budget-decay cascade**

Each request carries a wall-clock deadline (`x_slo_deadline_ms`). Before calling a tier, the executor checks estimated time-to-first-token against the remaining budget. If a tier's response fails structural verification (empty output, invalid JSON, truncated text), the gateway escalates to the next bandit-ranked tier — but only if budget remains. This is deadline-aware escalation, not blind retry-on-error.

**2. Router-layer batch shedding**

Interactive and batch traffic are classified at admission (`x_priority`). When P95 latency spikes or queues saturate, batch requests are rejected at the router with `503 + Retry-After` before they reach any model. Interactive traffic keeps stable latency under load instead of competing in the same server-side queue.

**3. Online contextual bandit learning**

A LinUCB bandit ranks tiers using a 16-dimensional feature vector extracted from each prompt (token estimate, code blocks, complexity keywords, current queue depth, live latency). Rewards come from actual production outcomes — verification pass/fail, latency, cost proxy — with infrastructure failures (5xx, circuit open) filtered out so they don't poison routing weights. The bandit learns from your traffic, not offline Arena labels.

### When to use inference-arbiter

**Use when:**

- You run multiple model tiers (small/medium/large, or cost/capability tiers) on shared hardware
- You need deadline-aware routing with explainable decisions (`X-Model-Tier`, `X-Tiers-Attempted`, audit API)
- You want `model: "auto"` that learns which tier works for which prompts over time
- You need to benchmark routing strategies (baseline vs arbiter vs round-robin) with built-in load testing

**Don't use when:**

- You need faster inference for a single model (use vLLM or TGI directly)
- You only need replica load balancing across identical deployments (use vLLM Router)
- You need multi-cloud provider proxying and API key rotation (use LiteLLM or OpenRouter)

### Feature comparison


| Feature                       | inference-arbiter      | vLLM / TGI     | LiteLLM / OpenRouter | RouteLLM / PROTEUS | Ray Serve      |
| ----------------------------- | ---------------------- | -------------- | -------------------- | ------------------ | -------------- |
| Runs inference                | — (routes only)        | ✓              | — (proxies)          | —                  | ✓ (you deploy) |
| Multi-tier capability routing | ✓ 1b / 3b / 8b         | — single model | — provider selection | ✓ offline router   | DIY pipelines  |
| SLO-budget cascade            | ✓ wall-clock per tier  | —              | —                    | —                  | DIY            |
| Quality-driven escalation     | ✓ structural verifiers | —              | —                    | paper-level        | DIY            |
| Online bandit learning        | ✓ live telemetry       | —              | —                    | offline labels     | —              |
| Batch admission shedding      | ✓ router-layer 503     | —              | —                    | —                  | DIY            |
| Per-request audit trail       | ✓ full RequestContext  | —              | —                    | —                  | DIY            |
| Circuit breakers + Prometheus | ✓                      | partial        | partial              | —                  | DIY            |
| OpenAI compatible gateway     | ✓                      | ✓              | ✓                    | —                  | DIY            |
| Built-in benchmark console    | ✓                      | —              | —                    | —                  | —              |


### One-liner

> Point your OpenAI SDK at `:8080` with `model: "auto"` — inference-arbiter learns which tier to try first, escalates when quality or SLO demands it, and sheds batch traffic before interactive latency degrades.





