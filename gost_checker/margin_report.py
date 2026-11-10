"""Общая оценка полей рамки 20 / 5 / 5 / 5 мм."""

from __future__ import annotations

from typing import Optional

from .constants import FRAME_LEFT_MM, FRAME_OTHER_MM

MARGIN_TOL_MM = 0.5
REQUIRED = {
    "left": FRAME_LEFT_MM,
    "right": FRAME_OTHER_MM,
    "top": FRAME_OTHER_MM,
    "bottom": FRAME_OTHER_MM,
}
LABELS = {"left": "слева", "right": "справа", "top": "сверху", "bottom": "снизу"}


def measure_margins(left: float, right: float, top: float, bottom: float, tol: float = MARGIN_TOL_MM) -> dict:
    items = {}
    for key, value in (("left", left), ("right", right), ("top", top), ("bottom", bottom)):
        required = REQUIRED[key]
        items[key] = {
            "value": float(value),
            "required": required,
            "ok": abs(float(value) - required) <= tol,
            "label": LABELS[key],
        }
    items["all_ok"] = all(items[k]["ok"] for k in REQUIRED)
    items["tol"] = tol
    return items


def margins_from_frames(outer, inner, to_mm: float, tol: float = MARGIN_TOL_MM) -> dict:
    left = (inner.xmin - outer.xmin) * to_mm
    right = (outer.xmax - inner.xmax) * to_mm
    bottom = (inner.ymin - outer.ymin) * to_mm
    top = (outer.ymax - inner.ymax) * to_mm
    return measure_margins(left, right, top, bottom, tol=tol)


def format_margin_lines(margins: dict) -> list[str]:
    if not margins:
        return []
    tol = margins.get("tol", MARGIN_TOL_MM)
    lines = [f"Поля внутренней рамки относительно внешней (норма 20 / 5 / 5 / 5 мм, допуск ±{tol:g} мм):"]
    parts = []
    for key in ("left", "right", "top", "bottom"):
        item = margins[key]
        mark = "да" if item["ok"] else "нет"
        lines.append(
            f"  {item['label']}: {item['value']:.2f} мм (норма {item['required']:.2f} мм) — сходимость: {mark}"
        )
        parts.append(f"{item['label']} {item['value']:.2f}")
    lines.append(
        "Фактические поля: "
        + ", ".join(f"{item['label']} {item['value']:.2f} мм" for item in (margins[k] for k in ("left", "right", "top", "bottom")))
        + " (ожидается 20 / 5 / 5 / 5)."
    )
    return lines


def margins_as_table_rows(margins: Optional[dict]) -> list[dict]:
    if not margins:
        return []
    rows = []
    for key in ("left", "right", "top", "bottom"):
        item = margins[key]
        rows.append(
            {
                "side": item["label"],
                "value": round(item["value"], 2),
                "required": item["required"],
                "ok": item["ok"],
            }
        )
    return rows
