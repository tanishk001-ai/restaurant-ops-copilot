#!/bin/sh
# Docker entrypoint — seeds DB then starts the server.
# Runs inside the app container after the DB healthcheck passes.
set -e

echo "[entrypoint] Running database seeder…"
python -m data_gen.seed

echo "[entrypoint] Seeder complete. Starting uvicorn on port ${PORT:-8000}…"
exec uvicorn api.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --log-level info
