"""Свободное место под размер и выноску. ГОСТ 2.307: 10 мм от контура и 7 мм между параллельными размерами (номер пункта не сверен)."""

from __future__ import annotations

import math
from pathlib import Path

from .models import CheckResult
from .sheetspace import (
    CONTOUR_GAP_MM,
    PARALLEL_GAP_MM,
    model_du,
    open_doc,
    paper_mm,
    scale_denom,
    text_height_paper_mm,
    to_mm,
)


def _dim_points(entity):
    points = {}
    for name in ("defpoint", "defpoint2", "defpoint3", "text_midpoint"):
        try:
            if entity.dxf.hasattr(name):
                point = entity.dxf.get(name)
                points[name] = (float(point.x), float(point.y))
        except Exception:
            continue
    return points


def _segment(p1, p2):
    from shapely.geometry import LineString

    return LineString([p1, p2])


def _text_box(x, y, width, height):
    from shapely.geometry import box

    return box(x, y, x + width, y + height)


def _obstacles(doc, ignore_handles: set[str]):
    from shapely.geometry import LineString, Point

    geoms = []
    for entity in doc.modelspace():
        handle = str(getattr(entity.dxf, "handle", ""))
        if handle in ignore_handles:
            continue
        try:
            if entity.dxftype() == "LINE":
                geoms.append(LineString([(entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)]))
            elif entity.dxftype() in {"TEXT", "MTEXT"}:
                ins = entity.dxf.insert
                height = float(getattr(entity.dxf, "height", 2.5) or 2.5)
                text = entity.plain_text() if entity.dxftype() == "MTEXT" else str(entity.dxf.text or "")
                width = max(height, 0.6 * height * max(len(text), 1))
                geoms.append(_text_box(ins.x, ins.y, width, height))
            elif entity.dxftype() == "INSERT" and "STAMP" in str(entity.dxf.name or "").upper():
                ins = entity.dxf.insert
                geoms.append(_text_box(ins.x, ins.y, 185, 55))
        except Exception:
            continue
    return geoms


def _frame_and_stamp(doc):
    from shapely.geometry import box

    frame = None
    stamp = None
    xs, ys = [], []
    for entity in doc.modelspace():
        layer = str(entity.dxf.layer or "").upper()
        if entity.dxftype() != "LWPOLYLINE":
            continue
        pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
        if len(pts) < 3:
            continue
        minx, miny = min(p[0] for p in pts), min(p[1] for p in pts)
        maxx, maxy = max(p[0] for p in pts), max(p[1] for p in pts)
        rect = box(minx, miny, maxx, maxy)
        if layer == "SPDS_FRAME":
            if frame is None or rect.area > frame.area:
                frame = rect
        if "STAMP" in layer or layer == "SPDS_STAMP":
            stamp = rect
        xs.extend((minx, maxx))
        ys.extend((miny, maxy))
    return frame, stamp


def analyze_doc(doc) -> list[dict]:
    unit = to_mm(doc)
    denom = scale_denom(doc)
    findings = []
    dims = []
    for entity in doc.modelspace().query("DIMENSION"):
        pts = _dim_points(entity)
        if "defpoint2" not in pts or "defpoint3" not in pts:
            continue
        p1, p2 = pts["defpoint2"], pts["defpoint3"]
        base = pts.get("text_midpoint") or pts.get("defpoint") or p1
        length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        # линия размера параллельна измеряемому отрезку и проходит через базовую точку
        if length < 1e-9:
            continue
        ux, uy = (p2[0] - p1[0]) / length, (p2[1] - p1[1]) / length
        nx, ny = -uy, ux
        offset = (base[0] - p1[0]) * nx + (base[1] - p1[1]) * ny
        gap_mm = paper_mm(offset, unit, denom)
        height = text_height_paper_mm(entity, doc, unit, denom)
        text = str(entity.dxf.text or "<>")
        shown = text if text not in {"", "<>"} else "размер"
        width_mm = 0.6 * height * max(len(shown), 4)
        width_du = model_du(width_mm, unit, denom)
        item = {
            "handle": str(entity.dxf.handle),
            "p1": p1,
            "p2": p2,
            "base": base,
            "offset": offset,
            "gap_mm": abs(gap_mm),
            "height_mm": height,
            "width_du": width_du,
            "length": length,
            "normal": (nx, ny),
            "direction": (ux, uy),
        }
        dims.append(item)
        if item["gap_mm"] + 1e-6 < CONTOUR_GAP_MM:
            findings.append(_finding(item, "error" if False else "warning", f"Размерная линия отстоит от контура на {item['gap_mm']:.1f} мм листа, минимум {CONTOUR_GAP_MM:.0f} мм (ГОСТ 2.307, номер пункта не сверен)."))
        if width_du > length + 1e-6:
            findings.append(_finding(item, "warning", "Текст размера шире промежутка между выносными линиями."))
        text_geom = _text_box(base[0], base[1], width_du, model_du(height, unit, denom))
        for other in _obstacles(doc, {item["handle"]}):
            if text_geom.intersects(other):
                findings.append(_finding(item, "warning", "Текст размера пересекается с другим объектом."))
                break
        frame, stamp = _frame_and_stamp(doc)
        if stamp is not None and text_geom.intersects(stamp):
            findings.append(_finding(item, "warning", "Текст размера заходит на основную надпись."))
        if frame is not None and not frame.covers(text_geom):
            findings.append(_finding(item, "warning", "Текст размера выходит за рамку листа."))
    for index, left in enumerate(dims):
        for right in dims[index + 1:]:
            dot = abs(left["direction"][0] * right["direction"][0] + left["direction"][1] * right["direction"][1])
            if dot < 0.98:
                continue
            dist_mm = paper_mm(abs(left["offset"] - right["offset"]), unit, denom)
            if 0.5 < dist_mm < PARALLEL_GAP_MM:
                findings.append(_finding(left, "warning", f"Параллельные размеры ближе {dist_mm:.1f} мм, минимум {PARALLEL_GAP_MM:.0f} мм (ГОСТ 2.307, номер пункта не сверен)."))
    leaders = []
    for name in ("LEADER", "MULTILEADER", "MLEADER"):
        try:
            leaders.extend(doc.modelspace().query(name))
        except Exception:
            continue
    for entity in leaders:
        try:
            vertices = [(float(v[0]), float(v[1])) for v in entity.vertices]
        except Exception:
            vertices = []
        if len(vertices) < 2:
            continue
        end = vertices[-1]
        box = _text_box(end[0], end[1], model_du(20, unit, denom), model_du(3.5, unit, denom))
        hit = False
        for other in _obstacles(doc, {str(entity.dxf.handle)}):
            if box.intersects(other):
                hit = True
                break
        if hit:
            findings.append({
                "severity": "warning",
                "message": "Выноска наезжает на текст или геометрию.",
                "bbox": [end[0], end[1], end[0] + 1, end[1] + 1],
                "handle": str(entity.dxf.handle),
            })
    return findings


def _finding(item, severity, message):
    x, y = item["base"]
    return {
        "severity": severity,
        "message": message,
        "handle": item["handle"],
        "bbox": [min(item["p1"][0], item["p2"][0], x), min(item["p1"][1], item["p2"][1], y), max(item["p1"][0], item["p2"][0], x), max(item["p1"][1], item["p2"][1], y)],
    }


def check_file(path: Path) -> CheckResult:
    doc = open_doc(path)
    findings = analyze_doc(doc)
    if not findings:
        return CheckResult("clearance", "Место под размер", "ГОСТ 2.307", "ok", "Проверенные размеры и выноски не заходят на объекты, штамп и рамку.", extra={"contour_mm": CONTOUR_GAP_MM, "parallel_mm": PARALLEL_GAP_MM})
    return CheckResult(
        "clearance",
        "Место под размер",
        "ГОСТ 2.307",
        "warning",
        f"Замечаний к месту размера или выноски: {len(findings)}.",
        details=[item["message"] for item in findings[:20]],
        findings_count=len(findings),
        extra={"markers": [{"text": item["message"], "bbox": item["bbox"], "space": "model"} for item in findings if item.get("bbox")], "findings": findings},
    )


def suggest(doc, p1, p2, text: str = "100", height_mm: float = 3.5) -> dict:
    unit = to_mm(doc)
    denom = scale_denom(doc)
    length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    if length < 1e-9:
        return {"fits": False, "message": "Точки совпадают.", "alternatives": []}
    ux, uy = (p2[0] - p1[0]) / length, (p2[1] - p1[1]) / length
    nx, ny = -uy, ux
    width = model_du(0.6 * height_mm * max(len(text), 1), unit, denom)
    height = model_du(height_mm, unit, denom)
    mid = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
    obstacles = _obstacles(doc, set())
    frame, stamp = _frame_and_stamp(doc)
    if stamp is not None:
        obstacles.append(stamp)

    def text_geom(offset):
        base = (mid[0] + nx * offset, mid[1] + ny * offset)
        return _text_box(base[0] - width / 2.0, base[1], width, height)

    def free(offset):
        geom = text_geom(offset)
        if frame is not None and not frame.buffer(0.5).covers(geom):
            return False
        return all(not geom.intersects(item) for item in obstacles)

    min_off = model_du(CONTOUR_GAP_MM, unit, denom)
    fits_between = width <= length
    if fits_between and free(min_off):
        return {"fits": True, "offset": min_off, "side": 1, "paper_mm": CONTOUR_GAP_MM, "message": "Размер помещается."}
    alternatives = []
    for step in (CONTOUR_GAP_MM, PARALLEL_GAP_MM, 14, 21, 28):
        for sign in (1, -1):
            offset = sign * model_du(step, unit, denom)
            if free(offset):
                alternatives.append({"offset": offset, "paper_mm": step * sign})
                if len(alternatives) >= 3:
                    break
        if len(alternatives) >= 3:
            break
    return {
        "fits": False,
        "between_extensions": fits_between,
        "alternatives": alternatives,
        "message": "В исходном положении размер не помещается." if not alternatives else "Есть свободное положение размерной линии.",
    }


def suggest_file(path: Path, p1, p2, text: str = "100") -> dict:
    return suggest(open_doc(path), tuple(p1), tuple(p2), text)


def clearance_cli(argv: list[str]) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="gost-check clearance")
    parser.add_argument("input")
    parser.add_argument("--at", help="x1,y1,x2,y2 — проверить, помещается ли размер")
    parser.add_argument("--text", default="100")
    args = parser.parse_args(argv)
    if args.at:
        nums = [float(part) for part in args.at.split(",")]
        payload = suggest_file(Path(args.input), (nums[0], nums[1]), (nums[2], nums[3]), args.text)
    else:
        payload = {"message": check_file(Path(args.input)).message, "details": check_file(Path(args.input)).details}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0
