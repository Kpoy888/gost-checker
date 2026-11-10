from __future__ import annotations

import numpy as np

from gost_checker.line_classify import classify_dxf, classify_weight, raster_pattern_kind
from gost_checker.units import doc_to_mm
from gost_checker.vision import classify_raster_lines


def test_raster_pattern_kind_requires_real_cycles():
    assert raster_pattern_kind([20.0], 0.95) is None
    assert raster_pattern_kind([12.0, -3.5, 1.2, -3.5, 12.0, -3.5, 1.2, -3.5], 0.55) == "dashdot"
    assert raster_pattern_kind([5.0, -1.5, 5.0, -1.5, 5.0, -1.5, 5.0], 0.55) == "dashed"
    # шум на сплошной: мелкие нерегулярные просветы
    assert raster_pattern_kind([8.0, -0.4, 0.8, -0.3, 9.0, -0.5, 7.0], 0.88) is None
    assert raster_pattern_kind([0.8, -0.4, 0.9, -0.3, 1.0], 0.65) is None


def test_classify_weight_labels_thick_as_main():
    assert classify_weight(2.11, 0.7) == "solid_main"
    assert classify_weight(None, 0.7) == "solid_thin"
    assert classify_weight(0.25, 0.7) == "solid_thin"


def _paint_pattern(width_mm: float, kind: str, thickness_mm: float = 0.5, px_per_mm: float = 6.0):
    w = int(width_mm * px_per_mm)
    h = int(40 * px_per_mm)
    image = np.full((h, w, 3), 255, dtype=np.uint8)
    y0 = h // 2
    half = max(1, int(thickness_mm * px_per_mm / 2.0))
    x = int(8 * px_per_mm)
    x_end = w - int(8 * px_per_mm)
    if kind == "solid":
        image[y0 - half : y0 + half + 1, x:x_end] = 0
    elif kind == "dashed":
        while x < x_end:
            dash = int(5.0 * px_per_mm)
            gap = int(1.5 * px_per_mm)
            image[y0 - half : y0 + half + 1, x : min(x + dash, x_end)] = 0
            x += dash + gap
    elif kind == "dashdot":
        while x < x_end:
            dash = int(12.0 * px_per_mm)
            gap = int(3.5 * px_per_mm)
            dot = int(1.4 * px_per_mm)
            image[y0 - half : y0 + half + 1, x : min(x + dash, x_end)] = 0
            x += dash + gap
            image[y0 - half : y0 + half + 1, x : min(x + dot, x_end)] = 0
            x += dot + gap
    return image, px_per_mm


def _majority(summary: dict) -> str:
    kinds = summary.get("kinds") or []
    assert kinds, summary
    return max(kinds, key=lambda row: row.get("length_mm") or row["count"])["kind"]


def test_synthetic_raster_line_types():
    solid, ppm = _paint_pattern(180, "solid", 0.8)
    dashed, _ = _paint_pattern(180, "dashed", 0.4)
    axis, _ = _paint_pattern(220, "dashdot", 0.4)
    assert _majority(classify_raster_lines(solid, ppm)) == "solid_main"
    assert _majority(classify_raster_lines(dashed, ppm)) == "dashed"
    assert _majority(classify_raster_lines(axis, ppm)) == "dashdot"


def test_bad_dxf_thick_polyline_and_circle(samples_dir, tmp_path):
    import ezdxf

    from gost_checker.engine import process_file

    original = ezdxf.readfile(str(samples_dir / "bad.dxf"))
    pre = classify_dxf(original, doc_to_mm(original))
    thick = [item for item in pre["items"] if item["dxftype"] == "LWPOLYLINE" and item.get("thickness_mm")]
    assert thick
    assert thick[0]["kind"] == "solid_main"
    assert any("толще нормы" in note for note in thick[0]["notes"])
    circle = next(item for item in pre["items"] if item["dxftype"] == "CIRCLE")
    assert circle["kind"] != "unknown"
    assert circle["kind"] == "solid_thin"

    result = process_file(
        samples_dir / "bad.dxf",
        tmp_path / "lw-fix",
        fix=True,
        title_meta={"обозначение": "КЖ-01", "разраб": "Иванов", "стадия": "Р", "наименование": "План"},
        assumed_format="A3",
    )
    doc = ezdxf.readfile(result.output_dxf)
    user = [entity for entity in doc.modelspace().query("LWPOLYLINE") if entity.dxf.layer == "Layer1"]
    assert user
    assert int(user[0].dxf.lineweight) == 140
    post = classify_dxf(doc, doc_to_mm(doc))
    user_cls = [item for item in post["items"] if item["dxftype"] == "LWPOLYLINE" and abs((item.get("thickness_mm") or 0) - 1.4) < 0.05]
    assert user_cls
    assert user_cls[0]["kind"] == "solid_main"
    circle_post = next(item for item in post["items"] if item["dxftype"] == "CIRCLE")
    assert circle_post["kind"] == "solid_thin"
    assert "Не определена" not in (circle_post.get("kind") or "")
