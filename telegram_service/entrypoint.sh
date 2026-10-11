#!/bin/sh
set -eu

if [ "${SKIP_INDEX_BUILD:-0}" != "1" ]; then
  python /app/bootstrap.py
fi

exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1
