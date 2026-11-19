"""Дополняет результат проверки перечнем замечаний, картинкой, PDF/Excel, историей и Telegram."""

from __future__ import annotations

from pathlib import Path

from .export_report import write_pdf, write_xlsx
from .highlight import render_dxf_highlight, render_raster_highlight
from .history import record_check
from .issues import build_issue_list
from .report import write_reports
from .ruleset import version_label
from .telegram_notify import notify_ready


def _render(result, out_dir: Path) -> None:
    issues = (result.extra or {}).get("issues") or []
    to_mm = float((result.extra or {}).get("units_to_mm") or 1.0)
    dest = out_dir / "issues.png"
    kind = result.kind
    if kind in {"dxf", "dwg"}:
        import ezdxf

        source = result.output_dxf or result.input_path
        path = Path(source)
        if path.suffix.lower() != ".dxf":
            converted = list(out_dir.glob("*.converted.dxf"))
            path = converted[0] if converted else Path(result.input_path)
        if path.suffix.lower() != ".dxf" or not path.is_file():
            return
        try:
            doc = ezdxf.readfile(str(path))
        except Exception:
            try:
                from ezdxf import recover

                doc, _auditor = recover.readfile(str(path))
            except Exception:
                return
        drawn = render_dxf_highlight(doc, issues, dest, to_mm)
        if drawn:
            result.render_issues = drawn
        return
    if kind == "raster":
        try:
            import cv2

            image = cv2.imread(str(result.input_path), cv2.IMREAD_COLOR)
        except Exception:
            image = None
        drawn = render_raster_highlight(image, issues, dest) if image is not None else None
        if drawn:
            result.render_issues = drawn
        return
    if kind == "pdf":
        try:
            from .compare import load_raster_or_pdf

            image = load_raster_or_pdf(Path(result.input_path))
        except Exception:
            image = None
        drawn = render_raster_highlight(image, issues, dest) if image is not None else None
        if drawn:
            result.render_issues = drawn


def publish_result(
    result,
    *,
    highlight: bool = False,
    export_pdf: bool = False,
    export_xlsx: bool = False,
    record_history: bool = False,
    telegram_chat_id: str = "",
    markup_notes: str = "",
):
    """Не роняет уже готовую проверку: сбой дополнений только попадает в примечания."""
    try:
        out_dir = Path(result.report_html).parent if result.report_html else None
        if out_dir is None:
            return result
        result.extra = dict(result.extra or {})
        try:
            source = Path(result.input_path)
            if source.suffix.lower() == ".dxf" and source.is_file():
                from .clearance import check_file as clearance_check
                from .dimconflict import check_file as dim_check
                from .electrical import check_file as gap_check
                from .report import summarize

                result.checks.extend([clearance_check(source), dim_check(source), gap_check(source)])
                result.summary = summarize(result.checks)
        except Exception as exc:
            result.notes.append(f"Дополнительные геометрические проверки пропущены: {exc}")
        result.extra["issues"] = build_issue_list(result.checks)
        result.extra["ruleset_version"] = version_label()
        try:
            from .markups import collect_for_result

            markups = collect_for_result(result, markup_notes)
            if markups:
                start = len(result.extra["issues"]) + 1
                for offset, item in enumerate(markups):
                    item["n"] = start + offset
                result.extra["markups"] = markups
                result.extra["issues"] = list(result.extra["issues"]) + markups
        except Exception as exc:
            result.notes.append(f"Пометки пользователя не прочитаны: {exc}")
        if highlight:
            try:
                _render(result, out_dir)
            except Exception as exc:
                result.notes.append(f"Подсветку сохранить не удалось: {exc}")
        if export_pdf:
            try:
                pdf_path = write_pdf(result, out_dir / "report.pdf")
                if pdf_path:
                    result.extra["report_pdf"] = pdf_path
            except Exception as exc:
                result.notes.append(f"PDF отчёта не записан: {exc}")
        if export_xlsx:
            try:
                xlsx_path = write_xlsx(result, out_dir / "report.xlsx")
                if xlsx_path:
                    result.extra["report_xlsx"] = xlsx_path
            except Exception as exc:
                result.notes.append(f"Excel отчёта не записан: {exc}")
        write_reports(result, out_dir)
        if record_history:
            try:
                record_check(result, Path(result.input_path) if result.input_path else None)
            except Exception as exc:
                result.notes.append(f"История не записана: {exc}")
        if telegram_chat_id or highlight or export_pdf:
            try:
                report = result.extra.get("report_pdf") or result.report_html
                notify_ready(
                    filename=result.input_path,
                    summary=result.summary,
                    report_path=report,
                    chat_id=telegram_chat_id,
                )
            except Exception as exc:
                result.notes.append(f"Уведомление Telegram не отправлено: {exc}")
    except Exception as exc:
        result.notes.append(f"Дополнение отчёта не выполнено: {exc}")
    return result
