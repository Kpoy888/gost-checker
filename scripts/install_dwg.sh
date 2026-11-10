#!/usr/bin/env bash
# Установка GNU LibreDWG (утилиты dwg2dxf и dxf2dwg) для gost-checker.
# 1) если утилиты уже есть — ничего не делает;
# 2) пробует пакет дистрибутива (libredwg-tools);
# 3) иначе собирает официальный tarball GNU.
# Пакета PyPI с двоичными dwg2dxf/dxf2dwg обычно нет — CLI ставим из GNU LibreDWG.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${LIBREDWG_PREFIX:-$ROOT/vendor/libredwg}"
VERSION="${LIBREDWG_VERSION:-0.13.3}"
TARBALL="libredwg-${VERSION}.tar.gz"

have_tools() {
  if command -v dwg2dxf >/dev/null 2>&1 && command -v dxf2dwg >/dev/null 2>&1; then
    echo "Найдены утилиты в PATH:"
    echo "  dwg2dxf: $(command -v dwg2dxf)"
    dwg2dxf --version 2>&1 | head -1 || true
    echo "  dxf2dwg: $(command -v dxf2dwg)"
    return 0
  fi
  if [[ -x "$PREFIX/bin/dwg2dxf" && -x "$PREFIX/bin/dxf2dwg" ]]; then
    echo "Уже установлено в $PREFIX/bin"
    "$PREFIX/bin/dwg2dxf" --version 2>&1 | head -1 || true
    return 0
  fi
  return 1
}

if have_tools; then
  exit 0
fi

if command -v apt-get >/dev/null 2>&1; then
  echo "Пробую пакет libredwg-tools через apt..."
  if apt-cache show libredwg-tools >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1; then
      sudo apt-get install -y libredwg-tools || true
    else
      apt-get install -y libredwg-tools || true
    fi
    if have_tools; then
      exit 0
    fi
  else
    echo "Пакета libredwg-tools в репозиториях нет — соберу GNU LibreDWG из исходников."
  fi
fi

if ! command -v make >/dev/null 2>&1; then
  echo "Не найден GNU make. Установите: sudo apt install build-essential" >&2
  exit 1
fi
if ! command -v gcc >/dev/null 2>&1 && ! command -v cc >/dev/null 2>&1; then
  echo "Не найден компилятор C (gcc). Установите: sudo apt install build-essential" >&2
  exit 1
fi
if ! command -v tar >/dev/null 2>&1 || ! command -v gzip >/dev/null 2>&1; then
  echo "Нужны tar и gzip." >&2
  exit 1
fi

DOWNLOADER=""
if command -v curl >/dev/null 2>&1; then
  DOWNLOADER="curl"
elif command -v wget >/dev/null 2>&1; then
  DOWNLOADER="wget"
else
  echo "Нужен curl или wget, чтобы скачать исходники GNU LibreDWG." >&2
  exit 1
fi

WORKDIR="${TMPDIR:-/tmp}/libredwg-build-$$"
mkdir -p "$WORKDIR"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

fetch() {
  local url="$1" dest="$2"
  echo "Скачиваю $url"
  if [[ "$DOWNLOADER" == "curl" ]]; then
    curl -fL --retry 3 -o "$dest" "$url"
  else
    wget -O "$dest" "$url"
  fi
}

ARCHIVE="$WORKDIR/$TARBALL"
OK=0
for url in \
  "https://ftp.gnu.org/gnu/libredwg/${TARBALL}" \
  "https://ftpmirror.gnu.org/libredwg/${TARBALL}"; do
  if fetch "$url" "$ARCHIVE"; then
    OK=1
    break
  fi
  echo "Не получилось скачать с $url, пробую зеркало..."
done
if [[ "$OK" -ne 1 ]]; then
  echo "Не удалось скачать LibreDWG ${VERSION} с ftp.gnu.org." >&2
  exit 1
fi

tar -xzf "$ARCHIVE" -C "$WORKDIR"
SRC_DIR="$(find "$WORKDIR" -maxdepth 1 -type d -name 'libredwg-*' | head -1)"
if [[ -z "$SRC_DIR" ]]; then
  echo "В архиве не найден каталог исходников." >&2
  exit 1
fi
cd "$SRC_DIR"
echo "Собираю LibreDWG ${VERSION} в $PREFIX ..."
# Относительный RPATH, чтобы каталог vendor/ можно было переносить вместе с проектом.
export LDFLAGS='-Wl,-rpath,$ORIGIN/../lib'
./configure --prefix="$PREFIX" --disable-bindings --disable-docs
make -j"$(nproc 2>/dev/null || echo 2)"
make install

if [[ -x "$PREFIX/bin/dwg2dxf" && -x "$PREFIX/bin/dxf2dwg" ]]; then
  echo "Готово:"
  "$PREFIX/bin/dwg2dxf" --version
  echo "Утилиты: $PREFIX/bin/dwg2dxf и $PREFIX/bin/dxf2dwg"
  echo "gost-checker найдёт их автоматически (vendor/libredwg/bin)."
  exit 0
fi

echo "Сборка завершилась, но двоичные файлы не найдены в $PREFIX/bin" >&2
exit 1
