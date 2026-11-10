"""Поиск рамки листа: полилинии, отдельные отрезки, блоки, слегка повёрнутые контуры."""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional

from ezdxf.math import Matrix44, Vec3

from .constants import ADDITIONAL_FORMATS, FORMAT_TOLERANCE_MM, FRAME_LEFT_MM, FRAME_OTHER_MM, SHEET_FORMATS
from .models import Rect
from .units import mm_to_du

MAX_SEGMENTS = 25000


def all_formats() -> dict[str, tuple[float, float]]:
    data = dict(SHEET_FORMATS)
    data.update(ADDITIONAL_FORMATS)
    return data


def format_size(name: str, orientation: str) -> tuple[float, float]:
    key = (name or "A3").upper()
    pair = all_formats().get(key, SHEET_FORMATS["A3"])
    short, long_ = sorted((pair[0], pair[1]))
    if key == "A4":
        if orientation == "landscape":
            return long_, short
        return short, long_
    if orientation == "portrait":
        return short, long_
    return long_, short


def inner_size(sheet_w: float, sheet_h: float) -> tuple[float, float]:
    return (sheet_w - FRAME_LEFT_MM - FRAME_OTHER_MM, sheet_h - 2 * FRAME_OTHER_MM)


def match_format(width_mm: float, height_mm: float, tol: float = FORMAT_TOLERANCE_MM):
    candidates = []
    for name, (a, b) in all_formats().items():
        for fw, fh, orient in ((a, b, "portrait"), (b, a, "landscape")):
            if abs(width_mm - fw) <= tol and abs(height_mm - fh) <= tol:
                candidates.append((name, orient, "outer", abs(width_mm - fw) + abs(height_mm - fh)))
            iw, ih = inner_size(fw, fh)
            if abs(width_mm - iw) <= tol and abs(height_mm - ih) <= tol:
                candidates.append((name, orient, "inner", abs(width_mm - iw) + abs(height_mm - ih)))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[3])
    name, orient, kind, _ = candidates[0]
    return name, orient, kind


def _aspect_rank(name: str, orient: str) -> int:
    if name == "A3" and orient == "landscape":
        return 0
    if name == "A4" and orient == "portrait":
        return 0
    if name == "A3" and orient == "portrait":
        return 1
    if name == "A4" and orient == "landscape":
        return 1
    base = {"A2": 3, "A1": 4, "A0": 5, "A5": 6}
    return base.get(name, 20)


def match_aspect(ratio: float, tol: float = 0.045):
    """Подобрать формат по отношению сторон. Ряд А имеет одно √2,
    поэтому без известного DPI предпочитаем А3 альбом / А4 портрет."""
    best = None
    iso_a = {"A0", "A1", "A2", "A3", "A4", "A5"}
    for name, (a, b) in all_formats().items():
        for fw, fh, orient in ((a, b, "portrait"), (b, a, "landscape")):
            target = fw / max(fh, 1e-9)
            err = abs(target - ratio) / max(target, 1e-9)
            if err > tol:
                continue
            if name in iso_a:
                key = (0.0, _aspect_rank(name, orient))
            else:
                key = (round(err, 5), 10 + _aspect_rank(name, orient))
            if best is None or key < (best[0], best[1]):
                best = (*key, name, orient, fw, fh)
    if not best:
        return None
    return best[2], best[3], best[4], best[5]


def _transform_xy(x: float, y: float, matrix: Optional[Matrix44]) -> tuple[float, float]:
    if matrix is None:
        return float(x), float(y)
    point = matrix.transform(Vec3(float(x), float(y), 0))
    return float(point.x), float(point.y)


def entity_segments(entity, matrix: Optional[Matrix44] = None) -> list[tuple[float, float, float, float]]:
    segs: list[tuple[float, float, float, float]] = []
    dtype = entity.dxftype()
    try:
        if dtype == "LINE":
            x1, y1 = _transform_xy(entity.dxf.start.x, entity.dxf.start.y, matrix)
            x2, y2 = _transform_xy(entity.dxf.end.x, entity.dxf.end.y, matrix)
            segs.append((x1, y1, x2, y2))
        elif dtype == "LWPOLYLINE":
            pts = [(_transform_xy(p[0], p[1], matrix)) for p in entity.get_points("xy")]
            if getattr(entity, "closed", False) and pts:
                pts = list(pts) + [pts[0]]
            elif len(pts) >= 4 and pts[0] != pts[-1]:
                # незамкнутая рамка из четырёх вершин
                if math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-6:
                    pass
                elif len(pts) == 4:
                    pts = list(pts) + [pts[0]]
            for i in range(len(pts) - 1):
                segs.append((pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]))
        elif dtype == "POLYLINE":
            pts = [
                _transform_xy(v.dxf.location.x, v.dxf.location.y, matrix) for v in entity.vertices
            ]
            if getattr(entity, "is_closed", False) and pts:
                pts = list(pts) + [pts[0]]
            elif len(pts) == 4:
                pts = list(pts) + [pts[0]]
            for i in range(len(pts) - 1):
                segs.append((pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]))
        elif dtype in {"SPLINE", "ELLIPSE", "ARC"}:
            try:
                points = list(entity.flattening(distance=2.5))
            except Exception:
                points = []
            pts = [_transform_xy(getattr(pt, "x", pt[0]), getattr(pt, "y", pt[1]), matrix) for pt in points]
            for i in range(len(pts) - 1):
                segs.append((pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]))
    except Exception:
        return []
    return segs


def iter_layout_entities(doc: Any, layout) -> Iterable[tuple[Any, Optional[Matrix44]]]:
    count = 0

    def walk(container, matrix: Optional[Matrix44], depth: int) -> Iterable[tuple[Any, Optional[Matrix44]]]:
        nonlocal count
        try:
            children = list(container)
        except Exception:
            return
        for entity in children:
            yield entity, matrix
            count += 1
            if count > MAX_SEGMENTS:
                return
            if entity.dxftype() != "INSERT" or depth >= 4:
                continue
            try:
                block = doc.blocks.get(entity.dxf.name)
                child_matrix = entity.matrix44()
                if matrix is not None:
                    child_matrix = matrix @ child_matrix
            except Exception:
                continue
            if block is None:
                continue
            yield from walk(block, child_matrix, depth + 1)
            if count > MAX_SEGMENTS:
                return

    yield from walk(layout, None, 0)


def _axis_aligned_rect(pts: list[tuple[float, float]], tol: float) -> Optional[tuple[float, float, float, float]]:
    if len(pts) < 4:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmax - xmin < 20 or ymax - ymin < 20:
        return None
    corners = {(xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax)}
    matched = 0
    for x, y in pts:
        if any(abs(x - cx) <= tol and abs(y - cy) <= tol for cx, cy in corners):
            matched += 1
    if matched < 4:
        return None
    return xmin, ymin, xmax, ymax


def _rotate(x: float, y: float, ang: float) -> tuple[float, float]:
    c, s = math.cos(ang), math.sin(ang)
    return (x * c - y * s, x * s + y * c)


def _cluster(values: list[float], tol: float) -> list[float]:
    if not values:
        return []
    values = sorted(values)
    groups = [[values[0]]]
    for value in values[1:]:
        if abs(value - groups[-1][-1]) <= tol:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def _rects_from_segments(
    segments: list[tuple[float, float, float, float]],
    to_mm: float,
    space: str,
) -> list[Rect]:
    found: list[Rect] = []
    min_len = mm_to_du(80, to_mm)
    line_tol = mm_to_du(0.9, to_mm)

    def consider(xmin, ymin, xmax, ymax) -> None:
        if xmax - xmin < mm_to_du(40, to_mm) or ymax - ymin < mm_to_du(40, to_mm):
            return
        found.append(Rect(xmin, ymin, xmax, ymax, space=space))

    # чуть повёрнутая рамка: выравниваем по доминирующему углу длинных отрезков
    angles = []
    for x1, y1, x2, y2 in segments:
        length = math.hypot(x2 - x1, y2 - y1)
        if length < min_len:
            continue
        ang = math.atan2(y2 - y1, x2 - x1)
        # к ближайшей оси, диапазон [-pi/4, pi/4]
        folded = (ang + math.pi / 2) % math.pi - math.pi / 2
        if folded > math.pi / 4:
            folded -= math.pi / 2
        elif folded < -math.pi / 4:
            folded += math.pi / 2
        angles.append(folded)
    rot = float(sorted(angles)[len(angles) // 2]) if angles else 0.0
    use_rot = abs(rot) > math.radians(1.5)

    aligned: list[tuple[float, float, float, float]] = []
    for x1, y1, x2, y2 in segments:
        if use_rot:
            a = _rotate(x1, y1, -rot)
            b = _rotate(x2, y2, -rot)
            aligned.append((a[0], a[1], b[0], b[1]))
        else:
            aligned.append((x1, y1, x2, y2))

    horiz: list[tuple[float, float, float]] = []
    vert: list[tuple[float, float, float]] = []
    for x1, y1, x2, y2 in aligned:
        if abs(y1 - y2) <= line_tol and abs(x1 - x2) > min_len:
            horiz.append(((y1 + y2) / 2.0, min(x1, x2), max(x1, x2)))
        elif abs(x1 - x2) <= line_tol and abs(y1 - y2) > min_len:
            vert.append(((x1 + x2) / 2.0, min(y1, y2), max(y1, y2)))

    ys = _cluster([h[0] for h in horiz], mm_to_du(1.2, to_mm))
    xs = _cluster([v[0] for v in vert], mm_to_du(1.2, to_mm))
    # также прямоугольники по парам длинных параллельных
    for i, y_a in enumerate(ys):
        for y_b in ys[i + 1 :]:
            if abs(y_a - y_b) < mm_to_du(40, to_mm):
                continue
            for j, x_a in enumerate(xs):
                for x_b in xs[j + 1 :]:
                    if abs(x_a - x_b) < mm_to_du(40, to_mm):
                        continue
                    consider(min(x_a, x_b), min(y_a, y_b), max(x_a, x_b), max(y_a, y_b))

    if use_rot:
        # координаты уже в системе, выровненной по рамке: так поля 20/5/5/5
        # считаются вдоль сторон рамки, а не по экранной AABB повёрнутого контура.
        return found
    return found


def _iter_layouts(doc: Any):
    yield "Model", doc.modelspace()
    for layout in doc.layouts:
        if layout.name.lower() == "model":
            continue
        yield layout.name, layout


def collect_rectangles(doc: Any, to_mm: float) -> list[Rect]:
    tol = mm_to_du(1.5, to_mm)
    found: list[Rect] = []

    def consider(xmin, ymin, xmax, ymax, space: str) -> None:
        found.append(Rect(xmin, ymin, xmax, ymax, space=space))

    for space, layout in _iter_layouts(doc):
        segments: list[tuple[float, float, float, float]] = []
        for entity, matrix in iter_layout_entities(doc, layout):
            dtype = entity.dxftype()
            if dtype == "LWPOLYLINE":
                try:
                    pts = [_transform_xy(p[0], p[1], matrix) for p in entity.get_points("xy")]
                except Exception:
                    pts = []
                if len(pts) >= 4:
                    box = _axis_aligned_rect(pts[:8], tol)
                    if box:
                        consider(*box, space)
            elif dtype == "POLYLINE":
                try:
                    pts = [
                        _transform_xy(v.dxf.location.x, v.dxf.location.y, matrix)
                        for v in entity.vertices
                    ]
                except Exception:
                    pts = []
                if len(pts) >= 4:
                    box = _axis_aligned_rect(pts[:8], tol)
                    if box:
                        consider(*box, space)
            segments.extend(entity_segments(entity, matrix))
            if len(segments) > MAX_SEGMENTS:
                break
        found.extend(_rects_from_segments(segments, to_mm, space))

    # убрать почти дубликаты
    unique: list[Rect] = []
    merge = mm_to_du(1.5, to_mm)
    for rect in found:
        if any(
            abs(rect.xmin - other.xmin) <= merge
            and abs(rect.ymin - other.ymin) <= merge
            and abs(rect.xmax - other.xmax) <= merge
            and abs(rect.ymax - other.ymax) <= merge
            for other in unique
        ):
            continue
        unique.append(rect)
    return unique
