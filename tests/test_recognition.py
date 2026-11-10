from __future__ import annotations

from pathlib import Path

import ezdxf

from gost_checker.checks import ALL_CHECKS
from gost_checker.constants import FONT_HEIGHTS_PRIMARY_MM
from gost_checker.engine import process_file
from gost_checker.frame_geometry import match_format


def test_plugins_include_lines():
    ids = {check.id for check in ALL_CHECKS}
    assert "lines" in ids


def test_good_margins_pass(samples_dir, tmp_path):
    result = process_file(samples_dir / "good.dxf", tmp_path / "good-margins", fix=False)
    table = result.extra.get("margins_table") or []
    assert table, result.extra
    by_side = {row["side"]: row for row in table}
    assert abs(by_side["слева"]["value"] - 20) <= 0.5
    assert abs(by_side["справа"]["value"] - 5) <= 0.5
    assert abs(by_side["сверху"]["value"] - 5) <= 0.5
    assert abs(by_side["снизу"]["value"] - 5) <= 0.5
    assert all(row["ok"] for row in table)
    frame = next(check for check in result.checks if check.check_id == "sheet_frame")
    assert frame.severity == "ok"


def test_frame_from_separate_lines(tmp_path):
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()

    def box(x0, y0, x1, y1):
        msp.add_line((x0, y0), (x1, y0))
        msp.add_line((x1, y0), (x1, y1))
        msp.add_line((x1, y1), (x0, y1))
        msp.add_line((x0, y1), (x0, y0))

    box(0, 0, 420, 297)
    box(20, 5, 415, 292)
    path = tmp_path / "line_frame.dxf"
    doc.saveas(str(path))
    result = process_file(path, tmp_path / "line-frame-out", fix=False)
    frame = next(check for check in result.checks if check.check_id == "sheet_frame")
    blob = frame.message + " " + " ".join(frame.details)
    assert "A3" in blob
    margins = result.extra.get("margins") or frame.extra.get("margins")
    assert margins
    assert abs(margins["left"]["value"] - 20) <= 0.5
    assert abs(margins["right"]["value"] - 5) <= 0.5
    assert frame.severity == "ok"


def test_primechanie_snaps_to_gost_row(samples_dir, tmp_path):
    result = process_file(
        samples_dir / "bad.dxf",
        tmp_path / "note-height",
        fix=True,
        title_meta={"обозначение": "КЖ-01", "разраб": "Иванов", "стадия": "Р"},
        assumed_format="A3",
    )
    doc = ezdxf.readfile(result.output_dxf)
    notes = [entity for entity in doc.modelspace().query("TEXT") if "Примечание" in (entity.dxf.text or "")]
    assert notes
    height = float(notes[0].dxf.height)
    assert height <= 7.05
    nearest = min(FONT_HEIGHTS_PRIMARY_MM, key=lambda item: abs(item - height))
    assert abs(height - nearest) < 0.15


def test_mtext_inline_height_rewritten(tmp_path):
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_mtext(r"{\H11;Крупный}", dxfattribs={"char_height": 5.0, "insert": (30, 40)})
    path = tmp_path / "mtext.dxf"
    doc.saveas(str(path))
    result = process_file(path, tmp_path / "mtext-out", fix=True)
    fixed = ezdxf.readfile(result.output_dxf)
    entity = next(iter(fixed.modelspace().query("MTEXT")))
    assert abs(float(entity.dxf.char_height) - 5.0) < 0.2
    raw = str(entity.dxf.text or "").replace(" ", "")
    assert "\\H7" in raw or "\\H7.0" in raw


def test_line_overlay_created(samples_dir, tmp_path):
    result = process_file(samples_dir / "good.dxf", tmp_path / "overlay", fix=False)
    lines = next(check for check in result.checks if check.check_id == "lines")
    assert lines.extra.get("total", 0) >= 1
    assert result.render_overlay and Path(result.render_overlay).exists()
    kinds = {row["kind"] for row in (result.extra.get("lines") or {}).get("kinds") or []}
    assert kinds


def test_a3x3_format_match():
    hit = match_format(891, 420)
    assert hit is not None
    assert hit[0] == "A3x3"


def test_aspect_prefers_a3_over_a0():
    from gost_checker.frame_geometry import match_aspect

    hit = match_aspect(420 / 297, tol=0.05)
    assert hit is not None
    assert hit[0] == "A3"
    assert hit[1] == "landscape"
    assert match_aspect(1667 / 1179, tol=0.05)[0] == "A3"
    portrait = match_aspect(210 / 297, tol=0.05)
    assert portrait[0] == "A4"
    assert portrait[1] == "portrait"


def test_empty_drawing_is_graceful(tmp_path):
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 4
    path = tmp_path / "empty.dxf"
    doc.saveas(str(path))
    result = process_file(path, tmp_path / "empty-out", fix=False)
    assert any("не содержит объектов" in note for note in result.notes)
    assert result.report_html and Path(result.report_html).exists()
    assert result.kind in {"dxf", "error"}


def test_empty_required_stamp_cells(tmp_path):
    from gost_checker.checks.sheet_frame import add_sheet_frame
    from gost_checker.titleblock import insert_form3, title_block_insert_point

    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 4
    _outer, inner = add_sheet_frame(doc, 1.0)
    point = title_block_insert_point(inner.xmin, inner.ymin, inner.xmax, 1.0)
    insert_form3(doc, point, {}, to_mm=1.0)
    path = tmp_path / "empty_cells.dxf"
    doc.saveas(str(path))
    result = process_file(path, tmp_path / "empty-cells-out", fix=False)
    title = next(check for check in result.checks if check.check_id == "title_block")
    blob = title.message + " " + " ".join(title.details)
    assert title.severity == "warning"
    assert "обязательн" in blob.lower()


def test_scan_demo_recognition(samples_dir, tmp_path):
    from samples.generate import make_scan_demo

    src = samples_dir / "a3_title_sample.pdf"
    scan = make_scan_demo(src, tmp_path / "scan_demo.png", angle_deg=3.0)
    assert scan.exists()
    result = process_file(scan, tmp_path / "scan-out", fix=False)
    assert result.kind == "raster"
    notes = " ".join(result.notes).lower()
    blob = " ".join(check.message + " " + " ".join(check.details) for check in result.checks)
    assert "перекос" in notes or "выровнен" in notes or "A3" in blob
    assert any(check.check_id == "lines" for check in result.checks)
    if result.render_overlay:
        assert Path(result.render_overlay).exists()


def test_corrupt_and_huge_files_report_error(tmp_path):
    broken = tmp_path / "broken.dxf"
    broken.write_text("это не DXF", encoding="utf-8")
    result = process_file(broken, tmp_path / "broken-out", fix=False)
    assert result.kind == "error" or any(check.severity == "error" for check in result.checks)
    # не создаём 80 МБ на диске: проверяем сам предел через пустой маркер + прямую константу
    from gost_checker.engine import MAX_INPUT_BYTES

    assert MAX_INPUT_BYTES == 80 * 1024 * 1024
    empty = tmp_path / "zero.dxf"
    empty.write_bytes(b"")
    empty_result = process_file(empty, tmp_path / "zero-out", fix=False)
    assert any("пуст" in check.message.lower() for check in empty_result.checks)
