#!/bin/sh
# Container entrypoint: migrate → seed reference data (idempotent) → serve.
set -e

echo "[start] applying database migrations"
alembic upgrade head

echo "[start] seeding canonical taxonomy (skills, families, skill graph)"
python -m scripts.seed_canonical || echo "[start] seed_canonical failed (continuing; API still serves)"

if [ "${ALERTS_ENABLED:-false}" = "true" ]; then
  echo "[start] starting saved-job alerts worker in the background"
  python scripts/send_job_alerts.py --loop &
fi

echo "[start] serving on :${PORT:-10000}"
# One worker: rate limits and the LLM cache are in-process (see app/core/rate_limit.py).
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-10000}" \
  --workers 1 \
  --proxy-headers \
  --forwarded-allow-ips "*"
