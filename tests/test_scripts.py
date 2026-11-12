"""Скрипты запуска должны быть корректным bash и не путать старый публичный адрес."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [
    ROOT / "scripts" / "run_public.sh",
    ROOT / "scripts" / "serve_forever.sh",
    ROOT / "scripts" / "tunnel_forever.sh",
    ROOT / "scripts" / "tunnel_alt_forever.sh",
    ROOT / "scripts" / "install_dwg.sh",
]


def test_shell_scripts_parse_and_shellcheck():
    for path in SCRIPTS:
        parsed = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert parsed.returncode == 0, parsed.stderr
    shellcheck = shutil.which("shellcheck")
    if not shellcheck:
        pytest.skip("shellcheck не установлен")
    checked = subprocess.run([shellcheck, "-x", *[str(path) for path in SCRIPTS]], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_public_url_helpers_ignore_stale_address_and_prefer_home_binary(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".local" / "bin").mkdir(parents=True)
    binary = home / ".local" / "bin" / "cloudflared"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    script = r"""
set -u
source "$1"
ROOT="$2"
HOME="$3"
unset CLOUDFLARED_BIN
got="$(pick_cloudflared_dest)"
[[ "$got" == "$HOME/.local/bin/cloudflared" ]]

mkdir -p "$ROOT"
printf '%s\n' 'https://stale.example' > "$ROOT/PUBLIC_URL.txt"
touch -d '2000-01-01 00:00:00' "$ROOT/PUBLIC_URL.txt"
if wait_url_file 1 "$(date +%s)"; then
  echo "stale url was accepted" >&2
  exit 1
fi
printf '%s\n' 'https://fresh.example' > "$ROOT/PUBLIC_URL.txt"
since="$(stat -c %Y "$ROOT/PUBLIC_URL.txt")"
fresh="$(wait_url_file 2 "$since")"
[[ "$fresh" == "https://fresh.example" ]]

x64="$(bore_asset_for_arch x86_64)"
arm="$(bore_asset_for_arch aarch64)"
[[ "$x64" == *x86_64* ]]
[[ "$arm" == *aarch64* ]]
[[ "$x64" != "$arm" ]]
if bore_asset_for_arch mips; then
  echo "unknown arch should fail" >&2
  exit 1
fi
log="$ROOT/tunnel.log"
printf 'old https://old.loca.lt/x\n' > "$log"
mark="$(wc -c < "$log" | tr -d '[:space:]')"
printf 'new https://abc.pinggy.link\n' >> "$log"
found="$(extract_url_from_log "$log" "$mark")"
[[ "$found" == "https://abc.pinggy.link" ]]
"""
    env = os.environ.copy()
    env["HOME"] = str(home)
    script = script.replace(
        'source "$1"\n',
        'source "$1"\nsource "$4"\n',
    )
    proc = subprocess.run(
        [
            "bash",
            "-c",
            script,
            "bash",
            str(ROOT / "scripts" / "run_public.sh"),
            str(project),
            str(home),
            str(ROOT / "scripts" / "tunnel_alt_forever.sh"),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_serve_forever_restarts_after_crash(tmp_path):
    fake = tmp_path / "python"
    fake.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    log = ROOT / "logs" / "web.log"
    before = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    env = os.environ.copy()
    env.update(
        {
            "GOST_PYTHON": str(fake),
            "RESTART_DELAY": "0.2",
            "PORT": "9",
            "HOST": "127.0.0.1",
        }
    )
    proc = subprocess.run(
        ["timeout", "1.6", "bash", str(ROOT / "scripts" / "serve_forever.sh")],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    # timeout завершает супервизор сигналом, код 124.
    assert proc.returncode in {124, 0}, proc.stderr
    after = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    fresh = after[len(before):] if after.startswith(before) else after
    assert fresh.count("запуск uvicorn") >= 2, fresh[-800:]
    for name in ("web.supervisor.pid", "web.pid"):
        pidfile = ROOT / "logs" / name
        if not pidfile.exists():
            continue
        pid = pidfile.read_text(encoding="utf-8").strip()
        alive = False
        if pid.isdigit():
            try:
                os.kill(int(pid), 0)
                alive = True
            except OSError:
                alive = False
        if not alive:
            pidfile.unlink(missing_ok=True)
