#!/bin/sh
set -e

case "$SERVICE" in
  receiver)
    exec uvicorn receiver.app:app --host 0.0.0.0 --port 8123
    ;;
  worker)
    # Seeds the characters table with the files in this image (insert-only). It logs its own problems and must never
    # keep the worker from starting, so a crash or a database that doesn't answer within 30 seconds is only a warning.
    timeout 30 python -m worker.seed_characters || echo "WARNING: could not seed the characters; starting the worker anyway"
    exec rq worker slack_events
    ;;
  scheduler)
    exec python -m scheduler.app
    ;;
  *)
    echo "ERROR: SERVICE must be 'receiver', 'worker', or 'scheduler', got: '${SERVICE}'"
    exit 1
    ;;
esac
