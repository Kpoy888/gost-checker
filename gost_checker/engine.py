from __future__ import annotations

from pathlib import Path
from typing import Optional

import ezdxf
from ezdxf import recover

from .checks import ALL_CHECKS, CheckContext
from .constants import CAD_EXTS, IFC_EXTS, PDF_EXTS, RASTER_EXTS, RENGA_EXTS, REVIT_EXTS
from .convert import converter_status, dwg_to_dxf, dxf_to_dwg
from .ifc_info import inspect_ifc
from .models import CheckResult, ProcessResult
from .raster import process_image, process_pdf
from .render import render_dxf_to_png
from .report import summarize, write_reports
from .units import doc_to_mm


COMMON_LIMITS = [
    "Программа не заменяет нормоконтроль и не подтверждает полное соответствие СПДС/ЕСКД.",
    "Не проверяются содержание чертежа, полнота видов, спецификации, штриховки, выноски, узлы и соответствие конструктивным решениям.",
    "Автоисправления консервативны: рамка и штамп добавляются, если их нет; содержимое, наехавшее на штамп, сдвигается в рабочее поле; толщины правятся у слоёв и у объектов с явной толщиной вне ряда.",
    "Перенос объектов на слои СПДС выполняется только с флагом --assign-layers (по умолчанию выключен).",
    "Сетка вставляемого штампа формы 3 — практическая реконструкция граф 185×55 мм, а не сертифицированная копия официального рисунка.",
]


MAX_INPUT_BYTES = 80 * 1024 * 1024


def _error_result(path: Path, out_dir: Path, message: str, kind: str = "error") -> ProcessResult:
    check = CheckResult(
        check_id="engine",
        title="Обработка файла",
        gost="—",
        severity="error",
        message=message,
        details=[],
        can_autofix=False,
        findings_count=1,
    )
    result = ProcessResult(
        input_path=str(path),
        kind=kind,
        checks=[check],
        notes=[message],
        limitations=list(COMMON_LIMITS),
    )
    result.summary = summarize(result.checks)
    return write_reports(result, out_dir)


def _write_overlay(result: ProcessResult, out_dir: Path, summary, background=None, page_size=None) -> None:
    if not summary:
        return
    try:
        from .line_classify import write_overlay_png

        path = write_overlay_png(
            summary,
            out_dir / "lines_overlay.png",
            background=background,
            page_size_mm=page_size,
        )
        if path:
            result.render_overlay = path
            result.notes.append("Цветная схема линий сохранена: lines_overlay.png")
    except Exception as exc:
        result.notes.append(f"Схему линий сохранить не удалось: {exc}")


def _maybe_collect() -> None:
    try:
        from .runtime_limits import light_memory

        if light_memory():
            import gc

            gc.collect()
    except Exception:
        pass


def _native_cad_result(path: Path, kind: str) -> ProcessResult:
    message = (
        f"Файлы {kind} напрямую не читаются. Экспортируйте чертёж в DXF или DWG "
        "(в Renga и Revit это делается командой экспорта) — после этого программа сможет "
        "проверить рамку, штамп, линии, шрифты и размеры. IFC принимается только для краткой справки."
    )
    check = CheckResult(
        check_id="native_format",
        title="Исходный формат CAD",
        gost="—",
        severity="info",
        message=message,
        details=[
            "Renga: экспорт в DXF/DWG или IFC.",
            "Revit: экспорт в DWG/DXF (при необходимости через набор видов) или IFC.",
        ],
        can_autofix=False,
    )
    result = ProcessResult(
        input_path=str(path),
        kind=kind,
        checks=[check],
        notes=[message],
        limitations=list(COMMON_LIMITS),
    )
    result.summary = summarize(result.checks)
    return result


def _open_dxf(path: Path):
    try:
        return ezdxf.readfile(str(path))
    except Exception:
        try:
            doc, _auditor = recover.readfile(str(path))
            return doc
        except Exception as exc:
            raise RuntimeError(f"Файл DXF повреждён или не читается: {exc}") from exc


def process_file(
    input_path: Path | str,
    out_dir: Path | str,
    fix: bool = False,
    title_meta: Optional[dict] = None,
    assumed_format: str = "A3",
    render: bool = False,
    assign_layers: bool = False,
) -> ProcessResult:
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        input_path = Path(input_path).expanduser().resolve()
    except Exception as exc:
        return _error_result(Path(str(input_path)), out_dir, f"Некорректный путь к файлу: {exc}")
    if not input_path.exists():
        return _error_result(input_path, out_dir, f"Файл не найден: {input_path}")
    try:
        size = input_path.stat().st_size
    except OSError as exc:
        return _error_result(input_path, out_dir, f"Не удалось прочитать файл: {exc}")
    if size == 0:
        return _error_result(input_path, out_dir, "Файл пустой (0 байт).")
    if size > MAX_INPUT_BYTES:
        return _error_result(
            input_path,
            out_dir,
            f"Файл слишком большой ({size / 1e6:.1f} МБ). Для этой программы предел — 80 МБ.",
        )

    suffix = input_path.suffix.lower()
    try:
        return _process_file_inner(
            input_path, out_dir, suffix, fix, title_meta, assumed_format, render, assign_layers
        )
    except Exception as exc:
        return _error_result(input_path, out_dir, f"Не удалось обработать файл: {exc}")


def _process_file_inner(
    input_path: Path,
    out_dir: Path,
    suffix: str,
    fix: bool,
    title_meta,
    assumed_format: str,
    render: bool,
    assign_layers: bool,
) -> ProcessResult:

    if suffix in REVIT_EXTS:
        result = _native_cad_result(input_path, "Revit")
        return write_reports(result, out_dir)
    if suffix in RENGA_EXTS:
        result = _native_cad_result(input_path, "Renga")
        return write_reports(result, out_dir)

    if suffix in IFC_EXTS:
        info = inspect_ifc(input_path)
        notes = [
            "IFC не является чертежом СПДС. Оформление рамки и штампа по ГОСТ здесь не проверяется.",
            "Для проверки оформления экспортируйте соответствующие виды в DXF/DWG.",
        ]
        details = []
        if info is None:
            notes.append("Библиотека ifcopenshell не установлена, поэтому даже краткие сведения из IFC не прочитаны.")
            details.append("Необязательно: pip install ifcopenshell")
        else:
            details.append(f"Схема: {info.get('schema')}")
            details.append(f"Проект: {info.get('project')}")
            details.append(f"Зданий: {info.get('buildings')}, этажей: {info.get('storeys')}")
        check = CheckResult(
            check_id="ifc_info",
            title="Сведения из IFC",
            gost="—",
            severity="info",
            message="IFC принят только как справка о модели, не как чертёж для нормоконтроля оформления.",
            details=details,
            can_autofix=False,
        )
        result = ProcessResult(
            input_path=str(input_path),
            kind="ifc",
            checks=[check],
            notes=notes,
            limitations=list(COMMON_LIMITS),
            extra=info or {},
        )
        result.summary = summarize(result.checks)
        return write_reports(result, out_dir)

    if suffix in PDF_EXTS:
        checks, notes, extra = process_pdf(input_path)
        preview = extra.pop("preview_image", None)
        lines_full = extra.pop("lines_full", None)
        result = ProcessResult(
            input_path=str(input_path),
            kind="pdf",
            checks=checks,
            notes=notes + ["Растровый/PDF-режим не изменяет исходный файл."],
            limitations=list(COMMON_LIMITS)
            + [
                "PDF и сканы не исправляются: нельзя корректно перечертить рамку и штамп в пиксельном файле.",
                "OCR (Tesseract, русский язык) ошибается на тонкой сетке штампа, печати и фотографиях с ракурсом.",
            ],
            extra=extra,
        )
        result.summary = summarize(result.checks)
        _write_overlay(result, out_dir, lines_full, preview, extra.get("page_size_mm"))
        preview = None
        lines_full = None
        _maybe_collect()
        return write_reports(result, out_dir)

    if suffix in RASTER_EXTS:
        checks, notes, extra = process_image(input_path)
        preview = extra.pop("preview_image", None)
        lines_full = extra.pop("lines_full", None)
        result = ProcessResult(
            input_path=str(input_path),
            kind="raster",
            checks=checks,
            notes=notes + ["Растровый режим не изменяет исходный файл."],
            limitations=list(COMMON_LIMITS)
            + [
                "Скан и фото не исправляются.",
                "Без известного DPI формат и поля оцениваются по контуру листа и таблице ГОСТ 2.301.",
            ],
            extra=extra,
        )
        result.summary = summarize(result.checks)
        _write_overlay(result, out_dir, lines_full, preview, extra.get("page_size_mm"))
        preview = None
        lines_full = None
        _maybe_collect()
        return write_reports(result, out_dir)

    if suffix not in CAD_EXTS:
        result = ProcessResult(
            input_path=str(input_path),
            kind="unknown",
            checks=[],
            notes=[f"Неподдерживаемое расширение {suffix or '(без расширения)'}."],
            limitations=list(COMMON_LIMITS),
        )
        result.summary = summarize(result.checks)
        return write_reports(result, out_dir)

    work_dxf = input_path
    notes = []
    status = converter_status()
    if suffix == ".dwg":
        converted, message = dwg_to_dxf(input_path, out_dir)
        notes.append(message)
        if converted is None:
            result = ProcessResult(
                input_path=str(input_path),
                kind="dwg",
                checks=[
                    CheckResult(
                        check_id="dwg_convert",
                        title="Чтение DWG",
                        gost="—",
                        severity="error",
                        message=message,
                        details=[
                            f"LibreDWG dwg2dxf: {status['dwg2dxf'] or 'не найден'}",
                            f"LibreDWG dxf2dwg: {status['dxf2dwg'] or 'не найден'}",
                            f"ODA File Converter: {status['oda'] or 'не найден'}",
                        ],
                        can_autofix=False,
                        findings_count=1,
                    )
                ],
                notes=notes,
                limitations=list(COMMON_LIMITS),
            )
            result.summary = summarize(result.checks)
            return write_reports(result, out_dir)
        work_dxf = converted

    doc = _open_dxf(work_dxf)
    to_mm = doc_to_mm(doc)
    notes.append(
        f"Единицы чертежа: $INSUNITS={doc.header.get('$INSUNITS', '?')}, "
        f"1 ед. = {to_mm:g} мм (при коде 0 принимаются миллиметры; дюймы и метры пересчитываются)."
    )
    try:
        model_count = sum(1 for _entity in doc.modelspace())
        if model_count == 0:
            notes.append("Пространство модели не содержит объектов — проверки выполнены по пустому листу.")
    except Exception:
        notes.append("Не удалось пересчитать объекты модели (файл может быть неполным).")

    render_before = None
    if render:
        render_before = render_dxf_to_png(doc, out_dir / "before.png")

    ctx = CheckContext(
        doc=doc,
        path=work_dxf,
        to_mm=to_mm,
        fix=fix,
        title_meta=title_meta or {},
        assumed_format=assumed_format or "A3",
        assign_layers=assign_layers,
    )
    checks = [check.run(ctx) for check in ALL_CHECKS]

    output_dxf = None
    output_dwg = None
    if fix:
        output_dxf_path = out_dir / f"{input_path.stem}_fixed.dxf"
        doc.saveas(str(output_dxf_path))
        output_dxf = str(output_dxf_path)
        notes.append(f"Исправленный DXF записан: {output_dxf_path.name}")
        dwg_path, dwg_msg = dxf_to_dwg(output_dxf_path, out_dir)
        notes.append(dwg_msg)
        if dwg_path:
            output_dwg = str(dwg_path)
    else:
        notes.append("Режим без --fix: файл не изменялся, только отчёт.")

    render_after = None
    if render and fix:
        render_after = render_dxf_to_png(doc, out_dir / "after.png")

    extra = {
        "converters": status,
        "units_to_mm": to_mm,
        "margins": ctx.extras.get("margins"),
        "margins_table": None,
        "lines": ctx.extras.get("lines"),
        "stamp_fields": ctx.extras.get("stamp_fields"),
    }
    if extra["margins"]:
        from .margin_report import margins_as_table_rows

        extra["margins_table"] = margins_as_table_rows(extra["margins"])
    result = ProcessResult(
        input_path=str(input_path),
        kind="dxf" if suffix == ".dxf" else "dwg",
        output_dxf=output_dxf,
        output_dwg=output_dwg,
        checks=checks,
        notes=notes,
        limitations=list(COMMON_LIMITS)
        + [
            "DWG читается через LibreDWG (vendor/libredwg или scripts/install_dwg.sh) либо ODA File Converter.",
            "Шрифт «GOST type A» должен быть установлен в вашей CAD-программе, иначе она подставит замену.",
        ],
        extra=extra,
        render_before=render_before,
        render_after=render_after,
    )
    line_items = ctx.extras.get("line_items") or []
    if line_items:
        line_summary = dict(ctx.extras.get("lines") or {})
        line_summary["items"] = line_items
        page = None
        if ctx.frame and ctx.frame.outer:
            page = (ctx.frame.outer.width * to_mm, ctx.frame.outer.height * to_mm)
        _write_overlay(result, out_dir, line_summary, page_size=page)
    result.summary = summarize(result.checks)
    return write_reports(result, out_dir)
