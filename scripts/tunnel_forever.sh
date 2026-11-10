#!/usr/bin/env bash
# Бесконечный перезапуск Cloudflare Quick Tunnel (trycloudflare.com).
# Аккаунт не нужен. Адрес https://*.trycloudflare.com МЕНЯЕТСЯ при каждом
# перезапуске процесса cloudflared — свежий URL пишется в PUBLIC_URL.txt.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/logs"

LOG="$ROOT/logs/tunnel.log"
URL_FILE="$ROOT/PUBLIC_URL.txt"
SUPER_PID="$ROOT/logs/tunnel.supervisor.pid"
CHILD_PID="$ROOT/logs/tunnel.pid"
ORIGIN="${TUNNEL_ORIGIN:-http://localhost:7860}"

if [[ -n "${CLOUDFLARED_BIN:-}" ]]; then
  BIN="$CLOUDFLARED_BIN"
elif [[ -x /workspace/bin/cloudflared ]]; then
  BIN=/workspace/bin/cloudflared
elif [[ -x "${HOME}/.local/bin/cloudflared" ]]; then
  BIN="${HOME}/.local/bin/cloudflared"
elif command -v cloudflared >/dev/null 2>&1; then
  BIN="$(command -v cloudflared)"
else
  echo "cloudflared не найден. Скачайте linux-amd64 с GitHub Releases в /workspace/bin/cloudflared или ~/.local/bin/cloudflared." >&2
  exit 1
fi

echo $$ > "$SUPER_PID"

CHILD=""
WATCH=""

cleanup() {
  if [[ -n "${WATCH}" ]] && kill -0 "${WATCH}" 2>/dev/null; then
    kill "${WATCH}" 2>/dev/null || true
  fi
  if [[ -n "${CHILD}" ]] && kill -0 "${CHILD}" 2>/dev/null; then
    kill "${CHILD}" 2>/dev/null || true
    wait "${CHILD}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

ts() { date "+%Y-%m-%d %H:%M:%S %Z"; }

watch_url() {
  local last=""
  while true; do
    if [[ -f "$LOG" ]]; then
      local url
      url="$(grep -oE 'https://[A-Za-z0-9.-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | tail -n 1 || true)"
      if [[ -n "$url" && "$url" != "$last" ]]; then
        printf '%s\n' "$url" > "$URL_FILE"
        echo "[$(ts)] опубликован адрес: ${url} (меняется при перезапуске туннеля)" >> "$LOG"
        last="$url"
      fi
    fi
    sleep 1
  done
}

watch_url &
WATCH=$!

while true; do
  echo "[$(ts)] запуск: ${BIN} tunnel --url ${ORIGIN} --no-autoupdate" >> "$LOG"
  "$BIN" tunnel --url "$ORIGIN" --no-autoupdate >> "$LOG" 2>&1 &
  CHILD=$!
  echo "${CHILD}" > "$CHILD_PID"
  set +e
  wait "${CHILD}"
  code=$?
  set -e
  echo "[$(ts)] cloudflared (pid ${CHILD}) завершился с кодом ${code}; перезапуск через 3 с. Новый запуск выдаст ДРУГОЙ trycloudflare-адрес." >> "$LOG"
  CHILD=""
  sleep 3
done
