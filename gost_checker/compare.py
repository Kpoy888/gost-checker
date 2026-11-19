"""Сравнение двух версий чертежа: сущности DXF и пиксели PDF/сканов."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional


def _round(value: float, ndigits: int = 3) -> float:
    return round(float(value), ndigits)


def _text_of(entity) -> str:
    dtype = entity.dxftype()
    try:
        if dtype == "TEXT":
            return str(entity.dxf.text or "")
        if dtype == "MTEXT":
            try:
                return entity.plain_text()
            except Exception:
                return str(entity.dxf.text or "")
        if dtype == "ATTRIB":
            return str(entity.dxf.text or "")
    except Exception:
        return ""
    return ""


def _geom_key(entity) -> tuple:
    dtype = entity.dxftype()
    try:
        if dtype == "LINE":
            a, b = entity.dxf.start, entity.dxf.end
            p1 = (_round(a.x), _round(a.y))
            p2 = (_round(b.x), _round(b.y))
            return ("LINE", tuple(sorted((p1, p2))))
        if dtype == "LWPOLYLINE":
            pts = tuple((_round(p[0]), _round(p[1])) for p in entity.get_points("xy"))
            return ("LWPOLYLINE", pts, bool(entity.closed))
        if dtype == "CIRCLE":
            c = entity.dxf.center
            return ("CIRCLE", _round(c.x), _round(c.y), _round(entity.dxf.radius))
        if dtype in {"TEXT", "MTEXT", "ATTRIB"}:
            ins = entity.dxf.insert
            return (dtype, _round(ins.x, 2), _round(ins.y, 2))
        if dtype == "INSERT":
            ins = entity.dxf.insert
            return ("INSERT", str(entity.dxf.name), _round(ins.x, 2), _round(ins.y, 2))
        if dtype == "DIMENSION":
            p = entity.dxf.defpoint
            return ("DIMENSION", _round(p.x, 2), _round(p.y, 2), str(entity.dxf.get("text", "")))
    except Exception:
        pass
    return (dtype, str(getattr(entity.dxf, "handle", "")))


def _props(entity) -> dict:
    def grab(name, default=""):
        try:
            if entity.dxf.hasattr(name):
                return entity.dxf.get(name)
        except Exception:
            return default
        return default

    return {
        "type": entity.dxftype(),
        "layer": str(grab("layer", "0")),
        "linetype": str(grab("linetype", "")),
        "lineweight": grab("lineweight", None),
        "color": grab("color", None),
        "text": _text_of(entity),
        "geom": _geom_key(entity),
        "handle": str(grab("handle", "")),
    }


def _entities(doc) -> list:
    items = []
    layouts = [doc.modelspace()]
    try:
        for layout in doc.layouts:
            if layout.name.lower() != "model":
                layouts.append(layout)
    except Exception:
        pass
    seen = set()
    for layout in layouts:
        if id(layout) in seen:
            continue
        seen.add(id(layout))
        space = getattr(layout, "name", "Model")
        try:
            entities = list(layout)
        except Exception:
            continue
        for entity in entities:
            if entity.dxftype() in {"VIEWPORT", "SEQEND"}:
                continue
            props = _props(entity)
            props["space"] = space
            items.append(props)
    return items


def _match_key(props: dict) -> tuple:
    return (props["space"], props["type"], props["layer"], props["geom"])


def diff_entities(old_doc, new_doc) -> dict:
    old_items = _entities(old_doc)
    new_items = _entities(new_doc)
    used_new = set()
    removed = []
    changed = []
    added = []
    # сначала по handle, если он есть с обеих сторон
    new_by_handle = {}
    for index, item in enumerate(new_items):
        if item["handle"]:
            new_by_handle.setdefault((item["space"], item["handle"]), []).append(index)
    consumed = set()
    for old in old_items:
        hit = None
        if old["handle"]:
            bucket = new_by_handle.get((old["space"], old["handle"])) or []
            for index in bucket:
                if index not in consumed:
                    hit = index
                    break
        if hit is None:
            key = _match_key(old)
            for index, new in enumerate(new_items):
                if index in consumed:
                    continue
                if _match_key(new) == key:
                    hit = index
                    break
        if hit is None:
            removed.append(old)
            continue
        consumed.add(hit)
        new = new_items[hit]
        fields = []
        for field in ("layer", "linetype", "lineweight", "color", "text", "geom"):
            if old.get(field) != new.get(field):
                fields.append(field)
        if fields:
            changed.append({"old": old, "new": new, "fields": fields})
    for index, new in enumerate(new_items):
        if index not in consumed:
            added.append(new)
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
    }


def diff_checks(old_checks, new_checks) -> dict:
    def brief(checks):
        out = {}
        for check in checks or []:
            out[getattr(check, "check_id", None) or check.get("id") or check.get("check_id")] = {
                "severity": getattr(check, "severity", None) or check.get("severity"),
                "message": getattr(check, "message", None) or check.get("message"),
                "title": getattr(check, "title", None) or check.get("title"),
            }
        return out

    old_map = brief(old_checks)
    new_map = brief(new_checks)
    appeared, disappeared, changed = [], [], []
    for key, item in new_map.items():
        prev = old_map.get(key)
        if prev is None:
            if item["severity"] in {"error", "warning"}:
                appeared.append({"check_id": key, **item})
            continue
        if prev["severity"] in {"error", "warning"} and item["severity"] in {"ok", "info"}:
            disappeared.append({"check_id": key, "was": prev["severity"], "now": item["severity"], "title": item["title"]})
        elif prev["severity"] != item["severity"] or prev["message"] != item["message"]:
            changed.append({"check_id": key, "was": prev, "now": item})
        if prev and prev["severity"] not in {"error", "warning"} and item["severity"] in {"error", "warning"}:
            if not any(row["check_id"] == key for row in appeared):
                appeared.append({"check_id": key, **item})
    for key, item in old_map.items():
        if key not in new_map and item["severity"] in {"error", "warning"}:
            disappeared.append({"check_id": key, "was": item["severity"], "now": "нет", "title": item["title"]})
    return {"appeared": appeared, "disappeared": disappeared, "changed": changed}


def render_entity_diff_png(old_doc, new_doc, diff: dict, dest: Path) -> Optional[str]:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None

    def draw(items, color, lw=1.0):
        for item in items:
            geom = item.get("geom")
            if not geom:
                continue
            kind = geom[0]
            if kind == "LINE":
                (x1, y1), (x2, y2) = geom[1]
                ax.plot([x1, x2], [y1, y2], color=color, lw=lw)
            elif kind == "LWPOLYLINE":
                pts = geom[1]
                if len(pts) >= 2:
                    ax.plot([p[0] for p in pts], [p[1] for p in pts], color=color, lw=lw)
            elif kind == "CIRCLE":
                circ = plt.Circle((geom[1], geom[2]), geom[3], fill=False, color=color, lw=lw)
                ax.add_patch(circ)

    fig, ax = plt.subplots(figsize=(8, 5.6))
    ax.set_aspect("equal")
    ax.set_facecolor("white")
    unchanged = []
    # серым — то, что не попало в added/removed. Для скорости рисуем new целиком серым, поверх diff.
    draw(_entities(new_doc), "#d0d0d0", 0.6)
    draw(diff.get("removed") or [], "#c0392b", 1.6)
    draw(diff.get("added") or [], "#1e8449", 1.6)
    for row in diff.get("changed") or []:
        draw([row["new"]], "#b9770e", 1.6)
    ax.autoscale()
    fig.savefig(dest, dpi=80, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(dest)


def align_and_diff_images(image_a, image_b, dest: Path) -> Optional[str]:
    """Пиксельный дифф. Добавленное — зелёным, удалённое — красным."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except Exception:
        return None

    def to_gray(image):
        arr = np.asarray(image)
        if arr.ndim == 3:
            return cv2.cvtColor(arr[:, :, :3], cv2.COLOR_BGR2GRAY if arr.shape[2] >= 3 else cv2.COLOR_RGB2GRAY)
        return arr

    gray_a = to_gray(image_a)
    gray_b = to_gray(image_b)
    if gray_b.shape != gray_a.shape:
        gray_b = cv2.resize(gray_b, (gray_a.shape[1], gray_a.shape[0]), interpolation=cv2.INTER_AREA)
    aligned = gray_b
    try:
        warp = np.eye(2, 3, dtype=np.float32)
        criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 1e-4)
        _cc, warp = cv2.findTransformECC(gray_a, gray_b, warp, cv2.MOTION_EUCLIDEAN, criteria)
        aligned = cv2.warpAffine(gray_b, warp, (gray_a.shape[1], gray_a.shape[0]), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
    except Exception:
        aligned = gray_b
    delta = aligned.astype(np.int16) - gray_a.astype(np.int16)
    canvas = np.stack([gray_a, gray_a, gray_a], axis=-1)
    removed = delta < -25
    added = delta > 25
    canvas[removed] = (40, 40, 200)
    canvas[added] = (40, 160, 40)
    Image.fromarray(canvas[:, :, ::-1]).save(dest)
    return str(dest)


def load_raster_or_pdf(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import fitz
        import numpy as np

        doc = fitz.open(str(path))
        try:
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if arr.shape[2] >= 3:
                return np.ascontiguousarray(arr[:, :, 2::-1])
            return arr
        finally:
            doc.close()
    import cv2

    return cv2.imread(str(path), cv2.IMREAD_COLOR)
