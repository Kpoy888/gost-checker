"""Пояснения к замечаниям: локальная карточка нормы и необязательная языковая модель.

Модель, если она настроена, только пересказывает карточку и текст замечания.
Номер пункта, которого нет в карточке, в ответ не пропускается: такие ответы
отбрасываются, и остаётся заготовленный текст.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

import httpx

from .models import CheckResult, ProcessResult

_RULES_PATH = Path(__file__).resolve().parent / "knowledge" / "rules.json"
_LOCK = threading.Lock()
_RULES: Optional[dict[str, dict[str, Any]]] = None

_UNKNOWN = {
    "mode": "system",
    "citation": "Отдельный пункт стандарта для этого кода проверки не указан.",
    "rule": "",
    "why": "В локальной базе нет карточки с таким кодом. Выше приведено только то, что установила программа.",
    "how": "Сверьтесь с полем «Норма» в отчёте. Номер пункта здесь сознательно не подставлен.",
}

_SYSTEM_PROMPT = (
    "Ты поясняешь замечание проверки оформления чертежа по СПДС и ЕСКД. "
    "Пиши по-русски, ясно, для студента и проектировщика. "
    "Опирайся только на карточку нормы и на конкретное замечание из запроса. "
    "Не добавляй номера ГОСТ, пункты, таблицы, приложения, рисунки и формы, которых нет в карточке. "
    "Если карточка называет стандарт без номера пункта, не выдумывай номер. "
    "Не добавляй факты о чертеже сверх текста замечания. "
    "Структура: «Что не так.», «Почему это важно.», «Как исправить.», «Основание.». "
    "В разделе «Основание.» дословно приведи строку citation из карточки."
)

_GOST_RE = re.compile(r"ГОСТ\s*Р?\s*\d+(?:\.\d+)*(?:\s*-\s*\d+)?", re.IGNORECASE)
_LOCATOR_RE = re.compile(
    r"(?:п\.|табл\.|прил\.|рис\.)\s*[0-9A-Za-zА-Яа-яЁё]+(?:\.[0-9A-Za-zА-Яа-яЁё]+)*",
    re.IGNORECASE,
)
_FORM_RE = re.compile(r"форм[аыуеой]?\s*\d+", re.IGNORECASE)


def load_rules() -> dict[str, dict[str, Any]]:
    global _RULES
    if _RULES is not None:
        return _RULES
    with _LOCK:
        if _RULES is None:
            data = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("База пояснений должна быть объектом JSON")
            _RULES = data
    return _RULES


def rule_ids() -> list[str]:
    return sorted(key for key in load_rules() if not str(key).startswith("_"))


def llm_settings() -> Optional[dict[str, Any]]:
    """Настройки OpenAI-совместимого API. Без ключа или без имени модели — выключено."""
    key = os.environ.get("LLM_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL", "").strip()
    if not key or not model:
        return None
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").strip() or "https://api.openai.com/v1"
    raw_timeout = os.environ.get("LLM_TIMEOUT_SEC", "20").strip() or "20"
    try:
        timeout = float(raw_timeout)
    except ValueError:
        timeout = 20.0
    if timeout <= 0:
        timeout = 20.0
    timeout = min(timeout, 120.0)
    return {
        "api_key": key,
        "model": model,
        "base_url": base.rstrip("/"),
        "timeout": timeout,
    }


def privacy_suffix() -> str:
    if llm_settings() is None:
        return (
            " Пояснения к замечаниям собираются на этом сервере из локальной базы правил "
            "и во внешний сервис не отправляются."
        )
    return (
        " Тексты замечаний (формулировка проверки и краткий перечень деталей, без файла чертежа) "
        "отправляются в указанный вами языковой сервис, чтобы пересказать пояснение из локальной базы. "
        "Сам чертёж не передаётся. Если сервис не ответил, остаётся локальный текст."
    )


def source_label(source: str) -> str:
    if source == "llm":
        return "языковая модель по карточке нормы"
    return "локальная карточка нормы"


def _normalize_refs(text: str) -> str:
    value = text.replace("—", "-").replace("–", "-").replace("−", "-")
    value = value.replace("ё", "е").replace("Ё", "Е")
    value = re.sub(r"\bпункт(?:е|а|у|ом)?\s+(?=\d)", "п. ", value, flags=re.IGNORECASE)
    value = re.sub(r"\bтаблиц[аеиуы]\s+(?=\d)", "табл. ", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\bприложени[еяийю]\s+(?=[0-9A-Za-zА-Яа-яЁё])",
        "прил. ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\bрисунк[еау]\s+(?=[0-9A-Za-zА-Яа-яЁё])",
        "рис. ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s+", " ", value)
    return value


def _gost_key(raw: str) -> str:
    return re.sub(r"\s+", "", _normalize_refs(raw)).upper()


def _locator_key(raw: str) -> str:
    return re.sub(r"\s+", "", _normalize_refs(raw)).lower()


def _collect(text: str) -> tuple[set[str], set[str], set[str]]:
    normalized = _normalize_refs(text)
    gosts = {_gost_key(match.group(0)) for match in _GOST_RE.finditer(normalized)}
    locators = {_locator_key(match.group(0)) for match in _LOCATOR_RE.finditer(normalized)}
    forms = set()
    for match in _FORM_RE.finditer(normalized):
        key = _locator_key(match.group(0))
        key = re.sub(r"^форм[а-яё]*", "форма", key)
        forms.add(key)
    return gosts, locators, forms


def _fits(candidate: str, allowed: set[str]) -> bool:
    if candidate in allowed:
        return True
    for item in allowed:
        if item.startswith(candidate + "-") or item.startswith(candidate + "."):
            return True
    return False


def invented_references(text: str, allowed_text: str) -> list[str]:
    """Ссылки из ответа, которых нет в карточке. Пустой список — ответ можно показать."""
    got_g, got_l, got_f = _collect(text)
    ok_g, ok_l, ok_f = _collect(allowed_text)
    invented: list[str] = []
    for item in sorted(got_g):
        if not _fits(item, ok_g):
            invented.append(item)
    for item in sorted(got_l):
        if not _fits(item, ok_l):
            invented.append(item)
    for item in sorted(got_f):
        if not _fits(item, ok_f):
            invented.append(item)
    return invented


def _entry(check_id: str) -> dict[str, Any]:
    raw = load_rules().get(check_id)
    if isinstance(raw, dict) and raw.get("why"):
        return raw
    return dict(_UNKNOWN)


def _allowed_text(entry: dict[str, Any]) -> str:
    return "\n".join(str(entry.get(key) or "") for key in ("citation", "rule", "why", "how"))


def _detail_lines(check: CheckResult, limit: int = 12) -> list[str]:
    lines = []
    for item in check.details or []:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > 500:
            text = text[:500].rstrip() + "…"
        lines.append(text)
        if len(lines) >= limit:
            break
    return lines


def _fix_note(check: CheckResult) -> str:
    if check.severity not in {"error", "warning"}:
        return ""
    if check.auto_fixed:
        return (
            "Безопасное исправление уже записано в выходной файл: "
            "откройте его в CAD и проверьте рамку, штамп и надписи."
        )
    if check.can_autofix:
        return (
            "Часть правки можно включить в этой программе: флаг --fix или галочка исправления на сайте. "
            "Геометрию, которую нельзя поправить безопасно, программа не двигает."
        )
    return ""


def _append_once(text: str, note: str) -> str:
    if not note or note in text:
        return text
    return text.rstrip() + "\n\n" + note


def compose_template(entry: dict[str, Any], check: CheckResult) -> str:
    citation = str(entry.get("citation") or "").strip()
    why = str(entry.get("why") or "").strip()
    how = str(entry.get("how") or "").strip()
    rule = str(entry.get("rule") or "").strip()
    mode = str(entry.get("mode") or "rule")
    shown = _detail_lines(check)
    detail_text = ""
    if shown:
        hidden = len([item for item in (check.details or []) if str(item).strip()]) - len(shown)
        body = "\n".join(f"— {line}" for line in shown)
        if hidden > 0:
            body += f"\n— … и ещё {hidden}"
        detail_text = "Что именно обнаружено.\n" + body

    if check.severity in {"error", "warning"}:
        parts = [f"Что не так. {check.message}".rstrip()]
        if detail_text:
            parts.append(detail_text)
        if why:
            parts.append(f"Почему это важно. {why}")
        if how:
            parts.append(f"Как исправить. {how}")
        if citation:
            parts.append(f"Основание. {citation}")
        if rule:
            parts.append(f"Кратко о норме. {rule}")
        return _append_once("\n\n".join(parts), _fix_note(check))

    if mode == "rule" and check.severity == "ok":
        parts = [f"Замечаний нет. {check.message}".rstrip()]
        if citation:
            parts.append(f"Основание. {citation}")
        if rule:
            parts.append(f"Кратко о норме. {rule}")
        return "\n\n".join(parts)

    parts = [check.message.strip() or "Сведение по этой проверке."]
    if detail_text:
        parts.append(detail_text)
    if why:
        parts.append(f"Пояснение. {why}")
    if how:
        parts.append(f"Что делать. {how}")
    if citation:
        parts.append(f"Основание. {citation}")
    if rule and mode == "rule":
        parts.append(f"Кратко о норме. {rule}")
    return "\n\n".join(parts)


def _user_prompt(entry: dict[str, Any], check: CheckResult) -> str:
    payload = {
        "rule_id": check.check_id,
        "title": check.title,
        "severity": check.severity,
        "message": check.message,
        "details": _detail_lines(check),
        "auto_fixed": bool(check.auto_fixed),
        "can_autofix": bool(check.can_autofix),
        "card": {
            "citation": entry.get("citation") or "",
            "rule": entry.get("rule") or "",
            "why": entry.get("why") or "",
            "how": entry.get("how") or "",
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def call_llm(settings: dict[str, Any], user_prompt: str) -> str:
    url = settings["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": settings["model"],
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {
        "Authorization": f"Bearer {settings['api_key']}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(settings["timeout"])
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        chunks = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                chunks.append(str(part.get("text") or ""))
        content = "".join(chunks)
    return str(content or "").strip()


def explain_check(check: CheckResult) -> tuple[str, str, str]:
    entry = _entry(check.check_id)
    template = compose_template(entry, check)
    citation = str(entry.get("citation") or "").strip()
    settings = llm_settings()
    if settings is None or check.severity not in {"error", "warning"}:
        return template, citation, "template"
    try:
        raw = call_llm(settings, _user_prompt(entry, check))
    except Exception:
        return template, citation, "template"
    text = (raw or "").strip()
    if len(text) < 80:
        return template, citation, "template"
    if invented_references(text, _allowed_text(entry)):
        return template, citation, "template"
    if citation and citation not in text:
        text = text.rstrip() + "\n\nОснование. " + citation
    text = _append_once(text, _fix_note(check))
    return text, citation, "llm"


def annotate_result(result: ProcessResult) -> ProcessResult:
    for check in result.checks:
        if not isinstance(check, CheckResult):
            continue
        if check.explanation:
            continue
        text, citation, source = explain_check(check)
        check.explanation = text
        check.citation = citation
        check.explanation_source = source
    return result
