"""Экспорт DXF в PDF и плоский IFC. DWG не создаётся."""

from __future__ import annotations

import zipfile

import ezdxf
import fitz
import pytest

from gost_checker.export_drawing import DWG_ERROR, export_drawing


def _sheet(path):
    from gost_checker.checks.sheet_frame import add_sheet_frame

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    add_sheet_frame(doc, 1.0, "A3", "landscape")
    doc.modelspace().add_line((30, 40), (180, 40), dxfattribs={"lineweight": 50})
    doc.modelspace().add_text("Проверка", dxfattribs={"insert": (40, 80), "height": 5})
    doc.saveas(str(path))


def test_pdf_and_ifc_without_dwg(tmp_path):
    pytest.importorskip("ifcopenshell")
    src = tmp_path / "sheet.dxf"
    _sheet(src)
    result = export_drawing(src, tmp_path / "out")
    assert result["ok"]
    assert result["dwg"]["written"] is False
    assert result["dwg"]["error"] == ""
    pdf = fitz.open(result["files"]["pdf"]["path"])
    try:
        assert pdf[0].rect.width == pytest.approx(420 * 72 / 25.4, abs=1)
        assert pdf[0].rect.height == pytest.approx(297 * 72 / 25.4, abs=1)
        assert "Проверка" in pdf[0].get_text()
        assert pdf[0].get_drawings()
    finally:
        pdf.close()
    import ifcopenshell

    model = ifcopenshell.open(result["files"]["ifc"]["path"])
    assert list(model.by_type("IfcAnnotation"))
    assert not list(model.by_type("IfcWall"))
    assert not list(model.by_type("IfcExtrudedAreaSolid"))
    names = zipfile.ZipFile(result["zip"]).namelist()
    assert "sheet.pdf" in names and "sheet.ifc" in names
    from fastapi.testclient import TestClient

    from gost_checker.web import create_app

    client = TestClient(create_app())
    response = client.post(
        "/api/export",
        files={"file": ("sheet.dxf", src.read_bytes(), "application/dxf")},
        data={"pdf": "true", "ifc": "true"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["dwg"]["written"] is False
    assert "pdf" in body["files"] and "ifc" in body["files"]
    page = client.get("/")
    assert 'id="export-form"' in page.text and "DWG (сейчас выключен)" in page.text

    with_dwg = export_drawing(src, tmp_path / "dwg", dwg=True)
    assert with_dwg["dwg"]["written"] is False
    assert DWG_ERROR in with_dwg["errors"]
    assert Path_pdf(with_dwg)
    versioned = export_drawing(src, tmp_path / "ver", version="v3", pdf=True, ifc=False)
    assert versioned["version"]["exported"] is False
    assert "не ведутся" in versioned["version"]["error"]
    assert "pdf" in versioned["files"]


def Path_pdf(result) -> bool:
    return result["files"]["pdf"]["path"].endswith(".pdf")


def test_ifc_source_is_copied(tmp_path):
    pytest.importorskip("ifcopenshell")
    import ifcopenshell.api.project as project
    import ifcopenshell.api.root as root

    model = project.create_file(version="IFC4")
    root.create_entity(model, ifc_class="IfcProject", name="Дом")
    wall = root.create_entity(model, ifc_class="IfcWall", name="Стена")
    assert wall.is_a("IfcWall")
    src = tmp_path / "model.ifc"
    model.write(str(src))
    result = export_drawing(src, tmp_path / "back", pdf=True, ifc=True)
    assert result["files"]["ifc"]["validated"] is True
    assert result["files"]["ifc"]["walls"] == 1
    assert result["files"]["ifc"]["unchanged"] is True
    assert any("не строится" in item for item in result["errors"])
    copied = (tmp_path / "back" / "model.ifc").read_bytes()
    assert copied == src.read_bytes()
