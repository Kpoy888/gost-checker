"""Выноски по кружкам и массовое обновление штампов."""

from __future__ import annotations

import hashlib
import json
import zipfile

import ezdxf
import fitz
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gost_checker.leaders import assign_numbers, write_leaders


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@given(
    st.lists(
        st.tuples(
            st.floats(min_value=0, max_value=200, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0, max_value=200, allow_nan=False, allow_infinity=False),
        ),
        min_size=1,
        max_size=6,
        unique_by=lambda item: (round(item[0], 3), round(item[1], 3)),
    )
)
@settings(max_examples=20)
def test_leader_numbers_stable(points):
    first = assign_numbers([{"x": x, "y": y, "number": None} for x, y in points])
    second = assign_numbers([{"x": x, "y": y, "number": None} for x, y in reversed(points)])
    assert [(round(item["x"], 3), item["number"]) for item in first] == [(round(item["x"], 3), item["number"]) for item in second]


def test_leaders_from_markup(tmp_path):
    doc = ezdxf.new("R2010")
    doc.layers.add("ОШИБКИ")
    doc.modelspace().add_circle((10, 10), 2, dxfattribs={"layer": "ОШИБКИ"})
    doc.modelspace().add_text("2", dxfattribs={"insert": (10, 10), "height": 2.5, "layer": "ОШИБКИ"})
    doc.modelspace().add_circle((40, 10), 2, dxfattribs={"layer": "ПРИМЕЧАНИЯ_МЕТКИ"})
    src = tmp_path / "marks.dxf"
    doc.saveas(str(src))
    before = _sha(src)
    result = write_leaders(src, tmp_path / "leaders.dxf", notes="2 — нет размера")
    assert result["original_unchanged"] and _sha(src) == before
    numbers = {item["number"] for item in result["leaders"]}
    assert 2 in numbers and len(numbers) == 2
    saved = ezdxf.readfile(str(tmp_path / "leaders.dxf"))
    assert any(entity.dxf.layer == "АВТО_ВЫНОСКИ" for entity in saved.modelspace())
    assert any("нет размера" in str(entity.dxf.text or "") for entity in saved.modelspace().query("TEXT"))

    pdf = tmp_path / "note.pdf"
    document = fitz.open()
    page = document.new_page()
    annot = page.add_circle_annot(fitz.Rect(20, 20, 40, 40))
    annot.set_info(content="1 — шрифт")
    annot.update()
    document.save(str(pdf))
    document.close()
    pdf_before = _sha(pdf)
    checked = write_leaders(pdf, tmp_path / "nope.dxf")
    assert checked["check_only"] and checked["written"] is False
    assert _sha(pdf) == pdf_before
    assert not (tmp_path / "nope.dxf").exists()
    from fastapi.testclient import TestClient

    from gost_checker.web import create_app

    client = TestClient(create_app())
    response = client.post("/api/leaders", files={"file": ("marks.dxf", src.read_bytes(), "application/dxf")})
    assert response.status_code == 200
    assert response.json()["leaders"]
    page = client.get("/")
    assert 'id="leaders-form"' in page.text and 'id="stamps-form"' in page.text


def _sheet(path, cipher="OLD-1"):
    from gost_checker.checks.sheet_frame import add_sheet_frame
    from gost_checker.titleblock import insert_form3

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    add_sheet_frame(doc, 1.0, "A3", "landscape")
    insert_form3(doc, (420 - 5 - 185, 5), {"обозначение": cipher, "наименование": "План", "лист": "1", "листов": "1"}, to_mm=1.0)
    doc.saveas(str(path))


def test_mass_stamp(tmp_path):
    from gost_checker.massstamp import update_set

    folder = tmp_path / "src"
    folder.mkdir()
    _sheet(folder / "a.dxf", "OLD-1")
    _sheet(folder / "b.dxf", "OLD-1")
    before = {path.name: _sha(path) for path in folder.iterdir()}
    card = {"обозначение": "NEW-1", "наименование": "Очень длинное наименование " * 6, "стадия": "Р"}
    preview = update_set(folder, tmp_path / "dry", card, renumber=True, dry_run=True)
    assert preview["dry_run"] is True
    assert not (tmp_path / "dry").exists()
    assert {path.name: _sha(path) for path in folder.iterdir()} == before
    assert any(row["warnings"] for row in preview["files"])

    applied = update_set(folder, tmp_path / "out", card, renumber=True, dry_run=False)
    assert all(row["original_unchanged"] for row in applied["files"])
    assert {path.name: _sha(path) for path in folder.iterdir()} == before
    for name in ("a.dxf", "b.dxf"):
        saved = ezdxf.readfile(str(tmp_path / "out" / name))
        texts = [attrib.dxf.text for insert in saved.modelspace().query("INSERT") for attrib in insert.attribs]
        assert "NEW-1" in texts
        assert "OLD-1" not in texts
    numbers = []
    for name in ("a.dxf", "b.dxf"):
        saved = ezdxf.readfile(str(tmp_path / "out" / name))
        for insert in saved.modelspace().query("INSERT"):
            for attrib in insert.attribs:
                if attrib.dxf.tag == "LIST":
                    numbers.append(attrib.dxf.text)
    assert sorted(numbers) == ["1", "2"]
    assert all(row["checker"]["ok"] for row in applied["files"])

    loose = tmp_path / "loose.dxf"
    doc = ezdxf.new("R2010")
    doc.layers.add("SPDS_STAMP")
    doc.blocks.new("MARK").add_circle((0, 0), 1)
    doc.modelspace().add_blockref("MARK", (0, 0), dxfattribs={"layer": "SPDS_STAMP"})
    doc.modelspace().add_text("OLD-1", dxfattribs={"insert": (10, 10), "height": 2.5})
    doc.modelspace().add_text("OLD-1", dxfattribs={"insert": (300, 200), "height": 2.5})
    doc.modelspace().add_text("OLD-1", dxfattribs={"insert": (12, 12), "height": 2.5})
    doc.saveas(str(loose))
    ambiguous = update_set(loose, tmp_path / "bad", {"обозначение": "NEW-1"}, old_cipher="OLD-1", dry_run=False)
    assert ambiguous["files"][0]["ambiguous"] is True
    assert not (tmp_path / "bad" / "loose.dxf").exists()
    saved = ezdxf.readfile(str(loose))
    assert [entity.dxf.text for entity in saved.modelspace().query("TEXT")].count("OLD-1") == 3

    pdf = tmp_path / "sheet.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "OLD-1")
    document.save(str(pdf))
    document.close()
    pdf_result = update_set(pdf, tmp_path / "pdf", {"обозначение": "NEW-1"}, dry_run=False)
    assert pdf_result["files"][0]["check_only"] is True
    assert pdf_result["files"][0]["written"] is False
    assert _sha(pdf) == hashlib.sha256(pdf.read_bytes()).hexdigest()

    dwg = tmp_path / "sheet.dwg"
    dwg.write_bytes(b"dwg")
    dwg_result = update_set(dwg, tmp_path / "dwg", {"обозначение": "NEW-1"}, dry_run=False)
    assert "пункта 12" in dwg_result["files"][0]["error"]
    assert dwg.read_bytes() == b"dwg"

    archive = tmp_path / "set.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(folder / "a.dxf", "a.dxf")
    zipped = update_set(archive, tmp_path / "zipout", {"обозначение": "ZIP-1"}, dry_run=False)
    assert zipped["files"][0]["written"] is True
    assert _sha(archive) == hashlib.sha256(archive.read_bytes()).hexdigest()
