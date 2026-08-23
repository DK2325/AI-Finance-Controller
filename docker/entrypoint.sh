#!/bin/sh
set -e

# Alembic is idempotent: "upgrade head" on an already-current database is a no-op,
# so this is safe on every container start, not just the first.
echo "[entrypoint] applying migrations..."
alembic upgrade head

echo "[entrypoint] starting api on ${PORT:-8000}..."
exec uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
