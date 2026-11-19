"""Слои, правка шрифта и марки оборудования."""

from __future__ import annotations

import hashlib

import ezdxf
import fitz

from gost_checker.equipment import SPEC_NOTE, write_equipment
from gost_checker.fontfix import fix_fonts, round_up_height, strip_codes
from gost_checker.layerstd import PROFILE_NAME, standardize


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_round_up_and_strip():
    assert round_up_height(2.5) == 2.5
    assert round_up_height(2.6) == 3.5
    assert round_up_height(3.2) == 3.5
    assert strip_codes(r"{\fArial|b0|i0;Кухня}") == "Кухня"


def test_layers_profile_and_yaml(tmp_path):
    doc = ezdxf.new("R2010")
    doc.layers.add("Стены")
    doc.layers.add("Прочее")
    doc.modelspace().add_line((0, 0), (1000, 0), dxfattribs={"layer": "Стены"})
    doc.modelspace().add_line((0, 10), (10, 10), dxfattribs={"layer": "Прочее"})
    src = tmp_path / "layers.dxf"
    doc.saveas(str(src))
    before = _sha(src)
    preview = standardize(src, dry_run=True)
    assert preview["profile"] == PROFILE_NAME
    assert "ISO 13567" in preview["note"] and "не проверенный" in preview["note"]
    assert preview["written"] is False
    assert any(item["to"] == "SPDS_OSN" for item in preview["plan"])
    assert "Прочее" in preview["unmatched"]
    assert _sha(src) == before
    assert not (tmp_path / "out.dxf").exists()

    applied = standardize(src, tmp_path / "out.dxf", dry_run=False)
    assert applied["original_unchanged"]
    assert _sha(src) == before
    saved = ezdxf.readfile(str(tmp_path / "out.dxf"))
    layers = {str(entity.dxf.layer) for entity in saved.modelspace()}
    assert "SPDS_OSN" in layers
    assert saved.layers.get("SPDS_OSN").dxf.lineweight == 70

    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "name: цеховой\nlayers:\n  стены:\n    target: АР_Стены\n    linetype: Continuous\n    lineweight_mm: 0.35\n",
        encoding="utf-8",
    )
    custom = standardize(src, tmp_path / "custom.dxf", profile, dry_run=False)
    assert custom["profile"] == "цеховой"
    custom_doc = ezdxf.readfile(str(tmp_path / "custom.dxf"))
    assert any(entity.dxf.layer == "АР_Стены" for entity in custom_doc.modelspace())
    assert custom_doc.layers.get("АР_Стены").dxf.lineweight == 35

    vague = ezdxf.new("R2010")
    vague.layers.add("xxxстенyyy")
    vague.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "xxxстенyyy"})
    vague_path = tmp_path / "vague.dxf"
    vague.saveas(str(vague_path))
    vague_plan = standardize(vague_path, tmp_path / "vague-out.dxf", dry_run=False)
    assert vague_plan["plan"] == []
    assert vague_plan["proposals"] and vague_plan["proposals"][0]["confidence"] == "неуверенно"
    kept = ezdxf.readfile(str(tmp_path / "vague-out.dxf"))
    assert any(entity.dxf.layer == "xxxстенyyy" for entity in kept.modelspace())
    again = standardize(tmp_path / "out.dxf", tmp_path / "again.dxf", dry_run=False)
    assert again["plan"] == []

    from fastapi.testclient import TestClient

    from gost_checker.web import create_app

    client = TestClient(create_app())
    response = client.post("/api/layers", files={"file": ("layers.dxf", src.read_bytes(), "application/dxf")})
    assert response.status_code == 200
    body = response.json()
    assert body["profile"] == PROFILE_NAME
    assert body["dry_run"] is True
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="layers-form"' in page.text
    assert "/api/fonts" in page.text
    assert "/api/equipment" in page.text


def test_font_fix_modes(tmp_path):
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    doc.styles.get("Standard").dxf.font = "arial.ttf"
    msp = doc.modelspace()
    msp.add_text("А", dxfattribs={"insert": (10, 20), "height": 2.6, "halign": 0})
    mtext = msp.add_mtext("x", dxfattribs={"char_height": 3.2, "insert": (30, 40), "attachment_point": 1})
    mtext.dxf.text = r"{\fArial|b0|i0;Кухня}"
    msp.add_text("Длинная надпись " * 8, dxfattribs={"insert": (0, 0), "height": 5})
    src = tmp_path / "text.dxf"
    doc.saveas(str(src))
    before = _sha(src)

    dry = fix_fonts(src, dry_run=True, limit_mm=40)
    assert dry["written"] is False
    assert _sha(src) == before
    heights = {item["was"]: item["now"] for item in dry["changes"]}
    assert heights[2.6] == 3.5
    assert heights[3.2] == 3.5
    assert any(item["text"] == "Кухня" for item in dry["changes"])
    assert dry["overflows"]

    nearest = fix_fonts(src, mode="nearest", dry_run=True, limit_mm=1000)
    nearest_heights = {item["was"]: item["now"] for item in nearest["changes"]}
    assert nearest_heights[2.6] == 2.5

    applied = fix_fonts(src, tmp_path / "fixed.dxf", dry_run=False, limit_mm=1000)
    assert applied["original_unchanged"]
    saved = ezdxf.readfile(str(tmp_path / "fixed.dxf"))
    texts = {entity.dxf.text: entity for entity in saved.modelspace() if entity.dxftype() == "TEXT"}
    assert texts["А"].dxf.height == 3.5
    assert (texts["А"].dxf.insert.x, texts["А"].dxf.insert.y) == (10, 20)
    mtexts = [entity for entity in saved.modelspace() if entity.dxftype() == "MTEXT"]
    assert mtexts[0].dxf.text == "Кухня"
    assert (mtexts[0].dxf.insert.x, mtexts[0].dxf.insert.y) == (30, 40)
    assert mtexts[0].dxf.attachment_point == 1
    assert saved.styles.get("Standard").dxf.font == "GOST type A"

    pdf = tmp_path / "note.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Тест", fontsize=12)
    document.save(str(pdf))
    document.close()
    pdf_before = _sha(pdf)
    checked = fix_fonts(pdf, tmp_path / "should-not.pdf", dry_run=False)
    assert checked["check_only"] is True
    assert checked["written"] is False
    assert checked["warnings"]
    assert _sha(pdf) == pdf_before
    assert not (tmp_path / "should-not.pdf").exists()

    scan = tmp_path / "scan.png"
    scan.write_bytes(b"\x89PNG\r\n")
    scan_checked = fix_fonts(scan, dry_run=False)
    assert scan_checked["check_only"] is True
    assert scan_checked["written"] is False


def test_equipment_marks(tmp_path):
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    known = doc.blocks.new("РАДИАТОР_1")
    known.add_circle((0, 0), 10)
    unknown = doc.blocks.new("НЕИЗВЕСТНЫЙ_УЗЕЛ")
    unknown.add_circle((0, 0), 5)
    msp = doc.modelspace()
    msp.add_blockref("РАДИАТОР_1", (0, 0))
    msp.add_blockref("РАДИАТОР_1", (1000, 0))
    msp.add_blockref("НЕИЗВЕСТНЫЙ_УЗЕЛ", (2000, 0))
    msp.add_line((0, 0), (1000, 0))
    src = tmp_path / "eq.dxf"
    doc.saveas(str(src))
    before = _sha(src)
    result = write_equipment(src, tmp_path / "marks.dxf")
    assert result["original_unchanged"]
    assert _sha(src) == before
    assert result["known"][0]["count"] == 2
    assert result["known"][0]["name"] == "Радиатор"
    assert result["unknown"][0]["block"] == "НЕИЗВЕСТНЫЙ_УЗЕЛ"
    assert result["unknown"][0]["name"] == ""
    assert result["note"] == SPEC_NOTE
    csv_text = (tmp_path / "marks.csv").read_text(encoding="utf-8")
    assert "ГОСТ 21.110 не сверена" in csv_text
    assert "НЕИЗВЕСТНЫЙ_УЗЕЛ" in csv_text
    lengths = [row["length_m"] for row in result["geometry_rows"]]
    assert any(abs(value - 1.0) < 1e-6 for value in lengths)
    saved = ezdxf.readfile(str(tmp_path / "marks.dxf"))
    labels = [entity.dxf.text for entity in saved.modelspace() if entity.dxftype() == "TEXT"]
    assert labels.count("Р-1") == 2
