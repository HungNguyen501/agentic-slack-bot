#!/bin/sh
set -e

case "$SERVICE" in
  receiver)
    exec uvicorn receiver.app:app --host 0.0.0.0 --port 8123
    ;;
  worker)
    exec rq worker slack_events
    ;;
  *)
    echo "ERROR: SERVICE must be 'receiver' or 'worker', got: '${SERVICE}'"
    exit 1
    ;;
esac
