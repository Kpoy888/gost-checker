"""Ведомость объёмов по IFC (Renga/Revit) и по длинам/площадям DXF.

Земляные работы и отделка попадают в ведомость только если такие элементы есть в модели.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Optional

CLASS_TITLES = {
    "IfcWall": "Стены",
    "IfcWallStandardCase": "Стены",
    "IfcSlab": "Перекрытия",
    "IfcColumn": "Колонны",
    "IfcBeam": "Балки",
    "IfcFooting": "Фундаменты",
    "IfcPile": "Фундаменты",
    "IfcWindow": "Окна",
    "IfcDoor": "Двери",
    "IfcStair": "Лестницы",
    "IfcStairFlight": "Лестницы",
    "IfcPipeSegment": "Инженерные сети",
    "IfcDuctSegment": "Инженерные сети",
    "IfcFlowSegment": "Инженерные сети",
    "IfcCableCarrierSegment": "Инженерные сети",
}


def _length_scale(model) -> float:
    """Метры на одну единицу длины модели."""
    import ifcopenshell.util.unit as unit

    try:
        return float(unit.calculate_unit_scale(model))
    except Exception:
        return 1.0


def _storey_name(model, product) -> str:
    try:
        for rel in getattr(product, "ContainedInStructure", []) or []:
            relating = rel.RelatingStructure
            if relating and relating.is_a("IfcBuildingStorey"):
                return str(relating.Name or "")
    except Exception:
        return ""
    return ""


def _material_name(product) -> str:
    try:
        for rel in getattr(product, "HasAssociations", []) or []:
            if not rel.is_a("IfcRelAssociatesMaterial"):
                continue
            mat = rel.RelatingMaterial
            name = getattr(mat, "Name", None)
            if name:
                return str(name)
            for attr in ("ForLayerSet", "MaterialLayers"):
                layers = getattr(mat, attr, None)
                if layers:
                    first = layers[0]
                    layer_mat = getattr(first, "Material", None)
                    if layer_mat and getattr(layer_mat, "Name", None):
                        return str(layer_mat.Name)
    except Exception:
        return ""
    return ""


def _qto_values(product) -> dict[str, float]:
    found: dict[str, float] = {}
    try:
        for rel in getattr(product, "IsDefinedBy", []) or []:
            definition = getattr(rel, "RelatingPropertyDefinition", None)
            if definition is None or not definition.is_a("IfcElementQuantity"):
                continue
            for quantity in definition.Quantities or []:
                name = str(quantity.Name or "")
                value = None
                for attr in ("LengthValue", "AreaValue", "VolumeValue", "CountValue"):
                    if hasattr(quantity, attr):
                        raw = getattr(quantity, attr)
                        if raw is not None:
                            value = float(raw)
                            break
                if value is not None and name:
                    found[name] = value
    except Exception:
        return found
    return found


def _pick_metric(values: dict[str, float], names: tuple[str, ...]) -> Optional[float]:
    lowered = {key.lower(): val for key, val in values.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _geometry_metrics(settings, product, scale: float) -> dict[str, float]:
    import ifcopenshell.geom
    import ifcopenshell.util.shape as shape

    try:
        created = ifcopenshell.geom.create_shape(settings, product)
        geometry = created.geometry
        volume = float(shape.get_volume(geometry)) * (scale ** 3)
        area = float(shape.get_area(geometry)) * (scale ** 2)
        dx = float(shape.get_x(geometry)) * scale
        dy = float(shape.get_y(geometry)) * scale
        dz = float(shape.get_z(geometry)) * scale
        length = max(dx, dy)
        return {"length_m": length, "area_m2": area, "volume_m3": volume, "height_m": dz}
    except Exception:
        return {}


def _row_from_product(model, product, settings, scale: float) -> Optional[dict]:
    cls = product.is_a()
    if cls in {"IfcSpace", "IfcOpeningElement", "IfcSite", "IfcBuilding", "IfcBuildingStorey", "IfcProject"}:
        return None
    if not cls.startswith("Ifc"):
        return None
    qto = _qto_values(product)
    length = _pick_metric(qto, ("Length", "NetLength", "GrossLength"))
    area = _pick_metric(qto, ("NetSideArea", "NetArea", "GrossArea", "GrossSideArea", "NetFloorArea"))
    volume = _pick_metric(qto, ("NetVolume", "GrossVolume"))
    count = _pick_metric(qto, ("Count",))
    source = "из модели" if any(value is not None for value in (length, area, volume, count)) else ""
    if source != "из модели":
        geom = _geometry_metrics(settings, product, scale)
        if geom:
            length = geom.get("length_m")
            area = geom.get("area_m2")
            volume = geom.get("volume_m3")
            source = "рассчитано по геометрии"
    if not source:
        source = "недостаточно данных"
    if length is not None and source == "из модели":
        length *= scale
    if area is not None and source == "из модели":
        area *= scale ** 2
    if volume is not None and source == "из модели":
        volume *= scale ** 3
    group = CLASS_TITLES.get(cls, "Прочие")
    name = str(getattr(product, "Name", "") or cls)
    predefined = str(getattr(product, "ObjectType", "") or getattr(product, "PredefinedType", "") or "")
    return {
        "class": cls,
        "group": group,
        "type": predefined or cls,
        "name": name,
        "storey": _storey_name(model, product),
        "material": _material_name(product),
        "global_id": str(getattr(product, "GlobalId", "") or ""),
        "length_m": length,
        "area_m2": area,
        "volume_m3": volume,
        "count": count if count is not None else 1,
        "source": source,
    }


def takeoff_ifc(path: Path) -> dict:
    path = Path(path)
    try:
        import ifcopenshell
        import ifcopenshell.geom
    except ImportError:
        return {"ok": False, "error": "ifcopenshell не установлен", "rows": [], "kind": "ifc"}
    try:
        model = ifcopenshell.open(str(path))
    except Exception as exc:
        return {"ok": False, "error": f"IFC не прочитан: {exc}", "rows": [], "kind": "ifc"}
    scale = _length_scale(model)
    settings = ifcopenshell.geom.settings()
    rows = []
    try:
        products = list(model.by_type("IfcProduct"))
    except Exception as exc:
        return {"ok": False, "error": f"IFC открыт, но элементы не прочитаны: {exc}", "rows": [], "kind": "ifc"}
    for product in products:
        try:
            row = _row_from_product(model, product, settings, scale)
        except Exception:
            continue
        if row:
            rows.append(row)
    return {"ok": True, "error": "", "rows": rows, "kind": "ifc", "length_scale_m": scale}


def _shoelace(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def takeoff_dxf(path: Path) -> dict:
    import ezdxf

    from .units import resolve_insunits

    try:
        doc = ezdxf.readfile(str(path))
    except Exception:
        try:
            from ezdxf import recover

            doc, _auditor = recover.readfile(str(path))
        except Exception as exc:
            return {"ok": False, "error": f"DXF не прочитан: {exc}", "rows": [], "kind": "dxf"}
    to_mm, _warning = resolve_insunits(doc)
    to_m = float(to_mm) / 1000.0
    buckets: dict[str, dict] = {}

    def bucket(layer: str) -> dict:
        row = buckets.get(layer)
        if row is None:
            row = {
                "class": "DXF",
                "group": "Геометрия чертежа",
                "type": layer or "0",
                "name": layer or "0",
                "storey": "",
                "material": "",
                "global_id": "",
                "length_m": 0.0,
                "area_m2": 0.0,
                "volume_m3": None,
                "count": 0,
                "source": "рассчитано по геометрии",
            }
            buckets[layer] = row
        return row

    for entity in doc.modelspace():
        layer = str(entity.dxf.layer or "0")
        dtype = entity.dxftype()
        try:
            if dtype == "LINE":
                start, end = entity.dxf.start, entity.dxf.end
                length = ((end.x - start.x) ** 2 + (end.y - start.y) ** 2) ** 0.5 * to_m
                row = bucket(layer)
                row["length_m"] += length
                row["count"] += 1
            elif dtype == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
                length = 0.0
                for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                    length += ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
                if entity.closed and len(pts) > 2:
                    length += ((pts[0][0] - pts[-1][0]) ** 2 + (pts[0][1] - pts[-1][1]) ** 2) ** 0.5
                row = bucket(layer)
                row["length_m"] += length * to_m
                if entity.closed:
                    row["area_m2"] += _shoelace(pts) * (to_m ** 2)
                row["count"] += 1
            elif dtype == "HATCH":
                area = 0.0
                try:
                    for path in entity.paths:
                        if getattr(path, "vertices", None):
                            verts = [(float(v[0]), float(v[1])) for v in path.vertices]
                            area += _shoelace(verts)
                except Exception:
                    area = 0.0
                row = bucket(layer)
                row["area_m2"] += area * (to_m ** 2)
                row["count"] += 1
        except Exception:
            continue
    return {"ok": True, "error": "", "rows": list(buckets.values()), "kind": "dxf"}


def takeoff_path(path: Path) -> dict:
    suffix = Path(path).suffix.lower()
    if suffix in {".ifc", ".ifczip"}:
        return takeoff_ifc(Path(path))
    if suffix == ".dxf":
        return takeoff_dxf(Path(path))
    return {"ok": False, "error": f"Для ведомости нужен IFC или DXF, получено {suffix or 'без расширения'}", "rows": [], "kind": suffix}


def grouped_rows(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for row in rows:
        key = (row.get("group"), row.get("type"), row.get("material"), row.get("storey"), row.get("source"))
        item = groups.get(key)
        if item is None:
            item = {
                "group": row.get("group"),
                "type": row.get("type"),
                "material": row.get("material") or "",
                "storey": row.get("storey") or "",
                "source": row.get("source"),
                "length_m": 0.0,
                "area_m2": 0.0,
                "volume_m3": 0.0,
                "count": 0,
                "has_volume": False,
            }
            groups[key] = item
        item["length_m"] += float(row.get("length_m") or 0)
        item["area_m2"] += float(row.get("area_m2") or 0)
        if row.get("volume_m3") is not None:
            item["volume_m3"] += float(row["volume_m3"])
            item["has_volume"] = True
        item["count"] += int(row.get("count") or 0)
    return list(groups.values())


def _qty_and_unit(row: dict) -> tuple[str, float]:
    if row.get("has_volume") and row.get("volume_m3"):
        return "м³", round(float(row["volume_m3"]), 3)
    if row.get("volume_m3") and not row.get("length_m") and not row.get("area_m2"):
        return "м³", round(float(row["volume_m3"]), 3)
    if row.get("area_m2") and not row.get("volume_m3"):
        return "м²", round(float(row["area_m2"]), 3)
    if row.get("has_volume"):
        return "м³", round(float(row["volume_m3"]), 3)
    if float(row.get("area_m2") or 0) > float(row.get("length_m") or 0):
        return "м²", round(float(row["area_m2"]), 3)
    if row.get("length_m"):
        return "м", round(float(row["length_m"]), 3)
    return "шт", int(row.get("count") or 0)


def write_outputs(data: dict, out_dir: Path, *, dxf_table: bool = False, template: Optional[Path] = None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = data.get("rows") or []
    groups = grouped_rows(rows)
    summary_path = out_dir / "quantities.json"
    summary_path.write_text(json.dumps({"ok": data.get("ok"), "error": data.get("error"), "rows": rows, "groups": groups}, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = out_dir / "quantities.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["№", "Наименование работ/элемента", "Ед. изм.", "Количество", "Примечание"])
        for index, row in enumerate(groups, start=1):
            unit, qty = _qty_and_unit(row)
            title = " / ".join(part for part in (row.get("group"), row.get("type"), row.get("material"), row.get("storey")) if part)
            writer.writerow([index, title, unit, qty, row.get("source")])
    xlsx_path = out_dir / "quantities.xlsx"
    _write_xlsx(groups, rows, xlsx_path)
    pdf_path = out_dir / "quantities.pdf"
    _write_pdf(groups, pdf_path)
    result = {"json": str(summary_path), "csv": str(csv_path), "xlsx": str(xlsx_path), "pdf": str(pdf_path) if pdf_path.is_file() else ""}
    if dxf_table:
        dxf_path = out_dir / "quantities_table.dxf"
        _write_dxf_table(groups, dxf_path)
        result["dxf"] = str(dxf_path)
    if template and Path(template).is_file():
        filled = out_dir / "quantities_filled.xlsx"
        values = {}
        for row in groups:
            if row.get("group") == "Перекрытия" and row.get("volume_m3"):
                values["объём_бетона"] = round(float(row["volume_m3"]), 3)
            if "Стен" in str(row.get("group")) and row.get("volume_m3"):
                values.setdefault("объём_стен", round(float(row["volume_m3"]), 3))
        _fill_template(Path(template), filled, values)
        result["template"] = str(filled)
    return result


def _write_xlsx(groups: list[dict], rows: list[dict], dest: Path) -> None:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Ведомость"
    sheet.append(["№", "Наименование работ/элемента", "Ед. изм.", "Количество", "Примечание"])
    for index, row in enumerate(groups, start=1):
        unit, qty = _qty_and_unit(row)
        title = " / ".join(part for part in (row.get("group"), row.get("type"), row.get("material"), row.get("storey")) if part)
        sheet.append([index, title, unit, qty, row.get("source")])
    detail = book.create_sheet("Элементы")
    detail.append(["GlobalId", "Имя", "Этаж", "Материал", "Длина, м", "Площадь, м²", "Объём, м³", "Источник"])
    for row in rows:
        detail.append([
            row.get("global_id"),
            row.get("name"),
            row.get("storey"),
            row.get("material"),
            row.get("length_m"),
            row.get("area_m2"),
            row.get("volume_m3"),
            row.get("source"),
        ])
    book.save(dest)


def _write_pdf(groups: list[dict], dest: Path) -> None:
    from .export_report import find_dejavu

    font = find_dejavu()
    if font is None:
        return
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("DejaVu", "", str(font))
    pdf.set_font("DejaVu", size=12)
    width = pdf.epw
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(width, 7, "Ведомость объёмов работ")
    pdf.set_font("DejaVu", size=9)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(width, 5, "Земляные работы и отделка включаются только если соответствующие элементы есть в модели.")
    for index, row in enumerate(groups, start=1):
        unit, qty = _qty_and_unit(row)
        title = " / ".join(part for part in (row.get("group"), row.get("type"), row.get("storey")) if part)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(width, 5, f"{index}. {title}: {qty} {unit} ({row.get('source')})")
    pdf.output(str(dest))


def _write_dxf_table(groups: list[dict], dest: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    y = 0.0
    msp.add_text("Ведомость объёмов", dxfattribs={"insert": (0, y), "height": 5, "layer": "0"})
    for index, row in enumerate(groups, start=1):
        y -= 8
        unit, qty = _qty_and_unit(row)
        title = row.get("group") or ""
        msp.add_text(f"{index}. {title} {qty} {unit}", dxfattribs={"insert": (0, y), "height": 3.5, "layer": "0"})
    doc.saveas(str(dest))


def _fill_template(template: Path, dest: Path, values: dict) -> None:
    from openpyxl import load_workbook

    book = load_workbook(template)
    for sheet in book.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if not isinstance(cell.value, str) or "{{" not in cell.value:
                    continue
                text = cell.value
                for key, value in values.items():
                    text = text.replace("{{" + key + "}}", str(value))
                cell.value = text
    book.save(dest)


def quantities_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gost-check quantities", description="Ведомость объёмов по IFC или DXF")
    parser.add_argument("input")
    parser.add_argument("--out", default="gost-quantities")
    parser.add_argument("--dxf-table", action="store_true")
    parser.add_argument("--template")
    args = parser.parse_args(argv)
    data = takeoff_path(Path(args.input))
    paths = write_outputs(data, Path(args.out), dxf_table=args.dxf_table, template=Path(args.template) if args.template else None)
    print(json.dumps({"ok": data.get("ok"), "error": data.get("error"), "rows": len(data.get("rows") or []), "files": paths}, ensure_ascii=False, indent=2))
    return 0 if data.get("ok") else 2
