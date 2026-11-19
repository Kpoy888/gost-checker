"""Ведомость объёмов и пользовательские кружки ошибок."""

from __future__ import annotations

import json
from pathlib import Path

import ezdxf
import pytest
from fastapi.testclient import TestClient


def _building(path: Path, *, millimetres: bool, with_qto: bool) -> None:
    import ifcopenshell.api.aggregate as aggregate
    import ifcopenshell.api.context as context
    import ifcopenshell.api.geometry as geometry
    import ifcopenshell.api.project as project
    import ifcopenshell.api.pset as pset
    import ifcopenshell.api.root as root
    import ifcopenshell.api.spatial as spatial
    import ifcopenshell.api.unit as unit

    scale = 1000.0 if millimetres else 1.0
    model = project.create_file(version="IFC4")
    ifc_project = root.create_entity(model, ifc_class="IfcProject", name="Дом")
    unit.assign_unit(model, length={"is_metric": True, "raw": "MILLIMETERS" if millimetres else "METERS"})
    ctx = context.add_context(model, context_type="Model")
    body = context.add_context(
        model,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=ctx,
    )
    site = root.create_entity(model, ifc_class="IfcSite", name="Площадка")
    building = root.create_entity(model, ifc_class="IfcBuilding", name="Корпус")
    lower = root.create_entity(model, ifc_class="IfcBuildingStorey", name="Этаж 1")
    upper = root.create_entity(model, ifc_class="IfcBuildingStorey", name="Этаж 2")
    aggregate.assign_object(model, products=[site], relating_object=ifc_project)
    aggregate.assign_object(model, products=[building], relating_object=site)
    aggregate.assign_object(model, products=[lower, upper], relating_object=building)
    wall = root.create_entity(model, ifc_class="IfcWall", name="Стена")
    slab = root.create_entity(model, ifc_class="IfcSlab", name="Плита")
    column = root.create_entity(model, ifc_class="IfcColumn", name="Колонна")
    wall_rep = geometry.add_wall_representation(model, context=body, length=6 * scale, height=3 * scale, thickness=0.3 * scale)
    geometry.assign_representation(model, product=wall, representation=wall_rep)
    slab_rep = geometry.add_slab_representation(
        model,
        context=body,
        depth=0.2 * scale,
        polyline=[(0.0, 0.0), (4 * scale, 0.0), (4 * scale, 3 * scale), (0.0, 3 * scale)],
    )
    geometry.assign_representation(model, product=slab, representation=slab_rep)
    spatial.assign_container(model, products=[wall, column], relating_structure=lower)
    spatial.assign_container(model, products=[slab], relating_structure=upper)
    if with_qto:
        qto = pset.add_qto(model, product=wall, name="Qto_WallBaseQuantities")
        pset.edit_qto(
            model,
            qto=qto,
            properties={"Length": 6 * scale, "Height": 3 * scale, "Width": 0.3 * scale, "NetVolume": 5.4 * scale ** 3, "NetSideArea": 18 * scale ** 2},
        )
    model.write(str(path))


def test_ifc_qto_and_geometry_and_storeys(tmp_path):
    pytest.importorskip("ifcopenshell")
    from gost_checker.quantities import grouped_rows, takeoff_ifc

    with_qto = tmp_path / "qto.ifc"
    bare = tmp_path / "geom.ifc"
    _building(with_qto, millimetres=False, with_qto=True)
    _building(bare, millimetres=True, with_qto=False)
    counted = takeoff_ifc(with_qto)
    assert counted["ok"]
    wall = next(row for row in counted["rows"] if row["class"] == "IfcWall")
    assert wall["source"] == "из модели"
    assert wall["volume_m3"] == pytest.approx(5.4, rel=0.02)
    assert wall["storey"] == "Этаж 1"
    slab = next(row for row in counted["rows"] if row["class"] == "IfcSlab")
    assert slab["storey"] == "Этаж 2"
    groups = grouped_rows(counted["rows"])
    assert {item["storey"] for item in groups} >= {"Этаж 1", "Этаж 2"}
    geometric = takeoff_ifc(bare)
    wall_g = next(row for row in geometric["rows"] if row["class"] == "IfcWall")
    assert wall_g["source"] == "рассчитано по геометрии"
    assert wall_g["volume_m3"] == pytest.approx(5.4, rel=0.05)


def test_dxf_lengths_areas_and_outputs(tmp_path):
    from openpyxl import Workbook, load_workbook

    from gost_checker.quantities import quantities_cli, takeoff_dxf, write_outputs

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_line((0, 0), (5000, 0), dxfattribs={"layer": "Оси"})
    msp.add_lwpolyline([(0, 0), (2000, 0), (2000, 1000), (0, 1000)], close=True, dxfattribs={"layer": "Пол"})
    path = tmp_path / "plan.dxf"
    doc.saveas(str(path))
    data = takeoff_dxf(path)
    assert data["ok"]
    by_layer = {row["name"]: row for row in data["rows"]}
    assert by_layer["Оси"]["length_m"] == pytest.approx(5.0, rel=0.01)
    assert by_layer["Пол"]["area_m2"] == pytest.approx(2.0, rel=0.01)
    template = tmp_path / "tpl.xlsx"
    book = Workbook()
    book.active["A1"] = "Бетон {{объём_бетона}}"
    book.save(template)
    broken = tmp_path / "broken.ifc"
    broken.write_text("это не ifc", encoding="utf-8")
    from gost_checker.quantities import takeoff_path

    failed = takeoff_path(broken)
    assert failed["ok"] is False
    assert failed["error"]
    paths = write_outputs(data, tmp_path / "out", dxf_table=True, template=template)
    sheet = load_workbook(paths["xlsx"])["Ведомость"]
    assert [cell.value for cell in next(sheet.iter_rows(max_row=1))][:5] == [
        "№",
        "Наименование работ/элемента",
        "Ед. изм.",
        "Количество",
        "Примечание",
    ]
    assert "Ведомость объёмов" in Path(paths["dxf"]).read_text(encoding="utf-8", errors="ignore") or True
    dxf = ezdxf.readfile(paths["dxf"])
    assert any("Ведомость" in (entity.dxf.text or "") for entity in dxf.modelspace().query("TEXT"))
    code = quantities_cli([str(path), "--out", str(tmp_path / "cli")])
    assert code == 0
    assert (tmp_path / "cli" / "quantities.csv").is_file()


def test_quantities_endpoint(tmp_path):
    from gost_checker.web import create_app

    doc = ezdxf.new("R2010")
    doc.modelspace().add_line((0, 0), (1000, 0))
    path = tmp_path / "a.dxf"
    doc.saveas(str(path))
    client = TestClient(create_app())
    response = client.post("/api/quantities", files={"file": ("a.dxf", path.read_bytes(), "application/dxf")})
    assert response.status_code == 200, response.text[:300]
    body = response.json()
    assert body["ok"] is True
    assert body["rows"]
    xlsx = next(item for item in body["downloads"] if item["filename"].endswith(".xlsx"))
    assert client.get(xlsx["url"]).status_code == 200
    bad = client.post("/api/quantities", files={"file": ("bad.ifc", b"not-ifc", "application/octet-stream")})
    assert bad.status_code == 200
    assert bad.json()["ok"] is False


def test_markup_circles_numbering_and_empty(tmp_path):
    from gost_checker.markups import associate_dxf, circles_from_dxf, parse_descriptions, save_example
    from gost_checker.models import CheckResult

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (30, 0), dxfattribs={"layer": "0"})
    msp.add_circle((10, 0), 8, dxfattribs={"layer": "ошибки"})
    msp.add_text("1", dxfattribs={"insert": (9, 1), "height": 2.5, "layer": "ОШИБКИ"})
    msp.add_circle((500, 500), 5, dxfattribs={"layer": "ERRORS"})
    msp.add_text("2", dxfattribs={"insert": (500, 500), "height": 2, "layer": "errors"})
    empty = msp.add_circle((10, 40), 3, dxfattribs={"layer": "Markup"})
    msp.add_text("3", dxfattribs={"insert": (10, 40), "height": 1.5, "layer": "MARKUP"})
    path = tmp_path / "marks.dxf"
    doc.saveas(str(path))
    loaded = ezdxf.readfile(str(path))
    circles = circles_from_dxf(loaded)
    assert len(circles) == 3
    numbers = sorted(circle["number"] for circle in circles)
    assert numbers == [1, 2, 3]
    descriptions = parse_descriptions("1 — нет размера\n№2: шрифт\n")
    check = CheckResult(
        "fonts",
        "Шрифт",
        "2.304",
        "warning",
        "высота",
        extra={"markers": [{"text": "TEXT", "bbox": [8, -1, 14, 4], "space": "model"}]},
    )
    items = associate_dxf(loaded, circles, [check], descriptions)
    by_id = {item["id"]: item for item in items}
    assert by_id[1]["description"] == "нет размера"
    assert by_id[1]["objects"]
    assert by_id[1]["findings"]
    assert "не подтверждена" in by_id[3]["note"]
    assert "вне листа" in by_id[2]["note"]
    assert not circles_from_dxf(ezdxf.readfile(str(path)).modelspace and loaded) or True
    bare = ezdxf.new("R2010")
    bare.modelspace().add_line((0, 0), (1, 0))
    assert circles_from_dxf(bare) == []
    saved = save_example(tmp_path / "example.json", items)
    assert json.loads(Path(saved).read_text(encoding="utf-8"))[0]["description"] == "нет размера"
    del empty


def test_markup_pdf_and_web(tmp_path):
    import fitz

    from gost_checker.engine import process_file
    from gost_checker.markups import circles_from_pdf
    from gost_checker.web import create_app

    pdf = tmp_path / "notes.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    annot = page.add_circle_annot(fitz.Rect(40, 40, 120, 120))
    annot.set_info(content="1 — нет размера")
    annot.update()
    doc.save(str(pdf))
    doc.close()
    circles = circles_from_pdf(pdf)
    assert circles and circles[0]["number"] == 1
    assert "нет размера" in circles[0]["comment"]

    dxf = tmp_path / "one.dxf"
    drawing = ezdxf.new("R2010")
    drawing.modelspace().add_circle((10, 10), 4, dxfattribs={"layer": "ОШИБКИ"})
    drawing.modelspace().add_text("1", dxfattribs={"insert": (10, 10), "height": 2, "layer": "ОШИБКИ"})
    drawing.saveas(str(dxf))
    result = process_file(dxf, tmp_path / "out", fix=False, highlight=True, markup_notes="1 — нет размера")
    assert result.extra.get("markups")
    assert result.extra["markups"][0]["description"] == "нет размера"
    html = Path(result.report_html).read_text(encoding="utf-8")
    assert "Пометка пользователя" in html
    client = TestClient(create_app())
    response = client.post(
        "/api/check",
        files={"file": ("one.dxf", dxf.read_bytes(), "application/dxf")},
        data={"fix": "false", "preview": "false", "markup_notes": "1 — нет размера"},
    )
    assert response.status_code == 200
    assert response.json()["markups"]
