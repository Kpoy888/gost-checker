"""Нумерованный перечень замечаний и подсказки, как исправить их вручную."""

from __future__ import annotations

from typing import Any, Optional

FIX_HINTS = {
    "sheet_frame": (
        "Рамка: в nanoCAD/AutoCAD начертите внешний контур формата и внутреннюю рамку "
        "с полями 20 мм слева и 5 мм с остальных сторон (слой SPDS_FRAME). "
        "В Renga и Revit рамка обычно сидит в шаблоне листа — проверьте формат и поля перед экспортом в DXF."
    ),
    "title_block": (
        "Основная надпись формы 3 (185×55 мм) — в правом нижнем углу. "
        "Заполните обозначение, наименование, стадию, лист, листов и подписи. "
        "В nanoCAD/AutoCAD удобен блок штампа; в Renga/Revit — семейство основной надписи."
    ),
    "layout_overlap": (
        "Сдвиньте текст и геометрию из зоны штампа в рабочее поле над основной надписью. "
        "В CAD: выделите объекты и перенесите командой перемещения, не задевая рамку."
    ),
    "linetypes": (
        "Толщины по ГОСТ 2.303: основная сплошная S = 0,5…1,4 мм, тонкая примерно S/3…S/2. "
        "В диспетчере слоёв nanoCAD/AutoCAD выставьте вес и тип линии; в Renga/Revit — свойства категории или слоя экспорта."
    ),
    "lines": (
        "Проверьте начертание: штрих 2–8 мм и промежуток 1–2 мм у штриховой, "
        "длинный штрих 5–30 мм у штрихпунктирной. Тип линии задаётся в слое или у объекта."
    ),
    "fonts": (
        "Назначьте стиль со шрифтом ГОСТ type A/Б или ISOCPEUR и высоту из ряда "
        "1,8; 2,5; 3,5; 5; 7; 10; 14; 20; 28; 40 мм. В CAD: STYLE / текстовый стиль."
    ),
    "scales": (
        "Оставьте масштаб из ряда ГОСТ 2.302 (1:1, 1:2, 1:2.5, 1:5, 1:10, 1:20, 1:25, 1:50, 1:100, 1:200 …). "
        "Надпись на листе и масштаб видового экрана должны совпадать."
    ),
    "dimensions": (
        "Для строительных чертежей линейный размер ограничивают засечками 2–4 мм под 45°, "
        "радиус, диаметр и угол — стрелками. Высота текста не ниже 2,5 мм. "
        "В nanoCAD/AutoCAD поправьте размерный стиль (DIMSTYLE); в Revit — тип размера."
    ),
    "clearance": (
        "По ГОСТ 2.307 размерная линия отстоит от контура не менее чем на 10 мм листа, "
        "параллельные размеры — не менее чем на 7 мм. Уведите полку выноски на свободное место."
    ),
    "norm_refs": (
        "В примечаниях и штампе замените ссылку на отменённый или заменённый документ актуальным обозначением из реестра."
    ),
}

CAD_STEPS = {
    "nanoCAD": "Диспетчер слоёв и стилей: СЛОЙ, СТИЛЬ, РЗМСТИЛЬ.",
    "AutoCAD": "LAYER, STYLE, DIMSTYLE; геометрия — MOVE.",
    "Renga": "Шаблон листа, стили отображения и экспорт в DWG/DXF.",
    "Revit": "Семейство основной надписи, типы размеров, экспорт DWG с весами по слоям.",
}


def rect_marker(text: str, rect, space: str = "model") -> dict[str, Any]:
    return {
        "text": text,
        "bbox": [float(rect.xmin), float(rect.ymin), float(rect.xmax), float(rect.ymax)],
        "space": space,
    }


def box_marker(text: str, bbox, space: str = "model") -> dict[str, Any]:
    xmin, ymin, xmax, ymax = bbox
    if xmax < xmin:
        xmin, xmax = xmax, xmin
    if ymax < ymin:
        ymin, ymax = ymax, ymin
    # вырожденный отрезок чуть расширяем, чтобы рамка была видна
    if abs(xmax - xmin) < 1e-6:
        xmin -= 1.0
        xmax += 1.0
    if abs(ymax - ymin) < 1e-6:
        ymin -= 1.0
        ymax += 1.0
    return {"text": text, "bbox": [float(xmin), float(ymin), float(xmax), float(ymax)], "space": space}


def fix_hint(check_id: str, message: str = "") -> str:
    base = FIX_HINTS.get(check_id) or "Исправьте замечание по указанному пункту ГОСТ и перепроверьте лист."
    if message and "исправ" in message.lower():
        return base
    return base


def _markers_of(check) -> list[dict]:
    extra = getattr(check, "extra", None) or {}
    raw = extra.get("markers") or []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(item)
    return out


def segments_bbox_mm(segments: list) -> Optional[list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for seg in segments or []:
        if not seg:
            continue
        if isinstance(seg[0], str) and seg[0] == "circle":
            _, cx, cy, radius = seg[:4]
            xs.extend((cx - radius, cx + radius))
            ys.extend((cy - radius, cy + radius))
            continue
        try:
            x1, y1, x2, y2 = seg[:4]
        except Exception:
            continue
        xs.extend((float(x1), float(x2)))
        ys.extend((float(y1), float(y2)))
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def build_issue_list(checks, line_items: Optional[list] = None, limit_markers: int = 40) -> list[dict[str, Any]]:
    """Плоский перечень ошибок и замечаний. Номер совпадает с маркером на картинке."""
    items: list[dict[str, Any]] = []
    marker_budget = limit_markers
    for check in checks or []:
        severity = getattr(check, "severity", "")
        if severity not in {"error", "warning"}:
            continue
        markers = _markers_of(check)
        if not markers and getattr(check, "check_id", "") == "lines" and line_items:
            for entry in line_items:
                if entry.get("ok", True):
                    continue
                bbox = segments_bbox_mm(entry.get("segments") or [])
                note = "; ".join(entry.get("notes") or []) or "замечание к линии"
                markers.append({"text": note, "bbox": bbox, "space": "mm" if bbox else None})
                if len(markers) >= 25:
                    break
        if markers:
            for marker in markers:
                bbox = marker.get("bbox")
                use_bbox = bbox if marker_budget > 0 else None
                if use_bbox:
                    marker_budget -= 1
                items.append(
                    {
                        "check_id": check.check_id,
                        "title": check.title,
                        "gost": check.gost,
                        "severity": severity,
                        "text": marker.get("text") or check.message,
                        "bbox": use_bbox,
                        "space": marker.get("space") if use_bbox else None,
                        "fix_hint": fix_hint(check.check_id, check.message),
                    }
                )
        else:
            items.append(
                {
                    "check_id": check.check_id,
                    "title": check.title,
                    "gost": check.gost,
                    "severity": severity,
                    "text": check.message,
                    "bbox": None,
                    "space": None,
                    "fix_hint": fix_hint(check.check_id, check.message),
                }
            )
    for index, item in enumerate(items, start=1):
        item["n"] = index
    return items
