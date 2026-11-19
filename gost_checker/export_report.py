"""Экспорт отчёта проверки в PDF (кириллица, DejaVu) и Excel."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .issues import build_issue_list

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)


def find_dejavu() -> Optional[Path]:
    for raw in FONT_CANDIDATES:
        path = Path(raw)
        if path.is_file():
            return path
    return None


def issues_of(result) -> list[dict]:
    extra = getattr(result, "extra", None) or {}
    if extra.get("issues"):
        return list(extra["issues"])
    return build_issue_list(getattr(result, "checks", []) or [])


def write_pdf(result, dest: Path) -> Optional[str]:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    font = find_dejavu()
    if font is None:
        return None
    try:
        from fpdf import FPDF
    except Exception:
        return None
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.add_font("DejaVu", "", str(font))
    pdf.set_font("DejaVu", size=14)
    width = pdf.epw

    def line(text: str, size: int = 10, height: float = 6) -> None:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("DejaVu", size=size)
        pdf.multi_cell(width, height, text or " ")

    line("Отчёт о проверке оформления чертежа", 14, 8)
    line(f"Файл: {result.input_path}")
    line(f"Тип: {result.kind}")
    rules = (result.extra or {}).get("ruleset_version") or ""
    if rules:
        line(f"Пакет правил: {rules}")
    summary = result.summary or {}
    line(
        "Итог: "
        f"ошибки {summary.get('error', 0)}, замечания {summary.get('warning', 0)}, "
        f"соответствует {summary.get('ok', 0)}."
    )
    line("Отчёт не заменяет нормоконтроль.", 9, 5)
    image = getattr(result, "render_issues", None)
    if image and Path(image).is_file():
        pdf.ln(2)
        pdf.set_x(pdf.l_margin)
        pdf.image(str(image), w=min(170, width))
        pdf.ln(2)
    line("Замечания", 12, 8)
    issues = issues_of(result)
    if not issues:
        line("Ошибок и замечаний с отдельными пунктами нет.", 9, 5)
    for issue in issues:
        where = "маркер на чертеже" if issue.get("bbox") else "без координат"
        line(
            f"{issue.get('n')}. [{issue.get('severity')}] {issue.get('title')}: {issue.get('text')} ({where})",
            9,
            5,
        )
        hint = issue.get("fix_hint") or ""
        if hint:
            line(f"Как исправить: {hint}", 9, 5)
    pdf.output(str(dest))
    return str(dest)


def _fill_issue_sheet(ws, issues: list[dict]) -> None:
    ws.append(["Номер", "Проверка", "ГОСТ", "Серьёзность", "Описание", "Как исправить"])
    for issue in issues:
        ws.append(
            [
                issue.get("n"),
                issue.get("title") or issue.get("check_id"),
                issue.get("gost") or "",
                issue.get("severity") or "",
                issue.get("text") or "",
                issue.get("fix_hint") or "",
            ]
        )


def write_xlsx(result, dest: Path) -> Optional[str]:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        from openpyxl import Workbook
    except Exception:
        return None
    book = Workbook()
    sheet = book.active
    sheet.title = "Ошибки"
    _fill_issue_sheet(sheet, issues_of(result))
    book.save(dest)
    return str(dest)


def write_batch_xlsx(rows: list[dict], dest: Path) -> Optional[str]:
    """rows: {filename, summary, issues}."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        from openpyxl import Workbook
    except Exception:
        return None
    book = Workbook()
    summary = book.active
    summary.title = "Сводка"
    summary.append(["Файл", "Ошибки", "Замечания", "Соответствует", "Исправлено"])
    all_issues = []
    for row in rows:
        counts = row.get("summary") or {}
        summary.append(
            [
                row.get("filename"),
                counts.get("error", 0),
                counts.get("warning", 0),
                counts.get("ok", 0),
                counts.get("fixed", 0),
            ]
        )
        for issue in row.get("issues") or []:
            item = dict(issue)
            item["text"] = f"{row.get('filename')}: {item.get('text') or ''}"
            all_issues.append(item)
    errors = book.create_sheet("Ошибки")
    _fill_issue_sheet(errors, all_issues)
    book.save(dest)
    return str(dest)
