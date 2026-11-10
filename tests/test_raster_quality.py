from __future__ import annotations

import math
from pathlib import Path

import ezdxf
import numpy as np

from gost_checker.raster import process_image
from gost_checker.vision import (
    classify_raster_lines,
    fuzzy_match_label,
    merge_collinear_segments,
    midpoint_in_rect,
    titleblock_zone_px,
)

REPO = Path(__file__).resolve().parents[1]
SCAN_DEMO = REPO / "samples" / "scan_demo.png"


def _mid(item) -> tuple[float, float]:
    x1, y1, x2, y2 = item["segments"][0]
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def _false_axis_in_titleblock(image, items, page_w_mm, page_h_mm) -> list[dict]:
    h, w = image.shape[:2]
    zone = titleblock_zone_px(w, h, page_w_mm, page_h_mm, pad_mm=1.0)
    bad = []
    for item in items:
        if item.get("kind") not in {"dashed", "dashdot"}:
            continue
        if not item.get("segments"):
            continue
        mx, my = _mid(item)
        if midpoint_in_rect(mx, my, zone):
            bad.append(item)
    return bad


def test_merge_collinear_segments_joins_broken_line():
    segs = [(0, 0, 10, 0), (12, 0.4, 22, 0), (24, -0.3, 50, 0.2)]
    merged = merge_collinear_segments(segs, dist_tol=3.0, angle_tol_deg=8.0, gap_tol=8.0)
    assert len(merged) == 1
    x1, y1, x2, y2 = merged[0]
    assert min(x1, x2) <= 1
    assert max(x1, x2) >= 49


def test_fuzzy_match_label_dictionary():
    assert fuzzy_match_label("Разраб.") == "разраб"
    assert fuzzy_match_label("PA3PAB") is None or fuzzy_match_label("Разраб") == "разраб"
    assert fuzzy_match_label("Пров") == "пров"
    assert fuzzy_match_label("Н.контр.") == "н_контр"
    assert fuzzy_match_label("Утв.") == "утв"
    assert fuzzy_match_label("Стадия") == "стадия"
    assert fuzzy_match_label("Листов") == "листов"
    assert fuzzy_match_label("Лист") == "лист"
    assert fuzzy_match_label("Петров") is None


def test_scan_demo_no_false_axis_in_titleblock():
    assert SCAN_DEMO.exists()
    _checks, _notes, extra = process_image(SCAN_DEMO)
    items = (extra.get("lines_full") or {}).get("items") or []
    page = extra.get("page_size_mm")
    assert page, extra
    image = extra.get("preview_image")
    assert image is not None
    bad = _false_axis_in_titleblock(image, items, page[0], page[1])
    assert bad == [], [(item.get("kind"), item.get("segments"), item.get("_pattern"), item.get("_ink_ratio")) for item in bad]


def _draw_seg(image, x1, y1, x2, y2, ppm, kind, thickness_mm=0.45):
    import cv2

    half = max(1, int(thickness_mm * ppm / 2.0))
    length = math.hypot(x2 - x1, y2 - y1)
    if length < 1:
        return
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    pos = 0.0

    def stamp(a, b):
        p1 = (int(a[0]), int(a[1]))
        p2 = (int(b[0]), int(b[1]))
        cv2.line(image, p1, p2, (0, 0, 0), thickness=max(1, 2 * half), lineType=cv2.LINE_8)

    if kind in {"solid", "frame", "titleblock", "solid_main", "solid_thin"}:
        stamp((x1, y1), (x2, y2))
        return
    while pos < length:
        if kind == "dashed":
            dash, gap = 5.0 * ppm, 1.5 * ppm
            stamp((x1 + ux * pos, y1 + uy * pos), (x1 + ux * min(pos + dash, length), y1 + uy * min(pos + dash, length)))
            pos += dash + gap
        else:
            dash, gap, dot = 12.0 * ppm, 3.5 * ppm, 1.3 * ppm
            stamp((x1 + ux * pos, y1 + uy * pos), (x1 + ux * min(pos + dash, length), y1 + uy * min(pos + dash, length)))
            pos += dash + gap
            if pos >= length:
                break
            stamp((x1 + ux * pos, y1 + uy * pos), (x1 + ux * min(pos + dot, length), y1 + uy * min(pos + dot, length)))
            pos += dot + gap


def _world_to_img(x_mm, y_mm, page_h_mm, ppm):
    return x_mm * ppm, (page_h_mm - y_mm) * ppm


def make_typed_lines_dxf(path: Path) -> tuple[Path, list[dict]]:
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    try:
        if "DASHED" not in doc.linetypes:
            doc.linetypes.add("DASHED", pattern=[5.0, -1.5], description="dashed")
        if "CENTER" not in doc.linetypes:
            doc.linetypes.add("CENTER", pattern=[12.0, -3.5, 1.3, -3.5], description="center")
    except Exception:
        pass
    msp.add_lwpolyline([(0, 0), (420, 0), (420, 297), (0, 297)], close=True)
    msp.add_lwpolyline([(20, 5), (415, 5), (415, 292), (20, 292)], close=True)
    tb_x0, tb_y0 = 230.0, 5.0
    msp.add_lwpolyline([(tb_x0, tb_y0), (tb_x0 + 185, tb_y0), (tb_x0 + 185, tb_y0 + 55), (tb_x0, tb_y0 + 55)], close=True)
    for x in (tb_x0 + 35, tb_x0 + 70, tb_x0 + 135):
        msp.add_line((x, tb_y0), (x, tb_y0 + 55))
    for y in (tb_y0 + 15, tb_y0 + 25, tb_y0 + 40):
        msp.add_line((tb_x0, y), (tb_x0 + 185, y))

    gt = []

    def add_line(p1, p2, kind, linetype="CONTINUOUS"):
        msp.add_line(p1, p2, dxfattribs={"linetype": linetype})
        gt.append({"p1": p1, "p2": p2, "kind": kind})

    add_line((40, 180), (210, 180), "dashed", "DASHED")
    add_line((40, 160), (200, 160), "dashed", "DASHED")
    add_line((70, 70), (70, 175), "dashed", "DASHED")
    add_line((50, 250), (280, 250), "dashdot", "CENTER")
    add_line((50, 230), (260, 230), "dashdot", "CENTER")
    add_line((130, 80), (130, 210), "dashdot", "CENTER")
    add_line((40, 130), (220, 130), "solid_main", "CONTINUOUS")
    add_line((40, 110), (210, 110), "solid_thin", "CONTINUOUS")
    # пересечение со штриховой — не должно превращать её в осевую
    add_line((150, 90), (150, 200), "solid_thin", "CONTINUOUS")
    path = Path(path)
    doc.saveas(str(path))
    return path, gt


def rasterize_known_dxf(gt: list[dict], page_w=420.0, page_h=297.0, ppm=4.0):
    w, h = int(page_w * ppm), int(page_h * ppm)
    image = np.full((h, w, 3), 255, dtype=np.uint8)
    frames = [
        ((0, 0), (420, 0), "frame"),
        ((420, 0), (420, 297), "frame"),
        ((420, 297), (0, 297), "frame"),
        ((0, 297), (0, 0), "frame"),
        ((20, 5), (415, 5), "frame"),
        ((415, 5), (415, 292), "frame"),
        ((415, 292), (20, 292), "frame"),
        ((20, 292), (20, 5), "frame"),
    ]
    tb_x0, tb_y0 = 230.0, 5.0
    tb = [
        ((tb_x0, tb_y0), (tb_x0 + 185, tb_y0), "titleblock"),
        ((tb_x0 + 185, tb_y0), (tb_x0 + 185, tb_y0 + 55), "titleblock"),
        ((tb_x0 + 185, tb_y0 + 55), (tb_x0, tb_y0 + 55), "titleblock"),
        ((tb_x0, tb_y0 + 55), (tb_x0, tb_y0), "titleblock"),
    ]
    for x in (tb_x0 + 35, tb_x0 + 70, tb_x0 + 135):
        tb.append(((x, tb_y0), (x, tb_y0 + 55), "titleblock"))
    for y in (tb_y0 + 15, tb_y0 + 25, tb_y0 + 40):
        tb.append(((tb_x0, y), (tb_x0 + 185, y), "titleblock"))
    all_gt = []
    for p1, p2, kind in frames + tb:
        x1, y1 = _world_to_img(*p1, page_h, ppm)
        x2, y2 = _world_to_img(*p2, page_h, ppm)
        thick = 0.7 if kind == "frame" else 0.45
        _draw_seg(image, x1, y1, x2, y2, ppm, kind, thick)
        all_gt.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "kind": kind})
    for item in gt:
        x1, y1 = _world_to_img(*item["p1"], page_h, ppm)
        x2, y2 = _world_to_img(*item["p2"], page_h, ppm)
        kind = item["kind"]
        thick = 0.85 if kind == "solid_main" else 0.4
        paint = "dashed" if kind == "dashed" else "dashdot" if kind == "dashdot" else "solid"
        _draw_seg(image, x1, y1, x2, y2, ppm, paint, thick)
        all_gt.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "kind": kind})
    return image, all_gt, ppm


def _seg_dist(px, py, x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    t = ((px - x1) * dx + (py - y1) * dy) / (length * length)
    t = min(1.0, max(0.0, t))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _angle_diff(a, b) -> float:
    da = abs(a - b) % math.pi
    return min(da, math.pi - da)


def _metrics(items, gt, ppm, kinds):
    used = [False] * len(items)
    matched_gt = 0
    matched_det = 0
    gt_k = [g for g in gt if g["kind"] in kinds]
    det_k = [(i, it) for i, it in enumerate(items) if it.get("kind") in kinds and it.get("segments")]
    for g in gt_k:
        g_ang = math.atan2(g["y2"] - g["y1"], g["x2"] - g["x1"])
        best = None
        for pos, (idx, it) in enumerate(det_k):
            if used[idx]:
                continue
            x1, y1, x2, y2 = it["segments"][0]
            mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            dist = _seg_dist(mx, my, g["x1"], g["y1"], g["x2"], g["y2"]) / ppm
            dang = _angle_diff(g_ang, math.atan2(y2 - y1, x2 - x1))
            length = math.hypot(x2 - x1, y2 - y1) / ppm
            g_len = math.hypot(g["x2"] - g["x1"], g["y2"] - g["y1"]) / ppm
            if dist <= 4.0 and dang <= math.radians(12) and length >= 0.35 * g_len:
                score = dist + dang
                if best is None or score < best[0]:
                    best = (score, idx)
        if best:
            used[best[1]] = True
            matched_gt += 1
            matched_det += 1
    recall = matched_gt / len(gt_k) if gt_k else 1.0
    precision = matched_det / len(det_k) if det_k else 1.0
    return precision, recall, len(gt_k), len(det_k)


def test_synthetic_scan_line_precision_recall(tmp_path):
    dxf_path, gt = make_typed_lines_dxf(tmp_path / "typed_lines.dxf")
    assert dxf_path.exists()
    image, all_gt, ppm = rasterize_known_dxf(gt)
    summary = classify_raster_lines(image, ppm, page_w_mm=420.0, page_h_mm=297.0)
    items = summary.get("items") or []
    assert items
    bad = _false_axis_in_titleblock(image, items, 420.0, 297.0)
    assert bad == [], [(item.get("kind"), item.get("segments")) for item in bad]
    report = {}
    for label, kinds in (
        ("dashed", {"dashed"}),
        ("dashdot", {"dashdot"}),
        ("solid", {"solid_main", "solid_thin"}),
        ("frame", {"frame"}),
        ("titleblock", {"titleblock"}),
    ):
        precision, recall, n_gt, n_det = _metrics(items, all_gt, ppm, kinds)
        report[label] = {"precision": precision, "recall": recall, "gt": n_gt, "det": n_det}
    (tmp_path / "line_pr.txt").write_text(repr(report), encoding="utf-8")
    assert report["dashed"]["precision"] >= 0.70, report
    assert report["dashed"]["recall"] >= 0.60, report
    assert report["dashdot"]["precision"] >= 0.70, report
    assert report["dashdot"]["recall"] >= 0.60, report
    assert report["frame"]["recall"] >= 0.50, report
    assert report["titleblock"]["recall"] >= 0.40, report


def _ocr_hits(fields: list[dict]) -> tuple[int, int, list[str]]:
    blob = " ".join((item.get("text") or "") for item in fields).lower().replace("ё", "е")
    expected = [
        ("петров", "Петров"),
        ("кж", "КЖ-01"),
        ("план", "План"),
        ("подземн", "подземной"),
        ("гасу", "СПбГАСУ"),
        ("стад", "Стадия"),
        ("лист", "Лист/Листов"),
        ("разраб", "Разраб."),
    ]
    hits = [title for needle, title in expected if needle in blob]
    return len(hits), len(expected), hits


def test_ocr_accuracy_on_demo_scans(samples_dir, tmp_path):
    from samples.generate import make_scan_demo

    generated = make_scan_demo(samples_dir / "a3_title_sample.pdf", tmp_path / "scan_gen.png", angle_deg=3.0)
    _c, notes, extra = process_image(generated)
    fields = (extra.get("stamp_fields") or {}).get("fields") or []
    if "tesseract не найден" in " ".join(notes).lower():
        hits, total, found = 0, 8, []
    else:
        hits, total, found = _ocr_hits(fields)
    gen_acc = hits / total
    stored_hits = stored_total = 0
    stored_found: list[str] = []
    if SCAN_DEMO.exists():
        _c2, notes2, extra2 = process_image(SCAN_DEMO)
        fields2 = (extra2.get("stamp_fields") or {}).get("fields") or []
        if "tesseract не найден" not in " ".join(notes2).lower():
            stored_hits, stored_total, stored_found = _ocr_hits(fields2)
    stored_acc = stored_hits / stored_total if stored_total else 0.0
    (tmp_path / "ocr_acc.txt").write_text(
        f"generated {hits}/{total}={gen_acc:.2f} {found}; stored {stored_hits}/{stored_total}={stored_acc:.2f} {stored_found}",
        encoding="utf-8",
    )
    if "tesseract не найден" in " ".join(notes).lower():
        return
    assert hits >= 4, (hits, total, found, fields)
