"""Противоречия размеров: одно и то же место, марки, переопределение текста, сумма цепочки."""

from __future__ import annotations

import math
import re
from pathlib import Path

from .models import CheckResult
from .sheetspace import header_dim, open_doc, to_mm

MARK_RE = re.compile(r"(?:ОК|ОКН|Д|Т|П)\s*-?\s*\d+", re.IGNORECASE)
NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _points(entity):
    found = []
    for name in ("defpoint2", "defpoint3", "defpoint"):
        try:
            if entity.dxf.hasattr(name):
                point = entity.dxf.get(name)
                found.append((float(point.x), float(point.y)))
        except Exception:
            continue
    return found


def _pair(entity):
    pts = _points(entity)
    if len(pts) < 2:
        return None
    a, b = pts[0], pts[1]
    return a, b, math.hypot(b[0] - a[0], b[1] - a[1])


def _displayed(doc, entity, length_du: float) -> tuple[float, float | None]:
    factor = header_dim(doc, "$DIMLFAC", 1.0) or 1.0
    raw = length_du * factor
    dec = header_dim(doc, "$DIMDEC", -1)
    rnd = header_dim(doc, "$DIMRND", 0.0) or 0.0
    if rnd > 1e-9:
        raw = round(raw / rnd) * rnd
    elif dec is not None and dec >= 0:
        raw = round(raw, int(dec))
    text = str(entity.dxf.text or "").strip()
    override = None
    if text and "<>" not in text:
        match = NUMBER_RE.search(text.replace(" ", ""))
        if match:
            override = float(match.group(0).replace(",", "."))
    return raw, override


def _key(p1, p2, tol: float):
    def rnd(point):
        return (round(point[0] / tol) * tol, round(point[1] / tol) * tol)

    a, b = rnd(p1), rnd(p2)
    return tuple(sorted((a, b)))


def analyze_doc(doc, tol_du: float = 0.5) -> list[dict]:
    unit = to_mm(doc)
    tol = max(tol_du, 0.5 / max(unit, 1e-9))
    dims = []
    seen_layouts = set()
    for layout in [doc.modelspace(), *list(doc.layouts)]:
        if id(layout) in seen_layouts:
            continue
        seen_layouts.add(id(layout))
        try:
            entities = list(layout.query("DIMENSION"))
        except Exception:
            continue
        space = getattr(layout, "name", "Model")
        for entity in entities:
            pair = _pair(entity)
            if pair is None:
                continue
            p1, p2, length = pair
            measured, override = _displayed(doc, entity, length)
            text = str(entity.dxf.text or "")
            marks = MARK_RE.findall(text)
            dims.append({
                "handle": str(entity.dxf.handle),
                "space": space,
                "p1": p1,
                "p2": p2,
                "length": length,
                "measured": measured,
                "override": override,
                "text": text,
                "marks": marks,
                "bbox": [min(p1[0], p2[0]), min(p1[1], p2[1]), max(p1[0], p2[0]), max(p1[1], p2[1])],
            })
    findings = []
    by_place: dict[tuple, list] = {}
    for item in dims:
        by_place.setdefault(_key(item["p1"], item["p2"], tol), []).append(item)
    for group in by_place.values():
        values = []
        for item in group:
            values.append(item["override"] if item["override"] is not None else item["measured"])
        if len(group) >= 2 and max(values) - min(values) > max(0.05, 0.001 * max(abs(v) for v in values)):
            findings.append(_both(group[0], group[1], "Между одними и теми же точками разные размерные числа."))
    for item in dims:
        if item["override"] is None:
            continue
        if abs(item["override"] - item["measured"]) > max(0.05, 10 ** (-_decimals(item)) / 2):
            findings.append({
                "message": f"Текст размера {item['override']} не совпадает с измерением {item['measured']:.4g} (учтены DIMLFAC, DIMRND и DIMDEC).",
                "handle": item["handle"],
                "bbox": item["bbox"],
                "values": [item["override"], item["measured"]],
            })
    by_mark: dict[str, list] = {}
    for item in dims:
        for mark in item["marks"]:
            by_mark.setdefault(mark.upper().replace(" ", ""), []).append(item)
    for mark, group in by_mark.items():
        values = [item["override"] if item["override"] is not None else item["measured"] for item in group]
        if len(group) >= 2 and max(values) - min(values) > 0.05:
            findings.append(_both(group[0], group[1], f"Марка {mark} стоит у разных размеров."))
    findings.extend(_chains(dims))
    return findings


def _decimals(item) -> int:
    text = str(item.get("text") or "")
    if "." in text or "," in text:
        frac = text.replace(",", ".").split(".")[-1]
        digits = "".join(ch for ch in frac if ch.isdigit())
        return max(len(digits), 1)
    return 0


def _both(left, right, message):
    box = [
        min(left["bbox"][0], right["bbox"][0]),
        min(left["bbox"][1], right["bbox"][1]),
        max(left["bbox"][2], right["bbox"][2]),
        max(left["bbox"][3], right["bbox"][3]),
    ]
    return {
        "message": f"{message} Значения: {_value(left)} и {_value(right)}.",
        "handle": left["handle"],
        "handles": [left["handle"], right["handle"]],
        "bbox": box,
        "values": [_value(left), _value(right)],
    }


def _value(item):
    return item["override"] if item["override"] is not None else round(item["measured"], 4)


def _contains(big, small) -> bool:
    return big["length"] > small["length"] + 1.0 and _on_segment(big, small["p1"]) and _on_segment(big, small["p2"])


def _on_segment(item, point) -> bool:
    a, b = item["p1"], item["p2"]
    cross = (point[0] - a[0]) * (b[1] - a[1]) - (point[1] - a[1]) * (b[0] - a[0])
    if abs(cross) > max(1.0, 0.01 * item["length"]):
        return False
    dot = (point[0] - a[0]) * (b[0] - a[0]) + (point[1] - a[1]) * (b[1] - a[1])
    return -1.0 <= dot <= item["length"] ** 2 + 1.0


def _chains(dims: list[dict]) -> list[dict]:
    findings = []
    parts = [item for item in dims if not any(_contains(item, other) for other in dims if other is not item)]
    overalls = [item for item in dims if any(_contains(item, other) for other in dims if other is not item)]
    used = set()
    for index, item in enumerate(parts):
        if index in used:
            continue
        chain = [item]
        used.add(index)
        changed = True
        while changed:
            changed = False
            for other_index, other in enumerate(parts):
                if other_index in used:
                    continue
                if _shares(chain, other) and _collinear(chain[0], other):
                    chain.append(other)
                    used.add(other_index)
                    changed = True
        if len(chain) < 2:
            continue
        ends = _ends(chain)
        part_sum = sum(float(_value(part)) for part in chain)
        for overall in overalls:
            matched = (_near(overall["p1"], ends[0]) and _near(overall["p2"], ends[1])) or (
                _near(overall["p1"], ends[1]) and _near(overall["p2"], ends[0])
            )
            if not matched:
                continue
            overall_value = float(_value(overall))
            if abs(part_sum - overall_value) > max(0.2, 0.01 * abs(overall_value)):
                findings.append({
                    "message": f"Сумма цепочки {part_sum:.4g} не равна габаритному размеру {overall_value}.",
                    "handle": overall["handle"],
                    "bbox": overall["bbox"],
                    "values": [part_sum, overall_value],
                })
    return findings


def _shares(chain, other) -> bool:
    points = [pt for item in chain for pt in (item["p1"], item["p2"])]
    return any(_near(other["p1"], pt) or _near(other["p2"], pt) for pt in points)


def _collinear(left, right) -> bool:
    v1 = (left["p2"][0] - left["p1"][0], left["p2"][1] - left["p1"][1])
    v2 = (right["p2"][0] - right["p1"][0], right["p2"][1] - right["p1"][1])
    n1 = math.hypot(*v1) or 1
    n2 = math.hypot(*v2) or 1
    return abs(v1[0] / n1 * v2[0] / n2 + v1[1] / n1 * v2[1] / n2) > 0.98


def _near(a, b, tol=1.0) -> bool:
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol


def _ends(chain):
    points = [pt for item in chain for pt in (item["p1"], item["p2"])]
    points.sort()
    return points[0], points[-1]


def check_file(path: Path) -> CheckResult:
    findings = analyze_doc(open_doc(path))
    if not findings:
        return CheckResult("dim_conflict", "Противоречия размеров", "ГОСТ 2.307", "ok", "Противоречий между размерными числами не найдено.")
    return CheckResult(
        "dim_conflict",
        "Противоречия размеров",
        "ГОСТ 2.307",
        "warning",
        f"Противоречий размеров: {len(findings)}.",
        details=[item["message"] for item in findings[:20]],
        findings_count=len(findings),
        extra={"markers": [{"text": item["message"], "bbox": item["bbox"], "space": "model"} for item in findings if item.get("bbox")], "findings": findings},
    )
