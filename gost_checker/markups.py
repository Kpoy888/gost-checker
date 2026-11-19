"""Пользовательские кружки ошибок на PDF и DXF. Без обучения и без OCR рукописи."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

DEFAULT_LAYERS = ("ошибки", "errors", "markup")
_DESC_RE = re.compile(r"(?:№\s*)?(\d+)\s*[—\-:]\s*(.+)")


def parse_descriptions(text: str) -> dict[int, str]:
    found: dict[int, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _DESC_RE.match(line)
        if not match:
            continue
        found[int(match.group(1))] = match.group(2).strip()
    return found


def _layer_ok(name: str, layers: tuple[str, ...]) -> bool:
    return (name or "").strip().casefold() in {item.casefold() for item in layers}


def _circle_from_entity(entity) -> Optional[dict]:
    dtype = entity.dxftype()
    try:
        if dtype == "CIRCLE":
            center = entity.dxf.center
            radius = float(entity.dxf.radius)
            return {"cx": float(center.x), "cy": float(center.y), "r": radius, "kind": "circle"}
        if dtype == "ELLIPSE":
            center = entity.dxf.center
            major = entity.dxf.major_axis
            radius = float((major.x ** 2 + major.y ** 2) ** 0.5)
            return {"cx": float(center.x), "cy": float(center.y), "r": radius, "kind": "ellipse"}
        if dtype == "LWPOLYLINE" and entity.closed:
            pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
            if len(pts) < 3:
                return None
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            radius = max(((p[0] - cx) ** 2 + (p[1] - cy) ** 2) ** 0.5 for p in pts)
            return {"cx": cx, "cy": cy, "r": radius, "kind": "polyline", "points": pts}
    except Exception:
        return None
    return None


def circles_from_dxf(doc, layers: tuple[str, ...] = DEFAULT_LAYERS) -> list[dict]:
    texts = []
    circles = []
    for entity in doc.modelspace():
        layer = str(entity.dxf.layer or "")
        if not _layer_ok(layer, layers):
            continue
        if entity.dxftype() in {"TEXT", "MTEXT"}:
            try:
                content = entity.plain_text() if entity.dxftype() == "MTEXT" else str(entity.dxf.text or "")
            except Exception:
                content = str(entity.dxf.text or "")
            ins = entity.dxf.insert
            texts.append({"text": content.strip(), "x": float(ins.x), "y": float(ins.y), "layer": layer})
            continue
        shape = _circle_from_entity(entity)
        if shape:
            shape["layer"] = layer
            shape["handle"] = str(entity.dxf.handle)
            circles.append(shape)
    for shape in circles:
        number = _nearest_number(shape, texts)
        shape["number"] = number
        shape["comment"] = ""
    return circles


def _nearest_number(shape: dict, texts: list[dict]) -> Optional[int]:
    best = None
    best_dist = 1e18
    limit = max(shape["r"] * 1.6, 1.0)
    for item in texts:
        if not re.fullmatch(r"\d+", item["text"]):
            continue
        dist = ((item["x"] - shape["cx"]) ** 2 + (item["y"] - shape["cy"]) ** 2) ** 0.5
        if dist <= limit and dist < best_dist:
            best = int(item["text"])
            best_dist = dist
    return best


def circles_from_pdf(path: Path) -> list[dict]:
    import fitz

    circles = []
    doc = fitz.open(str(path))
    try:
        for page_index, page in enumerate(doc):
            annots = page.annots()
            if annots is None:
                continue
            for annot in annots:
                kind = ""
                try:
                    kind = str(annot.type[1] if isinstance(annot.type, tuple) else annot.type)
                except Exception:
                    kind = ""
                if kind not in {"Circle", "Square", "Ink"}:
                    continue
                rect = annot.rect
                info = annot.info or {}
                content = str(info.get("content") or "").strip()
                number = None
                comment = content
                if re.fullmatch(r"\d+", content):
                    number = int(content)
                    comment = ""
                elif content:
                    parsed = parse_descriptions(content)
                    if len(parsed) == 1:
                        number = next(iter(parsed))
                        comment = parsed[number]
                circles.append(
                    {
                        "kind": kind.lower(),
                        "page": page_index,
                        "cx": (rect.x0 + rect.x1) / 2.0,
                        "cy": (rect.y0 + rect.y1) / 2.0,
                        "r": max(rect.width, rect.height) / 2.0,
                        "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
                        "number": number,
                        "comment": comment,
                        "space": "pdf",
                    }
                )
    finally:
        doc.close()
    return circles


def _geometry_extent(doc) -> Optional[tuple[float, float, float, float]]:
    xs: list[float] = []
    ys: list[float] = []
    for entity in doc.modelspace():
        if _layer_ok(str(entity.dxf.layer or ""), DEFAULT_LAYERS):
            continue
        try:
            if entity.dxftype() == "LINE":
                xs.extend((entity.dxf.start.x, entity.dxf.end.x))
                ys.extend((entity.dxf.start.y, entity.dxf.end.y))
            elif entity.dxftype() in {"TEXT", "MTEXT"}:
                ins = entity.dxf.insert
                xs.append(ins.x)
                ys.append(ins.y)
        except Exception:
            continue
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def associate_dxf(doc, circles: list[dict], checks: list, descriptions: Optional[dict[int, str]] = None) -> list[dict]:
    from shapely.geometry import LineString, Point, box
    from shapely.strtree import STRtree

    descriptions = descriptions or {}
    geoms = []
    owners = []
    for entity in doc.modelspace():
        if _layer_ok(str(entity.dxf.layer or ""), DEFAULT_LAYERS):
            continue
        try:
            if entity.dxftype() == "LINE":
                geom = LineString([(entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)])
            elif entity.dxftype() in {"TEXT", "MTEXT", "INSERT", "CIRCLE"}:
                if entity.dxftype() == "CIRCLE":
                    center = entity.dxf.center
                    point = (center.x, center.y)
                else:
                    ins = entity.dxf.insert
                    point = (ins.x, ins.y)
                geom = Point(point)
            else:
                continue
        except Exception:
            continue
        geoms.append(geom)
        owners.append({"type": entity.dxftype(), "layer": str(entity.dxf.layer), "handle": str(entity.dxf.handle)})
    tree = STRtree(geoms) if geoms else None
    extent = _geometry_extent(doc)
    findings = []
    for check in checks or []:
        for marker in (getattr(check, "extra", None) or {}).get("markers") or []:
            bbox = marker.get("bbox")
            if not bbox:
                continue
            findings.append({"check_id": check.check_id, "text": marker.get("text") or check.message, "bbox": bbox, "severity": check.severity})
    prepared = []
    for index, shape in enumerate(circles, start=1):
        zone = Point(shape["cx"], shape["cy"]).buffer(float(shape["r"]))
        inside = []
        if tree is not None:
            hits = tree.query(zone)
            for hit in hits:
                if zone.intersects(geoms[int(hit)]):
                    inside.append(owners[int(hit)])
        matched = []
        for finding in findings:
            xmin, ymin, xmax, ymax = finding["bbox"]
            if zone.intersects(box(xmin, ymin, xmax, ymax)):
                matched.append({"check_id": finding["check_id"], "text": finding["text"], "severity": finding["severity"]})
        number = shape.get("number") if shape.get("number") is not None else index
        description = descriptions.get(int(number), "") if number is not None else ""
        outside = False
        if extent is not None:
            xmin, ymin, xmax, ymax = extent
            outside = not box(xmin, ymin, xmax, ymax).intersects(zone)
        note = ""
        if not matched:
            note = "ошибка отмечена пользователем, автопроверкой не подтверждена"
        if outside:
            note = (note + "; " if note else "") + "кружок вне листа"
        prepared.append(
            {
                "id": int(number) if number is not None else index,
                "geometry": {"cx": shape["cx"], "cy": shape["cy"], "r": shape["r"], "kind": shape.get("kind")},
                "comment": shape.get("comment") or "",
                "description": description,
                "objects": inside,
                "findings": matched,
                "note": note,
                "bbox": [shape["cx"] - shape["r"], shape["cy"] - shape["r"], shape["cx"] + shape["r"], shape["cy"] + shape["r"]],
                "space": "model",
                "severity": "markup",
                "title": "Пометка пользователя",
                "text": description or shape.get("comment") or note or "кружок",
            }
        )
    return prepared


def save_example(path: Path, items: list[dict]) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for item in items:
        payload.append(
            {
                "id": item.get("id"),
                "geometry": item.get("geometry"),
                "description": item.get("description"),
                "comment": item.get("comment"),
                "note": item.get("note"),
                "findings": item.get("findings"),
                "objects": item.get("objects"),
            }
        )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def collect_for_result(result, notes: str = "") -> list[dict]:
    path = Path(result.input_path)
    if not path.is_file():
        return []
    descriptions = parse_descriptions(notes)
    suffix = path.suffix.lower()
    if suffix == ".dxf":
        import ezdxf

        try:
            doc = ezdxf.readfile(str(path))
        except Exception:
            return []
        circles = circles_from_dxf(doc)
        if not circles:
            return []
        return associate_dxf(doc, circles, result.checks, descriptions)
    if suffix == ".pdf":
        circles = circles_from_pdf(path)
        items = []
        for index, shape in enumerate(circles, start=1):
            number = shape.get("number") if shape.get("number") is not None else index
            items.append(
                {
                    "id": number,
                    "geometry": {"cx": shape["cx"], "cy": shape["cy"], "r": shape["r"], "kind": shape.get("kind")},
                    "comment": shape.get("comment") or "",
                    "description": "",
                    "objects": [],
                    "findings": [],
                    "note": "ошибка отмечена пользователем, автопроверкой не подтверждена",
                    "bbox": shape.get("bbox"),
                    "space": "pdf",
                    "severity": "markup",
                    "title": "Пометка пользователя",
                    "text": shape.get("comment") or "кружок",
                }
            )
        return items
    return []
