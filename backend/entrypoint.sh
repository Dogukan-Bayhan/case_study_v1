#!/usr/bin/env bash
set -euo pipefail

python -m app.db.wait
alembic -c /app/alembic.ini upgrade head
python -m app.db.seed

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
