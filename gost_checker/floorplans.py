"""Поэтажные планы из IFC.

Сечение на заданной высоте над полом этажа. Один масштаб на все этажи — по самому большому,
если не передан per_floor. Вид не режется на листы. Формат из ряда ГОСТ 2.301, масштаб из ГОСТ 2.302.
В штамп попадают только поля, которые есть в модели.
"""

from __future__ import annotations

import json
from pathlib import Path

from .constants import (
    FRAME_LEFT_MM,
    FRAME_OTHER_MM,
    SCALES_REDUCTION,
    SHEET_FORMATS,
    TITLE_BLOCK_H_MM,
    TITLE_BLOCK_W_MM,
)

DEFAULT_CUT_M = 1.2
CUT_NOTE = "Высота сечения над полом задаётся пользователем. Значение 1,2 м — середина обычного диапазона 1,0–1,5 м, это не пункт нормы."


def scale_denominators() -> list[float]:
    values = []
    for item in SCALES_REDUCTION:
        left, right = item.split(":")
        if float(left) == 1:
            values.append(float(right))
    return values


def usable_mm(fmt: str, orientation: str) -> tuple[float, float]:
    width, height = SHEET_FORMATS[fmt]
    if orientation == "landscape":
        width, height = max(width, height), min(width, height)
    else:
        width, height = min(width, height), max(width, height)
    return (
        width - FRAME_LEFT_MM - FRAME_OTHER_MM,
        height - 2 * FRAME_OTHER_MM - TITLE_BLOCK_H_MM,
    )


def choose_sheet(width_m: float, height_m: float) -> dict:
    """Самый крупный масштаб ГОСТ 2.302, на котором вид целиком входит в формат ГОСТ 2.301."""
    width_mm = max(width_m, 0.0) * 1000.0
    height_mm = max(height_m, 0.0) * 1000.0
    for denom in scale_denominators():
        paper_w = width_mm / denom
        paper_h = height_mm / denom
        for fmt in SHEET_FORMATS:
            for orientation in ("landscape", "portrait"):
                usable_w, usable_h = usable_mm(fmt, orientation)
                if paper_w <= usable_w + 1e-6 and paper_h <= usable_h + 1e-6:
                    return {
                        "format": fmt,
                        "orientation": orientation,
                        "denominator": denom,
                        "scale": f"1:{denom:g}",
                        "fits": True,
                        "warning": "",
                    }
    last = scale_denominators()[-1]
    return {
        "format": "A0",
        "orientation": "landscape",
        "denominator": last,
        "scale": f"1:{last:g}",
        "fits": False,
        "warning": (
            f"Даже А0 при {f'1:{last:g}'} не вмещает вид целиком. "
            "Вид не разрезан. Возьмите более мелкий масштаб."
        ),
    }


def _import_ifc():
    try:
        import ifcopenshell
        import ifcopenshell.geom
        import ifcopenshell.util.unit as unit
    except ImportError:
        return None
    return ifcopenshell, ifcopenshell.geom, unit


def _products(storey) -> list:
    found = []
    for rel in getattr(storey, "ContainsElements", []) or []:
        found.extend(list(rel.RelatedElements or []))
    return found


def _triangles(product, geom_module):
    settings = geom_module.settings()
    try:
        settings.set(settings.TRIANGULATED, True)
    except Exception:
        pass
    try:
        created = geom_module.create_shape(settings, product)
    except Exception:
        return []
    verts = list(created.geometry.verts)
    faces = list(created.geometry.faces)
    points = [(verts[i], verts[i + 1], verts[i + 2]) for i in range(0, len(verts), 3)]
    triangles = []
    for index in range(0, len(faces), 3):
        try:
            triangles.append((points[faces[index]], points[faces[index + 1]], points[faces[index + 2]]))
        except IndexError:
            continue
    return triangles


def _cut_triangle(triangle, z_cut: float) -> tuple | None:
    hits = []
    a, b, c = triangle
    for start, end in ((a, b), (b, c), (c, a)):
        z0, z1 = start[2], end[2]
        if abs(z0 - z_cut) <= 1e-6 and abs(z1 - z_cut) <= 1e-6:
            hits.append((start[0], start[1]))
            hits.append((end[0], end[1]))
            continue
        if (z0 - z_cut) * (z1 - z_cut) > 0:
            continue
        if abs(z1 - z0) <= 1e-12:
            continue
        factor = (z_cut - z0) / (z1 - z0)
        if factor < -1e-6 or factor > 1 + 1e-6:
            continue
        hits.append((start[0] + factor * (end[0] - start[0]), start[1] + factor * (end[1] - start[1])))
    unique = []
    for point in hits:
        if not any(abs(point[0] - old[0]) < 1e-6 and abs(point[1] - old[1]) < 1e-6 for old in unique):
            unique.append(point)
        if len(unique) == 2:
            break
    if len(unique) < 2:
        return None
    return unique[0], unique[1]


def section_storey(model, storey, cut_m: float, unit_scale: float) -> dict:
    z_cut = cut_m / unit_scale if unit_scale else cut_m
    segments = []
    skipped = []
    _, geom_module, _unit = _import_ifc()
    for product in _products(storey):
        kind = product.is_a()
        if kind in {"IfcSpace", "IfcOpeningElement", "IfcAnnotation"}:
            continue
        triangles = _triangles(product, geom_module)
        if not triangles:
            skipped.append(str(getattr(product, "Name", "") or kind))
            continue
        for triangle in triangles:
            segment = _cut_triangle(triangle, z_cut)
            if segment:
                segments.append(segment)
    return {"segments": segments, "skipped": skipped}


def _extent(segments, unit_scale: float) -> tuple[float, float, tuple]:
    xs = [point[0] for segment in segments for point in segment]
    ys = [point[1] for segment in segments for point in segment]
    if not xs:
        return 0.0, 0.0, (0.0, 0.0, 0.0, 0.0)
    return (
        (max(xs) - min(xs)) * unit_scale,
        (max(ys) - min(ys)) * unit_scale,
        (min(xs), min(ys), max(xs), max(ys)),
    )


def _stamp_meta(model, storey, unit_scale: float) -> dict:
    meta = {}
    projects = list(model.by_type("IfcProject"))
    if projects and getattr(projects[0], "Name", None):
        meta["объект"] = str(projects[0].Name)
    buildings = list(model.by_type("IfcBuilding"))
    if buildings and getattr(buildings[0], "Name", None):
        meta["здание"] = str(buildings[0].Name)
    name = str(getattr(storey, "Name", "") or "")
    elevation = float(getattr(storey, "Elevation", 0) or 0) * unit_scale
    if name:
        meta["наименование"] = f"План {name} на отм. {elevation:+.3f}"
        meta["отметка"] = f"{elevation:+.3f}"
    return meta


def _draw_sheet(path: Path, segments, bounds, sheet: dict, meta: dict, unit_scale: float, note: str) -> None:
    import ezdxf

    from .checks.sheet_frame import add_sheet_frame
    from .titleblock import insert_form3

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    _outer, inner = add_sheet_frame(doc, 1.0, sheet["format"], sheet["orientation"])
    width, height = SHEET_FORMATS[sheet["format"]]
    if sheet["orientation"] == "landscape":
        width, height = max(width, height), min(width, height)
    stamp_x = width - FRAME_OTHER_MM - TITLE_BLOCK_W_MM
    stamp_y = FRAME_OTHER_MM
    insert_form3(doc, (stamp_x, stamp_y), meta, to_mm=1.0)
    msp = doc.modelspace()
    if "ПЛАН" not in doc.layers:
        doc.layers.add("ПЛАН")
    origin_x = inner.xmin + 2
    origin_y = FRAME_OTHER_MM + TITLE_BLOCK_H_MM + 2
    denom = sheet["denominator"]
    min_x, min_y = bounds[0], bounds[1]
    for (x1, y1), (x2, y2) in segments:
        msp.add_line(
            (
                origin_x + (x1 - min_x) * unit_scale * 1000.0 / denom,
                origin_y + (y1 - min_y) * unit_scale * 1000.0 / denom,
            ),
            (
                origin_x + (x2 - min_x) * unit_scale * 1000.0 / denom,
                origin_y + (y2 - min_y) * unit_scale * 1000.0 / denom,
            ),
            dxfattribs={"layer": "ПЛАН"},
        )
    msp.add_text(
        f"Черновик, требует проверки. Масштаб {sheet['scale']}. {note}",
        dxfattribs={"insert": (origin_x, inner.ymax - 8), "height": 3.5, "layer": "ПЛАН"},
    )
    if sheet.get("warning"):
        msp.add_text(sheet["warning"], dxfattribs={"insert": (origin_x, origin_y), "height": 3.5, "layer": "ПЛАН"})
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(path))


def plans_from_ifc(src: Path, out_dir: Path, *, cut_m: float = DEFAULT_CUT_M, per_floor: bool = False) -> dict:
    imported = _import_ifc()
    if imported is None:
        return {"ok": False, "skipped": True, "error": "ifcopenshell не установлен, поэтажные планы пропущены", "sheets": []}
    ifcopenshell, _geom, unit = imported
    try:
        model = ifcopenshell.open(str(src))
    except Exception as exc:
        return {"ok": False, "skipped": False, "error": f"IFC не открыт: {exc}", "sheets": []}
    try:
        unit_scale = float(unit.calculate_unit_scale(model)) or 1.0
    except Exception:
        unit_scale = 1.0
    storeys = list(model.by_type("IfcBuildingStorey"))
    if not storeys:
        return {"ok": False, "skipped": False, "error": "В модели нет этажей IfcBuildingStorey", "sheets": []}
    prepared = []
    for storey in storeys:
        section = section_storey(model, storey, cut_m, unit_scale)
        width_m, height_m, bounds = _extent(section["segments"], unit_scale)
        prepared.append((storey, section, width_m, height_m, bounds))
    largest = max(prepared, key=lambda item: item[2] * item[3])
    common = choose_sheet(largest[2], largest[3]) if not per_floor else None
    out_dir = Path(out_dir)
    sheets = []
    for index, (storey, section, width_m, height_m, bounds) in enumerate(prepared, start=1):
        name = str(storey.Name or f"Этаж {index}")
        sheet = choose_sheet(width_m, height_m) if per_floor else common
        warning = ""
        if not section["segments"]:
            warning = f"{name}: этаж без геометрии на высоте сечения"
        if sheet["warning"]:
            warning = (warning + " " + sheet["warning"]).strip()
        safe = "".join(char if char.isalnum() else "_" for char in name).strip("_") or f"storey_{index}"
        dest = out_dir / f"{index:02d}_{safe}.dxf"
        meta = _stamp_meta(model, storey, unit_scale)
        _draw_sheet(dest, section["segments"], bounds, sheet, meta, unit_scale, CUT_NOTE)
        sheets.append(
            {
                "storey": name,
                "path": str(dest),
                "scale": sheet["scale"],
                "format": sheet["format"],
                "fits": sheet["fits"],
                "segments": len(section["segments"]),
                "width_m": round(width_m, 4),
                "height_m": round(height_m, 4),
                "warning": warning,
                "skipped_products": section["skipped"],
                "stamp": meta,
            }
        )
    return {"ok": True, "skipped": False, "error": "", "cut_m": cut_m, "note": CUT_NOTE, "sheets": sheets, "per_floor": per_floor}


def plans_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gost-check plans")
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    parser.add_argument("--cut", type=float, default=DEFAULT_CUT_M)
    parser.add_argument("--per-floor", action="store_true")
    args = parser.parse_args(argv)
    result = plans_from_ifc(Path(args.input), Path(args.out), cut_m=args.cut, per_floor=args.per_floor)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") or result.get("skipped") else 2
