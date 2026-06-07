.PHONY: install test eval run load-baseline load-arbiter

install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest tests/ -q

eval:
	.venv/bin/python eval/run_eval.py

run:
	.venv/bin/inference-arbiter

load-baseline:
	.venv/bin/locust -f load/locustfile.py BaselineUser --host http://127.0.0.1:8080

load-arbiter:
	.venv/bin/locust -f load/locustfile.py ArbiterUser --host http://127.0.0.1:8080
