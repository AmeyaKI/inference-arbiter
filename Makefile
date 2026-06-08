.PHONY: install test eval run dev up down console bench load-baseline load-arbiter load-round-robin

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
		*) echo "Unknown SCENARIO: $(SCENARIO). Use baseline|arbiter|round_robin"; exit 1 ;; \
	esac; \
	.venv/bin/locust -f load/locustfile.py $$USER \
		--host $(HOST) --headless -u $(USERS) -r $(SPAWN_RATE) -t $(DURATION)

load-baseline:
	$(MAKE) bench SCENARIO=baseline

load-arbiter:
	$(MAKE) bench SCENARIO=arbiter

load-round-robin:
	$(MAKE) bench SCENARIO=round_robin
