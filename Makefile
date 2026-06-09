.PHONY: install test eval run dev up down console bench load-baseline load-arbiter load-round-robin \
        start stop rebuild status compare request logs menu

HOST ?= http://127.0.0.1:8080
USERS ?= 10
SPAWN_RATE ?= 2
DURATION ?= 3m
SCENARIO ?= arbiter

install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest tests/ -q

eval:
	.venv/bin/python eval/run_eval.py

run:
	.venv/bin/inference-arbiter

dev:
	@bash scripts/dev.sh

menu:
	@bash scripts/arbiter

up:
	docker compose up -d

down:
	docker compose down

console:
	@echo "Open http://localhost:8080/console"

bench:
	@case "$(SCENARIO)" in \
		baseline) USER=BaselineUser ;; \
		arbiter) USER=ArbiterUser ;; \
		round_robin) USER=RoundRobinUser ;; \
		random) USER=RandomUser ;; \
		*) echo "Unknown SCENARIO: $(SCENARIO). Use baseline|arbiter|round_robin|random"; exit 1 ;; \
	esac; \
	.venv/bin/locust -f load/locustfile.py $$USER \
		--host $(HOST) --headless -u $(USERS) -r $(SPAWN_RATE) -t $(DURATION)

load-baseline:
	$(MAKE) bench SCENARIO=baseline

load-arbiter:
	$(MAKE) bench SCENARIO=arbiter

load-round-robin:
	$(MAKE) bench SCENARIO=round_robin

# ── intuitive shortcuts ──────────────────────────────────────

start:
	@bash scripts/dev.sh

stop:
	@docker compose down

rebuild:
	@docker compose up -d --build
	@echo "  Rebuilt. Check: curl localhost:8080/healthz"

status:
	@echo "=== gateway ===" && curl -s localhost:8080/healthz | python3 -m json.tool 2>/dev/null || echo "offline"
	@echo "=== readyz ===" && curl -s localhost:8080/readyz | python3 -m json.tool 2>/dev/null || echo "not found"

compare:
	@echo "--- baseline (2 users, 60s) ---"
	@$(MAKE) bench SCENARIO=baseline USERS=2 DURATION=60
	@echo "--- arbiter (2 users, 60s) ---"
	@$(MAKE) bench SCENARIO=arbiter USERS=2 DURATION=60

request:
	@curl -s localhost:8080/v1/chat/completions \
		-H "Content-Type: application/json" \
		-d '{"model":"auto","messages":[{"role":"user","content":"Say hello in one sentence."}]}' \
		| python3 -m json.tool

logs:
	@docker compose logs -f gateway
