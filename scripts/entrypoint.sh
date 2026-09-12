#!/bin/bash
set -e

# Extract the run mode (api, worker, or fallback command)
COMMAND=$1

if [ "$COMMAND" = "api" ]; then
    echo "[Entrypoint] Enforcing database migrations (Alembic upgrade)..."
    alembic upgrade head
    
    echo "[Entrypoint] Launching NomadX Web API Server (Uvicorn)..."
    exec uvicorn nomadx.apps.api.main:app --host 0.0.0.0 --port 8000
    
elif [ "$COMMAND" = "worker" ]; then
    echo "[Entrypoint] Launching NomadX Background Worker Loop..."
    exec python -m nomadx.apps.worker.main
    
else
    echo "[Entrypoint] Command not matched. Delegating to shell execution: $@"
    exec "$@"
fi
