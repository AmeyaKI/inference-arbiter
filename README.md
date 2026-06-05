# inference-arbiter

An OpenAI-compatible inference gateway that sits in front of Ollama and routes every chat completion to the right model tier. Instead of always sending traffic to one large model, the gateway classifies each request's complexity, picks the fastest endpoint that can handle it within your SLO deadline, and degrades gracefully when backends are saturated. Change one URL in your OpenAI SDK client — routing, observability, and priority admission happen transparently behind that URL.

```
client (OpenAI SDK)
       │  POST /v1/chat/completions
       ▼
┌─────────────────────────────────────────────────────┐
│                 inference-arbiter                   │
│                                                     │
│  classifier → router → priority gate → proxy        │
│                                                     │
│  • complexity label (simple / medium / complex)     │
│  • SLO ETA per endpoint                             │
│  • circuit breaker per endpoint                     │
│  • batch admission control                          │
└──────────┬──────────────┬──────────────┬────────────┘
           │              │              │
      llama3.2:1b   llama3.2:3b   llama3.1:8b
        (small)       (medium)       (large)
```

---

## Table of Contents

1. [How routing works](#how-routing-works)
2. [Run Option A — docker-compose (full stack)](#option-a--docker-compose-full-stack)
3. [Run Option B — local development](#option-b--local-development)
4. [Sending requests](#sending-requests)
5. [Understanding response headers](#understanding-response-headers)
6. [Audit trail](#audit-trail)
7. [Configuration reference](#configuration-reference)
8. [Running the test suite](#running-the-test-suite)
9. [Observability — Prometheus and Grafana](#observability)
10. [Locust benchmarks](#locust-benchmarks)
11. [Troubleshooting](#troubleshooting)

---

## How routing works

Every request goes through four stages:

**1. Complexity classification** — the heuristic classifier scores the prompt on word count, keyword signals (`compare`, `analyze`, `synthesize`, `step by step`), code blocks, math notation, question count, and structured data. It assigns a label:

- `simple` → routed to small tier (`llama3.2:1b`)
- `medium` → routed to medium tier (`llama3.2:3b`)
- `complex` → routed to large tier (`llama3.1:8b`)

**2. SLO-aware selection** — if you set `x_slo_deadline_ms`, the gateway computes each endpoint's ETA as `in_flight × latency_ema + base_latency`. If the preferred tier can't meet the deadline, it downgrades to a faster tier and sets `X-Degraded-Mode: true`.

**3. Circuit breaker** — each endpoint tracks consecutive 5xx failures. After 3 failures the circuit opens (requests skip that endpoint for 30 s, then probe with half-open). You never wait for a dead backend.

**4. Priority admission** — `x_priority: batch` requests are held for up to 5 s when any endpoint is saturated, then shed with `503 + Retry-After`. `critical` and `standard` always pass through.

---

## Option A — docker-compose (full stack)

This starts everything in containers: Ollama, the gateway, Prometheus, and Grafana.

### What you need

- [Docker Desktop](https://docs.docker.com/get-docker/) (or Docker Engine + Compose plugin)
- ~10 GB free disk space for model weights
- ~4 GB RAM for the large model

### First run

```bash
cd inference-arbiter
docker compose up --build
```

**What happens on first run:**

1. Docker builds the gateway image (~30 s)
2. The `ollama` container starts the Ollama server
3. The `ollama-init` container runs three `ollama pull` commands to download the model weights:
  - `llama3.2:1b` (~1.3 GB)
  - `llama3.2:3b` (~2.0 GB)
  - `llama3.1:8b` (~4.7 GB)
4. The gateway starts only after all three models are pulled
5. Prometheus and Grafana start after the gateway

**Model downloads can take 10–30 minutes depending on your connection.** Watch progress in a separate terminal:

```bash
docker compose logs -f ollama-init
```

You will see repeated lines like:

```
ollama-init-1  | pulling manifest
ollama-init-1  | pulling aabd4debf0c8... 100% ▕████████████▏ 1.3 GB
```

When `ollama-init` exits (you see `exited with code 0` or the log stream stops), the gateway starts.

### Verify everything is up

```bash
# Gateway health
curl http://localhost:8080/healthz
# Expected: {"status":"ok","routing_mode":"active"}

# List available models
curl http://localhost:8080/v1/models | python3 -m json.tool

# Check all containers
docker compose ps
```

All five services should show `running` (gateway, ollama, prometheus, grafana) and `ollama-init` should show `exited (0)`.

### Subsequent runs

Model weights are cached in the `ollama_data` Docker volume. Future starts are fast:

```bash
docker compose up
```

### Stop everything

```bash
docker compose down          # stops containers, keeps model volume
docker compose down -v       # stops and deletes the model volume (re-downloads next time)
```

---

## Option B — local development

Use this for faster iteration — no Docker build step, hot reload with uvicorn.

### 1. Install Ollama

Download from [ollama.com](https://ollama.com) and install it, then:

```bash
# Pull model weights (one-time, ~8 GB total)
ollama pull llama3.2:1b
ollama pull llama3.2:3b
ollama pull llama3.1:8b
```

Verify Ollama is running:

```bash
curl http://localhost:11434/api/tags
# Should return a JSON object with the three models listed
```

If Ollama is not running, start it:

```bash
ollama serve
```

### 2. Set up the Python environment

```bash
cd inference-arbiter
python3 -m venv .venv
source .venv/bin/activate      # on Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 3. Run the gateway

```bash
uvicorn inference_arbiter.main:app --reload --port 8080
```

You should see:

```
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
{"routing_mode": "active", "endpoints": ["small", "medium", "large"], "event": "gateway_started", ...}
```

For shadow mode (classify and log without actually routing to different tiers):

```bash
ARBITER_ROUTING_MODE=shadow uvicorn inference_arbiter.main:app --reload --port 8080
```

---

## Sending requests

### curl — basic auto-routing

The gateway classifies your prompt and picks the tier automatically:

```bash
curl -si http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "What is the capital of France?"}]
  }'
```

Look at the response headers — they tell you exactly what the gateway did:

```
X-Request-ID: 3f2a1b4c-...
X-Arbiter-Model-Tier: small
X-Arbiter-Complexity: simple
X-Degraded-Mode: false
```

A short factual question gets `simple` → `small` tier. The same gateway, with a complex prompt:

```bash
curl -si http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Compare the epistemological frameworks of Kant and Hegel across five dimensions. Provide step-by-step reasoning and cite conceptual tradeoffs."}]
  }'
```

```
X-Arbiter-Model-Tier: large
X-Arbiter-Complexity: complex
X-Degraded-Mode: false
```

### curl — pin to a specific tier

Bypass complexity routing entirely and send straight to a tier:

```bash
# Always use the large model regardless of prompt complexity
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "large", "messages": [{"role": "user", "content": "Hello"}]}' \
  | python3 -m json.tool
```

Valid model values: `auto`, `auto-degraded-ok`, `small`, `medium`, `large`, or the exact backend model name (e.g., `llama3.2:1b`).

### curl — SLO deadline

Set a latency budget. If the preferred tier's queue is too long to meet it, the gateway automatically downgrades:

```bash
# Tight deadline — forces downgrade to fastest available tier
curl -si http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Analyze the time complexity of merge sort step by step."}],
    "x_slo_deadline_ms": 1
  }' | grep "X-"
```

With a 1 ms deadline (impossible to meet), you get:

```
X-Arbiter-Model-Tier: small
X-Degraded-Mode: true
X-Degradation-Reason: DEADLINE_TOO_TIGHT
```

With a realistic deadline during normal load:

```bash
curl -si http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Explain gradient descent."}],
    "x_slo_deadline_ms": 5000
  }' | grep "X-"
```

If the preferred tier's estimated queue time is under 5000 ms, you get `X-Degraded-Mode: false` and the complexity-appropriate tier. If it is over, you get `ENDPOINT_SATURATED` and a faster tier.

### curl — priority

```bash
# Critical traffic — never shed, never delayed
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "urgent query"}], "x_priority": "critical"}'

# Standard traffic (default)
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "normal query"}], "x_priority": "standard"}'

# Batch traffic — shed with 503 when endpoints are saturated
curl -si http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "background job"}], "x_priority": "batch"}'
# Under pressure returns: HTTP 503 with Retry-After: 30 header
```

### curl — degraded-ok flag

Use `model: auto-degraded-ok` when your client is fine with a cheaper tier and you don't want the `X-Degraded-Mode: true` header polluting your metrics:

```bash
curl -si http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto-degraded-ok",
    "messages": [{"role": "user", "content": "Summarize the history of computing."}],
    "x_slo_deadline_ms": 500
  }' | grep "X-Degraded"
# X-Degraded-Mode: false  (even if it routed to a smaller tier than complexity suggests)
```

### curl — streaming

```bash
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Count from 1 to 10."}],
    "stream": true
  }'
# SSE stream: data: {"choices":[{"delta":{"content":"1"}}]}\n\n ...
```

### Python — OpenAI SDK

The gateway is a drop-in replacement. Only the `base_url` changes:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="not-checked",          # gateway does not validate API keys
)

# Auto-routing with SLO deadline
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Explain the CAP theorem."}],
    extra_body={
        "x_slo_deadline_ms": 3000,
        "x_priority": "standard",
    },
)
print(response.choices[0].message.content)
```

```python
# Streaming
stream = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "List the planets in order."}],
    stream=True,
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

---

## Understanding response headers

Every response from `POST /v1/chat/completions` carries routing metadata in the headers:


| Header                 | Example value        | Meaning                                                        |
| ---------------------- | -------------------- | -------------------------------------------------------------- |
| `X-Request-ID`         | `3f2a1b4c-...`       | Unique ID for this request; use it to look up the audit record |
| `X-Arbiter-Model-Tier` | `small`              | Which tier actually handled the request                        |
| `X-Arbiter-Complexity` | `complex`            | Complexity label assigned by the classifier                    |
| `X-Degraded-Mode`      | `true`               | Whether the request was handled by a non-preferred tier        |
| `X-Degradation-Reason` | `DEADLINE_TOO_TIGHT` | Why it degraded (see table below)                              |


**Degradation reasons:**


| Reason               | Meaning                                                                          |
| -------------------- | -------------------------------------------------------------------------------- |
| `ENDPOINT_SATURATED` | Preferred tier's queue depth was too high; downgraded to faster tier to meet SLO |
| `DEADLINE_TOO_TIGHT` | No tier could meet the SLO deadline; routed to fastest available                 |
| `CIRCUIT_OPEN`       | Preferred tier's circuit breaker tripped after repeated 5xx failures             |
| `MODEL_CAPACITY`     | No viable endpoint at all; fallback to small tier                                |


---

## Audit trail

Every routing decision is stored in memory and retrievable by request ID:

```bash
# Get the X-Request-ID from a previous response header, then:
curl http://localhost:8080/v1/routing/decisions/<request-id> | python3 -m json.tool
```

Example response:

```json
{
  "request_id": "3f2a1b4c-9d2e-...",
  "timestamp": 1748921234.5,
  "requested_model": "auto",
  "priority": "standard",
  "endpoint_name": "small",
  "tier": "small",
  "backend_model": "llama3.2:1b",
  "complexity": "simple",
  "complexity_confidence": 0.87,
  "routing_reason": "complexity",
  "slo_deadline_ms": 5000,
  "estimated_eta_ms": 152.3,
  "actual_latency_ms": 843.1,
  "degraded": false,
  "degradation_reason": null,
  "shadow_would_route_to": null,
  "routing_mode": "active",
  "endpoint_snapshot": {
    "in_flight": 0,
    "latency_ema_ms": 841.2,
    "queue_depth_estimate": 0,
    "circuit_breaker": "closed",
    "consecutive_failures": 0
  },
  "classifier_signals": {
    "words": 7,
    "messages": 1,
    "questions": 0,
    "has_code": false,
    "has_math": false,
    "has_table": false,
    "complex_keywords": 0,
    "score": 0.0
  },
  "status": "completed",
  "error": null
}
```

The audit store holds the last 10,000 decisions in memory (FIFO eviction). It resets on gateway restart.

---

## Configuration reference

All settings are environment variables with the `ARBITER_` prefix. Set them in your shell, in a `.env` file at the repo root, or in the `environment:` block of `docker-compose.yml`.


| Variable                             | Default                  | Description                                                                                           |
| ------------------------------------ | ------------------------ | ----------------------------------------------------------------------------------------------------- |
| `ARBITER_HOST`                       | `0.0.0.0`                | Interface to bind                                                                                     |
| `ARBITER_PORT`                       | `8080`                   | Port to listen on                                                                                     |
| `ARBITER_ROUTING_MODE`               | `active`                 | `active` routes requests; `shadow` classifies and logs but routes everything to the default tier      |
| `ARBITER_OLLAMA_BASE_URL`            | `http://127.0.0.1:11434` | Ollama server base URL                                                                                |
| `ARBITER_SMALL_MODEL`                | `llama3.2:1b`            | Backend model for the small tier                                                                      |
| `ARBITER_MEDIUM_MODEL`               | `llama3.2:3b`            | Backend model for the medium tier                                                                     |
| `ARBITER_LARGE_MODEL`                | `llama3.1:8b`            | Backend model for the large tier                                                                      |
| `ARBITER_LATENCY_EMA_ALPHA`          | `0.3`                    | EMA decay factor for per-endpoint latency tracking (0–1; higher = more weight on recent observations) |
| `ARBITER_QUEUE_PRESSURE_THRESHOLD`   | `3`                      | In-flight request count above which an endpoint is considered saturated                               |
| `ARBITER_CIRCUIT_FAILURE_THRESHOLD`  | `3`                      | Consecutive 5xx failures before opening the circuit breaker                                           |
| `ARBITER_CIRCUIT_RECOVERY_TIMEOUT_S` | `30.0`                   | Seconds before an open circuit tries a probe request                                                  |
| `ARBITER_BATCH_QUEUE_MAX_WAIT_S`     | `5.0`                    | How long to hold a batch request before shedding it                                                   |
| `ARBITER_BATCH_RETRY_AFTER_S`        | `30`                     | `Retry-After` value returned in 503 responses                                                         |
| `ARBITER_HTTP_TIMEOUT_S`             | `300.0`                  | httpx client timeout for backend requests                                                             |
| `ARBITER_AUDIT_MAX_RECORDS`          | `10000`                  | Maximum routing decisions to keep in memory                                                           |
| `ARBITER_LOW_CONFIDENCE_THRESHOLD`   | `0.7`                    | Classifier confidence below this value adds `low_confidence: true` to structured logs                 |
| `ARBITER_LOG_LEVEL`                  | `INFO`                   | Log level                                                                                             |


**Example: use a different Ollama model for the large tier**

```bash
ARBITER_LARGE_MODEL=llama3.1:70b uvicorn inference_arbiter.main:app --port 8080
```

**Example: tighten the circuit breaker**

```bash
ARBITER_CIRCUIT_FAILURE_THRESHOLD=2 ARBITER_CIRCUIT_RECOVERY_TIMEOUT_S=10 uvicorn inference_arbiter.main:app --port 8080
```

---

## Running the test suite

```bash
source .venv/bin/activate
pytest tests/ -v
```

Expected output — 22 tests, all passing:

```
tests/test_api.py::test_healthz PASSED
tests/test_api.py::test_models_list PASSED
tests/test_api.py::test_chat_completions_mocked PASSED
tests/test_api.py::test_streaming_mocked PASSED
tests/test_api.py::test_batch_shed_under_pressure PASSED
tests/test_api.py::test_shadow_mode_routes_to_default PASSED
tests/test_classifier.py::test_simple_factual PASSED
tests/test_classifier.py::test_complex_code PASSED
tests/test_classifier.py::test_medium_prompt PASSED
tests/test_classifier.py::test_math_notation PASSED
tests/test_endpoint_state.py::test_ema_and_in_flight PASSED
tests/test_endpoint_state.py::test_circuit_breaker_opens PASSED
tests/test_endpoint_state.py::test_circuit_recovery_half_open PASSED
tests/test_proxy.py::test_backend_timeout_returns_502_and_increments_failures PASSED
tests/test_proxy.py::test_backend_connection_error_returns_502_and_increments_failures PASSED
tests/test_proxy.py::test_backend_4xx_does_not_increment_failure_counter PASSED
tests/test_proxy.py::test_backend_500_increments_failure_counter PASSED
tests/test_router.py::test_auto_routes_simple_to_small PASSED
tests/test_router.py::test_pin_large_model PASSED
tests/test_router.py::test_slo_downgrade PASSED
tests/test_router.py::test_circuit_breaker_fallback PASSED
tests/test_router.py::test_auto_degraded_ok_suppresses_flag PASSED
22 passed in 5.3s
```

Tests use mocked backends — you do not need Ollama running to run the test suite.

---

## Observability

### Prometheus metrics

Metrics are exposed at `GET http://localhost:8080/metrics` in Prometheus text format.

```bash
curl -s http://localhost:8080/metrics | grep -E "^(requests_routed|routing_decision|slo_breach|endpoint_queue|endpoint_in_flight|request_latency)" | head -40
```

Key metrics:


| Metric                    | Labels                       | Description                                                                                                  |
| ------------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `requests_routed_total`   | `tier`, `policy`, `priority` | Count of routed requests                                                                                     |
| `routing_decision_total`  | `reason`                     | Count of routing decisions by reason (`complexity`, `slo_pressure`, `circuit_breaker`, `fallback`, `shadow`) |
| `slo_breach_total`        | `tier`, `reason`             | Count of requests where no endpoint could meet the deadline                                                  |
| `endpoint_queue_depth`    | `endpoint`                   | Current estimated queue depth per endpoint                                                                   |
| `endpoint_in_flight`      | `endpoint`                   | Current in-flight request count per endpoint                                                                 |
| `request_latency_seconds` | `tier`, `complexity`         | Histogram of end-to-end latency                                                                              |
| `classifier_confidence`   | `complexity`                 | Histogram of classifier confidence scores                                                                    |


**Useful Prometheus queries:**

```promql
# Request rate by tier (requests per second)
sum by (tier) (rate(requests_routed_total[1m]))

# P95 latency by tier
histogram_quantile(0.95, sum by (tier, le) (rate(request_latency_seconds_bucket[1m])))

# SLO breach rate
sum(rate(slo_breach_total[5m]))

# Routing reason breakdown
sum by (reason) (rate(routing_decision_total[5m]))

# Degraded request fraction
sum(rate(requests_routed_total{policy="slo_pressure"}[5m])) 
  / sum(rate(requests_routed_total[5m]))
```

### Prometheus UI

Go to `http://localhost:9090` (docker-compose) or run a local Prometheus pointed at `http://localhost:8080/metrics`.

1. Open `http://localhost:9090/targets` — confirm the `gateway` target shows `UP`
2. Go to `http://localhost:9090/graph`
3. Enter a query like `rate(requests_routed_total[1m])` and click Execute

### Grafana dashboard

Go to `http://localhost:3000` and log in with `admin` / `admin`.

The **inference-arbiter** dashboard is pre-loaded with 7 panels:


| Panel                          | What it shows                                              |
| ------------------------------ | ---------------------------------------------------------- |
| Request Rate by Tier           | Live requests/s split by small / medium / large            |
| Routing Decisions by Reason    | Pie chart — how often each routing path is taken           |
| SLO Breach Count               | Stat — how many requests couldn't meet their deadline      |
| Queue Depth per Endpoint       | Bar gauge — how many requests are waiting at each endpoint |
| In-Flight per Endpoint         | Bar gauge — current concurrency at each endpoint           |
| Latency P50 / P95 by Tier      | Line graph — latency percentiles over time                 |
| Classifier Confidence by Label | Line graph — p10 and p50 confidence scores                 |


The dashboard auto-refreshes every 10 seconds. Send a few requests first so there is data to display.

### Structured logs

The gateway emits one JSON log line per routing decision. In local dev you see them in the terminal. Example:

```json
{
  "event": "routing_decision",
  "request_id": "3f2a1b4c-...",
  "tier": "small",
  "endpoint": "small",
  "complexity": "simple",
  "complexity_confidence": 0.87,
  "low_confidence": false,
  "reason": "complexity",
  "degraded": false,
  "eta_ms": 152.3,
  "shadow_would_route_to": null,
  "level": "info",
  "timestamp": "2026-06-03T04:42:41.062Z"
}
```

`low_confidence: true` appears when `complexity_confidence` falls below `ARBITER_LOW_CONFIDENCE_THRESHOLD` (default 0.7) — useful for filtering ambiguous classifications.

---

## Locust benchmarks

Locust load tests are in `benchmarks/locustfile.py`. Three scenarios let you compare the router against static baselines.

### Prerequisites

Gateway and Ollama must be running (either docker-compose or local dev). Install dependencies:

```bash
pip install -e ".[dev]"
```

### Three scenarios

**Scenario 1 — Baseline (all-large)**

All traffic pinned to the large model, regardless of prompt complexity. This is the worst-case cost scenario — the ceiling you are trying to beat.

```bash
locust -f benchmarks/locustfile.py BaselineUser \
  --host http://localhost:8080 \
  --users 20 --spawn-rate 4 --run-time 5m \
  --headless --csv benchmarks/baseline
```

**Scenario 2 — Round-robin**

Traffic cycles evenly across small / medium / large (1:1:1). This is a static distribution that does not adapt to prompt complexity or endpoint health.

```bash
locust -f benchmarks/locustfile.py RoundRobinUser \
  --host http://localhost:8080 \
  --users 20 --spawn-rate 4 --run-time 5m \
  --headless --csv benchmarks/roundrobin
```

**Scenario 3 — inference-arbiter (auto-routing)**

Complexity-based routing with SLO deadlines and priority. 70% simple prompts, 20% medium, 10% complex, with a mix of priorities.

```bash
locust -f benchmarks/locustfile.py ArbiterUser \
  --host http://localhost:8080 \
  --users 20 --spawn-rate 4 --run-time 5m \
  --headless --csv benchmarks/arbiter
```

### Interactive web UI

Drop `--headless` to use Locust's browser UI. After starting, go to `http://localhost:8089`:

```bash
locust -f benchmarks/locustfile.py ArbiterUser --host http://localhost:8080
```

Enter 20 users, 4 spawn rate, then click Start. You get live charts for requests/s, response times, and failures.

### Reading the results

Each `--csv` run produces `<prefix>_stats.csv` and `<prefix>_stats_history.csv`. The key columns in `_stats.csv`:


| Column       | What it means                                   |
| ------------ | ----------------------------------------------- |
| `50%`        | Median latency (ms)                             |
| `95%`        | P95 latency (ms) — the primary benchmark target |
| `99%`        | P99 latency — tail risk                         |
| `Requests/s` | Throughput                                      |
| `Failures/s` | Error rate (5xx responses)                      |


Quick comparison after all three runs:

```bash
echo "=== Baseline ===" && grep "Aggregated" benchmarks/baseline_stats.csv | cut -d, -f1,6,9,10
echo "=== Round-robin ===" && grep "Aggregated" benchmarks/roundrobin_stats.csv | cut -d, -f1,6,9,10
echo "=== Arbiter ===" && grep "Aggregated" benchmarks/arbiter_stats.csv | cut -d, -f1,6,9,10
```

Fill in `benchmarks/results.md` with the numbers. Target: ArbiterUser P95 is at least 1.5× lower than BaselineUser P95, because ~70% of prompts are simple and get routed to the fast small-tier model instead of waiting for the large one.

### Watching Prometheus during a Locust run

Open two terminals simultaneously:

```bash
# Terminal 1 — run Locust
locust -f benchmarks/locustfile.py ArbiterUser --host http://localhost:8080 --users 20 --spawn-rate 4

# Terminal 2 — watch tier distribution update every 5 s
watch -n5 "curl -s http://localhost:8080/metrics | grep requests_routed_total"
```

Or watch the Grafana dashboard update live at `http://localhost:3000`.

---

## Troubleshooting

### `ollama-init` never starts or `docker compose logs -f ollama-init` shows nothing

The `ollama` container health check failed before `ollama-init` could depend on it. Check why:

```bash
docker compose ps
docker compose logs ollama
```

The health check uses `ollama list` which requires the server to be responsive. If you see the server started (`Listening on [::]:11434`) but the container is still `unhealthy`, the check may be timing out. Increase `start_period` in `docker-compose.yml`:

```yaml
healthcheck:
  test: ["CMD", "ollama", "list"]
  interval: 10s
  timeout: 10s
  retries: 15
  start_period: 60s    # increase this if your host is slow to start
```

### Gateway returns `502 Bad Gateway`

The gateway can reach its own routing logic but can't reach Ollama. Check:

```bash
# In docker-compose
docker compose logs ollama
docker compose logs gateway

# In local dev
curl http://localhost:11434/api/tags     # should return model list
```

If `ollama serve` is not running, start it. If the model isn't loaded, pull it.

### Gateway returns `503` for batch requests

This is expected behaviour under load. The priority gate holds `x_priority: batch` requests when any endpoint queue is above the pressure threshold (default: 3 in-flight). After 5 seconds it sheds with `503 + Retry-After: 30`. Lower the threshold to make it more aggressive, or raise it to be more permissive:

```bash
ARBITER_QUEUE_PRESSURE_THRESHOLD=10 uvicorn inference_arbiter.main:app --port 8080
```

### Requests always go to `small` tier, even complex prompts

The heuristic classifier uses word count, keywords, and structural signals. Very short complex prompts (e.g., `"Prove P≠NP"`) may score low. Check the classifier signals in the audit record:

```bash
curl http://localhost:8080/v1/routing/decisions/<request-id> | python3 -m json.tool | grep -A 15 classifier_signals
```

If `score` is below 4.5, the prompt is classified as `simple` or `medium`. Add more context, code blocks, or analytical keywords to raise the score.

### Port conflicts

If ports 8080, 9090, 3000, or 11434 are already in use, change them in `docker-compose.yml`:

```yaml
ports:
  - "8181:8080"   # map host 8181 → container 8080
```

Then use `http://localhost:8181` in all examples.

---

## Reference

- [SPEC.md](SPEC.md) — full product and architecture specification
- [BUILD.md](BUILD.md) — v1 implementation notes
- `GET /docs` — interactive FastAPI OpenAPI documentation (available when the gateway is running)

