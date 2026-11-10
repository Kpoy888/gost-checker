#!/usr/bin/env bash
# Бесконечный перезапуск веб-сервера gost-checker.
# Логи: <корень>/logs/web.log
# PID супервизора: logs/web.supervisor.pid
# PID uvicorn:     logs/web.pid

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/logs"

LOG="$ROOT/logs/web.log"
SUPER_PID="$ROOT/logs/web.supervisor.pid"
CHILD_PID="$ROOT/logs/web.pid"

echo $$ > "$SUPER_PID"

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-7860}"
export MPLBACKEND="${MPLBACKEND:-Agg}"
export PYTHONUNBUFFERED=1
export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"

PREFIX="$ROOT/vendor/libredwg"
if [[ -x "$PREFIX/bin/dwg2dxf" ]]; then
  export LIBREDWG_PREFIX="$PREFIX"
  export PATH="$PREFIX/bin:${PATH}"
  if [[ -d "$PREFIX/lib" ]]; then
    export LD_LIBRARY_PATH="$PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  fi
fi

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="$(command -v python3 || command -v python)"
fi

CHILD=""
cleanup() {
  if [[ -n "${CHILD}" ]] && kill -0 "${CHILD}" 2>/dev/null; then
    kill "${CHILD}" 2>/dev/null || true
    wait "${CHILD}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

ts() { date "+%Y-%m-%d %H:%M:%S %Z"; }

while true; do
  echo "[$(ts)] запуск uvicorn (${PYTHON}) на ${HOST}:${PORT}" >> "$LOG"
  "$PYTHON" -m uvicorn gost_checker.web:app --host "$HOST" --port "$PORT" --no-access-log >> "$LOG" 2>&1 &
  CHILD=$!
  echo "${CHILD}" > "$CHILD_PID"
  set +e
  wait "${CHILD}"
  code=$?
  set -e
  echo "[$(ts)] uvicorn (pid ${CHILD}) завершился с кодом ${code}; перезапуск через 2 с" >> "$LOG"
  CHILD=""
  sleep 2
done
