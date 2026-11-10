from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import CheckResult, ProcessResult

DISCLAIMER = (
    "Этот отчёт не удостоверяет полное соответствие чертежа СПДС или ЕСКД. "
    "Проверены только перечисленные ниже правила, и только в том объёме, "
    "который реализован в этой бесплатной программе. Нормоконтроль выполняет человек."
)


def _severity_ru(value: str) -> str:
    return {
        "ok": "соответствует",
        "info": "сведение",
        "warning": "замечание",
        "error": "ошибка",
    }.get(value, value)



def _rel_name(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).name


def _margins_rows(result: ProcessResult) -> list[dict]:
    extra = result.extra or {}
    table = extra.get("margins_table")
    if table:
        return table
    margins = extra.get("margins")
    if not margins:
        return []
    from .margin_report import margins_as_table_rows

    return margins_as_table_rows(margins)


def _lines_info(result: ProcessResult) -> dict:
    extra = result.extra or {}
    return extra.get("lines") or {}


def _markdown_tables(result: ProcessResult) -> list[str]:
    lines = []
    rows = _margins_rows(result)
    if rows:
        lines += ["", "## Поля рамки (норма 20 / 5 / 5 / 5 мм, допуск ±0,5 мм)", "", "| Сторона | Факт, мм | Норма, мм | Сходимость |", "| --- | ---: | ---: | --- |"]
        for row in rows:
            mark = "да" if row.get("ok") else "нет"
            lines.append(f"| {row.get('side')} | {float(row.get('value', 0)):.2f} | {float(row.get('required', 0)):.2f} | {mark} |")
    info = _lines_info(result)
    kinds = info.get("kinds") or []
    if kinds:
        lines += [
            "",
            "## Линии (ГОСТ 2.303-68)",
            "",
            f"Принятая толщина основной линии S ≈ {info.get('s_main_mm', '—')} мм, объектов: {info.get('total', 0)}.",
            "",
            "| Тип | Количество | Длина, мм |",
            "| --- | ---: | ---: |",
        ]
        for row in kinds:
            lines.append(f"| {row.get('title') or row.get('kind')} | {row.get('count', 0)} | {row.get('length_mm', 0)} |")
    images = []
    if result.render_before:
        images.append(f"- До исправления: `{_rel_name(result.render_before)}`")
    if result.render_after:
        images.append(f"- После исправления: `{_rel_name(result.render_after)}`")
    if result.render_overlay:
        images.append(f"- Схема линий: `{_rel_name(result.render_overlay)}`")
    if images:
        lines += ["", "## Изображения", ""] + images
    return lines


def _html_tables(result: ProcessResult) -> str:
    parts = []
    rows = _margins_rows(result)
    if rows:
        body = "".join(
            f"<tr class='{'ok' if row.get('ok') else 'fail'}'><td>{html.escape(str(row.get('side')))}</td>"
            f"<td>{float(row.get('value', 0)):.2f}</td><td>{float(row.get('required', 0)):.2f}</td>"
            f"<td>{'да' if row.get('ok') else 'нет'}</td></tr>"
            for row in rows
        )
        parts.append(
            "<h2>Поля рамки</h2><p>Норма: 20 мм слева и 5 мм с остальных сторон, допуск отчёта ±0,5 мм.</p>"
            "<table class='grid'><thead><tr><th>Сторона</th><th>Факт, мм</th><th>Норма, мм</th>"
            "<th>Сходимость</th></tr></thead><tbody>"
            + body
            + "</tbody></table>"
        )
    info = _lines_info(result)
    kinds = info.get("kinds") or []
    if kinds:
        body = "".join(
            "<tr><td><span class='sw' style='background:"
            + html.escape(str(row.get("color") or "#444"))
            + "'></span> "
            + html.escape(str(row.get("title") or row.get("kind")))
            + f"</td><td>{row.get('count', 0)}</td><td>{row.get('length_mm', 0)}</td></tr>"
            for row in kinds
        )
        parts.append(
            f"<h2>Линии (ГОСТ 2.303)</h2><p>S основной ≈ {html.escape(str(info.get('s_main_mm', '—')))} мм, "
            f"объектов: {info.get('total', 0)}.</p>"
            "<table class='grid'><thead><tr><th>Тип</th><th>Количество</th><th>Длина, мм</th></tr></thead><tbody>"
            + body
            + "</tbody></table>"
        )
    figures = []
    for caption, path in (
        ("До исправления", result.render_before),
        ("После исправления", result.render_after),
        ("Схема линий по типу", result.render_overlay),
    ):
        if path and Path(path).exists():
            figures.append(
                f"<figure><img src='{html.escape(_rel_name(path))}' alt='{html.escape(caption)}'>"
                f"<figcaption>{html.escape(caption)}</figcaption></figure>"
            )
    if figures:
        parts.append("<h2>Изображения</h2><div class='gallery'>" + "".join(figures) + "</div>")
    return "".join(parts)


def summarize(checks: Iterable[CheckResult]) -> dict[str, int]:
    summary = {"ok": 0, "info": 0, "warning": 0, "error": 0, "fixed": 0}
    for check in checks:
        summary[check.severity] = summary.get(check.severity, 0) + 1
        if check.auto_fixed:
            summary["fixed"] += 1
    return summary


def render_markdown(result: ProcessResult) -> str:
    lines = [
        "# Отчёт о проверке оформления чертежа",
        "",
        f"- Файл: `{result.input_path}`",
        f"- Тип проверки: {result.kind}",
        f"- Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        "",
        f"> {DISCLAIMER}",
        "",
        "## Кратко",
        "",
        (
            f"соответствует: {result.summary.get('ok', 0)}, "
            f"сведения: {result.summary.get('info', 0)}, "
            f"замечания: {result.summary.get('warning', 0)}, "
            f"ошибки: {result.summary.get('error', 0)}, "
            f"автоисправлений: {result.summary.get('fixed', 0)}"
        ),
        "",
    ]
    if result.output_dxf:
        lines += [f"- Исправленный DXF: `{result.output_dxf}`"]
    if result.output_dwg:
        lines += [f"- Исправленный DWG: `{result.output_dwg}`"]
    if result.notes:
        lines += ["", "## Примечания", ""]
        lines.extend(f"- {note}" for note in result.notes)
    if result.limitations:
        lines += ["", "## Ограничения", ""]
        lines.extend(f"- {item}" for item in result.limitations)
    lines.extend(_markdown_tables(result))
    lines += ["", "## Результаты проверок", ""]
    for check in result.checks:
        fixed = " да" if check.auto_fixed else " нет"
        lines += [
            f"### {check.title} (`{check.check_id}`)",
            "",
            f"- Норма: {check.gost}",
            f"- Оценка: {_severity_ru(check.severity)}",
            f"- Автоисправление: {fixed} (возможна: {'да' if check.can_autofix else 'нет'})",
            f"- {check.message}",
            "",
        ]
        for detail in check.details:
            lines.append(f"  - {detail}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_html(result: ProcessResult) -> str:
    cards = []
    for check in result.checks:
        details = "".join(f"<li>{html.escape(d)}</li>" for d in check.details)
        cards.append(
            f"""
            <article class="card {html.escape(check.severity)}">
              <header>
                <h3>{html.escape(check.title)}</h3>
                <span class="badge">{html.escape(_severity_ru(check.severity))}</span>
              </header>
              <p class="gost">{html.escape(check.gost)}</p>
              <p>{html.escape(check.message)}</p>
              <p class="meta">Код: {html.escape(check.check_id)} ·
                 автоисправление: {"выполнено" if check.auto_fixed else "не применялось"} ·
                 может исправлять: {"да" if check.can_autofix else "нет"}</p>
              <ul>{details}</ul>
            </article>
            """
        )
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in result.notes)
    limits = "".join(f"<li>{html.escape(n)}</li>" for n in result.limitations)
    files = []
    if result.output_dxf:
        files.append(f"<li>Исправленный DXF: {html.escape(result.output_dxf)}</li>")
    if result.output_dwg:
        files.append(f"<li>Исправленный DWG: {html.escape(result.output_dwg)}</li>")
    s = result.summary
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Отчёт о проверке оформления чертежа</title>
  <style>
    :root {{ --ink:#1b1f16; --paper:#f6f1e6; --accent:#2c4a3a; --err:#8b2e2e; --warn:#8a5a12; --ok:#2a5a34; }}
    body {{ margin:0; font-family:"PT Serif","Noto Serif","DejaVu Serif",Georgia,serif; background:var(--paper); color:var(--ink); }}
    main {{ max-width:960px; margin:0 auto; padding:32px 20px 64px; }}
    h1,h2,h3 {{ font-family:"PT Sans","Noto Sans","DejaVu Sans",sans-serif; }}
    header.hero {{ border:2px solid var(--ink); padding:20px 24px; margin-bottom:24px; }}
    .disc {{ background:#fff7dc; border-left:4px solid var(--warn); padding:12px 16px; }}
    .sum span {{ display:inline-block; margin:0 12px 8px 0; }}
    .card {{ background:white; border:1px solid #d7d0c3; padding:16px 18px; margin:16px 0; }}
    .card.error {{ border-left:6px solid var(--err); }}
    .card.warning {{ border-left:6px solid var(--warn); }}
    .card.ok {{ border-left:6px solid var(--ok); }}
    .card.info {{ border-left:6px solid #3b5878; }}
    table.grid {{ border-collapse:collapse; width:100%; background:white; margin:12px 0 20px; }}
    table.grid th, table.grid td {{ border:1px solid #d7d0c3; padding:8px 10px; text-align:left; }}
    table.grid tr.ok td:last-child {{ color:var(--ok); }}
    table.grid tr.fail td:last-child {{ color:var(--err); font-weight:700; }}
    .sw {{ display:inline-block; width:12px; height:12px; margin-right:6px; vertical-align:middle; border:1px solid #999; }}
    .gallery {{ display:grid; gap:16px; }}
    .gallery img {{ max-width:100%; border:1px solid #d7d0c3; background:white; }}
    figcaption {{ color:#5c564a; font-size:14px; }}
    .badge {{ float:right; font-size:13px; text-transform:uppercase; letter-spacing:.04em; }}
    .gost {{ color:#4a4336; font-size:14px; }}
    .meta {{ font-size:13px; color:#5c564a; }}
    ul {{ line-height:1.45; }}
  </style>
</head>
<body>
<main>
  <header class="hero">
    <h1>Отчёт о проверке оформления</h1>
    <p>Файл: {html.escape(result.input_path)}<br>
       Тип проверки: {html.escape(result.kind)}<br>
       Дата: {html.escape(datetime.now().strftime('%d.%m.%Y %H:%M'))}</p>
    <p class="disc">{html.escape(DISCLAIMER)}</p>
    <p class="sum">
      <span>соответствует: {s.get('ok', 0)}</span>
      <span>сведения: {s.get('info', 0)}</span>
      <span>замечания: {s.get('warning', 0)}</span>
      <span>ошибки: {s.get('error', 0)}</span>
      <span>автоисправлений: {s.get('fixed', 0)}</span>
    </p>
  </header>
  {"<h2>Файлы</h2><ul>" + "".join(files) + "</ul>" if files else ""}
  {"<h2>Примечания</h2><ul>" + notes + "</ul>" if notes else ""}
  {"<h2>Ограничения</h2><ul>" + limits + "</ul>" if limits else ""}
  {_html_tables(result)}
  <h2>Результаты проверок</h2>
  {''.join(cards)}
</main>
</body>
</html>
"""


def write_reports(result: ProcessResult, out_dir: Path) -> ProcessResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "report.md"
    html_path = out_dir / "report.html"
    json_path = out_dir / "report.json"
    md_path.write_text(render_markdown(result), encoding="utf-8")
    html_path.write_text(render_html(result), encoding="utf-8")
    json_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    result.report_md = str(md_path)
    result.report_html = str(html_path)
    result.report_json = str(json_path)
    return result
