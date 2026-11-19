"""Экспорт чертежа в векторный PDF и в IFC4 из двумерных пометок.

DWG здесь не создаётся: сверка конвертером отложена. Из плоского чертежа не строятся тела.
Если на входе уже IFC, файл возвращается как есть после проверки, что он открывается.
Версии чертежа не ведутся.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from .checks.linetypes import lw_to_mm, nearest_lineweight_mm
from .constants import SHEET_FORMATS
from .units import resolve_insunits

FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
DWG_ERROR = "DWG не создан: конвертер не вызывается, сверка DWG отложена. PDF и IFC от этого не зависят."
VERSION_ERROR = "Версии чертежа не ведутся, экспорт выбранной версии пропущен."
WEIGHT_NOTE = "Толщина штриха в PDF приведена к ряду ГОСТ 2.303, который уже использует проверка линий."


def _page_mm(doc) -> tuple[float, float, str]:
    xs: list[float] = []
    ys: list[float] = []
    for entity in doc.modelspace():
        if str(entity.dxf.layer or "") != "SPDS_FRAME" or entity.dxftype() != "LWPOLYLINE":
            continue
        for point in entity.get_points("xy"):
            xs.append(float(point[0]))
            ys.append(float(point[1]))
    to_mm, _warning = resolve_insunits(doc)
    if xs:
        return (max(xs) - min(xs)) * to_mm, (max(ys) - min(ys)) * to_mm, "по рамке листа"
    width, height = SHEET_FORMATS["A3"]
    return max(width, height), min(width, height), "рамка не найдена, лист А3"


def _weight_mm(entity, doc) -> float:
    code = None
    try:
        code = int(entity.dxf.lineweight)
    except Exception:
        code = None
    if code is None or code <= 0:
        try:
            code = int(doc.layers.get(entity.dxf.layer).dxf.lineweight)
        except Exception:
            code = None
    value = lw_to_mm(code) if code and code > 0 else 0.25
    return nearest_lineweight_mm(value or 0.25)


def _segments_and_texts(doc):
    to_mm, _warning = resolve_insunits(doc)
    segments = []
    texts = []
    for entity in doc.modelspace():
        if entity.dxftype() == "LINE":
            segments.append(
                {
                    "layer": str(entity.dxf.layer or "0"),
                    "a": (entity.dxf.start.x * to_mm, entity.dxf.start.y * to_mm),
                    "b": (entity.dxf.end.x * to_mm, entity.dxf.end.y * to_mm),
                    "weight": _weight_mm(entity, doc),
                }
            )
        elif entity.dxftype() == "TEXT":
            texts.append(
                {
                    "layer": str(entity.dxf.layer or "0"),
                    "at": (entity.dxf.insert.x * to_mm, entity.dxf.insert.y * to_mm),
                    "text": str(entity.dxf.text or ""),
                    "height": float(entity.dxf.height or 2.5) * to_mm,
                }
            )
    return segments, texts


def write_pdf(doc, dest: Path) -> dict:
    import fitz

    width_mm, height_mm, source = _page_mm(doc)
    segments, texts = _segments_and_texts(doc)
    pdf = fitz.open()
    page = pdf.new_page(width=width_mm * 72 / 25.4, height=height_mm * 72 / 25.4)
    font_note = ""
    if FONT.is_file():
        page.insert_font(fontname="dejavu", fontfile=str(FONT))
    else:
        font_note = "Шрифт DejaVu в системе не найден, кириллица в PDF не встроена."
    shape = page.new_shape()
    for item in segments:
        shape.draw_line(
            fitz.Point(item["a"][0] * 72 / 25.4, (height_mm - item["a"][1]) * 72 / 25.4),
            fitz.Point(item["b"][0] * 72 / 25.4, (height_mm - item["b"][1]) * 72 / 25.4),
        )
        shape.finish(color=(0, 0, 0), width=max(item["weight"], 0.05) * 72 / 25.4)
    shape.commit()
    for item in texts:
        if not item["text"]:
            continue
        kwargs = {"fontsize": max(item["height"], 1.8) * 72 / 25.4}
        if FONT.is_file():
            kwargs["fontname"] = "dejavu"
        page.insert_text(
            fitz.Point(item["at"][0] * 72 / 25.4, (height_mm - item["at"][1]) * 72 / 25.4),
            item["text"],
            **kwargs,
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(str(dest))
    pdf.close()
    return {"path": str(dest), "width_mm": width_mm, "height_mm": height_mm, "page": source, "font_note": font_note, "note": WEIGHT_NOTE}


def write_ifc(doc, dest: Path) -> dict:
    import ifcopenshell.api.context as context
    import ifcopenshell.api.project as project
    import ifcopenshell.api.root as root
    import ifcopenshell.api.unit as unit

    segments, _texts = _segments_and_texts(doc)
    model = project.create_file(version="IFC4")
    root.create_entity(model, ifc_class="IfcProject", name="Чертёж")
    unit.assign_unit(model, length={"is_metric": True, "raw": "MILLIMETERS"})
    plan = context.add_context(model, context_type="Plan")
    annotation_context = context.add_context(
        model,
        context_type="Plan",
        context_identifier="Annotation",
        target_view="PLAN_VIEW",
        parent=plan,
    )
    by_layer: dict[str, list] = {}
    for item in segments:
        by_layer.setdefault(item["layer"], []).append(item)
    for layer, items in by_layer.items():
        curves = []
        for item in items:
            start = model.create_entity("IfcCartesianPoint", Coordinates=(float(item["a"][0]), float(item["a"][1])))
            end = model.create_entity("IfcCartesianPoint", Coordinates=(float(item["b"][0]), float(item["b"][1])))
            curves.append(model.create_entity("IfcPolyline", Points=[start, end]))
        if not curves:
            continue
        geometric = model.create_entity("IfcGeometricCurveSet", Elements=curves)
        representation = model.create_entity(
            "IfcShapeRepresentation",
            ContextOfItems=annotation_context,
            RepresentationIdentifier="Annotation",
            RepresentationType="GeometricCurveSet",
            Items=[geometric],
        )
        shape = model.create_entity("IfcProductDefinitionShape", Representations=[representation])
        annotation = root.create_entity(model, ifc_class="IfcAnnotation", name=layer or "0")
        annotation.Representation = shape
    dest.parent.mkdir(parents=True, exist_ok=True)
    model.write(str(dest))
    return {
        "path": str(dest),
        "annotations": len(list(model.by_type("IfcAnnotation"))),
        "walls": len(list(model.by_type("IfcWall"))),
        "extrusions": len(list(model.by_type("IfcExtrudedAreaSolid"))),
        "note": "IFC содержит только плоские IfcAnnotation по слоям. Тела из чертежа не строились.",
    }


def _copy_ifc(src: Path, dest: Path) -> dict:
    import ifcopenshell

    model = ifcopenshell.open(str(src))
    walls = len(list(model.by_type("IfcWall")))
    solids = len(list(model.by_type("IfcExtrudedAreaSolid")))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    again = ifcopenshell.open(str(dest))
    return {
        "path": str(dest),
        "validated": True,
        "walls": len(list(again.by_type("IfcWall"))),
        "extrusions": len(list(again.by_type("IfcExtrudedAreaSolid"))),
        "unchanged": walls == len(list(again.by_type("IfcWall"))) and solids == len(list(again.by_type("IfcExtrudedAreaSolid"))),
        "note": "Исходный IFC возвращён как есть и повторно открыт. Новые тела не добавлялись.",
    }


def export_drawing(src: Path, out_dir: Path, *, pdf: bool = True, ifc: bool = True, dwg: bool = False, version: str = "") -> dict:
    src = Path(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "dwg": {"requested": dwg, "written": False, "error": DWG_ERROR if dwg else ""},
        "version": {"requested": version, "exported": False, "error": VERSION_ERROR if version else ""},
        "files": {},
        "errors": [],
    }
    if version:
        result["errors"].append(VERSION_ERROR)
    if dwg:
        result["errors"].append(DWG_ERROR)
    suffix = src.suffix.lower()
    if suffix in {".ifc", ".ifczip"}:
        if ifc:
            try:
                result["files"]["ifc"] = _copy_ifc(src, out_dir / (src.stem + ".ifc"))
            except Exception as exc:
                result["errors"].append(f"IFC не открыт: {exc}")
        if pdf:
            result["errors"].append("Из IFC векторный PDF чертежа не строится: файл возвращён как есть.")
    elif suffix == ".dxf":
        import ezdxf

        doc = ezdxf.readfile(str(src))
        if pdf:
            result["files"]["pdf"] = write_pdf(doc, out_dir / (src.stem + ".pdf"))
        if ifc:
            try:
                result["files"]["ifc"] = write_ifc(doc, out_dir / (src.stem + ".ifc"))
            except ImportError:
                result["errors"].append("ifcopenshell не установлен, IFC пропущен. PDF от этого не зависит.")
            except Exception as exc:
                result["errors"].append(f"IFC не записан: {exc}")
    else:
        result["errors"].append(f"Для экспорта нужен DXF или IFC, получено {suffix or 'без расширения'}.")
    produced = [Path(item["path"]) for item in result["files"].values() if item.get("path")]
    if produced:
        archive = out_dir / "export.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            for path in produced:
                handle.write(path, path.name)
        result["zip"] = str(archive)
    else:
        result["zip"] = ""
    result["ok"] = bool(produced) or (suffix in {".ifc", ".ifczip"} and not result["errors"])
    return result


def export_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="gost-check export")
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    parser.add_argument("--pdf", action="store_true")
    parser.add_argument("--ifc", action="store_true")
    parser.add_argument("--dwg", action="store_true")
    parser.add_argument("--version", default="")
    args = parser.parse_args(argv)
    pdf = args.pdf or not args.ifc
    ifc = args.ifc or not args.pdf
    if args.dwg and not args.pdf and not args.ifc:
        pdf, ifc = True, True
    result = export_drawing(Path(args.input), Path(args.out), pdf=pdf, ifc=ifc, dwg=args.dwg, version=args.version)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2
