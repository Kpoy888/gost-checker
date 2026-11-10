"""Растр: выравнивание листа, оценка масштаба, толщины линий, OCR граф штампа."""

from __future__ import annotations

import difflib
import math
from typing import Optional

import numpy as np

from .constants import FRAME_LEFT_MM, FRAME_OTHER_MM, TITLE_BLOCK_H_MM, TITLE_BLOCK_W_MM
from .frame_geometry import match_aspect
from .line_classify import (
    classify_weight,
    infer_s_main,
    raster_pattern_kind,
    summarize_items,
)
from .stamp_cells import STAMP_FIELDS


def deskew_image(image_bgr):
    import cv2

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 160)
    min_len = max(40, min(image_bgr.shape[:2]) // 8)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180.0, threshold=70, minLineLength=min_len, maxLineGap=16)
    if lines is None:
        return image_bgr, 0.0
    angles = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
        while ang > 45:
            ang -= 90
        while ang < -45:
            ang += 90
        if abs(ang) <= 25:
            angles.append(ang)
    if not angles:
        return image_bgr, 0.0
    angle = float(np.median(np.array(angles)))
    if abs(angle) < 0.35:
        return image_bgr, 0.0
    h, w = image_bgr.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    matrix[0, 2] += (nw - w) / 2.0
    matrix[1, 2] += (nh - h) / 2.0
    rotated = cv2.warpAffine(image_bgr, matrix, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
    return rotated, angle



def detect_rects_cv(image_bgr) -> list[tuple[int, int, int, int, float]]:
    import cv2

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 40, 130)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, kernel, iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    h, w = gray.shape[:2]
    min_area = 0.08 * w * h
    rects = []
    for contour in contours:
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) != 4:
            continue
        area = abs(cv2.contourArea(approx))
        if area < min_area:
            continue
        x, y, rw, rh = cv2.boundingRect(approx)
        if rw < 40 or rh < 40:
            continue
        rects.append((x, y, rw, rh, area, approx.reshape(4, 2).copy()))
    rects.sort(key=lambda item: item[4], reverse=True)
    unique = []
    for rect in rects:
        if any(abs(rect[0] - u[0]) < 8 and abs(rect[1] - u[1]) < 8 and abs(rect[2] - u[2]) < 8 and abs(rect[3] - u[3]) < 8 for u in unique):
            continue
        unique.append(rect)
    return unique



def order_quad_points(points):
    import numpy as np

    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1).reshape(4)
    return np.array([pts[np.argmin(sums)], pts[np.argmin(diffs)], pts[np.argmax(sums)], pts[np.argmax(diffs)]], dtype=np.float32)


def warp_quad_to_rect(image_bgr, points, width_px: int, height_px: int):
    import cv2
    import numpy as np

    src = order_quad_points(points)
    dst = np.array(
        [[0.0, 0.0], [width_px - 1.0, 0.0], [width_px - 1.0, height_px - 1.0], [0.0, height_px - 1.0]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image_bgr, matrix, (int(width_px), int(height_px)), borderValue=(255, 255, 255))


def choose_sheet_rect(rects, image_shape):
    """Выбрать внешний контур листа, если есть вложенная пара рамок."""
    if not rects:
        return None
    ordered = sorted(rects, key=lambda item: item[2] * item[3], reverse=True)
    for i, outer in enumerate(ordered):
        ox, oy, ow, oh = outer[:4]
        for inner in ordered[i + 1 :]:
            ix, iy, iw, ih = inner[:4]
            if ox <= ix + 4 and oy <= iy + 4 and ox + ow >= ix + iw - 4 and oy + oh >= iy + ih - 4:
                if iw * ih > 0.45 * ow * oh:
                    return outer
    return ordered[0]


def contour_looks_like_inner(x, y, rw, rh, img_w, img_h) -> bool:
    """Похоже ли, что найденная рамка — внутренняя (поле подшивки шире остальных, с учётом полей кадра)."""
    left = float(x)
    right = float(img_w - (x + rw))
    top = float(y)
    bottom = float(img_h - (y + rh))
    if rw * rh > 0.92 * img_w * img_h:
        return False
    others = [right, top, bottom]
    mean_other = sum(others) / 3.0
    if mean_other < 1:
        return False
    # на скане с полями: left ≈ pad+20мм, right ≈ pad+5мм → left заметно больше right
    if left > mean_other * 1.35 and left > right + 6:
        return True
    return False


def expand_inner_quad(points, page_w_mm: float, page_h_mm: float):
    import numpy as np

    tl, tr, br, bl = order_quad_points(points)
    def _unit(vec):
        norm = float(np.linalg.norm(vec))
        if norm < 1e-6:
            return vec
        return vec / norm

    right_d = _unit(tr - tl)
    down_d = _unit(bl - tl)
    sx = float(np.linalg.norm(tr - tl)) / max(page_w_mm - FRAME_LEFT_MM - FRAME_OTHER_MM, 1)
    sy = float(np.linalg.norm(bl - tl)) / max(page_h_mm - 2 * FRAME_OTHER_MM, 1)
    new_tl = tl - right_d * (FRAME_LEFT_MM * sx) - down_d * (FRAME_OTHER_MM * sy)
    new_tr = tr + right_d * (FRAME_OTHER_MM * sx) - down_d * (FRAME_OTHER_MM * sy)
    new_br = br + right_d * (FRAME_OTHER_MM * sx) + down_d * (FRAME_OTHER_MM * sy)
    new_bl = bl - right_d * (FRAME_LEFT_MM * sx) + down_d * (FRAME_OTHER_MM * sy)
    return np.array([new_tl, new_tr, new_br, new_bl], dtype=np.float32)


def infer_sheet_mm(image_bgr, declared_w: Optional[float], declared_h: Optional[float], frame_rect=None):
    """Вернуть (width_mm, height_mm, source, px_per_mm_x, px_per_mm_y)."""
    h, w = image_bgr.shape[:2]
    if declared_w and declared_h and declared_w > 20 and declared_h > 20:
        return declared_w, declared_h, "page", w / declared_w, h / declared_h
    target = frame_rect
    if target is None:
        rects = detect_rects_cv(image_bgr)
        chosen = choose_sheet_rect(rects, image_bgr.shape)
        target = chosen[:4] if chosen is not None else (0, 0, w, h)
    x, y, rw, rh = target[:4]
    ratio = rw / max(rh, 1)
    hit = match_aspect(ratio, tol=0.05)
    if hit:
        name, orient, fw, fh = hit
        if contour_looks_like_inner(x, y, rw, rh, w, h):
            inner_w, inner_h = fw - FRAME_LEFT_MM - FRAME_OTHER_MM, fh - 2 * FRAME_OTHER_MM
            return fw, fh, f"inner-aspect:{name}", rw / max(inner_w, 1), rh / max(inner_h, 1)
        if rw * rh > 0.55 * w * h:
            return fw, fh, f"aspect:{name}", rw / fw, rh / fh
        inner_w, inner_h = fw - FRAME_LEFT_MM - FRAME_OTHER_MM, fh - 2 * FRAME_OTHER_MM
        return fw, fh, f"inner-aspect:{name}", rw / max(inner_w, 1), rh / max(inner_h, 1)
    return None, None, "unknown", None, None


def _skeleton(binary):
    import cv2

    try:
        if hasattr(cv2, "ximgproc") and hasattr(cv2.ximgproc, "thinning"):
            return cv2.ximgproc.thinning(binary)
    except Exception:
        pass
    skeleton = np.zeros_like(binary)
    element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    img = binary.copy()
    guard = 0
    while guard < 400:
        guard += 1
        eroded = cv2.erode(img, element)
        unused = cv2.dilate(eroded, element)
        temp = cv2.subtract(img, unused)
        skeleton = cv2.bitwise_or(skeleton, temp)
        img = eroded
        if cv2.countNonZero(img) == 0:
            break
    return skeleton



def titleblock_zone_px(width_px: int, height_px: int, page_w_mm: Optional[float], page_h_mm: Optional[float], pad_mm: float = 2.5):
    """Пиксельный прямоугольник основной надписи (изображение = лист, Y вниз)."""
    if not page_w_mm or not page_h_mm or page_w_mm < 20 or page_h_mm < 20:
        return None
    sx, sy = width_px / page_w_mm, height_px / page_h_mm
    x1 = (page_w_mm - FRAME_OTHER_MM + pad_mm) * sx
    y1 = (page_h_mm - FRAME_OTHER_MM + pad_mm) * sy
    x0 = (page_w_mm - FRAME_OTHER_MM - TITLE_BLOCK_W_MM - pad_mm) * sx
    y0 = (page_h_mm - FRAME_OTHER_MM - TITLE_BLOCK_H_MM - pad_mm) * sy
    return (x0, y0, x1, y1)


def midpoint_in_rect(x: float, y: float, rect) -> bool:
    if not rect:
        return False
    x0, y0, x1, y1 = rect
    return x0 <= x <= x1 and y0 <= y <= y1


def segment_role(x1, y1, x2, y2, width_px, height_px, page_w_mm, page_h_mm) -> Optional[str]:
    """Рамка листа или сетка штампа — не штриховая/осевая."""
    if not page_w_mm or not page_h_mm:
        return None
    sx, sy = width_px / page_w_mm, height_px / page_h_mm
    zone = titleblock_zone_px(width_px, height_px, page_w_mm, page_h_mm)
    xs = np.linspace(x1, x2, 9)
    ys = np.linspace(y1, y2, 9)
    if zone:
        x0, y0, x1z, y1z = zone
        inside = float(np.mean((xs >= x0) & (xs <= x1z) & (ys >= y0) & (ys <= y1z)))
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        if inside >= 0.45 or midpoint_in_rect(mx, my, zone):
            return "titleblock"
    length = math.hypot(x2 - x1, y2 - y1)
    if length < 25:
        return None
    is_horiz = abs(y2 - y1) <= max(3.0, 0.12 * abs(x2 - x1))
    is_vert = abs(x2 - x1) <= max(3.0, 0.12 * abs(y2 - y1))
    tol_v = 2.6 * sx
    tol_h = 2.6 * sy
    min_len = 35 * max(sx, sy)

    def near_x(x, target):
        return abs(x - target) <= tol_v

    def near_y(y, target):
        return abs(y - target) <= tol_h

    if is_vert and length >= min_len * 0.45:
        for xmm in (0.0, FRAME_LEFT_MM, page_w_mm - FRAME_OTHER_MM, page_w_mm):
            if near_x(x1, xmm * sx) and near_x(x2, xmm * sx):
                return "frame"
    if is_horiz and length >= min_len * 0.45:
        for ymm in (0.0, FRAME_OTHER_MM, page_h_mm - FRAME_OTHER_MM, page_h_mm):
            if near_y(y1, ymm * sy) and near_y(y2, ymm * sy):
                return "frame"
    return None


def merge_collinear_segments(segments, dist_tol: float = 3.0, angle_tol_deg: float = 5.0, gap_tol: float = 18.0):
    """Склеить сонаправленные отрезки Хафа перед разбором штрихов."""
    segs = [tuple(float(v) for v in item[:4]) for item in segments]
    if len(segs) <= 1:
        return segs

    def angle(seg):
        ang = math.atan2(seg[3] - seg[1], seg[2] - seg[0]) % math.pi
        return ang if ang >= 0 else ang + math.pi

    def unit(seg):
        dx, dy = seg[2] - seg[0], seg[3] - seg[1]
        length = math.hypot(dx, dy) or 1.0
        return dx / length, dy / length

    def dist_point(px, py, seg):
        ux, uy = unit(seg)
        return abs((px - seg[0]) * (-uy) + (py - seg[1]) * ux)

    def proj_range(seg, ux, uy):
        a = seg[0] * ux + seg[1] * uy
        b = seg[2] * ux + seg[3] * uy
        return (min(a, b), max(a, b))

    def compatible(a, b):
        da = abs(angle(a) - angle(b))
        da = min(da, math.pi - da)
        if da > math.radians(angle_tol_deg):
            return False
        if min(dist_point(b[0], b[1], a), dist_point(b[2], b[3], a), dist_point(a[0], a[1], b), dist_point(a[2], a[3], b)) > dist_tol:
            return False
        ux, uy = unit(a)
        ra, rb = proj_range(a, ux, uy), proj_range(b, ux, uy)
        if ra[1] < rb[0]:
            gap = rb[0] - ra[1]
        elif rb[1] < ra[0]:
            gap = ra[0] - rb[1]
        else:
            gap = 0.0
        return gap <= gap_tol

    def combine(a, b):
        ux, uy = unit(a)
        pts = [(a[0], a[1]), (a[2], a[3]), (b[0], b[1]), (b[2], b[3])]
        projs = [pt[0] * ux + pt[1] * uy for pt in pts]
        lo, hi = pts[int(np.argmin(projs))], pts[int(np.argmax(projs))]
        return (lo[0], lo[1], hi[0], hi[1])

    current = segs
    for _ in range(8):
        used = [False] * len(current)
        nxt = []
        merged_any = False
        for i, a in enumerate(current):
            if used[i]:
                continue
            cur = a
            used[i] = True
            for j, b in enumerate(current):
                if used[j]:
                    continue
                if compatible(cur, b):
                    cur = combine(cur, b)
                    used[j] = True
                    merged_any = True
            nxt.append(cur)
        current = nxt
        if not merged_any:
            break
    return current


def _suppress_crossings(ink, binary, xs, ys, nx, ny, ppm: float):
    """Убрать ложные «точки» осевой в местах пересечения со сплошными линиями."""
    n = len(xs)
    if n == 0 or ink.size == 0:
        return ink
    h, w = binary.shape[:2]
    extent = max(5, int(3.6 * ppm))
    offs = np.arange(-extent, extent + 1)
    perp_hits = np.zeros(n, dtype=np.float64)
    for k in offs:
        xi = np.clip(np.rint(xs + k * nx).astype(int), 0, w - 1)
        yi = np.clip(np.rint(ys + k * ny).astype(int), 0, h - 1)
        perp_hits += binary[yi, xi] > 0
    perp_ratio = perp_hits / max(len(offs), 1)
    t = max(3, int(1.6 * ppm))
    ink_f = ink.astype(np.float64)
    window = np.convolve(ink_f, np.ones(2 * t + 1), mode="same") - ink_f
    tang_ratio = window / float(2 * t)
    crossing = ink & (perp_ratio >= 0.42) & (tang_ratio <= 0.38)
    cleaned = ink.copy()
    cleaned[crossing] = False
    return cleaned


def classify_raster_lines(
    image_bgr,
    px_per_mm: Optional[float],
    page_w_mm: Optional[float] = None,
    page_h_mm: Optional[float] = None,
) -> dict:
    import cv2

    scale = 1.0
    work = image_bgr
    h, w = work.shape[:2]
    from .runtime_limits import max_image_side

    max_side = max_image_side()
    if max(h, w) > max_side:
        scale = max_side / max(h, w)
        work = cv2.resize(work, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        h, w = work.shape[:2]
        if px_per_mm:
            px_per_mm = px_per_mm * scale
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    binary = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 8)
    sharp = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    dist = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    edges = cv2.Canny(gray, 40, 140)
    min_len = max(24, min(h, w) // 18)
    max_gap = max(8, int(4.8 * px_per_mm)) if px_per_mm else 8
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180.0, threshold=50, minLineLength=min_len, maxLineGap=max_gap)
    items = []
    thicknesses = []
    ppm = px_per_mm or 1.0
    if lines is None:
        return summarize_items([], 0.7) | {"items": []}
    gap_tol = max(16.0, 8.0 * ppm) if px_per_mm else 18.0
    merged_segs = merge_collinear_segments(lines.reshape(-1, 4), dist_tol=3.0, angle_tol_deg=5.0, gap_tol=gap_tol)
    for x1, y1, x2, y2 in merged_segs:
        length_px = math.hypot(x2 - x1, y2 - y1)
        n = max(int(length_px), 4)
        xs = np.linspace(x1, x2, n)
        ys = np.linspace(y1, y2, n)
        if length_px > 1:
            nx = -(y2 - y1) / length_px
            ny = (x2 - x1) / length_px
        else:
            nx, ny = 0.0, 1.0
        samples = []
        ink_flags = []
        for ox, oy in ((0.0, 0.0), (nx, ny), (-nx, -ny), (2 * nx, 2 * ny), (-2 * nx, -2 * ny)):
            xi = np.clip(np.rint(xs + ox).astype(int), 0, w - 1)
            yi = np.clip(np.rint(ys + oy).astype(int), 0, h - 1)
            samples.append(dist[yi, xi])
            ink_flags.append(sharp[yi, xi] > 0)
        samples = np.max(np.stack(samples, axis=0), axis=0)
        ink = np.any(np.stack(ink_flags, axis=0), axis=0)
        ink = _suppress_crossings(ink, sharp, xs, ys, nx, ny, ppm)
        thick_px = float(np.median(samples) * 2.0)
        thick_mm = thick_px / ppm if px_per_mm else None
        if thick_mm:
            thicknesses.append(thick_mm)
        ink_ratio = float(np.mean(ink)) if ink.size else 1.0
        runs = []
        current = bool(ink[0]) if ink.size else True
        length = 1
        for flag in ink[1:]:
            if flag == current:
                length += 1
            else:
                runs.append((current, length / ppm if px_per_mm else length))
                current = flag
                length = 1
        if ink.size:
            runs.append((current, length / ppm if px_per_mm else length))
        merged = []
        for is_ink, run_mm in runs:
            if merged and run_mm < 0.45:
                prev_ink, prev_mm = merged[-1]
                if is_ink == prev_ink or not is_ink:
                    merged[-1] = (prev_ink, prev_mm + run_mm)
                else:
                    merged.append((is_ink, run_mm))
            else:
                merged.append((is_ink, run_mm))
        coalesced = []
        for is_ink, run_mm in merged:
            if coalesced and coalesced[-1][0] == is_ink:
                coalesced[-1] = (is_ink, coalesced[-1][1] + run_mm)
            else:
                coalesced.append((is_ink, run_mm))
        merged = coalesced
        pattern = []
        if px_per_mm and len(merged) >= 3:
            for is_ink, run_mm in merged:
                if run_mm < 0.3:
                    continue
                pattern.append(run_mm if is_ink else -run_mm)
        role = segment_role(x1, y1, x2, y2, w, h, page_w_mm, page_h_mm)
        if role:
            kind = role
        else:
            kind = raster_pattern_kind(pattern, ink_ratio) if pattern else None
        items.append(
            {
                "kind": kind,
                "thickness_mm": thick_mm,
                "length_mm": length_px / ppm if px_per_mm else length_px,
                "linetype": "raster",
                "notes": [],
                "ok": True,
                "segments": [(float(x1) / scale, float(y1) / scale, float(x2) / scale, float(y2) / scale)],
                "space": "raster",
                "dxftype": "HOUGH",
                "_pattern": pattern,
                "_ink_ratio": ink_ratio,
                "_role": role,
            }
        )
    s_main = infer_s_main(thicknesses)
    for item in items:
        if not item["kind"]:
            item["kind"] = classify_weight(item["thickness_mm"], s_main)
    summary = summarize_items(items, s_main)
    summary["items"] = items
    return summary


_HEADER_WORDS = {
    "разраб", "разраб.", "пров", "пров.", "н.контр", "н.контр.", "н контр", "утв", "утв.",
    "стадия", "лист", "листов", "изм", "кол", "подп", "дата", "обозначение",
}


_LOOKALIKES = str.maketrans({
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х",
    "y": "у", "k": "к", "b": "в", "m": "м", "h": "н", "t": "т",
})
_LABEL_CANON = (
    ("разраб", ("разраб", "разработал", "разраб.")),
    ("пров", ("пров", "проверил", "пров.")),
    ("н_контр", ("н.контр", "н контр", "нконтр", "нормоконтроль")),
    ("утв", ("утв", "утвердил", "утв.")),
    ("стадия", ("стадия",)),
    ("лист", ("лист",)),
    ("листов", ("листов",)),
)


def _canon_token(token: str) -> str:
    raw = (token or "").lower().replace("ё", "е").translate(_LOOKALIKES)
    return "".join(ch for ch in raw if ch.isalnum() or ch == ".")


def fuzzy_match_label(token: str) -> Optional[str]:
    tok = _canon_token(token)
    if len(tok) < 3:
        return None
    best_id, best = None, 0.0
    for field_id, variants in _LABEL_CANON:
        for variant in variants:
            vn = _canon_token(variant)
            if not vn:
                continue
            if tok == vn or tok.rstrip(".") == vn.rstrip("."):
                return field_id
            if tok.startswith(vn) and len(tok) - len(vn) <= 2:
                ratio = 0.93
            elif vn.startswith(tok) and len(tok) >= 4:
                ratio = 0.90
            else:
                ratio = difflib.SequenceMatcher(None, tok, vn).ratio()
                if abs(len(tok) - len(vn)) >= 2 or ratio < 0.88:
                    continue
            if ratio > best:
                best, best_id = ratio, field_id
    return best_id if best >= 0.80 else None


def _strip_field_label(text: str, field: dict) -> str:
    raw = " ".join((text or "").split())
    if not raw:
        return ""
    parts = raw.split(None, 1)
    first = parts[0].rstrip(".:;-—")
    matched = fuzzy_match_label(first)
    if matched == field["id"] or matched is not None and first.lower().startswith(str(field.get("label") or "").lower()[:4]):
        raw = parts[1] if len(parts) > 1 else ""
    else:
        low = raw.lower().replace("ё", "е")
        labels = [str(field.get("label") or "").lower().rstrip("."), field["id"].replace("_", "."), field["id"].replace("_", " ")]
        for lab in labels:
            lab = lab.strip().lower()
            if lab and low.startswith(lab):
                raw = raw[len(lab):].lstrip(" .:;-—")
                break
    compact = raw.lower().replace("ё", "е").replace(".", "").replace(" ", "")
    if compact in {w.replace(".", "").replace(" ", "") for w in _HEADER_WORDS}:
        return ""
    if fuzzy_match_label(raw) and len(raw.split()) <= 1:
        return ""
    return raw


def _preprocess_cell(crop, trim_border: bool = True):
    import cv2

    if crop.ndim == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    else:
        gray = crop.copy()
    h, w = gray.shape[:2]
    # срезать рамку ячейки
    if trim_border:
        m = max(2, min(h, w) // 8)
        if h > 2 * m + 6 and w > 2 * m + 6:
            gray = gray[m:h - m, m:w - m]
    if min(gray.shape[:2]) >= 8:
        gray = cv2.medianBlur(gray, 3)
    scale = 3.0 if min(gray.shape[:2]) < 48 else 2.5
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    return gray


def _call_ocr(ocr_fn, image, psm: int, prepared: bool = True):
    try:
        result = ocr_fn(image, psm=psm, prepared=prepared, with_conf=True)
        if isinstance(result, tuple) and len(result) >= 3:
            return result[0] or "", result[1], float(result[2] or 0)
        if isinstance(result, tuple):
            return result[0] or "", result[1], _ocr_quality(result[0] or "")
    except TypeError:
        pass
    try:
        text, err = ocr_fn(image, psm=psm)
        return text or "", err, _ocr_quality(text or "")
    except TypeError:
        text, err = ocr_fn(image)
        return text or "", err, _ocr_quality(text or "")


_OCR_DICT = (
    "разраб", "пров", "контр", "утв", "стадия", "лист", "листов",
    "петров", "план", "гасу", "кж",
)


def _dict_bonus(text: str) -> float:
    low = (text or "").lower().replace("ё", "е")
    return float(sum(10.0 for tok in _OCR_DICT if tok in low))


def _ocr_crop_best(crop, ocr_fn, trim_border: bool = True) -> str:
    try:
        prepared = _preprocess_cell(crop, trim_border=trim_border)
    except Exception:
        prepared = crop
    candidates = [(prepared, True)]
    if not trim_border:
        candidates.append((crop, False))
    psms = (6, 4, 11, 3) if not trim_border else (7, 8, 6, 13)
    best_text, best_score = "", -1.0
    for image, prepared_flag in candidates:
        for psm in psms:
            try:
                text, _err, conf = _call_ocr(ocr_fn, image, psm, prepared=prepared_flag)
            except Exception:
                continue
            text = " ".join((text or "").split())
            if not text:
                continue
            score = float(conf) + 0.25 * _ocr_quality(text) + _dict_bonus(text)
            if score > best_score:
                best_score, best_text = score, text
            if trim_border and float(conf) >= 68 and _ocr_quality(text) >= 6:
                break
    return best_text


def _grid_lines(roi_gray):
    import cv2

    h, w = roi_gray.shape[:2]
    edges = cv2.Canny(roi_gray, 40, 140)
    min_h, min_v = max(20, w // 6), max(16, h // 6)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180.0, threshold=40, minLineLength=min(min_h, min_v), maxLineGap=8)
    xs, ys = [0, w - 1], [0, h - 1]
    if lines is None:
        return sorted(set(xs)), sorted(set(ys))
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        if abs(y2 - y1) <= 3 and abs(x2 - x1) >= min_h:
            ys.append(int((y1 + y2) / 2))
        elif abs(x2 - x1) <= 3 and abs(y2 - y1) >= min_v:
            xs.append(int((x1 + x2) / 2))
    def cluster(vals, tol):
        vals = sorted(vals)
        out, group = [], [vals[0]]
        for v in vals[1:]:
            if abs(v - group[-1]) <= tol:
                group.append(v)
            else:
                out.append(int(sum(group) / len(group)))
                group = [v]
        out.append(int(sum(group) / len(group)))
        return out
    return cluster(xs, 5), cluster(ys, 5)


def ocr_stamp_cells(image_bgr, page_w_mm: Optional[float], page_h_mm: Optional[float], ocr_fn) -> list[dict]:
    if not page_w_mm or not page_h_mm:
        return []
    h, w = image_bgr.shape[:2]
    sx, sy = w / page_w_mm, h / page_h_mm
    x1 = w - FRAME_OTHER_MM * sx
    y1 = h - FRAME_OTHER_MM * sy
    x0 = x1 - TITLE_BLOCK_W_MM * sx
    y0 = y1 - TITLE_BLOCK_H_MM * sy
    x0i, y0i, x1i, y1i = [int(v) for v in (x0, y0, x1, y1)]
    stamp = image_bgr[max(y0i, 0): min(y1i, h), max(x0i, 0): min(x1i, w)]
    grid_cells = []
    if stamp.size and stamp.shape[0] > 20 and stamp.shape[1] > 20:
        import cv2

        gray = cv2.cvtColor(stamp, cv2.COLOR_BGR2GRAY) if stamp.ndim == 3 else stamp
        xs, ys = _grid_lines(gray)
        if len(xs) >= 3 and len(ys) >= 3:
            sh, sw = gray.shape[:2]
            for i, xa in enumerate(xs[:-1]):
                for j, ya in enumerate(ys[:-1]):
                    xb, yb = xs[i + 1], ys[j + 1]
                    if xb - xa < 8 or yb - ya < 6:
                        continue
                    # центр ячейки в мм штампа, Y вверх
                    cx_mm = ((xa + xb) / 2.0) / max(sw, 1) * TITLE_BLOCK_W_MM
                    cy_from_top = ((ya + yb) / 2.0) / max(sh, 1) * TITLE_BLOCK_H_MM
                    cy_mm = TITLE_BLOCK_H_MM - cy_from_top
                    grid_cells.append(((xa, ya, xb, yb), cx_mm, cy_mm))

    results = []
    for field in STAMP_FIELDS:
        bx0, by0, bx1, by1 = field["box"]
        crop = None
        matched = None
        for (xa, ya, xb, yb), cx_mm, cy_mm in grid_cells:
            if bx0 <= cx_mm <= bx1 and by0 <= cy_mm <= by1:
                matched = (xa, ya, xb, yb)
                break
        if matched and stamp.size:
            xa, ya, xb, yb = matched
            pad = 1
            crop = stamp[max(ya + pad, 0): max(yb - pad, ya + pad + 1), max(xa + pad, 0): max(xb - pad, xa + pad + 1)]
        if crop is None or crop.size == 0:
            px0 = int(x0 + bx0 * sx)
            px1 = int(x0 + bx1 * sx)
            py1 = int(y0 + (TITLE_BLOCK_H_MM - by0) * sy)
            py0 = int(y0 + (TITLE_BLOCK_H_MM - by1) * sy)
            pad = 2
            crop = image_bgr[max(py0 - pad, 0): min(py1 + pad, h), max(px0 - pad, 0): min(px1 + pad, w)]
        text = ""
        if crop is not None and crop.size and crop.shape[0] > 4 and crop.shape[1] > 4:
            text = _ocr_crop_best(crop, ocr_fn)
            text = _strip_field_label(text, field)
        text = _clean_ocr_value(text)
        results.append(
            {
                "id": field["id"],
                "label": field["label"],
                "required": field["required"],
                "text": text,
                "empty": len(text) < 2,
            }
        )
    if stamp.size:
        try:
            full = _ocr_crop_best(stamp, ocr_fn, trim_border=False)
        except Exception:
            try:
                full, _err = ocr_fn(stamp)
            except Exception:
                full = ""
        if full:
            _fill_fields_from_blob(results, full)
    return results


def _clean_ocr_value(text: str) -> str:
    import re

    raw = " ".join((text or "").split())
    if not raw:
        return ""
    compact = raw.replace(" ", "")
    if len(compact) < 2:
        if compact.isdigit() or compact.upper() in {"Р", "П", "Э", "P", "R", "A", "А", "С", "C"}:
            return raw
        return ""
    if re.fullmatch(r"[A-ZА-Я]{1,6}[-–]?\d{1,6}", compact, flags=re.IGNORECASE):
        return raw
    junk = set("-—_~|=+*«»“”•·,;:<>[](){}/\\.'`\" ")
    if all(ch in junk for ch in raw):
        return ""
    letters = sum(ch.isalnum() for ch in raw)
    if letters == 0:
        return ""
    if letters / max(len(compact), 1) < 0.45 and letters < 8:
        return ""
    return raw


def _ocr_quality(text: str) -> float:
    raw = text or ""
    if not raw:
        return -1.0
    alnum = sum(ch.isalnum() for ch in raw)
    return alnum / max(len(raw), 1) * min(len(raw), 24)


def _fill_fields_from_blob(results: list[dict], blob: str) -> None:
    import re

    text = " ".join((blob or "").split())
    if not text:
        return
    by_id = {item["id"]: item for item in results}

    def take(field_id: str, value: Optional[str]) -> None:
        item = by_id.get(field_id)
        if not item or not value:
            return
        value = _clean_ocr_value(value)
        if not value:
            return
        structured = field_id in {"лист", "листов", "стадия", "обозначение", "разраб", "организация"}
        better = item["empty"] or _ocr_quality(value) > _ocr_quality(item["text"]) + 0.05
        if structured and value:
            if fuzzy_match_label(item["text"]) or len(item["text"]) > 24:
                better = True
            if field_id in {"лист", "листов"} and value.isdigit():
                better = True
            if field_id == "стадия" and len(value) <= 2:
                better = True
            if field_id == "обозначение" and any(ch.isdigit() for ch in value):
                better = True
        if better:
            item["text"] = value
            item["empty"] = len(value) < 2

    low = text.lower().replace("ё", "е")
    patterns = [
        ("разраб", r"разраб\.?\s*([A-Za-zА-Яа-яЁё-]{3,})"),
        ("пров", r"пров\.?\s*([A-Za-zА-Яа-яЁё-]{3,})"),
        ("н_контр", r"н\.?\s*контр\.?\s*([A-Za-zА-Яа-яЁё-]{3,})"),
        ("утв", r"утв\.?\s*([A-Za-zА-Яа-яЁё-]{3,})"),
        ("стадия", r"стад[иi]я\s*([А-ЯA-ZРP])"),
        ("лист", r"лист(?!ов)\s*(\d+)"),
        ("листов", r"листов\s*(\d+)"),
        ("обозначение", r"\b([A-ZА-Я]{1,5}[-–]?\d{1,5})\b"),
        ("наименование", r"(план(?:\s+[A-Za-zА-Яа-яЁё0-9./-]+){0,8})"),
        ("организация", r"(сп[бbо0]гасу|спбгас[уy]|ooo\s+\S+|ооо\s+\S+)"),
    ]
    for field_id, pattern in patterns:
        source = text if field_id in {"обозначение", "наименование"} else low
        match = re.search(pattern, source, flags=re.IGNORECASE)
        if match:
            take(field_id, match.group(1))
    tokens = text.split()
    for index, token in enumerate(tokens):
        attached = re.match(r"(.+?)(\d+)$", token)
        if attached and fuzzy_match_label(attached.group(1)):
            take(fuzzy_match_label(attached.group(1)), attached.group(2))
            continue
        glued = re.match(r"(стад[иi]я)\s*([A-ZА-ЯРP])$", token, flags=re.IGNORECASE)
        if glued:
            take("стадия", glued.group(2))
            continue
        field_id = fuzzy_match_label(token)
        if not field_id or index + 1 >= len(tokens):
            continue
        nxt = tokens[index + 1].strip(" .:;-—")
        if fuzzy_match_label(nxt):
            continue
        if field_id in {"стадия", "лист", "листов"}:
            take(field_id, nxt)
        elif field_id in {"разраб", "пров", "н_контр", "утв"} and len(nxt) >= 3:
            if re.search(r"\d", nxt) or nxt.lower().startswith("план"):
                continue
            take(field_id, nxt)
