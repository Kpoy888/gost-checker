"""Устойчивый поиск внешней и внутренней рамки на растре."""

from __future__ import annotations

from typing import Optional

import numpy as np

from .constants import FRAME_LEFT_MM, FRAME_OTHER_MM
from .margin_report import measure_margins

SHEET_EDGE_MAX_MM = 2.8
SCAN_MARGIN_TOL_MM = 1.0


def _prepare_masks(image_bgr):
    import cv2

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    binary = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 35, 7
    )
    h, w = gray.shape
    kx = max(3, w // 350)
    ky = max(3, h // 350)
    closed = cv2.morphologyEx(
        binary, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (kx * 2 + 1, 1))
    )
    closed = cv2.morphologyEx(
        closed, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, ky * 2 + 1))
    )
    closed = cv2.morphologyEx(closed, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
    return gray, binary, closed


def margins_of_px_rect(x, y, rw, rh, width_px, height_px, page_w_mm, page_h_mm, inset_px: float = 0.0, tol: float = SCAN_MARGIN_TOL_MM):
    sx = width_px / max(page_w_mm, 1e-6)
    sy = height_px / max(page_h_mm, 1e-6)
    left = (x + inset_px) / sx
    top = (y + inset_px) / sy
    right = (width_px - (x + rw) + inset_px) / sx
    bottom = (height_px - (y + rh) + inset_px) / sy
    return measure_margins(left, right, top, bottom, tol=tol)


def is_sheet_edge(margins, max_mm: float = SHEET_EDGE_MAX_MM) -> bool:
    if not margins:
        return True
    values = [float(margins[key]["value"]) for key in ("left", "right", "top", "bottom")]
    return max(values) < max_mm


def inner_score(margins) -> float:
    return (
        abs(margins["left"]["value"] - FRAME_LEFT_MM)
        + abs(margins["right"]["value"] - FRAME_OTHER_MM)
        + abs(margins["top"]["value"] - FRAME_OTHER_MM)
        + abs(margins["bottom"]["value"] - FRAME_OTHER_MM)
    )


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


def _rects_from_contours(mask) -> list[tuple[int, int, int, int, float]]:
    import cv2

    h, w = mask.shape[:2]
    min_area = 0.08 * w * h
    found = []
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        area = abs(cv2.contourArea(contour))
        if area < min_area:
            continue
        peri = cv2.arcLength(contour, True)
        box = None
        for eps in (0.015, 0.02, 0.03, 0.045):
            approx = cv2.approxPolyDP(contour, eps * peri, True)
            if len(approx) == 4:
                x, y, rw, rh = cv2.boundingRect(approx)
                box = (x, y, rw, rh, area)
                break
        if box is None:
            rect = cv2.minAreaRect(contour)
            (cw, ch) = rect[1]
            if cw < 40 or ch < 40:
                continue
            rectangularity = area / max(cw * ch, 1)
            if rectangularity < 0.78:
                continue
            x, y, rw, rh = cv2.boundingRect(contour)
            box = (x, y, rw, rh, area)
        if box[2] < 40 or box[3] < 40:
            continue
        found.append(box)
    found.sort(key=lambda item: item[4], reverse=True)
    unique = []
    for rect in found:
        if any(abs(rect[0] - u[0]) < 6 and abs(rect[1] - u[1]) < 6 and abs(rect[2] - u[2]) < 6 and abs(rect[3] - u[3]) < 6 for u in unique):
            continue
        unique.append(rect)
    return unique


def _rects_from_hough(mask) -> list[tuple[int, int, int, int, float]]:
    import cv2

    h, w = mask.shape[:2]
    edges = cv2.Canny(mask, 40, 120)
    edges = cv2.bitwise_or(edges, mask)
    min_len = max(40, min(w, h) // 6)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180.0, threshold=70, minLineLength=min_len, maxLineGap=max(16, min(w, h) // 30))
    if lines is None:
        return []
    horiz = []
    vert = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        if abs(y2 - y1) <= 4 and abs(x2 - x1) >= min_len:
            horiz.append(((y1 + y2) / 2.0, min(x1, x2), max(x1, x2)))
        elif abs(x2 - x1) <= 4 and abs(y2 - y1) >= min_len:
            vert.append(((x1 + x2) / 2.0, min(y1, y2), max(y1, y2)))
    ys = _cluster([item[0] for item in horiz if item[2] - item[1] >= w * 0.35], max(3.0, h * 0.006))
    xs = _cluster([item[0] for item in vert if item[2] - item[1] >= h * 0.35], max(3.0, w * 0.006))
    found = []
    for i, x0 in enumerate(xs):
        for x1 in xs[i + 1 :]:
            for j, y0 in enumerate(ys):
                for y1 in ys[j + 1 :]:
                    rw, rh = abs(x1 - x0), abs(y1 - y0)
                    if rw < w * 0.35 or rh < h * 0.35:
                        continue
                    xa, ya = int(min(x0, x1)), int(min(y0, y1))
                    found.append((xa, ya, int(rw), int(rh), float(rw * rh)))
    found.sort(key=lambda item: item[4], reverse=True)
    return found[:40]


def _probe_expected(mask, page_w_mm: float, page_h_mm: float) -> Optional[tuple[int, int, int, int, float]]:
    h, w = mask.shape[:2]
    sx = w / max(page_w_mm, 1e-6)
    sy = h / max(page_h_mm, 1e-6)
    band_mm = 4.0

    def vert_peak(x_mm: float) -> Optional[int]:
        x0 = max(0, int((x_mm - band_mm) * sx))
        x1 = min(w, int((x_mm + band_mm) * sx) + 1)
        if x1 - x0 < 2:
            return None
        col = mask[:, x0:x1].sum(axis=0).astype(np.float64)
        if col.max() < h * 0.05 * 255:
            return None
        return x0 + int(np.argmax(col))

    def horiz_peak(y_mm: float) -> Optional[int]:
        y0 = max(0, int((y_mm - band_mm) * sy))
        y1 = min(h, int((y_mm + band_mm) * sy) + 1)
        if y1 - y0 < 2:
            return None
        row = mask[y0:y1, :].sum(axis=1).astype(np.float64)
        if row.max() < w * 0.05 * 255:
            return None
        return y0 + int(np.argmax(row))

    left = vert_peak(FRAME_LEFT_MM)
    right = vert_peak(page_w_mm - FRAME_OTHER_MM)
    top = horiz_peak(FRAME_OTHER_MM)
    bottom = horiz_peak(page_h_mm - FRAME_OTHER_MM)
    if None in (left, right, top, bottom):
        return None
    if right - left < 40 or bottom - top < 40:
        return None
    return (left, top, right - left, bottom - top, float((right - left) * (bottom - top)))


def detect_sheet_frames(image_bgr, page_w_mm: Optional[float], page_h_mm: Optional[float]) -> dict:
    """Найти внешний край листа и внутреннюю рамку. Край листа за внутреннюю рамку не выдаётся."""
    result = {"outer": None, "inner": None, "margins": None, "rects": [], "method": None}
    if image_bgr is None or page_w_mm is None or page_h_mm is None:
        return result
    h, w = image_bgr.shape[:2]
    _gray, binary, closed = _prepare_masks(image_bgr)
    candidates = []
    candidates.extend(_rects_from_contours(closed))
    candidates.extend(_rects_from_hough(closed))
    for mask, tag in ((closed, "expected-probe"), (binary, "expected-probe-binary")):
        probed = _probe_expected(mask, page_w_mm, page_h_mm)
        if probed:
            candidates.append(probed)
            result["method"] = tag
    result["rects"] = candidates

    scored_inner = []
    largest = None
    for item in candidates:
        x, y, rw, rh = item[:4]
        margins = margins_of_px_rect(x, y, rw, rh, w, h, page_w_mm, page_h_mm)
        if largest is None or rw * rh > largest[0]:
            largest = (rw * rh, (x, y, rw, rh))
        if is_sheet_edge(margins):
            continue
        scored_inner.append((inner_score(margins), margins, (x, y, rw, rh)))
    if largest:
        result["outer"] = largest[1]
    if scored_inner:
        scored_inner.sort(key=lambda row: row[0])
        score, margins, rect = scored_inner[0]
        # принимаем, если похоже на 20/5/5/5 хотя бы грубо (сумма отклонений < 16 мм)
        if score <= 16:
            result["inner"] = rect
            result["margins"] = margins
            if result["method"] is None:
                result["method"] = "nested-rect"
    return result
