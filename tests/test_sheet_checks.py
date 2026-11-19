"""Место под размер, противоречия размеров и расстояния электроустановочных изделий."""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest
from fastapi.testclient import TestClient


def _dim(msp, p1, p2, base, text=None):
    dim = msp.add_linear_dim(base=base, p1=p1, p2=p2)
    dim.render()
    entity = list(msp.query("DIMENSION"))[-1]
    if text is not None:
        entity.dxf.text = text
    return entity


def _save(doc, path: Path, units: int = 4) -> Path:
    doc.header["$INSUNITS"] = units
    doc.saveas(str(path))
    return path


def test_clearance_gap_text_stamp_and_parallel(tmp_path):
    from gost_checker.clearance import analyze_doc, suggest
    from gost_checker.sheetspace import open_doc

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (80, 0))
    msp.add_lwpolyline([(0, -20), (200, -20), (200, 80), (0, 80)], close=True, dxfattribs={"layer": "SPDS_FRAME"})
    _dim(msp, (0, 0), (40, 0), (0, 5))
    tight = tmp_path / "tight.dxf"
    _save(doc, tight)
    messages = " ".join(item["message"] for item in analyze_doc(open_doc(tight)))
    assert "10" in messages

    wide = ezdxf.new("R2010")
    wide.modelspace().add_line((0, 0), (80, 0))
    wide.modelspace().add_lwpolyline([(0, -20), (220, -20), (220, 100), (0, 100)], close=True, dxfattribs={"layer": "SPDS_FRAME"})
    _dim(wide.modelspace(), (0, 0), (40, 0), (0, 12))
    _dim(wide.modelspace(), (0, 0), (40, 0), (0, 16))
    path = tmp_path / "parallel.dxf"
    _save(wide, path)
    parallel = " ".join(item["message"] for item in analyze_doc(open_doc(path)))
    assert "Параллельные" in parallel

    ok = ezdxf.new("R2010")
    ok.modelspace().add_line((0, 0), (80, 0))
    ok.modelspace().add_lwpolyline([(0, -20), (220, -20), (220, 100), (0, 100)], close=True, dxfattribs={"layer": "SPDS_FRAME"})
    _dim(ok.modelspace(), (0, 0), (40, 0), (0, 12))
    _dim(ok.modelspace(), (50, 0), (90, 0), (50, 20))
    clean = " ".join(item["message"] for item in analyze_doc(open_doc(_save(ok, tmp_path / "ok.dxf"))))
    assert "Параллельные" not in clean
    assert "10" not in clean

    stamp = ezdxf.new("R2010")
    sm = stamp.modelspace()
    sm.add_lwpolyline([(0, 0), (200, 0), (200, 120), (0, 120)], close=True, dxfattribs={"layer": "SPDS_FRAME"})
    sm.add_lwpolyline([(15, 5), (200, 5), (200, 60), (15, 60)], close=True, dxfattribs={"layer": "SPDS_STAMP"})
    _dim(sm, (20, 10), (60, 10), (20, 16))
    stamped = " ".join(item["message"] for item in analyze_doc(open_doc(_save(stamp, tmp_path / "stamp.dxf"))))
    assert "основную надпись" in stamped

    outside = ezdxf.new("R2010")
    om = outside.modelspace()
    om.add_lwpolyline([(0, 0), (50, 0), (50, 40), (0, 40)], close=True, dxfattribs={"layer": "SPDS_FRAME"})
    _dim(om, (80, 80), (100, 80), (80, 95))
    assert "рамку" in " ".join(item["message"] for item in analyze_doc(open_doc(_save(outside, tmp_path / "out.dxf"))))

    narrow = ezdxf.new("R2010")
    _dim(narrow.modelspace(), (0, 0), (2, 0), (0, 12), text="слишком длинный текст")
    assert "шире промежутка" in " ".join(item["message"] for item in analyze_doc(open_doc(_save(narrow, tmp_path / "narrow.dxf"))))

    blocked = ezdxf.new("R2010")
    blocked.modelspace().add_line((0, 0), (80, 0))
    blocked.modelspace().add_line((0, 10), (80, 10))
    placed = suggest(open_doc(_save(blocked, tmp_path / "blocked.dxf")), (0, 0), (40, 0), "10")
    assert placed.get("alternatives")
    assert abs(placed["alternatives"][0]["paper_mm"]) >= 10


def test_clearance_scale_units_and_api(tmp_path):
    from gost_checker.clearance import analyze_doc
    from gost_checker.sheetspace import open_doc, scale_denom, text_height_paper_mm
    from gost_checker.web import create_app

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    psp = doc.layouts.new("Лист1")
    psp.page_setup(size=(420, 297), margins=(0, 0, 0, 0), units="mm")
    doc.header["$DIMSCALE"] = 100
    doc.header["$DIMTXT"] = 0.035
    _dim(msp, (0, 0), (1, 0), (0, 0.12))
    psp.add_viewport(center=(210, 150), size=(200, 100), view_center_point=(0.5, 0), view_height=10)
    path = tmp_path / "m.dxf"
    doc.saveas(str(path))
    loaded = open_doc(path)
    assert scale_denom(loaded) == pytest.approx(0.1, rel=0.01) or scale_denom(loaded) > 0
    height = text_height_paper_mm(list(loaded.modelspace().query("DIMENSION"))[-1], loaded, 1000.0, scale_denom(loaded))
    assert height > 0
    assert isinstance(analyze_doc(loaded), list)

    client = TestClient(create_app())
    raw = path.read_bytes()
    response = client.post(
        "/api/clearance",
        files={"file": ("m.dxf", raw, "application/dxf")},
        data={"x1": "0", "y1": "0", "x2": "1", "y2": "0", "text": "1000"},
    )
    assert response.status_code == 200
    assert "fits" in response.json() or "message" in response.json()


def test_dimension_conflicts(tmp_path):
    from gost_checker.dimconflict import analyze_doc
    from gost_checker.sheetspace import open_doc

    same = ezdxf.new("R2010")
    msp = same.modelspace()
    _dim(msp, (0, 0), (100, 0), (0, 20), text="100")
    _dim(msp, (0, 0), (100, 0), (0, 40), text="90")
    found = analyze_doc(open_doc(_save(same, tmp_path / "same.dxf")))
    assert any("одними и теми же" in item["message"] for item in found)

    agreed = ezdxf.new("R2010")
    _dim(agreed.modelspace(), (0, 0), (100, 0), (0, 20), text="100")
    _dim(agreed.modelspace(), (0, 0), (100, 0), (0, 40), text="100")
    assert not any("одними и теми же" in item["message"] for item in analyze_doc(open_doc(_save(agreed, tmp_path / "agreed.dxf"))))

    marked = ezdxf.new("R2010")
    mm = marked.modelspace()
    _dim(mm, (0, 0), (50, 0), (0, 15), text="ОК-1 50")
    _dim(mm, (0, 30), (80, 30), (0, 45), text="ОК-1 80")
    assert any("Марка" in item["message"] for item in analyze_doc(open_doc(_save(marked, tmp_path / "mark.dxf"))))

    rounded = ezdxf.new("R2010")
    rounded.header["$DIMDEC"] = 0
    rounded.header["$DIMLFAC"] = 1
    _dim(rounded.modelspace(), (0, 0), (10.4, 0), (0, 8), text="10")
    msgs = analyze_doc(open_doc(_save(rounded, tmp_path / "round.dxf")))
    assert not any("не совпадает" in item["message"] for item in msgs)

    mismatch = ezdxf.new("R2010")
    mismatch.header["$DIMLFAC"] = 1
    _dim(mismatch.modelspace(), (0, 0), (10, 0), (0, 8), text="12")
    assert any("не совпадает" in item["message"] for item in analyze_doc(open_doc(_save(mismatch, tmp_path / "bad.dxf"))))

    scaled = ezdxf.new("R2010")
    scaled.header["$DIMLFAC"] = 0.1
    _dim(scaled.modelspace(), (0, 0), (10, 0), (0, 8), text="1")
    assert not any("не совпадает" in item["message"] for item in analyze_doc(open_doc(_save(scaled, tmp_path / "lfac.dxf", units=6))))

    chain = ezdxf.new("R2010")
    cm = chain.modelspace()
    _dim(cm, (0, 0), (100, 0), (0, 10), text="100")
    _dim(cm, (100, 0), (200, 0), (100, 10), text="100")
    _dim(cm, (0, 0), (200, 0), (0, 25), text="250")
    assert any("цепочки" in item["message"] for item in analyze_doc(open_doc(_save(chain, tmp_path / "chain.dxf"))))

    sheets = ezdxf.new("R2010")
    _dim(sheets.modelspace(), (0, 0), (30, 0), (0, 8), text="30")
    layout = sheets.layouts.new("Лист2")
    _dim(layout, (0, 0), (30, 0), (0, 12), text="40")
    assert any("одними и теми же" in item["message"] for item in analyze_doc(open_doc(_save(sheets, tmp_path / "sheets.dxf"))))


def test_electrical_distances(tmp_path):
    from gost_checker.electrical import analyze_doc, load_rules
    from gost_checker.sheetspace import open_doc
    from gost_checker.web import create_app

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    block = doc.blocks.new("РОЗЕТКА")
    block.add_lwpolyline([(0, 0), (80, 0), (80, 80), (0, 80)], close=True)
    block.add_attdef("ОТМ", insert=(0, 0), height=2.5)
    msp = doc.modelspace()
    ref = msp.add_blockref("РОЗЕТКА", (0, 0), dxfattribs={"layer": "ЭО", "rotation": 0})
    ref.add_auto_attribs({"ОТМ": "1.5"})
    msp.add_blockref("РОЗЕТКА", (0, 0), dxfattribs={"layer": "ЭО", "rotation": 90, "xscale": 1, "yscale": 1})
    msp.add_line((130, 0), (130, 200), dxfattribs={"layer": "В1"})
    msp.add_blockref("НЕИЗВЕСТНО", (400, 0))
    path = tmp_path / "plan.dxf"
    doc.saveas(str(path))
    rules = {"rules": [{"fixture": "розетка", "target": "труба", "min_mm": 100}]}
    data = analyze_doc(open_doc(path), rules)
    assert data["violations"]
    assert any("плановая" in note for note in data["notes"])
    assert any(item["name"] == "НЕИЗВЕСТНО" for item in data["unknown"])

    far = ezdxf.new("R2010")
    far.header["$INSUNITS"] = 4
    far.blocks.new("РОЗЕТКА").add_lwpolyline([(0, 0), (80, 0), (80, 80), (0, 80)], close=True)
    far.modelspace().add_blockref("РОЗЕТКА", (0, 0), dxfattribs={"layer": "ЭО"})
    far.modelspace().add_line((180, 0), (180, 100), dxfattribs={"layer": "В1"})
    exact = analyze_doc(open_doc(_save(far, tmp_path / "exact.dxf")), rules)
    assert not exact["violations"]

    further = ezdxf.new("R2010")
    further.blocks.new("РОЗЕТКА").add_lwpolyline([(0, 0), (80, 0), (80, 80), (0, 80)], close=True)
    further.modelspace().add_blockref("РОЗЕТКА", (0, 0), dxfattribs={"layer": "ЭО"})
    further.modelspace().add_line((250, 0), (250, 100), dxfattribs={"layer": "В1"})
    assert not analyze_doc(open_doc(_save(further, tmp_path / "far.dxf")), rules)["violations"]

    meters = ezdxf.new("R2010")
    meters.header["$INSUNITS"] = 6
    meters.blocks.new("РОЗЕТКА").add_lwpolyline([(0, 0), (0.08, 0), (0.08, 0.08), (0, 0.08)], close=True)
    meters.modelspace().add_blockref("РОЗЕТКА", (0, 0), dxfattribs={"layer": "ЭО"})
    meters.modelspace().add_line((0.13, 0), (0.13, 0.2), dxfattribs={"layer": "В1"})
    assert analyze_doc(open_doc(_save(meters, tmp_path / "meters.dxf", units=6)), rules)["violations"]

    with pytest.raises(ValueError):
        load_rules({"rules": [{"fixture": "розетка", "target": "труба", "min_mm": -5}]})

    client = TestClient(create_app())
    response = client.post(
        "/api/gaps",
        files={"file": ("plan.dxf", path.read_bytes(), "application/dxf")},
        data={"rules": '{"rules":[{"fixture":"розетка","target":"труба","min_mm":100}]}'},
    )
    assert response.status_code == 200
    assert response.json()["violations"]
    bad = client.post(
        "/api/gaps",
        files={"file": ("plan.dxf", path.read_bytes(), "application/dxf")},
        data={"rules": '{"rules":[{"min_mm":-1}]}'},
    )
    assert bad.status_code == 400


def test_ocr_skips_without_tesseract_binary(monkeypatch, samples_dir, tmp_path):
    import shutil

    from tests.test_raster_quality import test_ocr_accuracy_on_demo_scans

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(pytest.skip.Exception):
        test_ocr_accuracy_on_demo_scans(samples_dir, tmp_path)
