"""Пояснения к замечаниям: база правил, запасной текст и заглушка языковой модели."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from gost_checker.checks import ALL_CHECKS
from gost_checker.engine import process_file
from gost_checker.explain import (
    explain_check,
    invented_references,
    load_rules,
    llm_settings,
    rule_ids,
)
from gost_checker.models import CheckResult, ProcessResult
from gost_checker.report import render_html, render_markdown
from gost_checker.web import create_app

ROOT = Path(__file__).resolve().parents[1]
_CLAUSE = re.compile(r"п\.\s*\d")


def _source_ids() -> set[str]:
    ids: set[str] = set()
    for path in (ROOT / "gost_checker").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        ids.update(re.findall(r'check_id\s*=\s*"([a-z0-9_]+)"', text))
        if path.parent.name == "checks":
            ids.update(re.findall(r'^\s+id\s*=\s*"([a-z0-9_]+)"', text, re.MULTILINE))
    return ids


def _warning(check_id: str, message: str = "Контрольная формулировка замечания.") -> CheckResult:
    rules = load_rules()
    return CheckResult(
        check_id=check_id,
        title=rules[check_id].get("citation") or check_id,
        gost="норма",
        severity="warning",
        message=message,
        details=["деталь: стиль «Arial», высота 11 мм"],
        can_autofix=True,
    )


def test_knowledge_base_covers_every_rule_id():
    source_ids = _source_ids()
    covered = set(rule_ids())
    assert source_ids, "в коде не найден ни один код проверки"
    assert source_ids == covered
    plugin_ids = {check.id for check in ALL_CHECKS}
    assert plugin_ids <= covered
    rules = load_rules()
    for rule_id, entry in rules.items():
        if str(rule_id).startswith("_"):
            continue
        assert entry.get("why")
        assert entry.get("how")
        citation = entry.get("citation") or ""
        if entry.get("mode") == "system":
            assert "ГОСТ" not in citation
            assert _CLAUSE.search(citation) is None
        else:
            assert "ГОСТ" in citation
            assert entry.get("rule")


def test_template_fallback_without_key_quotes_message_and_citation(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("без ключа языковая модель не вызывается")

    monkeypatch.setattr("gost_checker.explain.call_llm", fail_if_called)
    assert llm_settings() is None
    check = _warning("fonts", "Есть замечания по шрифтам и/или высотам текста.")
    text, citation, source = explain_check(check)
    assert source == "template"
    assert check.message in text
    assert "Что не так." in text
    assert "Почему это важно." in text
    assert "Как исправить." in text
    assert "Основание." in text
    assert "ГОСТ 2.304-81" in citation
    assert "п. 5.1.5" in citation
    assert citation in text
    assert "деталь: стиль «Arial»" in text
    assert "--fix" in text


def test_unknown_rule_does_not_invent_a_clause():
    check = CheckResult(
        check_id="not_a_real_rule",
        title="Неизвестная проверка",
        gost="—",
        severity="error",
        message="Служебный текст без нормы.",
        details=[],
    )
    text, citation, source = explain_check(check)
    assert source == "template"
    assert "Служебный текст без нормы." in text
    assert "ГОСТ" not in citation
    assert _CLAUSE.search(text) is None
    assert invented_references(text, "") == [] or "ГОСТ" not in text


def test_system_message_is_not_presented_as_a_gost_clause():
    check = CheckResult(
        check_id="engine",
        title="Обработка файла",
        gost="—",
        severity="error",
        message="Файл пустой (0 байт).",
        details=[],
    )
    text, citation, source = explain_check(check)
    assert source == "template"
    assert "Файл пустой" in text
    assert "Что не так." in text
    assert "ГОСТ" not in citation
    assert _CLAUSE.search(citation) is None


def test_reference_guard_rejects_unknown_clause_and_allows_rephrase():
    card = "\n".join(
        str(load_rules()["dimensions"].get(key) or "")
        for key in ("citation", "rule", "why", "how")
    )
    bad = "Исправьте засечки по ГОСТ 9.999-2099 п. 8.8 и оставьте п. 5.4.2."
    assert invented_references(bad, card)
    good = (
        "Что не так. Линейные размеры со стрелками.\n\n"
        "Почему это важно. На стройчертеже нужна засечка.\n\n"
        "Как исправить. Поставьте засечки 2–4 мм по п. 5.4.2.\n\n"
        "Основание. ГОСТ Р 21.101-2020 п. 5.4.2; ГОСТ 2.307-68; ГОСТ 2.304-81"
    )
    assert invented_references(good, card) == []


def test_llm_rephrase_is_kept_when_it_stays_inside_the_card(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    citation = load_rules()["sheet_frame"]["citation"]
    reply = (
        "Что не так. Рамка листа не найдена, формат не определён.\n\n"
        "Почему это важно. Лист нельзя подшить без рамки.\n\n"
        "Как исправить. Добавьте внешнюю и внутреннюю рамки.\n\n"
        f"Основание. {citation}"
    )
    captured = {}

    def fake_call(settings, user_prompt):
        captured["settings"] = settings
        captured["prompt"] = user_prompt
        return reply

    monkeypatch.setattr("gost_checker.explain.call_llm", fake_call)
    check = _warning("sheet_frame", "Рамка листа не найдена.")
    check.can_autofix = True
    text, got_citation, source = explain_check(check)
    assert source == "llm"
    assert "Рамка листа не найдена, формат не определён." in text
    assert got_citation == citation
    assert citation in text
    assert captured["settings"]["base_url"] == "https://llm.example/v1"
    assert captured["settings"]["api_key"] == "test-key"
    prompt = json.loads(captured["prompt"])
    assert prompt["message"] == "Рамка листа не найдена."
    assert prompt["card"]["citation"] == citation
    assert "input_path" not in captured["prompt"]


def test_llm_appends_citation_if_the_model_omits_it(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    citation = load_rules()["lines"]["citation"]

    def fake_call(_settings, _prompt):
        return (
            "Что не так. Начертание части линий выпадает из допустимого.\n\n"
            "Почему это важно. На печати тип линии читается неправильно.\n\n"
            "Как исправить. Поправьте толщину и тип линии в CAD."
        )

    monkeypatch.setattr("gost_checker.explain.call_llm", fake_call)
    check = _warning("lines", "Линии классифицированы; замечаний к начертанию: 2.")
    check.can_autofix = False
    text, got, source = explain_check(check)
    assert source == "llm"
    assert got == citation
    assert citation in text
    assert text.strip().endswith(citation)


def test_llm_timeout_and_invented_clause_fall_back(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    check = _warning("title_block", "Основная надпись не найдена.")

    def timeout(_settings, _prompt):
        raise httpx.TimeoutException("secret-timeout-marker")

    monkeypatch.setattr("gost_checker.explain.call_llm", timeout)
    text, citation, source = explain_check(check)
    assert source == "template"
    assert "secret-timeout-marker" not in text
    assert "Основная надпись не найдена." in text
    assert "форма 3" in citation
    assert citation in text

    def invented(_settings, _prompt):
        return (
            "Что не так. Штампа нет.\n\n"
            "Как исправить. Оформите лист по ГОСТ 9.999-2099 п. 99.1.\n\n"
            f"Основание. {citation}"
        )

    monkeypatch.setattr("gost_checker.explain.call_llm", invented)
    text, _citation, source = explain_check(check)
    assert source == "template"
    assert "9.999" not in text
    assert "п. 99.1" not in text
    assert citation in text


def test_llm_http_error_falls_back(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    def broken(_settings, _prompt):
        raise httpx.ConnectError("secret-connect-marker")

    monkeypatch.setattr("gost_checker.explain.call_llm", broken)
    text, _citation, source = explain_check(_warning("scales", "Масштаб 1:75 вне ряда."))
    assert source == "template"
    assert "secret-connect-marker" not in text
    assert "Масштаб 1:75 вне ряда." in text


def test_call_llm_posts_openai_compatible_chat(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "demo-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1/")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "пересказ карточки без новых ссылок, достаточно длинный"}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    from gost_checker.explain import call_llm, llm_settings

    text = call_llm(llm_settings(), '{"card": true}')
    assert text.startswith("пересказ")
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["json"]["model"] == "demo-model"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["messages"][0]["role"] == "system"
    assert "не выдумывай" in captured["json"]["messages"][0]["content"].lower()


def test_reports_include_explanation():
    check = _warning("dimensions", "Размерные стили не соответствуют правилам засечек.")
    check.can_autofix = True
    result = ProcessResult(input_path="лист.dxf", kind="dxf", checks=[check], summary={"warning": 1})
    html = render_html(result)
    markdown = render_markdown(result)
    payload = result.to_dict()
    assert "Пояснение" in html
    assert "п. 5.4.2" in html
    assert "Что не так." in html
    assert "Пояснение" in markdown
    assert payload["checks"][0]["explanation"]
    assert payload["checks"][0]["citation"]
    assert payload["checks"][0]["explanation_source"] == "template"
    assert "Размерные стили не соответствуют" in payload["checks"][0]["explanation"]


def test_ok_result_states_there_is_no_finding():
    check = CheckResult(
        check_id="fonts",
        title="Шрифты и высоты текста",
        gost="ГОСТ 2.304-81",
        severity="ok",
        message="Шрифты и высоты текста соответствуют проверенным правилам ГОСТ 2.304.",
    )
    text, _citation, source = explain_check(check)
    assert source == "template"
    assert text.startswith("Замечаний нет.")
    assert "Что не так." not in text
    assert "ГОСТ 2.304-81" in text


def test_process_file_embeds_explanations(samples_dir, tmp_path):
    result = process_file(samples_dir / "bad.dxf", tmp_path / "out", fix=False)
    problems = [check for check in result.checks if check.severity in {"error", "warning"}]
    assert problems
    for check in problems:
        assert check.explanation
        assert check.citation
        assert check.explanation_source == "template"
        assert check.message in check.explanation
        assert "Основание." in check.explanation
    html = Path(result.report_html).read_text(encoding="utf-8")
    markdown = Path(result.report_md).read_text(encoding="utf-8")
    data = json.loads(Path(result.report_json).read_text(encoding="utf-8"))
    assert "Пояснение" in html
    assert "Пояснение" in markdown
    assert all(item.get("explanation") for item in data["checks"])


def test_web_api_returns_explanation(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    def fake_process(path, out, *_args, **_kwargs):
        check = CheckResult(
            check_id="sheet_frame",
            title="Формат листа и рамка",
            gost="ГОСТ 2.301-68",
            severity="error",
            message="Рамка листа не найдена.",
            details=["кандидатов: 0"],
            can_autofix=True,
        )
        return ProcessResult(input_path=str(path), kind="dxf", checks=[check], summary={"error": 1})

    monkeypatch.setattr("gost_checker.web.process_file", fake_process)
    client = TestClient(create_app())
    response = client.post(
        "/api/check",
        files={"file": ("bad.dxf", b"not-a-real-dxf", "application/dxf")},
        data={"fix": "false", "sheet_format": "A3"},
    )
    assert response.status_code == 200, response.text[:400]
    payload = response.json()
    frame = payload["checks"][0]
    assert frame["id"] == "sheet_frame"
    assert "Рамка листа не найдена." in frame["explanation"]
    assert "п. 5.2.7" in frame["citation"]
    assert frame["explanation_source"] == "template"
    assert "локальн" in payload["privacy"].lower()
    page = client.get("/")
    assert page.status_code == 200
    assert "explain-body" in page.text
    assert "explainBlock" in page.text
