#!/bin/sh
set -eu
MODE_FILE="${TENGSL_MODE_FILE:-/runtime/mode}"

child_pid=""
term_handler() {
  if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  exit 0
}
trap term_handler TERM INT

while :; do
  MODE="production"
  if [ -f "$MODE_FILE" ]; then
    MODE="$(tr -d '[:space:]' < "$MODE_FILE" || true)"
  fi

  case "$MODE" in
    development|dev)
      echo "[tengsl-edge] Starting in development mode (watchfiles)"
      watchfiles --filter python "python -m app.main --config /app/config.yaml" /app/app &
      ;;
    production|prod|"")
      echo "[tengsl-edge] Starting in production mode"
      python -m app.main --config /app/config.yaml &
      ;;
    *)
      echo "[tengsl-edge] Invalid mode '$MODE', falling back to production" >&2
      python -m app.main --config /app/config.yaml &
      ;;
  esac

  child_pid=$!
  rc=0
  wait "$child_pid" || rc=$?
  child_pid=""

  # Agent exits normally on an administrative restart. Re-read runtime mode.
  if [ "$rc" -eq 0 ] || [ "$rc" -eq 75 ]; then
    echo "[tengsl-edge] Restart requested/clean exit; starting again with current runtime mode"
    continue
  fi
  exit "$rc"
done
