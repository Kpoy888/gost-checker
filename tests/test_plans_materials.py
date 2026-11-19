"""Поэтажные планы и ведомость материалов."""

from __future__ import annotations

import sys
from pathlib import Path

import ezdxf
import pytest


def _model(path: Path, *, millimetres: bool, storeys: bool = True, long_second: bool = False):
    import ifcopenshell.api.aggregate as aggregate
    import ifcopenshell.api.context as context
    import ifcopenshell.api.geometry as geometry
    import ifcopenshell.api.material as material
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
    body = context.add_context(model, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)
    site = root.create_entity(model, ifc_class="IfcSite", name="Площадка")
    building = root.create_entity(model, ifc_class="IfcBuilding", name="Корпус")
    aggregate.assign_object(model, products=[site], relating_object=ifc_project)
    aggregate.assign_object(model, products=[building], relating_object=site)
    if not storeys:
        model.write(str(path))
        return
    first = root.create_entity(model, ifc_class="IfcBuildingStorey", name="Этаж 1")
    second = root.create_entity(model, ifc_class="IfcBuildingStorey", name="Этаж 2")
    third = root.create_entity(model, ifc_class="IfcBuildingStorey", name="Этаж 3")
    first.Elevation = 0.0
    second.Elevation = 3 * scale
    third.Elevation = 6 * scale
    aggregate.assign_object(model, products=[first, second, third], relating_object=building)
    wall = root.create_entity(model, ifc_class="IfcWall", name="Наружная")
    other = root.create_entity(model, ifc_class="IfcWall", name="Внутренняя")
    wall_rep = geometry.add_wall_representation(model, context=body, length=6 * scale, height=3 * scale, thickness=0.3 * scale)
    other_length = 80 * scale if long_second else 2 * scale
    other_rep = geometry.add_wall_representation(model, context=body, length=other_length, height=3 * scale, thickness=0.2 * scale)
    geometry.assign_representation(model, product=wall, representation=wall_rep)
    geometry.assign_representation(model, product=other, representation=other_rep)
    spatial.assign_container(model, products=[wall], relating_structure=first)
    spatial.assign_container(model, products=[other], relating_structure=second)
    brick = material.add_material(model, name="Кирпич керамический")
    material.assign_material(model, products=[wall], material=brick)
    model.write(str(path))


def test_sheet_choice_boundary():
    from gost_checker.floorplans import choose_sheet, usable_mm

    usable_w, usable_h = usable_mm("A3", "landscape")
    fits = choose_sheet(usable_w / 1000.0 * 100.0 - 0.05, 10.0)
    assert fits["fits"] is True
    overflows = choose_sheet(100_000, 100_000)
    assert overflows["fits"] is False
    assert "более мелкий" in overflows["warning"]
    assert "1:" in overflows["scale"]


def test_three_storeys_scales_and_units(tmp_path):
    pytest.importorskip("ifcopenshell")
    from gost_checker.floorplans import plans_from_ifc

    meters = tmp_path / "m.ifc"
    millimetres = tmp_path / "mm.ifc"
    _model(meters, millimetres=False, long_second=True)
    _model(millimetres, millimetres=True)
    result = plans_from_ifc(meters, tmp_path / "plans", cut_m=1.2)
    assert result["ok"]
    assert len(result["sheets"]) == 3
    assert result["sheets"][0]["scale"] == result["sheets"][1]["scale"] == result["sheets"][2]["scale"]
    assert result["sheets"][0]["segments"] > 0
    assert result["sheets"][1]["segments"] > 0
    assert result["sheets"][2]["segments"] == 0
    assert "без геометрии" in result["sheets"][2]["warning"]
    assert result["sheets"][0]["stamp"].get("объект") == "Дом"
    assert result["sheets"][0]["stamp"].get("здание") == "Корпус"
    assert "разраб" not in result["sheets"][0]["stamp"]
    drawn = ezdxf.readfile(result["sheets"][0]["path"])
    xs = [entity.dxf.start.x for entity in drawn.modelspace().query("LINE")]
    assert max(xs) - min(xs) == pytest.approx(6000 / float(result["sheets"][0]["scale"].split(":")[1]), rel=0.05)
    per_floor = plans_from_ifc(meters, tmp_path / "each", per_floor=True)
    assert per_floor["sheets"][0]["scale"] != per_floor["sheets"][1]["scale"]
    metric = plans_from_ifc(millimetres, tmp_path / "mm")
    assert metric["ok"] and metric["sheets"][0]["segments"] > 0
    assert metric["sheets"][0]["width_m"] == pytest.approx(6.0, rel=0.05)
    from fastapi.testclient import TestClient

    from gost_checker.web import create_app

    client = TestClient(create_app())
    response = client.post("/api/plans", files={"file": ("m.ifc", meters.read_bytes(), "application/octet-stream")})
    assert response.status_code == 200
    assert len(response.json()["sheets"]) == 3
    page = client.get("/")
    assert 'id="plans-form"' in page.text and 'id="materials-form"' in page.text


def test_no_storeys_and_missing_ifcopenshell(tmp_path, monkeypatch):
    pytest.importorskip("ifcopenshell")
    from gost_checker.floorplans import plans_from_ifc

    empty = tmp_path / "empty.ifc"
    _model(empty, millimetres=False, storeys=False)
    failed = plans_from_ifc(empty, tmp_path / "out")
    assert failed["ok"] is False
    assert "этаж" in failed["error"].casefold()
    monkeypatch.setitem(sys.modules, "ifcopenshell", None)
    monkeypatch.setitem(sys.modules, "ifcopenshell.geom", None)
    monkeypatch.setitem(sys.modules, "ifcopenshell.util.unit", None)
    skipped = plans_from_ifc(empty, tmp_path / "skip")
    assert skipped["skipped"] is True
    assert skipped["sheets"] == []


def test_materials_reuse_takeoff(tmp_path):
    pytest.importorskip("ifcopenshell")
    import ifcopenshell.api.aggregate as aggregate
    import ifcopenshell.api.context as context
    import ifcopenshell.api.geometry as geometry
    import ifcopenshell.api.material as material
    import ifcopenshell.api.project as project
    import ifcopenshell.api.pset as pset
    import ifcopenshell.api.root as root
    import ifcopenshell.api.spatial as spatial
    import ifcopenshell.api.unit as unit

    from gost_checker.materials import BRICK_VOLUME_M3, load_keywords, write_materials
    from gost_checker.quantities import takeoff_ifc

    model = project.create_file(version="IFC4")
    ifc_project = root.create_entity(model, ifc_class="IfcProject", name="Дом")
    unit.assign_unit(model, length={"is_metric": True, "raw": "METERS"})
    ctx = context.add_context(model, context_type="Model")
    body = context.add_context(model, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)
    site = root.create_entity(model, ifc_class="IfcSite")
    building = root.create_entity(model, ifc_class="IfcBuilding")
    storey = root.create_entity(model, ifc_class="IfcBuildingStorey", name="Этаж 1")
    aggregate.assign_object(model, products=[site], relating_object=ifc_project)
    aggregate.assign_object(model, products=[building], relating_object=site)
    aggregate.assign_object(model, products=[storey], relating_object=building)
    wall = root.create_entity(model, ifc_class="IfcWall", name="Кладка")
    odd = root.create_entity(model, ifc_class="IfcSlab", name="Обшивка")
    plaster = root.create_entity(model, ifc_class="IfcCovering", name="Отделка")
    door = root.create_entity(model, ifc_class="IfcDoor", name="Дверь")
    wall_rep = geometry.add_wall_representation(model, context=body, length=6, height=3, thickness=0.3)
    geometry.assign_representation(model, product=wall, representation=wall_rep)
    spatial.assign_container(model, products=[wall, odd, plaster, door], relating_structure=storey)
    material.assign_material(model, products=[wall], material=material.add_material(model, name="Brick"))
    material.assign_material(model, products=[odd], material=material.add_material(model, name="Вагонка"))
    material.assign_material(model, products=[plaster], material=material.add_material(model, name="Штукатурка"))
    qto = pset.add_qto(model, product=plaster, name="Qto_CoveringBaseQuantities")
    pset.edit_qto(model, qto=qto, properties={"NetArea": 12.5})
    door_qto = pset.add_qto(model, product=door, name="Qto_DoorBaseQuantities")
    pset.edit_qto(model, qto=door_qto, properties={"NetArea": 2.0})
    path = tmp_path / "mat.ifc"
    model.write(str(path))
    keywords = tmp_path / "kw.yaml"
    keywords.write_text("кирпич:\n  - brick\nштукатурка:\n  - штукатур\n", encoding="utf-8")
    parsed = load_keywords(keywords)
    assert "brick" in parsed["кирпич"]
    base = takeoff_ifc(path)
    result = write_materials(path, tmp_path / "out", keywords)
    assert result["ok"]
    brick = next(row for row in result["rows"] if row["material"] == "кирпич")
    wall_volume = next(row["volume_m3"] for row in base["rows"] if row["name"] == "Кладка")
    assert brick["quantity"] == pytest.approx(wall_volume / BRICK_VOLUME_M3, rel=0.01)
    assert brick["source"] == "объём модели / объём одного кирпича, без нормы расхода"
    plaster_row = next(row for row in result["rows"] if row["name"] == "Отделка")
    assert plaster_row["quantity"] == pytest.approx(12.5)
    assert plaster_row["source"] == "из модели"
    assert any(item["name"] == "Вагонка" for item in result["unrecognized"])
    assert not any(row["name"] == "Дверь" for row in result["rows"])
    bare = tmp_path / "door.ifc"
    door_model = project.create_file(version="IFC4")
    door_project = root.create_entity(door_model, ifc_class="IfcProject", name="Дом")
    unit.assign_unit(door_model, length={"is_metric": True, "raw": "METERS"})
    door_only = root.create_entity(door_model, ifc_class="IfcDoor", name="Дверь")
    storey_only = root.create_entity(door_model, ifc_class="IfcBuildingStorey", name="Этаж 1")
    site_only = root.create_entity(door_model, ifc_class="IfcSite")
    building_only = root.create_entity(door_model, ifc_class="IfcBuilding")
    aggregate.assign_object(door_model, products=[site_only], relating_object=door_project)
    aggregate.assign_object(door_model, products=[building_only], relating_object=site_only)
    aggregate.assign_object(door_model, products=[storey_only], relating_object=building_only)
    spatial.assign_container(door_model, products=[door_only], relating_structure=storey_only)
    opening = pset.add_qto(door_model, product=door_only, name="Qto_DoorBaseQuantities")
    pset.edit_qto(door_model, qto=opening, properties={"NetArea": 2.0})
    door_model.write(str(bare))
    openings = write_materials(bare, tmp_path / "open", plaster_thickness_m=0.02)
    row = openings["rows"][0]
    assert row["source"] == "проём × заданная толщина"
    assert row["quantity"] == pytest.approx(2.0)
    assert row["volume_m3"] == pytest.approx(0.04)

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_lwpolyline([(0, 0), (2000, 0), (2000, 1000), (0, 1000)], close=True, dxfattribs={"layer": "Кирпич"})
    doc.modelspace().add_line((0, 0), (1000, 0), dxfattribs={"layer": "Мебель"})
    dxf_path = tmp_path / "plan.dxf"
    doc.saveas(str(dxf_path))
    dxf_result = write_materials(dxf_path, tmp_path / "dxf")
    assert any(row["material"] == "кирпич" and row["quantity"] is None for row in dxf_result["rows"])
    assert any(item["name"] == "Мебель" for item in dxf_result["unrecognized"])
    assert "ГОСТ" not in Path(dxf_result["csv"]).read_text(encoding="utf-8").splitlines()[-1] or "Норма расхода" in Path(dxf_result["csv"]).read_text(encoding="utf-8")
