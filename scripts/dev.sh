#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │         inference-arbiter dev stack         │"
echo "  └─────────────────────────────────────────────┘"
echo ""

docker compose up -d

# Show Ollama model pull progress in background while we wait for gateway.
# ollama-init exits 0 on success so we stop tailing once it completes.
INIT_STATUS=$(docker compose ps -q ollama-init 2>/dev/null || true)
if [ -n "$INIT_STATUS" ]; then
  echo "Pulling Ollama models (this may take several minutes on first run)..."
  docker compose logs -f ollama-init 2>/dev/null &
  LOGS_PID=$!
  # Kill the log tail once ollama-init exits
  ( docker compose wait ollama-init >/dev/null 2>&1; kill "$LOGS_PID" 2>/dev/null || true ) &
fi

echo "Waiting for gateway to become ready..."
TRIES=0
until curl -sf http://localhost:8080/healthz >/dev/null 2>&1; do
  TRIES=$((TRIES + 1))
  if [ "$TRIES" -ge 72 ]; then
    echo ""
    # Check if ollama-init is still running (model download in progress)
    INIT_RUNNING=$(docker compose ps --status running --services 2>/dev/null | grep -c ollama-init || true)
    if [ "$INIT_RUNNING" -gt 0 ]; then
      echo "  Models are still downloading. Run this to check progress:"
      echo "    docker compose logs -f ollama-init"
      echo ""
      echo "  Re-run once download completes:"
      echo "    make dev"
    else
      echo "  Gateway did not become ready within 6 minutes."
      echo "  Check logs:"
      echo "    docker compose logs gateway"
      echo "    docker compose logs ollama-init"
    fi
    exit 1
  fi
  sleep 5
done

echo ""
echo "  Stack ready."
echo ""
echo "  Console (everything in one place):"
echo "    http://localhost:8080/console"
echo ""
echo "  Interactive menu: arbiter  (or: make menu)"
echo ""
echo "  Advanced (optional):"
echo "    Prometheus  http://localhost:9090"
echo "    Grafana     http://localhost:3000  (admin/admin)"
echo ""

if [[ "$(uname)" == "Darwin" ]]; then
  open "http://localhost:8080/console" 2>/dev/null || true
fi
