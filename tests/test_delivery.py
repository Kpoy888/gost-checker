"""Пакет, подсветка, Telegram, сравнение, история, PDF и Excel."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import ezdxf
import pytest
from fastapi.testclient import TestClient


def _line(path: Path, extra: bool = False) -> None:
    doc = ezdxf.new("R2010")
    doc.modelspace().add_line((0, 0), (100, 0))
    if extra:
        doc.modelspace().add_line((0, 0), (0, 40))
        doc.modelspace().add_text("Другой", dxfattribs={"insert": (10, 10), "height": 5})
    else:
        doc.modelspace().add_text("Текст", dxfattribs={"insert": (10, 10), "height": 5})
    doc.saveas(str(path))


def test_issue_list_keeps_items_without_coordinates():
    from gost_checker.issues import build_issue_list
    from gost_checker.models import CheckResult

    checks = [
        CheckResult("sheet_frame", "Рамка", "ГОСТ 2.301", "error", "Рамки нет", findings_count=1),
        CheckResult(
            "fonts",
            "Шрифт",
            "ГОСТ 2.304",
            "warning",
            "Высота",
            extra={"markers": [{"text": "TEXT 11 мм", "bbox": [1, 2, 3, 4], "space": "model"}]},
        ),
    ]
    issues = build_issue_list(checks)
    assert [item["n"] for item in issues] == [1, 2]
    assert issues[0]["bbox"] is None
    assert issues[1]["bbox"] == [1, 2, 3, 4]


def test_highlight_png_and_report(samples_dir, tmp_path):
    from gost_checker.engine import process_file

    result = process_file(samples_dir / "bad.dxf", tmp_path / "out", fix=False, highlight=True)
    assert result.render_issues and Path(result.render_issues).is_file()
    html = Path(result.report_html).read_text(encoding="utf-8")
    assert "Перечень замечаний" in html
    assert "issues.png" in html
    assert any(item.get("bbox") for item in result.extra["issues"])
    assert any(item.get("bbox") is None for item in result.extra["issues"])


def test_telegram_silent_without_token(monkeypatch, tmp_path):
    from gost_checker import telegram_notify as tn

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("GOST_BOT_TOKEN", raising=False)
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise RuntimeError("network")

    monkeypatch.setattr(tn, "_post", boom)
    assert tn.notify_ready(filename="a.dxf", summary={"error": 1}, chat_id="1") is False
    assert called["n"] == 0


def test_telegram_network_error_does_not_raise(monkeypatch, tmp_path):
    from gost_checker import telegram_notify as tn

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(tn, "_post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("timeout")))
    report = tmp_path / "report.html"
    report.write_text("<p>отчёт</p>", encoding="utf-8")
    assert tn.notify_ready(filename="a.dxf", summary={"error": 2}, report_path=str(report), chat_id="42") is False


def test_telegram_sends_message(monkeypatch, tmp_path):
    from gost_checker import telegram_notify as tn

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    seen = []

    def fake_post(method, data, files=None, timeout=12.0):
        seen.append((method, data, files is not None))
        return None

    monkeypatch.setattr(tn, "_post", fake_post)
    report = tmp_path / "report.html"
    report.write_text("ok", encoding="utf-8")
    assert tn.notify_ready(filename="a.dxf", summary={"error": 1, "warning": 0, "ok": 2, "fixed": 0}, report_path=str(report), chat_id="77")
    assert seen[0][0] == "sendMessage"
    assert seen[1][0] == "sendDocument"
    assert "test-token" not in str(seen)


def test_entity_diff_and_checks(tmp_path):
    from gost_checker.compare import diff_checks, diff_entities
    from gost_checker.models import CheckResult

    old = tmp_path / "old.dxf"
    new = tmp_path / "new.dxf"
    _line(old, extra=False)
    _line(new, extra=True)
    diff = diff_entities(ezdxf.readfile(str(old)), ezdxf.readfile(str(new)))
    assert diff["added_count"] >= 1
    assert diff["removed_count"] == 0
    checks = diff_checks(
        [CheckResult("fonts", "Шрифт", "2.304", "warning", "было")],
        [CheckResult("fonts", "Шрифт", "2.304", "ok", "стало")],
    )
    assert checks["disappeared"]
    image = tmp_path / "diff.png"
    from gost_checker.compare import render_entity_diff_png

    assert render_entity_diff_png(ezdxf.readfile(str(old)), ezdxf.readfile(str(new)), diff, image)
    assert image.is_file()


def test_history_survives_and_deletes(tmp_path, monkeypatch, samples_dir):
    monkeypatch.setenv("GOST_HISTORY_DIR", str(tmp_path / "hist"))
    from gost_checker.engine import process_file
    from gost_checker.history import delete_check, get_check, list_checks

    result = process_file(samples_dir / "good.dxf", tmp_path / "out", record_history=True)
    rows = list_checks(filename="good.dxf")
    assert rows
    row_id = rows[0]["id"]
    stored = get_check(row_id)
    assert stored["report_relpath"]
    assert (tmp_path / "hist" / stored["report_relpath"]).is_file()
    shutil_job = tmp_path / "out"
    import shutil

    shutil.rmtree(shutil_job)
    assert get_check(row_id)["errors"] == result.summary.get("error")
    assert delete_check(row_id) is True
    assert get_check(row_id) is None


def test_pdf_and_xlsx(samples_dir, tmp_path):
    import fitz
    from openpyxl import load_workbook

    from gost_checker.engine import process_file

    result = process_file(
        samples_dir / "bad.dxf",
        tmp_path / "out",
        fix=False,
        export_pdf=True,
        export_xlsx=True,
    )
    pdf = Path(result.extra["report_pdf"])
    xlsx = Path(result.extra["report_xlsx"])
    assert pdf.is_file() and xlsx.is_file()
    text = "\n".join(page.get_text() for page in fitz.open(pdf))
    assert "Замечания" in text or "замечан" in text.lower()
    book = load_workbook(xlsx)
    header = [cell.value for cell in next(book["Ошибки"].iter_rows(max_row=1))]
    assert header[:6] == ["Номер", "Проверка", "ГОСТ", "Серьёзность", "Описание", "Как исправить"]


def test_batch_endpoint_and_cli_folder(samples_dir, tmp_path, monkeypatch):
    from gost_checker.cli import main
    from gost_checker.web import create_app

    monkeypatch.setenv("GOST_HISTORY_DIR", str(tmp_path / "hist"))
    client = TestClient(create_app())
    data_a = (samples_dir / "good.dxf").read_bytes()
    data_b = (samples_dir / "bad.dxf").read_bytes()
    response = client.post(
        "/api/check-batch",
        files=[
            ("files", ("../../etc/passwd.dxf", data_a, "application/dxf")),
            ("files", ("bad.dxf", data_b, "application/dxf")),
        ],
        data={"fix": "false"},
    )
    assert response.status_code == 200, response.text[:500]
    payload = response.json()
    assert payload["totals"]["files"] == 2
    assert payload["sheets"][0]["filename"] == "passwd.dxf"
    zip_bytes = client.get(payload["zip_url"]).content
    names = zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist()
    assert any(name.endswith("report.html") for name in names)
    assert all(".." not in name.split("/") for name in names)

    folder = tmp_path / "sheets"
    folder.mkdir()
    (folder / "one.dxf").write_bytes(data_a)
    code = main([str(folder), "--out", str(tmp_path / "cli-batch")])
    assert code in {0, 2}
    assert (tmp_path / "cli-batch" / "one" / "report.html").exists()


def test_batch_rejects_too_many(monkeypatch):
    from gost_checker import web_extra
    from gost_checker.web import create_app

    monkeypatch.setattr(web_extra, "MAX_BATCH_FILES", 1)
    client = TestClient(create_app())
    response = client.post(
        "/api/check-batch",
        files=[
            ("files", ("a.dxf", b"0\nEOF\n", "application/dxf")),
            ("files", ("b.dxf", b"0\nEOF\n", "application/dxf")),
        ],
    )
    assert response.status_code == 400


def test_compare_endpoint(tmp_path):
    from gost_checker.web import create_app

    old = tmp_path / "old.dxf"
    new = tmp_path / "new.dxf"
    _line(old, False)
    _line(new, True)
    client = TestClient(create_app())
    response = client.post(
        "/api/compare",
        files={
            "file_a": ("old.dxf", old.read_bytes(), "application/dxf"),
            "file_b": ("new.dxf", new.read_bytes(), "application/dxf"),
        },
    )
    assert response.status_code == 200, response.text[:400]
    body = response.json()
    assert body["entities"]["added"] >= 1
    assert client.get(body["diff_url"]).status_code == 200


def test_history_api_delete(tmp_path, monkeypatch, samples_dir):
    monkeypatch.setenv("GOST_HISTORY_DIR", str(tmp_path / "hist"))
    from gost_checker.web import create_app

    client = TestClient(create_app())
    response = client.post(
        "/api/check",
        files={"file": ("good.dxf", (samples_dir / "good.dxf").read_bytes(), "application/dxf")},
        data={"fix": "false", "preview": "false"},
    )
    assert response.status_code == 200
    listed = client.get("/api/history", params={"filename": "good.dxf"})
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert items
    row_id = items[0]["id"]
    assert client.get(f"/api/history/{row_id}/file/report.html").status_code == 200
    assert client.delete(f"/api/history/{row_id}").status_code == 200
    assert client.get(f"/api/history/{row_id}").status_code == 404
    assert client.get(f"/api/history/{row_id}/file/../report.html").status_code in {400, 404}
