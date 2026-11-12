"""Соответствие автоисправлений числовым правилам ГОСТ, а не внутренним упрощениям."""

from __future__ import annotations

from pathlib import Path

import ezdxf

from gost_checker.checks.fonts import snap_drawing_height
from gost_checker.constants import FONT_HEIGHTS_MM
from gost_checker.engine import process_file
from gost_checker.titleblock import build_form3_block


def test_font_height_snaps_to_full_gost_2304_series():
    assert snap_drawing_height(11.0) == 10.0
    assert snap_drawing_height(6.1) == 7.0
    assert snap_drawing_height(25.0) == 28.0
    assert snap_drawing_height(10.0) == 10.0
    for height in FONT_HEIGHTS_MM:
        assert snap_drawing_height(height) == height


def test_form3_name_graph_is_one_cell():
    doc = ezdxf.new("R2013")
    build_form3_block(doc, to_mm=1.0)
    block = doc.blocks.get("SPDS_F3")
    horizontals = []
    for entity in block:
        if entity.dxftype() != "LINE":
            continue
        y1 = float(entity.dxf.start.y)
        y2 = float(entity.dxf.end.y)
        if abs(y1 - y2) > 0.05:
            continue
        x0 = min(float(entity.dxf.start.x), float(entity.dxf.end.x))
        x1 = max(float(entity.dxf.start.x), float(entity.dxf.end.x))
        horizontals.append((y1, x0, x1))
    split = [row for row in horizontals if abs(row[0] - 10.0) < 0.2 and row[1] >= 60 and row[2] <= 140]
    assert split == []
    assert any(abs(row[0] - 25.0) < 0.2 and row[1] <= 70 and row[2] >= 130 for row in horizontals)
    labels = []
    for entity in block.query("TEXT"):
        labels.append(entity.dxf.text or "")
    assert "Кол.уч." in labels
    assert "№ док." in labels


def test_valid_dimension_text_is_kept_and_radius_keeps_arrow(tmp_path):
    doc = ezdxf.new("R2013", setup=True)
    doc.header["$INSUNITS"] = 4
    style = doc.dimstyles.get("Standard")
    style.dxf.dimtxt = 5.0
    style.dxf.dimexe = 2.0
    style.dxf.dimexo = 0.5
    style.dxf.dimasz = 3.0
    msp = doc.modelspace()
    msp.add_linear_dim(base=(0, 12), p1=(0, 0), p2=(40, 0), dimstyle="Standard").render()
    msp.add_radius_dim(center=(70, 0), radius=8, angle=30, dimstyle="Standard").render()
    path = tmp_path / "dims.dxf"
    doc.saveas(str(path))
    result = process_file(path, tmp_path / "out", fix=True)
    saved = ezdxf.readfile(result.output_dxf)
    standard = saved.dimstyles.get("Standard")
    assert float(standard.dxf.dimtxt) == 5.0
    assert float(standard.dxf.dimexe) == 2.0
    assert abs(float(standard.dxf.dimexo) - 0.5) < 1e-6
    assert float(standard.dxf.dimasz) == 3.0
    linear = []
    radial = []
    for entity in saved.modelspace().query("DIMENSION"):
        names = ",".join(entity.override().get_arrow_names()).upper()
        if int(entity.dimtype) in {2, 3, 4, 5}:
            radial.append(names)
        else:
            linear.append(names)
    assert linear and all("OBLIQUE" in name or "TICK" in name or "ARCH" in name for name in linear)
    assert radial and all("OBLIQUE" not in name and "TICK" not in name for name in radial)
    blob = " ".join(check.message + " " + " ".join(check.details) for check in result.checks if check.check_id == "dimensions")
    assert "стрелк" in blob.lower()


def test_small_circle_centerline_becomes_solid(tmp_path):
    doc = ezdxf.new("R2013", setup=True)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_circle((0, 0), 4, dxfattribs={"linetype": "CENTER"})  # диаметр 8 < 12
    msp.add_circle((30, 0), 15, dxfattribs={"linetype": "CENTER"})  # диаметр 30
    path = tmp_path / "holes.dxf"
    doc.saveas(str(path))
    result = process_file(path, tmp_path / "out", fix=True)
    saved = ezdxf.readfile(result.output_dxf)
    by_radius = {round(float(entity.dxf.radius), 1): str(entity.dxf.linetype).upper() for entity in saved.modelspace().query("CIRCLE")}
    assert by_radius[4.0] == "CONTINUOUS"
    assert "CENTER" in by_radius[15.0]
    lines = next(check for check in result.checks if check.check_id == "linetypes")
    assert "12" in lines.message or any("12" in line for line in lines.details)
    assert Path(result.output_dxf).exists()
