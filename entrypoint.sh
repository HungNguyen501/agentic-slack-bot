#!/bin/sh
set -e

case "$SERVICE" in
  receiver)
    exec uvicorn receiver.app:app --host 0.0.0.0 --port 8123
    ;;
  worker)
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
