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
      echo "[tengsl-backend] Starting in development mode (hot reload)"
      uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
      ;;
    production|prod|"")
      echo "[tengsl-backend] Starting in production mode"
      uvicorn app.main:app --host 0.0.0.0 --port 8000 &
      ;;
    *)
      echo "[tengsl-backend] Invalid mode '$MODE', falling back to production" >&2
      uvicorn app.main:app --host 0.0.0.0 --port 8000 &
      ;;
  esac

  child_pid=$!
  rc=0
  wait "$child_pid" || rc=$?
  child_pid=""

  # A clean child exit is used for an administrative restart. The Docker stop
  # path is handled by term_handler above, so it is safe to restart here.
  if [ "$rc" -eq 0 ] || [ "$rc" -eq 75 ] || [ "$rc" -eq 143 ]; then
    echo "[tengsl-backend] Restart requested/clean exit; starting again with current runtime mode"
    continue
  fi
  exit "$rc"
done
