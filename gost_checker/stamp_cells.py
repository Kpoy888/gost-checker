"""Графы основной надписи формы 3 и дополнительные зоны."""

from __future__ import annotations

from typing import Optional

from .models import Rect
from .units import du_to_mm, mm_to_du

# Координаты в мм от нижнего левого угла штампа 185×55.
STAMP_FIELDS = [
    {"id": "разраб", "label": "Разраб.", "required": True, "box": (20.0, 25.0, 40.0, 30.0), "tag": "RAZRAB"},
    {"id": "пров", "label": "Пров.", "required": False, "box": (20.0, 20.0, 40.0, 25.0), "tag": "PROV"},
    {"id": "н_контр", "label": "Н.контр.", "required": False, "box": (20.0, 5.0, 40.0, 10.0), "tag": "NKONTR"},
    {"id": "утв", "label": "Утв.", "required": False, "box": (20.0, 0.0, 20.0 + 20.0, 5.0), "tag": "UTV"},
    {"id": "обозначение", "label": "Обозначение", "required": True, "box": (65.0, 40.0, 135.0, 55.0), "tag": "OBOZN"},
    {"id": "объект", "label": "Объект", "required": False, "box": (135.0, 40.0, 185.0, 55.0), "tag": "OBEKT"},
    {"id": "здание", "label": "Здание", "required": False, "box": (65.0, 25.0, 135.0, 40.0), "tag": "ZDANIE"},
    {"id": "наименование", "label": "Наименование", "required": True, "box": (65.0, 0.0, 135.0, 25.0), "tag": "NAIMEN"},
    {"id": "стадия", "label": "Стадия", "required": True, "box": (135.0, 20.0, 150.0, 35.0), "tag": "STADIYA"},
    {"id": "лист", "label": "Лист", "required": False, "box": (150.0, 20.0, 165.0, 35.0), "tag": "LIST"},
    {"id": "листов", "label": "Листов", "required": False, "box": (165.0, 20.0, 185.0, 35.0), "tag": "LISTOV"},
    {"id": "организация", "label": "Организация", "required": False, "box": (135.0, 0.0, 185.0, 20.0), "tag": "ORG"},
]
TAG_TO_ID = {f["tag"]: f["id"] for f in STAMP_FIELDS}


def fields_from_inserts(doc) -> list[dict]:
    data = {f["id"]: {**f, "text": "", "empty": True} for f in STAMP_FIELDS}
    layouts = [doc.modelspace(), *list(doc.layouts)]
    seen = set()
    for layout in layouts:
        if id(layout) in seen:
            continue
        seen.add(id(layout))
        try:
            inserts = list(layout.query("INSERT"))
        except Exception:
            continue
        for insert in inserts:
            for attrib in getattr(insert, "attribs", []) or []:
                tag = str(attrib.dxf.tag or "").upper()
                key = TAG_TO_ID.get(tag)
                if not key:
                    continue
                text = str(attrib.dxf.text or "").strip()
                data[key]["text"] = text
                data[key]["empty"] = not text
    return list(data.values())


def find_extra_zones(rects: list[Rect], stamp: Rect, inner: Optional[Rect], to_mm: float) -> list[dict]:
    extra = []
    tol = mm_to_du(4.0, to_mm)

    def overlap(a0, a1, b0, b1) -> float:
        return max(0.0, min(a1, b1) - max(a0, b0))

    for rect in rects:
        if (
            abs(rect.xmin - stamp.xmin) < tol
            and abs(rect.ymin - stamp.ymin) < tol
            and abs(rect.xmax - stamp.xmax) < tol
            and abs(rect.ymax - stamp.ymax) < tol
        ):
            continue
        w = du_to_mm(rect.width, to_mm)
        h = du_to_mm(rect.height, to_mm)
        if w < 8 or h < 8:
            continue
        if abs(rect.xmax - stamp.xmin) <= tol and overlap(rect.ymin, rect.ymax, stamp.ymin, stamp.ymax) > 0.4 * stamp.height:
            extra.append({"where": "слева от штампа", "width_mm": round(w, 1), "height_mm": round(h, 1)})
        if abs(rect.ymin - stamp.ymax) <= tol and overlap(rect.xmin, rect.xmax, stamp.xmin, stamp.xmax) > 0.35 * stamp.width:
            extra.append({"where": "над штампом", "width_mm": round(w, 1), "height_mm": round(h, 1)})
        if inner and abs(rect.xmin - inner.xmin) <= tol and w <= 28 and h >= 40:
            extra.append({"where": "у левой рамки (дополнительные графы)", "width_mm": round(w, 1), "height_mm": round(h, 1)})
    # unique by where+size
    uniq = []
    seen = set()
    for item in extra:
        key = (item["where"], item["width_mm"], item["height_mm"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


def field_details(fields: list[dict]) -> tuple[list[str], list[str], dict]:
    lines = ["Графы основной надписи (форма 3):"]
    empty_required = []
    for field in fields:
        text = field.get("text") or ""
        empty = bool(field.get("empty", not text))
        state = "заполнена" if text and not empty else ("пусто (обязательная)" if field.get("required") else "пусто")
        shown = text if text and not empty else "—"
        lines.append(f"  {field['label']}: {shown} — {state}")
        if field.get("required") and (empty or not text):
            empty_required.append(field["label"])
    extra = {"fields": [{"id": f["id"], "label": f["label"], "text": f.get("text") or "", "empty": f.get("empty", True), "required": f.get("required")} for f in fields],
             "empty_required": empty_required}
    return lines, empty_required, extra
