# inference-arbiter

OpenAI-compatible inference gateway that routes chat completions across model tiers using request complexity, SLO deadlines, endpoint health, and priority admission.

Point any OpenAI SDK client at this gateway instead of a single model server.

## Quickstart

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com) running locally with tier models:

```bash
ollama pull llama3.2:1b
ollama pull llama3.2:3b
ollama pull llama3.1:8b
```

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Run gateway

```bash
uvicorn inference_arbiter.main:app --reload --port 8080
```

Health: `GET http://127.0.0.1:8080/healthz`  
Metrics: `GET http://127.0.0.1:8080/metrics`

### Example (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="ollama")

resp = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "What is the capital of France?"}],
    extra_body={
        "x_slo_deadline_ms": 2000,
        "x_priority": "standard",
    },
)
print(resp.choices[0].message.content)
```

### Routing fields

| Field | Description |
|-------|-------------|
| `model: auto` | Complexity-based tier selection |
| `model: auto-degraded-ok` | Same as `auto`; downtier without degraded flag |
| `model: small\|medium\|large` | Pin tier |
| `x_slo_deadline_ms` | Latency deadline for SLO-aware downgrade |
| `x_priority` | `critical`, `standard`, or `batch` |
| `x_request_id` | Optional client request ID for audit lookup |

### Audit a routing decision

```bash
curl http://127.0.0.1:8080/v1/routing/decisions/<request_id>
```

Response headers include `X-Request-ID`, `X-Arbiter-Model-Tier`, `X-Arbiter-Complexity`, `X-Degraded-Mode`, `X-Degradation-Reason`.

## Configuration

Environment variables (prefix `ARBITER_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `ARBITER_ROUTING_MODE` | `active` | `active` or `shadow` |
| `ARBITER_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama base URL |
| `ARBITER_LATENCY_EMA_ALPHA` | `0.3` | EMA decay for latency |
| `ARBITER_QUEUE_PRESSURE_THRESHOLD` | `3` | Batch shed threshold |
| `ARBITER_SMALL_MODEL` | `llama3.2:1b` | Small tier backend model |
| `ARBITER_MEDIUM_MODEL` | `llama3.2:3b` | Medium tier backend model |
| `ARBITER_LARGE_MODEL` | `llama3.1:8b` | Large tier backend model |

Shadow mode classifies and logs decisions but routes all traffic to the default tier.

## Tests

```bash
pytest -q
```

## Benchmarks

With the gateway and Ollama running:

```bash
locust -f benchmarks/locustfile.py --host http://127.0.0.1:8080
```

Compare:

- **ArbiterUser** — `model=auto` with mixed complexity (70/20/10 style tasks)
- **BaselineUser** — `model=large` for all traffic

```bash
locust -f benchmarks/locustfile.py BaselineUser --host http://127.0.0.1:8080
```

## Docker (gateway + Prometheus + Grafana)

Ollama should run on the host; the compose file points at `host.docker.internal:11434`.

```bash
docker compose up --build
```

## Docs

- [`SPEC.md`](SPEC.md) — product and architecture specification
- [`BUILD.md`](BUILD.md) — v1 implementation plan
