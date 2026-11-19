"""Уведомление в Telegram, когда проверка готова. Без токена функция выключена."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

API = "https://api.telegram.org"


def bot_token() -> str:
    return (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def default_chat_id() -> str:
    return (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()


def enabled() -> bool:
    return bool(bot_token())


def normalize_chat_id(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith("@") and 2 <= len(raw) <= 64 and all(ch.isalnum() or ch == "_" for ch in raw[1:]):
        return raw
    if raw.lstrip("-").isdigit() and 1 <= len(raw.lstrip("-")) <= 20:
        return raw
    return ""


def _post(method: str, data: dict, files=None, timeout: float = 12.0):
    import httpx

    token = bot_token()
    url = f"{API}/bot{token}/{method}"
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, data=data, files=files)
        response.raise_for_status()
        return response


def notify_ready(
    *,
    filename: str,
    summary: Optional[dict] = None,
    report_path: Optional[str] = None,
    chat_id: Optional[str] = None,
    extra_lines: Optional[list[str]] = None,
) -> bool:
    """Отправить сообщение. Сетевые ошибки не пробрасываются. Возвращает, ушло ли сообщение."""
    if not enabled():
        return False
    chat = normalize_chat_id(chat_id) or normalize_chat_id(default_chat_id())
    if not chat:
        return False
    summary = summary or {}
    lines = [
        "Проверка чертежа готова.",
        f"Файл: {Path(filename).name}",
        (
            f"ошибки: {summary.get('error', 0)}, замечания: {summary.get('warning', 0)}, "
            f"соответствует: {summary.get('ok', 0)}, исправлено: {summary.get('fixed', 0)}"
        ),
        "Это не нормоконтроль: проверены только правила программы.",
    ]
    for line in extra_lines or []:
        if line:
            lines.append(str(line))
    text = "\n".join(lines)[:3500]
    try:
        _post("sendMessage", {"chat_id": chat, "text": text})
    except Exception:
        return False
    path = Path(report_path) if report_path else None
    if path and path.is_file():
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if 0 < size <= 45 * 1024 * 1024:
            try:
                with path.open("rb") as handle:
                    _post(
                        "sendDocument",
                        {"chat_id": chat, "caption": "Отчёт"},
                        files={"document": (path.name, handle, "application/octet-stream")},
                    )
            except Exception:
                # сообщение уже ушло — вложение необязательно
                return True
    return True
