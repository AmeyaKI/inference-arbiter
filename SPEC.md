# inference-arbiter Specification

## What it is

An intelligent, OpenAI-compatible API gateway that sits in front of a pool of model endpoints (Ollama in v1, vLLM-compatible in production). Every request is classified, routed to an appropriate model tier, and fully observable.

Clients change one URL; the gateway handles complexity-based routing, SLO enforcement, and priority admission.

## v1 scope

| Area | v1 |
|------|-----|
| Backend | Ollama-first (`/v1/chat/completions`), backend-neutral config for vLLM later |
| Classifier | Heuristic cascade + optional `BaseComplexityClassifier` hook (no training pipeline) |
| API | `POST /v1/chat/completions`, `GET /v1/models`, `GET /v1/routing/decisions/{id}`, `GET /healthz`, `GET /metrics` |
| Routing | Complexity tiers, SLO ETA downgrade, circuit breaker, priority admission |
| Modes | `ROUTING_MODE=active` (default) or `shadow` (classify/log only, route to default) |
| Streaming | Full SSE proxy; in-flight decremented on stream end |
| Observability | Structured logs, Prometheus metrics, routing audit trail |
| Local tiers | `llama3.2:1b` (small), `llama3.2:3b` (medium), `llama3.1:8b` (large) |

## Post-v1

- DistilBERT / FastText classifier with training pipeline
- Semantic request fingerprinting and routing-decision cache
- Classifier feedback loop (`GET /v1/classifier/feedback`)
- Grafana dashboard JSON in repo
- vLLM-first deployment docs and multi-node endpoint pools

## Problems solved

1. **Waste** — Simple prompts should not pay large-model latency/cost.
2. **SLO violation** — Track per-endpoint queue/ETA; downgrade or flag degradation before missing deadlines.
3. **Priority inversion** — Shed or delay `batch` traffic under saturation; protect `critical` and `standard`.

## Architecture

### 1. Complexity classifier (v1: heuristics)

Two-stage design; v1 implements stage 1 only:

- **Stage 1 (v1):** Heuristics — token/word count, multi-question signals, keywords (`compare`, `analyze`, `synthesize`, `step by step`), structure (code blocks, tables, math).
- **Stage 2 (post-v1):** Lightweight ML model for ambiguous cases (~5ms).

Output: `ComplexityLabel` (`simple` | `medium` | `complex`) and confidence `0.0–1.0`.

### 2. Endpoint state (no polling)

Per-endpoint local state updated only from gateway observations:

- `in_flight_count`
- `latency_ema` (α configurable, default `0.3`)
- `circuit_breaker_state` (`closed` | `open` | `half_open`)
- `queue_depth_estimate` (from in-flight + concurrency)

### 3. SLO-aware routing

When `x_slo_deadline_ms` is set:

```
ETA(endpoint) = queue_depth_estimate × latency_ema_ms + model_base_latency_ms
```

If preferred endpoint ETA exceeds deadline, downgrade to a faster tier. If none meet the deadline, route to fastest available, set `X-Degraded-Mode: true`, and increment `slo_breach_total`.

Degradation reasons (response header `X-Degradation-Reason`):

- `ENDPOINT_SATURATED`
- `MODEL_CAPACITY`
- `DEADLINE_TOO_TIGHT`
- `CIRCUIT_OPEN`

### 4. Priority queuing

Tiers: `critical`, `standard`, `batch` (default: `standard`).

Under pressure (any endpoint queue depth above threshold):

- `critical` — serve immediately
- `standard` — serve with possible downtier
- `batch` — bounded wait or `503` with `Retry-After`

### 5. OpenAI-compatible API

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "What is the capital of France?"}],
  "x_slo_deadline_ms": 1500,
  "x_priority": "standard",
  "x_request_id": "optional-client-id"
}
```

| `model` value | Behavior |
|---------------|----------|
| `auto` | Complexity-based routing |
| `auto-degraded-ok` | Same as `auto`; downtier without degraded flag |
| `small` / `medium` / `large` | Pin to tier |
| Backend model name | Pin to matching endpoint |

Response headers: `X-Request-ID`, `X-Arbiter-Model-Tier`, `X-Arbiter-Complexity`, `X-Degraded-Mode`, `X-Degradation-Reason`.

### 6. Observability

Structured routing decision per request. Prometheus metrics:

- `requests_routed_total{tier,policy,priority}`
- `routing_decision_total{reason}`
- `slo_breach_total{tier,reason}`
- `endpoint_queue_depth{endpoint}`
- `endpoint_in_flight{endpoint}`
- `request_latency_seconds{tier,complexity}`
- `classifier_confidence{complexity}`

Audit: `GET /v1/routing/decisions/{request_id}`.

### 7. Shadow mode

`ROUTING_MODE=shadow`: classify and log intended route; dispatch all traffic to the configured default tier.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Ollama (host):

```bash
ollama pull llama3.2:1b
ollama pull llama3.2:3b
ollama pull llama3.1:8b
```

Run gateway: `uvicorn inference_arbiter.main:app --reload --port 8080`

## Benchmarks

Locust mixed load: 70% simple, 20% medium, 10% complex.

Compare: baseline (all large), static round-robin, inference-arbiter.

Metrics: P50/P95/P99 latency, SLO hit rate, throughput, cost proxy (tier weight × duration).
