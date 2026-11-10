#!/usr/bin/env bash
# Публикация сайта gost-checker с вашего Linux-компьютера.
# Сначала Cloudflare Quick Tunnel (без аккаунта и без карты).
# Если снаружи /health не отвечает (часто HTTP 530 при закрытом порте 7844),
# запускается запасной туннель: scripts/tunnel_alt_forever.sh
#
# Сайт слушает локально порт 7860. Публичный адрес пишется в PUBLIC_URL.txt
# и МЕНЯЕТСЯ при каждом перезапуске туннеля.
#
# Использование:
#   bash scripts/run_public.sh          # сайт + cloudflared, при неудаче — запасной
#   TUNNEL_PROVIDER=alt bash scripts/run_public.sh
#   TUNNEL_ALT=pinggy bash scripts/run_public.sh
#   bash scripts/run_public.sh status
#   bash scripts/run_public.sh stop
#
# Требования: Python 3.10+ (.venv), tesseract-ocr и tesseract-ocr-rus.
# LibreDWG необязателен (bash scripts/install_dwg.sh).

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/logs"

PORT="${PORT:-7860}"
HOST="${HOST:-0.0.0.0}"

stop_pidfile() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    return 0
  fi
  local pid
  pid="$(tr -d '[:space:]' < "$file" || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null || true
    local i
    for i in 1 2 3 4 5; do
      kill -0 "${pid}" 2>/dev/null || break
      sleep 0.3
    done
    if kill -0 "${pid}" 2>/dev/null; then
      kill -9 "${pid}" 2>/dev/null || true
    fi
  fi
  rm -f "$file"
}

cmd_stop() {
  echo "Останавливаю туннель и веб-сервер..."
  stop_pidfile "$ROOT/logs/tunnel_alt.supervisor.pid"
  stop_pidfile "$ROOT/logs/tunnel_alt.pid"
  stop_pidfile "$ROOT/logs/tunnel.supervisor.pid"
  stop_pidfile "$ROOT/logs/tunnel.pid"
  stop_pidfile "$ROOT/logs/web.supervisor.pid"
  stop_pidfile "$ROOT/logs/web.pid"
  echo "Остановлено. При следующем запуске туннель выдаст НОВЫЙ публичный адрес."
}

cmd_status() {
  echo "Каталог проекта: $ROOT"
  if [[ -f "$ROOT/logs/web.supervisor.pid" ]]; then
    echo "Супервизор сайта: PID $(cat "$ROOT/logs/web.supervisor.pid") (logs/web.supervisor.pid)"
  else
    echo "Супервизор сайта: не запущен"
  fi
  if [[ -f "$ROOT/logs/web.pid" ]]; then
    echo "uvicorn:           PID $(cat "$ROOT/logs/web.pid") (logs/web.pid)"
  fi
  if [[ -f "$ROOT/logs/tunnel.supervisor.pid" ]]; then
    echo "Супервизор Cloudflare: PID $(cat "$ROOT/logs/tunnel.supervisor.pid")"
  fi
  if [[ -f "$ROOT/logs/tunnel.pid" ]]; then
    echo "cloudflared:        PID $(cat "$ROOT/logs/tunnel.pid")"
  fi
  if [[ -f "$ROOT/logs/tunnel_alt.supervisor.pid" ]]; then
    echo "Супервизор запаса:  PID $(cat "$ROOT/logs/tunnel_alt.supervisor.pid")"
  fi
  if [[ -f "$ROOT/logs/tunnel_alt.pid" ]]; then
    echo "клиент запаса:      PID $(cat "$ROOT/logs/tunnel_alt.pid")"
  fi
  if [[ -f "$ROOT/PUBLIC_URL.txt" ]]; then
    echo "Публичный адрес:    $(cat "$ROOT/PUBLIC_URL.txt")"
    echo "(адрес меняется, если перезапустить туннель)"
  else
    echo "Публичный адрес:    ещё нет (смотрите logs/tunnel.log или logs/tunnel_alt.log)"
  fi
}

pick_cloudflared_dest() {
  if [[ -n "${CLOUDFLARED_BIN:-}" ]]; then
    printf '%s\n' "$CLOUDFLARED_BIN"
    return
  fi
  if [[ -x /workspace/bin/cloudflared ]]; then
    printf '%s\n' /workspace/bin/cloudflared
    return
  fi
  if [[ -d /workspace/bin && -w /workspace/bin ]]; then
    printf '%s\n' /workspace/bin/cloudflared
    return
  fi
  mkdir -p "${HOME}/.local/bin"
  printf '%s\n' "${HOME}/.local/bin/cloudflared"
}

ensure_cloudflared() {
  local dest asset url
  dest="$(pick_cloudflared_dest)"
  if [[ -x "$dest" ]]; then
    export CLOUDFLARED_BIN="$dest"
    return 0
  fi
  case "$(uname -m)" in
    x86_64|amd64) asset="cloudflared-linux-amd64" ;;
    aarch64|arm64) asset="cloudflared-linux-arm64" ;;
    *)
      echo "Неподдерживаемая архитектура: $(uname -m). Скачайте cloudflared вручную: https://github.com/cloudflare/cloudflared/releases" >&2
      exit 1
      ;;
  esac
  url="https://github.com/cloudflare/cloudflared/releases/latest/download/${asset}"
  echo "Скачиваю официальный бинарник ${asset} → ${dest}"
  mkdir -p "$(dirname "$dest")"
  curl -fL --retry 3 --retry-delay 2 -o "$dest" "$url"
  chmod +x "$dest"
  "$dest" --version
  export CLOUDFLARED_BIN="$dest"
}

public_health_ok() {
  local url="$1"
  local code
  code="$(curl -sS -o /tmp/gost-pub-health.body -w '%{http_code}' --max-time 12 \
    -A 'Mozilla/5.0 gost-checker' \
    -H 'bypass-tunnel-reminder: true' \
    -H 'Bypass-Tunnel-Reminder: true' \
    "${url}/health" 2>/dev/null || echo 000)"
  [[ "$code" == "200" ]] && grep -q '"ok"' /tmp/gost-pub-health.body 2>/dev/null
}

wait_url_file() {
  local seconds="${1:-90}" i url
  url=""
  for i in $(seq 1 "$seconds"); do
    if [[ -f "$ROOT/PUBLIC_URL.txt" ]]; then
      url="$(tr -d '[:space:]' < "$ROOT/PUBLIC_URL.txt" || true)"
      if [[ "$url" == http://* || "$url" == https://* ]]; then
        printf '%s\n' "$url"
        return 0
      fi
    fi
    sleep 1
  done
  return 1
}

start_alt_tunnel() {
  chmod +x "$ROOT/scripts/tunnel_alt_forever.sh" 2>/dev/null || true
  echo "Запускаю запасной туннель (scripts/tunnel_alt_forever.sh, TUNNEL_ALT=${TUNNEL_ALT:-auto})..."
  nohup env PORT="$PORT" TUNNEL_ALT="${TUNNEL_ALT:-auto}" \
    bash "$ROOT/scripts/tunnel_alt_forever.sh" >> "$ROOT/logs/tunnel_alt.supervisor.out" 2>&1 &
  disown || true
}

wait_local_health() {
  local i
  for i in $(seq 1 60); do
    if curl -sf --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "Сайт не ответил на http://127.0.0.1:${PORT}/health за 30 с. Смотрите logs/web.log" >&2
  return 1
}

cmd_start() {
  chmod +x "$ROOT/scripts/serve_forever.sh" \
           "$ROOT/scripts/tunnel_forever.sh" \
           "$ROOT/scripts/tunnel_alt_forever.sh" 2>/dev/null || true

  if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    echo "Нет виртуального окружения .venv. Создайте его:"
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
  fi
  if ! command -v tesseract >/dev/null 2>&1 && [[ ! -x "${HOME}/.local/bin/tesseract" ]]; then
    echo "Предупреждение: tesseract не найден в PATH. OCR штампа на сканах/PDF не заработает."
    echo "На Debian/Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-rus"
  fi

  if [[ "${TUNNEL_PROVIDER:-auto}" != "alt" ]]; then
    ensure_cloudflared
  fi

  if curl -sf --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "Сайт уже отвечает на порту ${PORT}, новый супервизор не запускаю."
  else
    echo "Запускаю супервизор сайта (scripts/serve_forever.sh)..."
    nohup bash "$ROOT/scripts/serve_forever.sh" >> "$ROOT/logs/web.supervisor.out" 2>&1 &
    disown || true
  fi
  wait_local_health

  local prefer="${TUNNEL_PROVIDER:-auto}"
  local url=""
  local used="none"

  if [[ "$prefer" != "alt" ]]; then
    if [[ -f "$ROOT/logs/tunnel.supervisor.pid" ]] && kill -0 "$(cat "$ROOT/logs/tunnel.supervisor.pid")" 2>/dev/null; then
      echo "Супервизор Cloudflare уже запущен (PID $(cat "$ROOT/logs/tunnel.supervisor.pid"))."
    else
      echo "Запускаю Cloudflare Quick Tunnel (аккаунт не нужен)..."
      nohup env CLOUDFLARED_BIN="$CLOUDFLARED_BIN" TUNNEL_ORIGIN="http://localhost:${PORT}" \
        bash "$ROOT/scripts/tunnel_forever.sh" >> "$ROOT/logs/tunnel.supervisor.out" 2>&1 &
      disown || true
    fi
    echo "Жду адреса trycloudflare.com..."
    url="$(wait_url_file 50 || true)"
    if [[ -n "$url" ]]; then
      echo "Проверяю ${url}/health снаружи..."
      local i ok=0
      for i in 1 2 3 4 5 6 7 8; do
        if public_health_ok "$url"; then ok=1; break; fi
        sleep 3
      done
      if [[ "$ok" -eq 1 ]]; then
        used="cloudflared"
      else
        echo "Cloudflare выдал адрес, но /health не ответил (часто HTTP 530, если закрыт порт 7844)."
        if [[ "$prefer" == "cloudflared" ]]; then
          used="cloudflared-unverified"
        else
          echo "Останавливаю cloudflared и пробую запасной туннель..."
          stop_pidfile "$ROOT/logs/tunnel.supervisor.pid"
          stop_pidfile "$ROOT/logs/tunnel.pid"
          url=""
        fi
      fi
    fi
  fi

  if [[ "$used" == "none" ]]; then
    if [[ -f "$ROOT/logs/tunnel_alt.supervisor.pid" ]] && kill -0 "$(cat "$ROOT/logs/tunnel_alt.supervisor.pid")" 2>/dev/null; then
      echo "Запасной супервизор уже запущен (PID $(cat "$ROOT/logs/tunnel_alt.supervisor.pid"))."
    else
      start_alt_tunnel
    fi
    echo "Жду адрес запасного туннеля..."
    url="$(wait_url_file 80 || true)"
    used="alt"
  fi

  echo
  if [[ -n "$url" ]]; then
    echo "Публичный адрес: ${url}"
    echo "Канал: ${used}"
    echo "Он сохранён в ${ROOT}/PUBLIC_URL.txt"
    echo "После перезапуска туннеля адрес будет ДРУГИМ — смотрите этот файл снова."
  else
    echo "Адрес ещё не появился. Подождите и выполните: bash scripts/run_public.sh status"
    echo "Логи: ${ROOT}/logs/tunnel.log и ${ROOT}/logs/tunnel_alt.log"
  fi
  echo
  echo "Локально:  http://127.0.0.1:${PORT}/"
  echo "Логи сайта:   ${ROOT}/logs/web.log"
  echo "Логи туннеля: ${ROOT}/logs/tunnel.log , ${ROOT}/logs/tunnel_alt.log"
  echo "Остановка:    bash scripts/run_public.sh stop"
}

case "${1:-start}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  *)
    echo "Использование: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
