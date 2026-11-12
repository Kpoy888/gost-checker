#!/usr/bin/env bash
# Запасной публичный туннель, если Cloudflare Quick Tunnel не поднимается
# (закрыт исходящий UDP/TCP 7844). Аккаунт и карта не нужны.
#
# Поставщик: TUNNEL_ALT=auto|localtunnel|pinggy|serveo|localhost_run|tunnelmole|bore
#   auto — пробует по очереди и оставляет первый, у которого /health отвечает снаружи.
#
# Логи: logs/tunnel_alt.log
# PID супервизора: logs/tunnel_alt.supervisor.pid
# PID клиента:     logs/tunnel_alt.pid
# Адрес:           PUBLIC_URL.txt  (меняется при каждом перезапуске)
#
# Замечания по сервисам:
#   localtunnel  — URL *.loca.lt; бывает промежуточная страница, заголовок
#                  bypass-tunnel-reminder: true
#   pinggy       — ssh -p 443; бесплатная сессия около 60 минут, потом новый URL
#   serveo       — ssh на 22; адрес *.serveo.net
#   localhost_run — ssh -p 443 nokey@localhost.run
#   tunnelmole   — npm; websocket на service.tunnelmole.com:8083 (не 443!)
#   bore         — bore.pub, обычно http://bore.pub:<порт>, не HTTPS

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

LOG="$ROOT/logs/tunnel_alt.log"
URL_FILE="$ROOT/PUBLIC_URL.txt"
SUPER_PID="$ROOT/logs/tunnel_alt.supervisor.pid"
CHILD_PID="$ROOT/logs/tunnel_alt.pid"
PORT="${PORT:-7860}"
PROVIDER="${TUNNEL_ALT:-auto}"

LOG_OFFSET=0
CHILD=""
cleanup() {
  local why="${1:-EXIT}"
  if [[ -n "${CHILD}" ]] && kill -0 "${CHILD}" 2>/dev/null; then
    kill "${CHILD}" 2>/dev/null || true
    wait "${CHILD}" 2>/dev/null || true
  fi
  if [[ "$why" == "TERM" || "$why" == "INT" ]]; then
    trap - EXIT INT TERM
    exit 0
  fi
}

ts() { date "+%Y-%m-%d %H:%M:%S %Z"; }

extract_url_from_log() {
  local file="$1"
  local mark="${2:-0}"
  [[ -f "$file" ]] || return 1
  [[ "$mark" =~ ^[0-9]+$ ]] || mark=0
  # Только хвост после старта текущего поставщика, иначе берётся URL предыдущей попытки.
  tail -c +"$((mark + 1))" "$file" 2>/dev/null \
    | grep -oE 'https://[A-Za-z0-9.-]+\.(loca\.lt|localtunnel\.me|trycloudflare\.com|pinggy\.io|pinggy\.link|a\.free\.pinggy\.link|serveo\.net|localhost\.run|lhr\.life|tunnelmole\.(net|com)|tmole\.cloud)' \
    | tail -n 1
}

extract_bore_url() {
  local file="$1"
  local mark="${2:-0}"
  [[ -f "$file" ]] || return 1
  [[ "$mark" =~ ^[0-9]+$ ]] || mark=0
  # listening at bore.pub:XXXXX
  local hit
  hit="$(tail -c +"$((mark + 1))" "$file" 2>/dev/null | grep -oE 'bore\.pub:[0-9]+' | tail -n 1 || true)"
  if [[ -n "$hit" ]]; then
    printf 'http://%s\n' "$hit"
  fi
}

write_url() {
  local url="$1"
  [[ -n "$url" ]] || return 1
  printf '%s\n' "$url" > "$URL_FILE"
  echo "[$(ts)] опубликован адрес: ${url} (меняется при перезапуске туннеля)" >> "$LOG"
}

public_health_ok() {
  local url="$1"
  local code
  code="$(curl -sS -o /tmp/gost-alt-health.body -w '%{http_code}' --max-time 12 \
    -A 'Mozilla/5.0 gost-checker' \
    -H 'bypass-tunnel-reminder: true' \
    -H 'Bypass-Tunnel-Reminder: true' \
    "${url}/health" 2>/dev/null || echo 000)"
  if [[ "$code" == "200" ]] && grep -q '"ok"' /tmp/gost-alt-health.body 2>/dev/null; then
    return 0
  fi
  return 1
}

bore_asset_for_arch() {
  case "$1" in
    x86_64|amd64) printf '%s\n' "bore-v0.5.2-x86_64-unknown-linux-musl.tar.gz" ;;
    aarch64|arm64) printf '%s\n' "bore-v0.5.2-aarch64-unknown-linux-musl.tar.gz" ;;
    *) return 1 ;;
  esac
}

ensure_bore() {
  if command -v bore >/dev/null 2>&1; then
    command -v bore
    return 0
  fi
  if [[ -x "${HOME}/.local/bin/bore" ]]; then
    printf '%s\n' "${HOME}/.local/bin/bore"
    return 0
  fi
  mkdir -p "${HOME}/.local/bin"
  local dest="${HOME}/.local/bin/bore"
  local asset url
  asset="$(bore_asset_for_arch "$(uname -m)")" || {
    echo "[$(ts)] нет сборки bore для $(uname -m)" >> "$LOG"
    return 1
  }
  url="https://github.com/ekzhang/bore/releases/download/v0.5.2/${asset}"
  echo "[$(ts)] скачиваю bore → ${dest}" >> "$LOG"
  local tmp
  tmp="$(mktemp -d)"
  if curl -fL --retry 3 --retry-delay 2 -o "${tmp}/bore.tgz" "$url"; then
    tar -xzf "${tmp}/bore.tgz" -C "$tmp"
    if [[ -x "${tmp}/bore" ]]; then
      cp "${tmp}/bore" "$dest"
      chmod +x "$dest"
      rm -rf "$tmp"
      printf '%s\n' "$dest"
      return 0
    fi
  fi
  rm -rf "$tmp"
  return 1
}

start_localtunnel() {
  npx --yes localtunnel --port "$PORT" >> "$LOG" 2>&1 &
  CHILD=$!
}

start_pinggy() {
  ssh -n -T -p 443 \
    -o StrictHostKeyChecking=accept-new \
    -o ServerAliveInterval=30 \
    -o ExitOnForwardFailure=yes \
    -o BatchMode=yes \
    -R0:localhost:"$PORT" \
    a.pinggy.io x:https >> "$LOG" 2>&1 &
  CHILD=$!
}

start_serveo() {
  ssh -n -T -p 22 \
    -o StrictHostKeyChecking=accept-new \
    -o ServerAliveInterval=30 \
    -o ExitOnForwardFailure=yes \
    -o BatchMode=yes \
    -R 80:localhost:"$PORT" \
    serveo.net >> "$LOG" 2>&1 &
  CHILD=$!
}

start_localhost_run() {
  ssh -n -T -p 443 \
    -o StrictHostKeyChecking=accept-new \
    -o ServerAliveInterval=30 \
    -o ExitOnForwardFailure=yes \
    -o BatchMode=yes \
    -R 80:localhost:"$PORT" \
    nokey@localhost.run >> "$LOG" 2>&1 &
  CHILD=$!
}

start_tunnelmole() {
  npx --yes tunnelmole "$PORT" >> "$LOG" 2>&1 &
  CHILD=$!
}

start_bore() {
  local bin
  bin="$(ensure_bore)" || return 1
  "$bin" local "$PORT" --to bore.pub >> "$LOG" 2>&1 &
  CHILD=$!
}

start_provider() {
  local name="$1"
  if [[ -f "$LOG" ]]; then
    LOG_OFFSET="$(wc -c < "$LOG" | tr -d '[:space:]')"
  else
    LOG_OFFSET=0
  fi
  [[ "$LOG_OFFSET" =~ ^[0-9]+$ ]] || LOG_OFFSET=0
  echo "[$(ts)] запуск запасного туннеля: ${name} (порт ${PORT})" >> "$LOG"
  case "$name" in
    localtunnel) start_localtunnel ;;
    pinggy) start_pinggy ;;
    serveo) start_serveo ;;
    localhost_run) start_localhost_run ;;
    tunnelmole) start_tunnelmole ;;
    bore) start_bore ;;
    *)
      echo "[$(ts)] неизвестный TUNNEL_ALT=${name}" >> "$LOG"
      return 1
      ;;
  esac
  echo "${CHILD}" > "$CHILD_PID"
}

wait_for_url() {
  local name="$1" seconds="${2:-40}" _i url
  for _i in $(seq 1 "$seconds"); do
    if [[ "$name" == "bore" ]]; then
      url="$(extract_bore_url "$LOG" "${LOG_OFFSET:-0}" || true)"
    else
      url="$(extract_url_from_log "$LOG" "${LOG_OFFSET:-0}" || true)"
    fi
    if [[ -n "$url" ]]; then
      printf '%s\n' "$url"
      return 0
    fi
    if [[ -n "${CHILD}" ]] && ! kill -0 "${CHILD}" 2>/dev/null; then
      return 1
    fi
    sleep 1
  done
  return 1
}

stop_child() {
  if [[ -n "${CHILD}" ]] && kill -0 "${CHILD}" 2>/dev/null; then
    kill "${CHILD}" 2>/dev/null || true
    wait "${CHILD}" 2>/dev/null || true
  fi
  CHILD=""
}

run_one() {
  local name="$1" url
  start_provider "$name" || return 1
  url="$(wait_for_url "$name" 45 || true)"
  if [[ -z "$url" ]]; then
    echo "[$(ts)] ${name}: адрес не появился" >> "$LOG"
    stop_child
    return 1
  fi
  write_url "$url"
  echo "[$(ts)] ${name}: проверяю ${url}/health" >> "$LOG"
  local _i
  for _i in 1 2 3 4 5 6; do
    if public_health_ok "$url"; then
      echo "[$(ts)] ${name}: /health отвечает, оставляю этот туннель" >> "$LOG"
      wait "${CHILD}"
      local code=$?
      echo "[$(ts)] ${name} (pid ${CHILD}) завершился с кодом ${code}" >> "$LOG"
      CHILD=""
      return 0
    fi
    sleep 3
  done
  echo "[$(ts)] ${name}: снаружи /health не ответил, пробую другой сервис" >> "$LOG"
  stop_child
  return 1
}

ALL_PROVIDERS=(localtunnel pinggy serveo localhost_run tunnelmole bore)

main_loop() {
  cd "$ROOT" || exit 1
  mkdir -p "$ROOT/logs"
  echo $$ > "$SUPER_PID"
  trap 'cleanup EXIT' EXIT
  trap 'cleanup INT' INT
  trap 'cleanup TERM' TERM
  while true; do
  if [[ "$PROVIDER" == "auto" ]]; then
    local_ok=0
    for name in "${ALL_PROVIDERS[@]}"; do
      if run_one "$name"; then
        local_ok=1
        break
      fi
    done
    if [[ "$local_ok" -eq 0 ]]; then
      echo "[$(ts)] ни один запасной туннель не подтвердил /health; повтор через 15 с" >> "$LOG"
      sleep 15
    fi
  else
    if ! run_one "$PROVIDER"; then
      echo "[$(ts)] ${PROVIDER} не заработал; повтор через 8 с" >> "$LOG"
      sleep 8
    fi
  fi
  echo "[$(ts)] перезапуск запасного туннеля. Новый запуск выдаст ДРУГОЙ адрес." >> "$LOG"
  sleep 3
  done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main_loop
fi
